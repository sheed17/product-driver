"""Asking the target repository what it already knows about what changed.

The failure: a candidate was reported ready for the founder to push after adding
two persisted tables. Every generated scenario passed, the independent review
reproduced runtime evidence, and the target repository's own standing guard —
"no persisted table may appear outside the baseline manifest" — failed in CI on
the exact tree. The driver had never asked.

What these tests hold in place:

* the check fires from the DIFF, not from the task, and only for surfaces that
  carry repository-wide guarantees;
* what runs is the repository's own test, discovered by reading the repository.
  Nothing here names a product, a table, a phase or a test;
* a feature test is not mistaken for a repository-wide guard, and a repository
  with no guard is recorded as having none rather than being told to grow one;
* a guard that FAILS is a product finding. A guard that cannot be EXECUTED is a
  fact about this environment — it blocks the push-readiness claim without
  being reported as a defect in the product;
* nothing is written into the repository being verified.

Every repository here is synthetic and lives in a temporary directory.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from neyma_product_driver.repo_verification import (
    detect_test_runner,
    discover_verification,
    surfaces_touched,
    verify_repository_surfaces,
)

# --------------------------------------------------------------------------
# A synthetic target repository with a real, runnable standing guard
# --------------------------------------------------------------------------

#: The repository's own baseline: the persisted tables it has adjudicated.
MANIFEST = """\
adjudicated_tables:
  - accounts
  - ledger_entries
"""

#: The repository's own repository-WIDE guard. It discovers the persisted
#: surface at runtime and refuses anything the baseline does not account for —
#: the shape that notices a table a change added and nobody classified.
POSTURE_GUARD = '''\
"""Standing guards over the persisted surface as a whole."""

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def declared_tables():
    text = (ROOT / "baseline_manifest.yaml").read_text()
    return set(yaml.safe_load(text)["adjudicated_tables"])


def live_tables():
    found = set()
    for path in (ROOT / "db").rglob("*.py"):
        found |= set(re.findall(r"CREATE TABLE (\\w+)", path.read_text()))
    return found


def test_every_persisted_table_is_declared_in_the_baseline_manifest():
    assert live_tables() <= declared_tables(), (
        f"table(s) outside the baseline manifest: {sorted(live_tables() - declared_tables())}"
    )


def test_no_new_table_appeared_without_a_column_policy():
    for name in live_tables():
        assert name.islower(), name


def test_the_manifest_declares_a_schema_for_every_column_group():
    assert declared_tables()


def test_all_migrations_are_rerunnable_against_the_schema():
    assert (ROOT / "db" / "migrations").exists()
'''

#: A feature test: full of the same vocabulary, about one thing. It must not be
#: mistaken for a standing guard, or "bounded check" means "run the suite".
FEATURE_TEST = '''\
"""The ledger table's own behaviour."""


def test_a_ledger_entry_is_written_with_its_table_column_defaults():
    assert True


def test_a_ledger_entry_rejects_a_duplicate_index_on_the_same_column():
    assert True


def test_the_ledger_schema_migration_adds_the_amount_column():
    assert True


def test_a_ledger_read_uses_the_database_index():
    assert True


def test_two_ledger_writes_in_one_storage_transaction_are_atomic():
    assert True
