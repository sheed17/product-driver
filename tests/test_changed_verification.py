"""When the deliverable IS a guard, the guard gets run.

The failure this file reproduces, in the shape it actually occurred. A task's
whole deliverable was two boundary guards: one corrected oracle, one new
reconciliation guard over a status document. The builder changed exactly those
two files and reported both green. Product Driver executed one permanent
regression scenario for an unrelated unit, the evaluator correctly refused to
treat a builder's self-report as an observation, and the run STOPPED and asked
the founder to relay two measurements that were one command each, already in the
repository, and already passing.

Nothing was wrong with the product, the evaluator, or the acceptance gate. The
driver had never asked the changed guard to run. What these tests hold:

* a changed verification file is a risk surface, and an unrelated passing
  scenario is not an observation of it, however green;
* the observation is taken by the DRIVER, from the repository's own guard, as the
  repository runs it. A builder's report does not count and a generated
  approximation is not substituted for a real guard that exists;
* a changed guard that genuinely FAILS is executable engineering work and goes
  back to the builder, not to the founder;
* a changed guard that cannot be executed is a GAP — it refuses the claim, not
  the product — and so is a changed absence-asserting guard with nothing to show
  it can still fire;
* verification stays bounded. A test-file edit runs that file and the narrow set
  of guards the repository already keeps over the other files in the diff. It
  never runs the repository;
* an interrupted run does not forget what it still owes.

Every repository here is synthetic and lives in a temporary directory. Every
Claude session is faked; nothing here consumes Claude usage.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from neyma_product_driver.changed_verification import (
    changed_verification_paths,
    discrimination_gaps,
    select_changed_verification,
    verify_changed_surface,
)
from neyma_product_driver.cli import run_control_loop
from neyma_product_driver.config import ChangedVerificationConfig, DriverConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import (
    Decision,
    EvaluatorDecision,
    RunState,
    RunStatus,
)
from neyma_product_driver.scenarios import Scenario

from test_scenario_loop import FakeEvaluator, FakeTurn, RecordingExecutor

# --------------------------------------------------------------------------
# A synthetic target repository whose deliverable is its own guards
# --------------------------------------------------------------------------

#: The status document the second guard reconciles against. Prose plus a bounded
#: machine-readable block, which is the shape the real defect was about.
STATUS_DOC = """\
# Where the work stands

Historical narrative nobody may rewrite.

<!-- LIVE-STATUS:BEGIN -->
active_unit: U-7
readiness: LOCALLY_IMPLEMENTED
<!-- LIVE-STATUS:END -->
"""

REGISTRY = """\
active_unit: U-7
readiness: LOCALLY_IMPLEMENTED
"""

#: The guard as it stood BEFORE the change: its fourth clause reads a lifecycle
#: field that legitimate acceptance moves, so it is about to go stale.
SHIPS_DARK_BEFORE = '''\
"""Standing guards that the unit ships dark."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def readiness():
    for line in (ROOT / "registry.yaml").read_text().splitlines():
        if line.startswith("readiness:"):
            return line.split(":", 1)[1].strip()
    return ""


def test_no_production_module_enables_the_unit():
    assert "ENABLED" not in (ROOT / "app" / "runtime.py").read_text()


def test_the_recorded_readiness_is_not_promoted():
    assert readiness() == "LOCALLY_IMPLEMENTED"
'''

#: The same guard AFTER the change, with the stale clause corrected and its own
#: discrimination case: it admits the accepted-but-still-dark state and still
#: catches a real promotion.
SHIPS_DARK_AFTER = '''\
"""Standing guards that the unit ships dark."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

DARK_READINESS = {"LOCALLY_IMPLEMENTED", "ACCEPTED_STILL_DARK"}


def readiness():
    for line in (ROOT / "registry.yaml").read_text().splitlines():
        if line.startswith("readiness:"):
            return line.split(":", 1)[1].strip()
    return ""


def over_promoted(value):
    return value not in DARK_READINESS


def test_no_production_module_enables_the_unit():
    assert "ENABLED" not in (ROOT / "app" / "runtime.py").read_text()


def test_the_recorded_readiness_is_not_promoted():
    assert not over_promoted(readiness())


def test_the_oracle_admits_an_accepted_but_dark_unit_and_catches_real_enablement():
    assert not over_promoted("ACCEPTED_STILL_DARK")
    assert over_promoted("DEPLOYED")
    assert over_promoted("ENABLED")
'''

#: The corrected guard, minus its discrimination case. Structurally green and
#: unable to prove it discriminates anything.
SHIPS_DARK_AFTER_VACUOUS = SHIPS_DARK_AFTER.replace(
    '''

def test_the_oracle_admits_an_accepted_but_dark_unit_and_catches_real_enablement():
    assert not over_promoted("ACCEPTED_STILL_DARK")
    assert over_promoted("DEPLOYED")
    assert over_promoted("ENABLED")
''',
    "",
)

#: The new reconciliation guard, with its own anti-vacuity case.
RECONCILIATION_GUARD = '''\
"""The live-status block may not drift from the registry."""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
BLOCK = re.compile(r"LIVE-STATUS:BEGIN -->(.*?)<!-- LIVE-STATUS:END", re.S)


def live_block(text=None):
    text = text if text is not None else (ROOT / "status" / "CURRENT.md").read_text()
    found = BLOCK.search(text)
    return dict(
        line.split(":", 1)[0].strip() and (line.split(":", 1)[0].strip(), line.split(":", 1)[1].strip())
        for line in (found.group(1) if found else "").splitlines()
        if ":" in line
    )


def registry():
    return dict(
        (line.split(":", 1)[0].strip(), line.split(":", 1)[1].strip())
        for line in (ROOT / "registry.yaml").read_text().splitlines()
        if ":" in line
    )


def test_the_live_status_block_does_not_drift_from_the_registry():
    assert live_block() == registry()


def test_the_reconciliation_is_populated_and_its_drift_detector_fires():
    assert live_block(), "the live-status block is empty; the guard would pass vacuously"
    drifted = (ROOT / "status" / "CURRENT.md").read_text().replace("U-7", "U-9")
    assert live_block(drifted) != registry()
'''

#: A guard the repository already keeps over the status document — the narrow
#: collateral a status change earns, and nothing wider.
STATUS_SHAPE_GUARD = '''\
"""The status document's own shape."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_status_document_keeps_its_historical_narrative():
    text = (ROOT / "status" / "CURRENT.md").read_text()
    assert "Historical narrative" in text
    assert "LIVE-STATUS:BEGIN" in text
'''

#: Two unrelated suites. They exist to be NOT run: a driver that answers "a test
#: file changed" by running every test file has discovered nothing.
UNRELATED_SUITE = '''\
"""An unrelated feature's own tests."""


def test_the_feature_does_its_thing():
    assert True


def test_the_feature_refuses_a_bad_input():
    assert True