'''


class TargetRepo:
    """A product repository with persisted storage and its own standing guards."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
        # pyproject is how `detect_test_runner` recognises a Python repository
        # with no virtualenv of its own.
        self.write("pyproject.toml", "[project]\nname = 'target'\nversion = '0'\n")
        self.write("baseline_manifest.yaml", MANIFEST)
        self.write(
            "db/migrations/0001_initial.py",
            "SQL = '''CREATE TABLE accounts (id TEXT);\nCREATE TABLE ledger_entries (id TEXT);'''\n",
        )
        self.write("tests/test_storage_posture.py", POSTURE_GUARD)
        self.write("tests/test_ledger_feature.py", FEATURE_TEST)
        self.write("README.md", "# target\n")
        self.commit("init")

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def commit(self, message: str) -> None:
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=self.root, check=True)

    def add_unadjudicated_table(self) -> str:
        """The change that CI caught and the driver did not."""
        rel = "db/migrations/0002_add_evidence.py"
        self.write(rel, "SQL = '''CREATE TABLE evidence (id TEXT);'''\n")
        self.commit("add evidence table")
        return rel

    def dirty(self) -> str:
        return subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()


@pytest.fixture
def target(tmp_path: Path) -> TargetRepo:
    return TargetRepo(tmp_path / "target")


def verify(target: TargetRepo, changed: list[str], **kw):
    return verify_repository_surfaces(target.root, changed, timeout_s=300, **kw)


# --------------------------------------------------------------------------
# 1. It fires on the surface, from the diff
# --------------------------------------------------------------------------


class TestSurfaceDetection:
    def test_a_migration_change_touches_the_persisted_surface(
        self, target: TargetRepo
    ) -> None:
        specs, evidence = surfaces_touched(target.root, ["db/migrations/0002_add_evidence.py"])
        assert [s.name for s in specs] == ["persisted schema, migration or storage"]
        assert "0002_add_evidence.py" in " ".join(evidence)

    def test_a_file_that_declares_a_table_is_persisted_whatever_it_is_called(
        self, target: TargetRepo
    ) -> None:
        target.write("app/core.py", "SQL = 'CREATE TABLE surprises (id TEXT)'\n")
        specs, _ = surfaces_touched(target.root, ["app/core.py"])
        assert specs, "a file declaring a table changed persisted schema"

    def test_a_documentation_change_touches_nothing(self, target: TargetRepo) -> None:
        specs, _ = surfaces_touched(target.root, ["README.md"])
        assert specs == []

    def test_a_change_outside_the_surface_runs_no_repository_verification(
        self, target: TargetRepo
    ) -> None:
        record = verify(target, ["README.md"])
        assert record.applicable is False
        assert record.results == []
        assert record.blocks_push is False
        assert "not applicable" in record.headline()


# --------------------------------------------------------------------------
# 2. Discovery reads the repository, and stays bounded
# --------------------------------------------------------------------------


class TestDiscovery:
    def test_the_repositorys_own_standing_guard_is_found(self, target: TargetRepo) -> None:
        specs, _ = surfaces_touched(target.root, ["db/migrations/0002_add_evidence.py"])
        targets, _ = discover_verification(target.root, specs, max_targets=3)
        assert [t.path for t in targets] == ["tests/test_storage_posture.py"]

    def test_a_feature_test_is_not_a_repository_wide_guard(self, target: TargetRepo) -> None:
        specs, _ = surfaces_touched(target.root, ["db/migrations/0002_add_evidence.py"])
        targets, _ = discover_verification(target.root, specs, max_targets=5)
        assert "tests/test_ledger_feature.py" not in [t.path for t in targets]

    def test_the_number_executed_is_capped(self, target: TargetRepo) -> None:
        for n in range(4):
            target.write(
                f"tests/test_extra_posture_{n}.py",
                POSTURE_GUARD.replace("def test_", f"def test_{n}_"),
            )
        target.commit("more guards")
        specs, _ = surfaces_touched(target.root, ["db/migrations/0002_add_evidence.py"])
        targets, notes = discover_verification(target.root, specs, max_targets=2)
        assert len(targets) == 2
        assert any("bounded check" in n for n in notes)

    def test_a_repository_with_no_guard_is_recorded_not_corrected(
        self, tmp_path: Path
    ) -> None:
        bare = TargetRepo(tmp_path / "bare")
        (bare.root / "tests" / "test_storage_posture.py").unlink()
        (bare.root / "tests" / "test_ledger_feature.py").unlink()
        bare.commit("no guards at all")
        record = verify(bare, ["db/migrations/0002_add_evidence.py"])
        assert record.applicable is True
        assert record.targets == []
        assert record.blocks_push is False, "an absent guard is not a demand to grow one"
        assert any("could be found" in n for n in record.notes)

    def test_the_chosen_command_explains_itself(self, target: TargetRepo) -> None:
        specs, _ = surfaces_touched(target.root, ["db/migrations/0002_add_evidence.py"])
        targets, _ = discover_verification(target.root, specs, max_targets=1)
        why = " ".join(targets[0].why)
        assert "persisted schema" in why
        assert "asserts across the surface" in why

    def test_the_runner_comes_from_the_repository(self, target: TargetRepo) -> None:
        assert detect_test_runner(target.root).endswith("-m pytest")
        (target.root / ".venv" / "bin").mkdir(parents=True)
        (target.root / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")
        assert detect_test_runner(target.root).startswith(".venv/bin/python")


# --------------------------------------------------------------------------
# 3. Execution, and what each outcome means
# --------------------------------------------------------------------------


class TestExecution:
    def test_a_passing_guard_clears_the_push_boundary(self, target: TargetRepo) -> None:
        record = verify(target, ["db/migrations/0001_initial.py"], max_targets=1)
        assert [r.passed for r in record.results] == [True]
        assert record.blocks_push is False
        assert record.blocks_acceptance is False

    def test_the_failure_the_driver_should_have_caught_before_the_push_boundary(
        self, target: TargetRepo
    ) -> None:
        """The whole defect, executed.

        A new persisted table the baseline does not account for. Nothing the run
        generated would notice; the repository's own guard notices immediately.
        """
        changed = target.add_unadjudicated_table()
        record = verify(target, [changed], max_targets=1)
        assert record.product_failures, record.summary_block()
        assert record.blocks_acceptance is True
        assert record.blocks_push is True
        assert "evidence" in record.product_failures[0].detail

    def test_a_failure_is_attributed_to_the_repositorys_own_verification(
        self, target: TargetRepo
    ) -> None:
        changed = target.add_unadjudicated_table()
        record = verify(target, [changed], max_targets=1)
        assert "tests/test_storage_posture.py" in record.headline()
        assert record.infrastructure_problems == []

    def test_a_guard_that_cannot_run_is_infrastructure_not_a_product_defect(
        self, target: TargetRepo
    ) -> None:
        target.write(
            "tests/test_storage_posture.py",
            "import a_module_that_does_not_exist_anywhere\n" + POSTURE_GUARD,
        )
        target.commit("break the guard's own imports")
        record = verify(target, ["db/migrations/0001_initial.py"], max_targets=1)
        assert record.infrastructure_problems, record.summary_block()
        assert record.product_failures == []
        assert record.blocks_acceptance is False, "CI/infra is not a product defect"
        assert record.blocks_push is True, "an unanswered guard is not a green one"
        assert "could not be executed" in record.headline()

    def test_nothing_is_written_into_the_repository_being_verified(
        self, target: TargetRepo
    ) -> None:
        assert target.dirty() == ""
        verify(target, ["db/migrations/0001_initial.py"], max_targets=2)
        assert target.dirty() == "", "verifying a tree must not change it"

    def test_the_record_names_the_tree_it_is_about(self, target: TargetRepo) -> None:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(target.root),
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        record = verify_repository_surfaces(
            target.root, ["db/migrations/0001_initial.py"], max_targets=1, commit=head
        )
        assert record.commit == head

    def test_a_command_the_guard_refuses_is_never_executed(self, target: TargetRepo) -> None:
        from neyma_product_driver.repo_verification import VerificationTarget, run_verification

        record = run_verification(
            target.root,
            [VerificationTarget(path="x", command="git push origin main")],
            timeout_s=30,
        )
        assert record.results[0].infrastructure is True
        assert "command guard refuses" in record.results[0].detail
        assert record.blocks_acceptance is False


# --------------------------------------------------------------------------
# 4. It is not a suite run
# --------------------------------------------------------------------------


def test_it_does_not_run_every_test_for_every_task(target: TargetRepo) -> None:
    """The property that keeps this affordable.

    A documentation change runs nothing at all, and a persisted change runs the
    guards for that surface — not the repository's suite.
    """
    assert verify(target, ["README.md"]).results == []
    record = verify(target, ["db/migrations/0001_initial.py"], max_targets=3)
    assert 0 < len(record.results) < 3
    assert all("test_ledger_feature" not in r.target.path for r in record.results)


def test_the_python_running_the_guard_is_the_repositorys_own(target: TargetRepo) -> None:
    specs, _ = surfaces_touched(target.root, ["db/migrations/0001_initial.py"])
    targets, _ = discover_verification(target.root, specs, max_targets=1)
    assert targets[0].command.startswith(sys.executable)
    assert "-p no:cacheprovider" in targets[0].command


# --------------------------------------------------------------------------
# 5. Inside the control loop, at the push boundary
# --------------------------------------------------------------------------


async def test_the_loop_refuses_an_accept_the_repositorys_own_guard_refutes(
    target: TargetRepo, tmp_path: Path
) -> None:
    """End to end: product evaluator says ACCEPT, the repository says no.

    The evaluator is happy, the scenario passes, nothing the run generated knows
    about the baseline manifest — and the run must not end ACCEPTED, because the
    guard the repository already runs fails on this exact tree.
    """
    import shutil

    from neyma_product_driver.cli import run_control_loop
    from neyma_product_driver.config import DriverConfig
    from neyma_product_driver.context import RepositoryContextLoader, load_founder_context
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.models import (
        AssertionResult,
        Decision,
        EvaluatorDecision,
        RunState,
        RunStatus,
        ScenarioResult,
    )
    from neyma_product_driver.scenarios import Scenario

    driver_root = tmp_path / "driver"
    driver_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        Path(__file__).resolve().parent.parent / "founder_context",
        driver_root / "founder_context",
        dirs_exist_ok=True,
    )
    config = DriverConfig(
        neyma_repo=target.root, driver_root=driver_root, task="add the evidence store",
        max_iterations=1,
    )
    config.repository_verification.timeout_s = 300
    config.repository_verification.max_targets = 1
    assert config.runs_dir is not None
    store = EvidenceStore(config.runs_dir, "surface-run")
    state = RunState(run_id=store.run_id, task="add the evidence store", max_iterations=1)

    class Builder:
        """A builder that does the work and COMMITS it, as a real one does.

        The committed half is the half that used to escape: at the moment the
        run decides whether to declare push readiness, a builder that committed
        leaves a clean working tree, and a check that reads only the tree sees
        an empty diff and verifies nothing.
        """

        session_id = "b1"

        async def send(self, prompt, timeout_s=None):
            target.add_unadjudicated_table()

            class T:
                text = "I added the evidence store. Targeted tests pass."
                session_id = "b1"
                tool_uses: list[str] = []
                denied_requests: list[str] = []
                is_error = False
                error_detail = ""

            return T()

    class Evaluator:
        session_id = "e1"

        async def evaluate(self, prompt, timeout_s=None):
            return EvaluatorDecision(
                decision=Decision.ACCEPT, summary="the store works", confidence=0.9
            )

    def make_executor(artifact_dir: Path):
        class Ex:
            service_logs: dict[str, str] = {}

            async def execute(self, sc):
                return ScenarioResult(
                    scenario_name=sc.name,
                    assertions=[AssertionResult(kind="expect_visible", target="x", passed=True)],
                )

        return Ex()

    result = await run_control_loop(
        config=config,
        scenario=Scenario(name="surface-scenario"),
        store=store,
        state=state,
        builder=Builder(),
        evaluator=Evaluator(),
        make_executor=make_executor,
        emit=lambda _m: None,
        founder=load_founder_context(driver_root),
        repo_loader=RepositoryContextLoader(target.root),
    )

    assert result.status is not RunStatus.ACCEPTED
    verification = result.repository_verification
    assert verification is not None and verification.product_failures
    assert verification.blocks_push is True
    recorded = store.run_dir / "iteration-01" / "repository-verification.json"
    assert recorded.exists(), "the refusal must be evidence, not only a terminal line"