'''


class GuardRepo:
    """A target repository whose deliverable is its own verification."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._git("init", "-q")
        self._git("config", "user.email", "t@e.com")
        self._git("config", "user.name", "t")
        self.write("CLAUDE.md", "# authority\n")
        # How `detect_test_runner` recognises a Python repository with no
        # virtualenv of its own.
        self.write("pyproject.toml", "[project]\nname = 'target'\nversion = '0'\n")
        self.write("registry.yaml", REGISTRY)
        self.write("status/CURRENT.md", STATUS_DOC)
        self.write("app/runtime.py", "MODE = 'dark'\n")
        self.write("guards/test_ships_dark.py", SHIPS_DARK_BEFORE)
        self.write("guards/test_status_shape.py", STATUS_SHAPE_GUARD)
        self.write("guards/test_unrelated_feature.py", UNRELATED_SUITE)
        self.write("guards/test_another_unrelated.py", UNRELATED_SUITE)
        self.commit("init")

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True, check=False
        ).stdout

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def commit(self, message: str) -> None:
        self._git("add", "-A")
        self._git("commit", "-qm", message)

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").strip()

    # -- the change the builder makes -----------------------------------

    def deliver_the_two_guards(self, *, ships_dark: str = SHIPS_DARK_AFTER) -> list[str]:
        """Exactly the diff of the run this file is about: two guards and a doc."""
        self.write("guards/test_ships_dark.py", ships_dark)
        self.write("guards/test_status_reconciliation.py", RECONCILIATION_GUARD)
        self.write(
            "status/CURRENT.md",
            STATUS_DOC.replace("readiness: LOCALLY_IMPLEMENTED", "readiness: LOCALLY_IMPLEMENTED"),
        )
        return [
            "guards/test_ships_dark.py",
            "guards/test_status_reconciliation.py",
            "status/CURRENT.md",
        ]

    def deliver_a_failing_guard(self) -> list[str]:
        """The same change, with the corrected oracle actually wrong."""
        self.write(
            "guards/test_ships_dark.py",
            SHIPS_DARK_AFTER.replace(
                'assert over_promoted("DEPLOYED")', 'assert not over_promoted("DEPLOYED")'
            ),
        )
        return ["guards/test_ships_dark.py"]

    def deliver_an_unrunnable_guard(self) -> list[str]:
        """A changed guard that never reaches an assertion here."""
        self.write(
            "guards/test_ships_dark.py",
            "import a_module_this_environment_does_not_have\n\n\n"
            "def test_no_production_module_enables_the_unit():\n    assert True\n",
        )
        return ["guards/test_ships_dark.py"]

    def deliver_a_vacuous_negative_guard(self) -> list[str]:
        return self.deliver_the_two_guards(ships_dark=SHIPS_DARK_AFTER_VACUOUS)

    def touch_an_unrelated_test(self) -> list[str]:
        self.write(
            "guards/test_unrelated_feature.py",
            UNRELATED_SUITE + "\n\ndef test_one_more_unrelated_thing():\n    assert True\n",
        )
        return ["guards/test_unrelated_feature.py"]


@pytest.fixture
def guard_repo(tmp_path: Path) -> GuardRepo:
    return GuardRepo(tmp_path / "target")


def changed_config(guard_repo: GuardRepo, tmp_path: Path, **overrides) -> DriverConfig:
    return DriverConfig(
        neyma_repo=guard_repo.root,
        driver_root=tmp_path / "driver",
        runs_dir=tmp_path / "driver" / "runs",
        scenarios_dir=tmp_path / "driver" / "scenarios",
        task=(
            "Correct the stale clause in the ships-dark guard so it admits an "
            "accepted-but-still-dark unit and still catches real enablement, and add a "
            "guard proving the live status block cannot drift from the registry."
        ),
        max_iterations=3,
        changed_verification=ChangedVerificationConfig(**overrides),
    )


# --------------------------------------------------------------------------
# Fakes: a builder that really changes the tree, an evaluator that blocks
# --------------------------------------------------------------------------


class DeliveringBuilder:
    """A builder that makes the change and reports it green — a claim, not proof.

    The self-report is the whole point: it is the only evidence the defective run
    ever had about the two guards, and it must never be enough.
    """

    def __init__(self, repo: GuardRepo, deliver: str = "deliver_the_two_guards") -> None:
        self.session_id = "builder-1"
        self.repo = repo
        self.deliver = deliver
        self.prompts: list[str] = []

    async def send(self, prompt: str, timeout_s: int | None = None) -> FakeTurn:
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            getattr(self.repo, self.deliver)()
        return FakeTurn(
            text=(
                "Both guards are in place. I ran them: 3 passed and 2 passed.\n\n"
                "RUNNABLE CHECKPOINT: run the two guard files."
            )
        )


def never_observed() -> EvaluatorDecision:
    """The refusal the real run recorded, in the real run's own terms."""
    return EvaluatorDecision(
        decision=Decision.BLOCKED,
        summary=(
            "The only scenario executed was an unrelated permanent regression. Neither "
            "changed guard was operated, so the behaviour this change altered was never "
            "observed. The builder's counts are self-reported."
        ),
        problems=[
            "no direct observation that the corrected oracle admits an accepted-but-dark "
            "unit and turns red on a real promotion",
            "no direct observation that the reconciliation guard passes clean and fires on "
            "a forced drift",
        ],
        observed_behavior=["the unrelated scenario passed"],
        confidence=0.7,
    )


def accepts() -> EvaluatorDecision:
    return EvaluatorDecision(
        decision=Decision.ACCEPT,
        summary="the changed guards were operated here and both pass",
        observed_behavior=["both changed guards executed green"],
        confidence=0.9,
    )


async def drive(
    config: DriverConfig,
    guard_repo: GuardRepo,
    decisions: list[EvaluatorDecision],
    *,
    deliver: str = "deliver_the_two_guards",
):
    """One run of the loop: unrelated permanent scenario, no generated coverage."""
    assert config.runs_dir is not None
    store = EvidenceStore(config.runs_dir, "20260913-041432")
    state = RunState(
        run_id=store.run_id, task=config.task, max_iterations=config.max_iterations
    )
    log: list[str] = []
    evaluator = FakeEvaluator(list(decisions))
    result = await run_control_loop(
        config=config,
        scenario=Scenario(name="p6_unrelated_brake"),
        store=store,
        state=state,
        builder=DeliveringBuilder(guard_repo, deliver=deliver),
        evaluator=evaluator,
        make_executor=lambda d: RecordingExecutor(d, {}, log),
        emit=lambda _m: None,
    )
    return result, log, evaluator, store


# --------------------------------------------------------------------------
# 1. Selection: what a changed guard is, read from the diff
# --------------------------------------------------------------------------


class TestSelection:
    def test_a_changed_test_file_is_recognised_as_changed_verification(self) -> None:
        paths = changed_verification_paths(
            [
                "guards/test_ships_dark.py",
                "guards/test_status_reconciliation.py",
                "status/CURRENT.md",
                "app/runtime.py",
                "scripts/probe_boundary.py",
                ".venv/lib/test_vendored.py",
            ]
        )
        assert paths == [
            "guards/test_ships_dark.py",
            "guards/test_status_reconciliation.py",
            "scripts/probe_boundary.py",
        ]

    def test_a_diff_with_no_verification_file_selects_nothing(
        self, guard_repo: GuardRepo
    ) -> None:
        guards, changed, related, _notes = select_changed_verification(
            guard_repo.root, ["app/runtime.py", "status/CURRENT.md"]
        )
        assert (guards, changed, related) == ([], [], [])

    def test_the_changed_guards_and_their_narrow_collateral_are_selected(
        self, guard_repo: GuardRepo
    ) -> None:
        base = guard_repo.head()
        diff = guard_repo.deliver_the_two_guards()

        guards, changed, related, _notes = select_changed_verification(
            guard_repo.root, diff, base_commit=base
        )

        assert changed == [
            "guards/test_ships_dark.py",
            "guards/test_status_reconciliation.py",
        ]
        # The repository's own guard over the status document the diff also
        # changed. Collateral, discovered, and capped.
        assert related == ["guards/test_status_shape.py"]
        # And nothing else. The two unrelated suites are not in the selection.
        selected = {g.path for g in guards}
        assert "guards/test_unrelated_feature.py" not in selected
        assert "guards/test_another_unrelated.py" not in selected

    def test_a_changed_guard_knows_it_asserts_an_absence_and_that_its_semantics_moved(
        self, guard_repo: GuardRepo
    ) -> None:
        base = guard_repo.head()
        diff = guard_repo.deliver_the_two_guards()

        guards, _changed, _related, _notes = select_changed_verification(
            guard_repo.root, diff, base_commit=base
        )
        by_path = {g.path: g for g in guards}

        oracle = by_path["guards/test_ships_dark.py"]
        assert oracle.is_negative_guard
        assert oracle.semantics_changed, "the diff changed what this guard asserts"
        assert oracle.needs_discrimination
        assert oracle.discrimination_names, "its own anti-vacuity case satisfies it"

        new_guard = by_path["guards/test_status_reconciliation.py"]
        assert new_guard.added
        assert new_guard.semantics_changed

        # Collateral is not treated as the deliverable: nobody changed what it
        # asserts, so it earns no discrimination demand.
        shape = by_path["guards/test_status_shape.py"]
        assert shape.relation == "related"
        assert not shape.semantics_changed

    def test_a_new_case_does_not_move_what_an_untouched_negative_test_asserts(
        self, guard_repo: GuardRepo
    ) -> None:
        """Attribution is per test, not per file.

        A suite that happens to contain a `refuses` test does not start owing
        discrimination evidence because somebody added an unrelated case to the
        same file. A file-level answer demanded work nobody owed, which is the
        way a real control turns into noise and then gets switched off.
        """
        base = guard_repo.head()
        diff = guard_repo.touch_an_unrelated_test()

        guards, *_ = select_changed_verification(guard_repo.root, diff, base_commit=base)

        assert len(guards) == 1
        guard = guards[0]
        assert guard.negative_names == ["test_the_feature_refuses_a_bad_input"]
        assert guard.changed_expectation_names == ["test_one_more_unrelated_thing"]
        assert guard.changed_negative_names == []
        assert not guard.needs_discrimination
        assert discrimination_gaps(guard_repo.root, guards) == []

    def test_a_docstring_only_edit_does_not_move_what_a_guard_asserts(
        self, guard_repo: GuardRepo
    ) -> None:
        base = guard_repo.head()
        guard_repo.write(
            "guards/test_ships_dark.py",
            SHIPS_DARK_BEFORE.replace(
                '"""Standing guards that the unit ships dark."""',
                '"""Standing guards that the unit ships dark. Reworded."""',
            ),
        )

        guards, _c, _r, _n = select_changed_verification(
            guard_repo.root, ["guards/test_ships_dark.py"], base_commit=base
        )

        assert [g.semantics_changed for g in guards] == [False]


# --------------------------------------------------------------------------
# 2. Discrimination: a negative guard that cannot fire proves nothing
# --------------------------------------------------------------------------


class TestDiscrimination:
    def test_a_changed_negative_guard_with_an_anti_vacuity_case_has_no_gap(
        self, guard_repo: GuardRepo
    ) -> None:
        base = guard_repo.head()
        diff = guard_repo.deliver_the_two_guards()
        guards, *_ = select_changed_verification(guard_repo.root, diff, base_commit=base)

        assert discrimination_gaps(guard_repo.root, guards) == []

    def test_a_changed_negative_guard_with_no_anti_vacuity_case_is_a_gap(
        self, guard_repo: GuardRepo
    ) -> None:
        base = guard_repo.head()
        diff = guard_repo.deliver_a_vacuous_negative_guard()
        guards, *_ = select_changed_verification(guard_repo.root, diff, base_commit=base)

        gaps = discrimination_gaps(guard_repo.root, guards)

        assert len(gaps) == 1
        assert "guards/test_ships_dark.py" in gaps[0]
        assert "still fires" in gaps[0]


# --------------------------------------------------------------------------
# 3. Execution: the repository's own guard, run as the repository runs it
# --------------------------------------------------------------------------


class TestExecution:
    def test_the_changed_guards_are_executed_and_pass(self, guard_repo: GuardRepo) -> None:
        base = guard_repo.head()
        diff = guard_repo.deliver_the_two_guards()

        record = verify_changed_surface(
            guard_repo.root, diff, base_commit=base, timeout_s=300
        )

        assert record.applicable
        assert not record.product_failures
        assert not record.unexecutable
        assert not record.blocks_claim
        observed = set(record.executed_paths)
        assert {"guards/test_ships_dark.py", "guards/test_status_reconciliation.py"} <= observed

    def test_the_command_executed_is_the_repository_s_own_file_not_a_substitute(
        self, guard_repo: GuardRepo
    ) -> None:
        """A real guard is run, never replaced by a generated approximation."""
        base = guard_repo.head()
        diff = guard_repo.deliver_the_two_guards()
        before = sorted(p.name for p in (guard_repo.root / "guards").iterdir())

        record = verify_changed_surface(
            guard_repo.root, diff, base_commit=base, timeout_s=300
        )

        for result in record.results:
            assert result.target.path in [g.path for g in record.guards]
            assert result.target.path in result.target.command
            assert result.target.command.startswith(sys.executable)
        # Nothing was written into the repository being verified.
        assert sorted(p.name for p in (guard_repo.root / "guards").iterdir()) == before

    def test_a_changed_guard_that_fails_is_a_product_failure(
        self, guard_repo: GuardRepo
    ) -> None:
        base = guard_repo.head()
        diff = guard_repo.deliver_a_failing_guard()

        record = verify_changed_surface(
            guard_repo.root, diff, base_commit=base, timeout_s=300
        )

        assert [r.target.path for r in record.product_failures] == [
            "guards/test_ships_dark.py"
        ]
        assert record.blocks_acceptance
        assert record.blocks_claim

    def test_a_changed_guard_that_cannot_run_is_infrastructure_not_a_failure(
        self, guard_repo: GuardRepo
    ) -> None:
        base = guard_repo.head()
        diff = guard_repo.deliver_an_unrunnable_guard()

        record = verify_changed_surface(
            guard_repo.root, diff, base_commit=base, timeout_s=300
        )

        assert not record.product_failures, "an import error is not a statement about the product"
        assert [r.target.path for r in record.unexecutable] == ["guards/test_ships_dark.py"]
        assert record.blocks_claim, "a guard nobody could run is not a passing guard"
        assert not record.blocks_acceptance

    def test_an_unrelated_test_edit_runs_that_file_and_not_the_repository(
        self, guard_repo: GuardRepo
    ) -> None:
        base = guard_repo.head()
        diff = guard_repo.touch_an_unrelated_test()

        record = verify_changed_surface(
            guard_repo.root, diff, base_commit=base, timeout_s=300
        )

        assert record.executed_paths == ["guards/test_unrelated_feature.py"]
        # Not the other suites, not the guards, and no directory-wide invocation.
        for result in record.results:
            assert " guards " not in result.target.command
            assert result.target.command.count(".py") == 1


# --------------------------------------------------------------------------
# 4. THE DEFECT ITSELF, inside the control loop
# --------------------------------------------------------------------------


class TestTheRunThatStoppedBlocked:
    """Run 20260913-041432, reproduced and then fixed.

    The contrast is the point. With the changed-verification step switched OFF
    the run reproduces the defect exactly — an unrelated scenario, a builder's
    word, and a BLOCKED that asks the founder to relay two commands. With it on,
    the driver takes the observation itself and the run carries on.
    """

    @pytest.mark.asyncio
    async def test_without_the_step_the_run_stops_blocked_on_a_builders_word(
        self, guard_repo: GuardRepo, tmp_path: Path
    ) -> None:
        config = changed_config(guard_repo, tmp_path, enabled=False)

        result, log, evaluator, _store = await drive(
            config, guard_repo, [never_observed(), accepts()]
        )

        # The defect, in full: the only thing operated was an unrelated scenario,
        # the two changed guards were never run, the evaluator was never shown an
        # observation of them, and the run ended asking the founder for one.
        assert result.status is RunStatus.BLOCKED
        assert log == ["p6_unrelated_brake"]
        assert result.changed_verification is None
        assert len(evaluator.prompts) == 1
        assert "DIRECT OBSERVATION" not in evaluator.prompts[0]
        assert "3 passed and 2 passed" in evaluator.prompts[0], (
            "the builder's self-report was the only word on the subject"
        )

    @pytest.mark.asyncio
    async def test_with_the_step_the_changed_guards_are_run_and_the_run_continues(
        self, guard_repo: GuardRepo, tmp_path: Path
    ) -> None:
        config = changed_config(guard_repo, tmp_path, timeout_s=300)

        result, log, evaluator, store = await drive(
            config, guard_repo, [never_observed(), accepts()]
        )

        # 1. the unrelated scenario still ran, exactly once, and is still not the
        #    observation this change needed.
        assert log == ["p6_unrelated_brake"]

        # 2. the driver operated BOTH changed guards itself, and the
        #    discrimination cases inside them came with them.
        verification = result.changed_verification
        assert verification is not None and verification.applicable
        assert set(verification.executed_paths) >= {
            "guards/test_ships_dark.py",
            "guards/test_status_reconciliation.py",
        }
        assert not verification.blocks_claim
        observations = "\n".join(verification.direct_observations())
        assert "catches_real_enablement" in observations
        assert "drift_detector_fires" in observations

        # 3. the evaluator was shown it, before it was asked anything.
        assert "DIRECT OBSERVATION OF THE VERIFICATION THIS CHANGE ITSELF CHANGED" in (
            evaluator.prompts[0]
        )
        assert "guards/test_ships_dark.py" in evaluator.prompts[0]

        # 4. and when it blocked anyway for want of that observation, the run
        #    asked again with it rather than ending and asking the founder.
        assert len(evaluator.prompts) == 2
        assert "THE OBSERVATION YOU RECORDED AS MISSING HAS BEEN TAKEN" in (
            evaluator.prompts[1]
        )
        assert result.status is RunStatus.ACCEPTED

        # 5. it is on disk as evidence, not only in memory.
        assert (store.iteration_dir(1) / "changed-verification.json").is_file()

    @pytest.mark.asyncio
    async def test_a_second_refusal_is_final(
        self, guard_repo: GuardRepo, tmp_path: Path
    ) -> None:
        """The re-ask is one question, not an argument the evaluator must lose."""
        config = changed_config(guard_repo, tmp_path, timeout_s=300)

        result, _log, evaluator, _store = await drive(
            config, guard_repo, [never_observed(), never_observed(), accepts()]
        )

        assert len(evaluator.prompts) == 2
        assert result.status is RunStatus.BLOCKED

    @pytest.mark.asyncio
    async def test_the_re_ask_can_be_switched_off_without_losing_the_observation(
        self, guard_repo: GuardRepo, tmp_path: Path
    ) -> None:
        config = changed_config(
            guard_repo, tmp_path, timeout_s=300, reask_after_observation=False
        )

        result, _log, evaluator, _store = await drive(
            config, guard_repo, [never_observed(), accepts()]
        )

        assert len(evaluator.prompts) == 1
        assert result.status is RunStatus.BLOCKED
        # The measurement was still taken and still recorded. Only the second
        # question is off.
        assert result.changed_verification is not None
        assert set(result.changed_verification.executed_paths) >= {
            "guards/test_ships_dark.py",
            "guards/test_status_reconciliation.py",
        }


class TestWhereItRoutes:
    @pytest.mark.asyncio
    async def test_a_changed_guard_that_genuinely_fails_goes_to_the_builder(
        self, guard_repo: GuardRepo, tmp_path: Path
    ) -> None:
        config = changed_config(guard_repo, tmp_path, timeout_s=300)

        result, _log, _evaluator, _store = await drive(
            config,
            guard_repo,
            [accepts(), accepts(), accepts()],
            deliver="deliver_a_failing_guard",
        )

        # An ACCEPT does not survive a changed guard that refuses, and the
        # refusal reaches the builder as grounded work rather than the founder.
        first = result.state.iterations[0]
        assert first.decision is not None
        assert first.decision.decision is Decision.FIX
        assert "guards/test_ships_dark.py" in first.decision.problems[0]
        assert "Do NOT weaken" in first.decision.correction_prompt
        assert first.correction_prompt_sent, "the correction was sent to the builder"
        assert result.status is not RunStatus.ACCEPTED

    @pytest.mark.asyncio
    async def test_a_changed_guard_that_cannot_run_is_a_gap_not_a_false_pass(
        self, guard_repo: GuardRepo, tmp_path: Path
    ) -> None:
        config = changed_config(guard_repo, tmp_path, timeout_s=300)

        result, _log, _evaluator, _store = await drive(
            config,
            guard_repo,
            [accepts(), accepts(), accepts()],
            deliver="deliver_an_unrunnable_guard",
        )

        first = result.state.iterations[0]
        assert first.decision is not None
        assert first.decision.decision is Decision.FIX
        assert "VERIFICATION GAP" in first.decision.correction_prompt
        assert "NOT a product defect" in first.decision.correction_prompt
        assert result.status is not RunStatus.ACCEPTED

    @pytest.mark.asyncio
    async def test_a_changed_negative_guard_that_cannot_fire_blocks_the_claim(
        self, guard_repo: GuardRepo, tmp_path: Path
    ) -> None:
        config = changed_config(guard_repo, tmp_path, timeout_s=300)

        result, _log, _evaluator, _store = await drive(
            config,
            guard_repo,
            [accepts(), accepts(), accepts()],
            deliver="deliver_a_vacuous_negative_guard",
        )

        verification = result.changed_verification
        assert verification is not None
        assert not verification.product_failures, "every guard passed when run"
        assert verification.discrimination_gaps
        first = result.state.iterations[0]
        assert first.decision is not None
        assert first.decision.decision is Decision.FIX
        assert "still fires" in " ".join(first.decision.problems)
        assert result.status is not RunStatus.ACCEPTED

    @pytest.mark.asyncio
    async def test_an_unrelated_test_edit_neither_blocks_nor_runs_the_repository(
        self, guard_repo: GuardRepo, tmp_path: Path
    ) -> None:
        config = changed_config(guard_repo, tmp_path, timeout_s=300)

        result, _log, _evaluator, _store = await drive(
            config,
            guard_repo,
            [accepts()],
            deliver="touch_an_unrelated_test",
        )

        verification = result.changed_verification
        assert verification is not None
        assert verification.executed_paths == ["guards/test_unrelated_feature.py"]
        assert not verification.blocks_claim
        assert result.status is RunStatus.ACCEPTED


class TestResume:
    @pytest.mark.asyncio
    async def test_an_interrupted_run_still_owes_the_observation(
        self, guard_repo: GuardRepo, tmp_path: Path
    ) -> None:
        """A restart is not a discharge.

        The first run leaves a guard that could not be executed here, so the
        obligation is open. A resumed run must know that — a run that re-read a
        quiet tree, found nothing outstanding and accepted would have lost the
        one thing it owed.
        """
        config = changed_config(guard_repo, tmp_path, timeout_s=300)

        first, _log, _evaluator, store = await drive(
            config,
            guard_repo,
            [accepts(), accepts(), accepts()],
            deliver="deliver_an_unrunnable_guard",
        )
        assert first.changed_verification is not None
        assert first.changed_verification.blocks_claim

        # Persisted on the RUN, which is what a restart reads.
        saved = store.load_state()
        assert saved is not None
        assert saved.verification_obligation is not None
        assert saved.verification_obligation["changed_paths"] == [
            "guards/test_ships_dark.py"
        ]

        # Resume: same run directory, same state, a builder that changes nothing.
        from neyma_product_driver.changed_verification import ChangedSurfaceVerification

        restored = ChangedSurfaceVerification.model_validate(saved.verification_obligation)
        assert restored.blocks_claim
        assert [g.path for g in restored.pending] == []
        assert restored.unexecutable

        saved.status = RunStatus.RUNNING
        resumed = await run_control_loop(
            config=config,
            scenario=Scenario(name="p6_unrelated_brake"),
            store=store,
            state=saved,
            builder=DeliveringBuilder(guard_repo, deliver="deliver_an_unrunnable_guard"),
            evaluator=FakeEvaluator([accepts(), accepts()]),
            make_executor=lambda d: RecordingExecutor(d, {}, []),
            emit=lambda _m: None,
        )

        # The obligation survived, was re-measured rather than assumed, and still
        # refuses the claim.
        assert resumed.changed_verification is not None
        assert resumed.changed_verification.blocks_claim
        assert resumed.status is not RunStatus.ACCEPTED

    @pytest.mark.asyncio
    async def test_an_unreadable_obligation_does_not_break_the_run(
        self, guard_repo: GuardRepo, tmp_path: Path
    ) -> None:
        config = changed_config(guard_repo, tmp_path, timeout_s=300)
        assert config.runs_dir is not None
        store = EvidenceStore(config.runs_dir, "20260913-999999")
        state = RunState(run_id=store.run_id, task=config.task, max_iterations=1)
        state.verification_obligation = {"changed_paths": "not a list at all"}

        result = await run_control_loop(
            config=config,
            scenario=Scenario(name="p6_unrelated_brake"),
            store=store,
            state=state,
            builder=DeliveringBuilder(guard_repo),
            evaluator=FakeEvaluator([accepts()]),
            make_executor=lambda d: RecordingExecutor(d, {}, []),
            emit=lambda _m: None,
        )

        # Re-measured from the tree rather than trusting or crashing on a record
        # this driver can no longer read.
        assert result.changed_verification is not None
        assert set(result.changed_verification.executed_paths) >= {
            "guards/test_ships_dark.py",
            "guards/test_status_reconciliation.py",
        }
