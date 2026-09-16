"""A narrowed iteration keeps valid same-subject evidence, and only that.

Run 20260916-070036 is the case: iteration 3 executed the whole generated suite
green on one tree; iteration 4's builder changed one verification script and the
narrowed pass that followed did not re-select the service_unavailable,
stale_state and concurrency scenarios. The gate judged iteration 4 alone, so it
reported those scenarios as "no result recorded" and their risks as uncovered —
as though the same-tree evidence had never been taken.

The fix must not swing the other way. Evidence from a tree that has since
changed inside a scenario's subject is not evidence, and is re-executed.

Everything runs a real (tiny) repository through the real executor, the real
suite and the real gate. Nothing consumes Claude usage.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from neyma_product_driver.cli import run_control_loop
from neyma_product_driver.config import ScenarioGenerationConfig, ScenarioRunConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.evidence_lineage import (
    SubjectIndex,
    accumulated_result,
    assess,
    inherit,
    worktree_identity,
)
from neyma_product_driver.models import AssertionResult, RunState, RunStatus, ScenarioResult
from neyma_product_driver.scenario_gate import (
    FRESH,
    INHERITED,
    REEXERCISED,
    GateStatus,
    evaluate_gate,
)
from neyma_product_driver.scenario_plan import (
    GeneratedAction,
    IdentifiedRisk,
    Priority,
    RiskCategory,
    compile_to_scenario,
)
from neyma_product_driver.scenario_planner import ScenarioPlanner
from neyma_product_driver.scenario_suite import (
    Outcome,
    SuiteExecutor,
    build_suite,
    select_rerun,
)
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor

from scenario_fixtures import (
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    base_scenario,
    make_scenario,
    raw_payload,
    raw_scenario,
)
from test_scenario_loop import FakeBuilder, FakeEvaluator, FakeRepoLoader, FakeTurn, accept

PY = shlex.quote(sys.executable)
STORE = f"{PY} -c \"import sys; sys.path.insert(0, '.'); import app.store as s; print(s.ok())\""
OTHER = f"{PY} -c \"import sys; sys.path.insert(0, '.'); import app.other as o; print(o.ok())\""
BATTERY = f"{PY} scripts/battery.py"
RUN_ID = "20260916-999999"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def product(root: Path) -> Path:
    repo = root / "product"
    files = {
        "app/__init__.py": "",
        "app/store.py": "from app import util\n\n\ndef ok():\n    return util.value()\n",
        "app/util.py": "def value():\n    return 'store-ok'\n",
        "app/other.py": "def ok():\n    return 'other-ok'\n",
        "app/schema.sql": "create table t (x int);\n",
        "scripts/battery.py": "print('battery-ok')\n",
        "docs/notes.md": "# notes\n",
        "pyproject.toml": "[project]\nname = 'app'\n",
        ".gitignore": "__pycache__/\n",
    }
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def change(repo: Path, rel: str, text: str, *, commit: bool = True) -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)
    if commit:
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", f"change {rel}")


def case(scenario_id: str, command: str, prints: str, category: RiskCategory):
    return make_scenario(
        scenario_id,
        risk_category=category,
        actions=[
            GeneratedAction(
                kind="command",
                name=scenario_id,
                command=command,
                expect_outcome="permitted",
                expect_contains=[prints],
            )
        ],
        state_checks=[],
        service_refs=[],
        cleanup=[],
        isolation_note="reads source only",
        expected_observations=[prints],
    )


CASES = {
    "gen-store": (STORE, "store-ok", RiskCategory.STALE_STATE),
    "gen-other": (OTHER, "other-ok", RiskCategory.SERVICE_UNAVAILABLE),
    "gen-battery": (BATTERY, "battery-ok", RiskCategory.CONCURRENCY),
}


def suite_of(*ids: str, overrides: dict | None = None):
    generated = []
    for scenario_id in ids:
        command, prints, category = (overrides or {}).get(scenario_id, CASES[scenario_id])
        model = case(scenario_id, command, prints, category)
        generated.append(
            (model, compile_to_scenario(model, approved_commands=set(model.command_strings())))
        )
    return build_suite(generated=generated)


def risks() -> list[IdentifiedRisk]:
    return [
        IdentifiedRisk(id=f"R-{c.value}", description=f"{c.value} risk", risk_category=c, severity=Priority.P0)
        for _cmd, _p, c in CASES.values()
    ]


class Run:
    """One run directory, executed iteration by iteration the way the loop does."""

    def __init__(self, root: Path, repo: Path) -> None:
        self.repo = repo
        self.store = EvidenceStore(root / "runs", RUN_ID)
        self.previous = None
        self.iteration = 0

    def execute(self, suite, *, forced=(), selection=None):
        self.iteration += 1
        only, reason = select_rerun(suite, self.previous, must_run=forced)
        if selection is not None:
            only = list(selection)
        check = assess(
            self.previous,
            suite,
            repo=self.repo,
            run_dir=self.store.run_dir,
            run_id=RUN_ID,
            forced=forced,
        )
        if self.previous is not None:
            only = [*only, *[i for i in check.must_run(suite) if i not in only]]
        executor = SuiteExecutor(
            make_executor=lambda d: ScenarioExecutor(
                repo=self.repo, run_config=ScenarioRunConfig(command_timeout_s=60), artifact_dir=d
            ),
            artifact_root=self.store.iteration_dir(self.iteration),
            run_id=RUN_ID,
            iteration=self.iteration,
            repo=self.repo,
        )
        result = asyncio.run(executor.run(suite, only=only, selection_reason=reason))
        if self.previous is not None:
            result = inherit(result, suite, check, iteration=self.iteration)
        (self.store.iteration_dir(self.iteration) / "suite-result.json").write_text(
            result.model_dump_json()
        )
        self.previous = result
        self.executed = sorted(
            o.scenario_id for o in result.outcomes if not o.inherited_from_iteration
        )
        return result, check


@pytest.fixture
def run(tmp_path):
    repo = product(tmp_path)
    return Run(tmp_path, repo)


ALL = ("gen-store", "gen-other", "gen-battery")


# ==========================================================================
# 1 — full evidence survives a later narrowed iteration on the same subject
# ==========================================================================


class TestSameSubjectEvidenceSurvives:
    def test_full_suite_then_unrelated_change_then_narrowed_iteration(self, run):
        first, _ = run.execute(suite_of(*ALL))
        assert first.full_run and all(o.outcome is Outcome.PASSED for o in first.outcomes)
        assert evaluate_gate(first, risks=risks()).status is GateStatus.VERIFIED

        # The builder changes a verification script only the battery reads.
        change(run.repo, "scripts/battery.py", "print('battery-ok')  # hardened\n")
        second, check = run.execute(suite_of(*ALL))

        assert run.executed == ["gen-battery"]
        assert second.full_run
        assert second.lineage.inherited_ids() == {"gen-store", "gen-other"}
        assert second.lineage.invalidated_ids() == {"gen-battery"}
        verdict = evaluate_gate(second, risks=risks())
        assert verdict.status is GateStatus.VERIFIED, verdict.summary_block()
        freshness = {r.risk_category: r.freshness for r in verdict.covered_risks}
        assert freshness == {
            "stale_state": INHERITED,
            "service_unavailable": INHERITED,
            "concurrency": REEXERCISED,
        }

    def test_without_lineage_the_same_narrowed_pass_reports_false_gaps(self, run):
        """The defect, reproduced: judged alone, the narrowed pass blocks."""
        run.execute(suite_of(*ALL))
        change(run.repo, "scripts/battery.py", "print('battery-ok')  # hardened\n")
        suite = suite_of(*ALL)
        executor = SuiteExecutor(
            make_executor=lambda d: ScenarioExecutor(
                repo=run.repo, run_config=ScenarioRunConfig(command_timeout_s=60), artifact_dir=d
            ),
            artifact_root=run.store.iteration_dir(2),
            run_id=RUN_ID,
            iteration=2,
        )
        narrowed = asyncio.run(executor.run(suite, only=["gen-battery"]))
        verdict = evaluate_gate(narrowed, risks=risks())
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert {c.scenario_id for c in verdict.unverified} == {"gen-store", "gen-other"}
        assert {r.risk_category for r in verdict.uncovered_risks} == {
            "stale_state",
            "service_unavailable",
        }

    def test_unrelated_changes_invalidate_nothing(self, run):
        run.execute(suite_of(*ALL))
        change(run.repo, "docs/notes.md", "# notes, revised\n")
        second, _ = run.execute(suite_of(*ALL))
        assert run.executed == []
        assert second.lineage.inherited_ids() == set(ALL)
        assert evaluate_gate(second, risks=risks()).status is GateStatus.VERIFIED

    def test_inherited_evidence_keeps_its_source_and_artifact(self, run):
        first, _ = run.execute(suite_of(*ALL))
        tree = worktree_identity(run.repo).tree
        change(run.repo, "docs/notes.md", "# notes, revised\n")
        second, _ = run.execute(suite_of(*ALL))
        carried = second.by_id("gen-store")
        assert carried.inherited_from_iteration == 1
        assert carried.evidence_iteration == 1
        assert carried.evidence_tree == tree
        assert "iteration-01" in carried.evidence_path
        assert (Path(carried.evidence_path) / "result.json").exists()
        entry = next(e for e in second.lineage.inherited if e.scenario_id == "gen-store")
        assert entry.evidence_path == carried.evidence_path
        assert "none inside its subject" in entry.reason
        # The record is persisted, so the next iteration can see where it came from.
        again = json.loads((run.store.iteration_dir(2) / "suite-result.json").read_text())
        assert any(o["inherited_from_iteration"] == 1 for o in again["outcomes"])

    def test_the_lineage_chains_across_iterations_without_re_execution(self, run):
        run.execute(suite_of(*ALL))
        change(run.repo, "docs/notes.md", "# a\n")
        run.execute(suite_of(*ALL))
        change(run.repo, "docs/notes.md", "# b\n")
        third, _ = run.execute(suite_of(*ALL))
        assert run.executed == []
        assert {o.evidence_iteration for o in third.outcomes} == {1}


# ==========================================================================
# 2 — a change inside the subject invalidates exactly that evidence
# ==========================================================================


class TestSubjectChangesInvalidate:
    def test_a_transitively_imported_module_invalidates_its_importer_only(self, run):
        run.execute(suite_of(*ALL))
        change(run.repo, "app/util.py", "def value():\n    return 'store-ok'  # v2\n")
        second, check = run.execute(suite_of(*ALL))
        assert run.executed == ["gen-store"]
        assert "app/util.py" in check.invalidated["gen-store"].reason
        assert second.lineage.inherited_ids() == {"gen-other", "gen-battery"}

    def test_a_regression_in_the_subject_is_caught_not_inherited(self, run):
        run.execute(suite_of(*ALL))
        change(run.repo, "app/util.py", "def value():\n    return 'store-BROKEN'\n")
        second, _ = run.execute(suite_of(*ALL))
        assert second.by_id("gen-store").outcome is Outcome.FAILED
        assert not second.by_id("gen-store").inherited_from_iteration
        verdict = evaluate_gate(second, risks=risks())
        assert verdict.status is GateStatus.NOT_VERIFIED

    def test_a_data_file_beside_an_imported_module_invalidates(self, run):
        run.execute(suite_of(*ALL))
        change(run.repo, "app/schema.sql", "create table t (x int, y int);\n")
        _, check = run.execute(suite_of(*ALL))
        assert set(run.executed) == {"gen-store", "gen-other"}
        assert "sits beside" in check.invalidated["gen-store"].reason

    def test_repository_wide_configuration_invalidates_everything(self, run):
        run.execute(suite_of(*ALL))
        change(run.repo, "pyproject.toml", "[project]\nname = 'app2'\n")
        second, _ = run.execute(suite_of(*ALL))
        assert run.executed == sorted(ALL)
        # A full re-execution forced by invalidation is still reported as such.
        assert second.lineage.invalidated_ids() == set(ALL)
        verdict = evaluate_gate(second, risks=risks())
        assert {r.freshness for r in verdict.covered_risks} == {REEXERCISED}

    def test_an_uncommitted_edit_counts_as_a_change(self, run):
        run.execute(suite_of(*ALL))
        change(run.repo, "app/other.py", "def ok():\n    return 'other-ok'  # wip\n", commit=False)
        run.execute(suite_of(*ALL))
        assert run.executed == ["gen-other"]

    def test_evidence_taken_on_a_dirty_tree_is_never_inherited(self, run):
        change(run.repo, "docs/notes.md", "# dirty\n", commit=False)
        first, _ = run.execute(suite_of(*ALL))
        assert all(o.evidence_tree == "" for o in first.outcomes)
        _git(run.repo, "commit", "-qam", "commit it")
        _, check = run.execute(suite_of(*ALL))
        assert run.executed == sorted(ALL)
        assert all("single clean tree" in e.reason for e in check.invalidated.values())

    def test_a_computed_import_makes_the_subject_unbounded(self, run):
        change(
            run.repo,
            "app/other.py",
            "import importlib\n\n\ndef ok():\n    importlib.import_module('app.' + 'util')\n    return 'other-ok'\n",
        )
        run.execute(suite_of(*ALL))
        change(run.repo, "app/unrelated.py", "X = 1\n")
        _, check = run.execute(suite_of(*ALL))
        assert run.executed == ["gen-other"]
        assert "unbounded" in check.invalidated["gen-other"].reason

    def test_a_changed_definition_is_not_the_same_measurement(self, run):
        run.execute(suite_of(*ALL))
        edited = {"gen-other": (OTHER, "other", RiskCategory.SERVICE_UNAVAILABLE)}
        _, check = run.execute(suite_of(*ALL, overrides=edited))
        assert run.executed == ["gen-other"]
        assert "definition changed" in check.invalidated["gen-other"].reason

    def test_a_re_materialized_scenario_is_always_re_executed(self, run):
        run.execute(suite_of(*ALL))
        run.execute(suite_of(*ALL), forced=["gen-store"])
        assert run.executed == ["gen-store"]

    def test_evidence_whose_artifact_vanished_is_not_inherited(self, run):
        first, _ = run.execute(suite_of(*ALL))
        shutil.rmtree(first.by_id("gen-other").evidence_path)
        _, check = run.execute(suite_of(*ALL))
        assert run.executed == ["gen-other"]
        assert "no longer resolves" in check.invalidated["gen-other"].reason

    def test_evidence_whose_record_was_altered_is_not_inherited(self, run):
        first, _ = run.execute(suite_of(*ALL))
        record = Path(first.by_id("gen-other").evidence_path) / "result.json"
        data = json.loads(record.read_text())
        data["assertions"][0]["passed"] = False
        record.write_text(json.dumps(data))
        run.execute(suite_of(*ALL))
        assert run.executed == ["gen-other"]


# ==========================================================================
# 3 — nothing is manufactured
# ==========================================================================


class TestNothingIsManufactured:
    def test_a_scenario_never_executed_is_executed_not_inherited(self, run):
        run.execute(suite_of("gen-store", "gen-other"))
        second, _ = run.execute(suite_of(*ALL))
        assert run.executed == ["gen-battery"]
        assert "gen-battery" not in second.lineage.inherited_ids()

    def test_a_risk_nothing_exercised_stays_uncovered(self, run):
        run.execute(suite_of("gen-store", "gen-other"))
        change(run.repo, "docs/notes.md", "# x\n")
        second, _ = run.execute(suite_of("gen-store", "gen-other"))
        verdict = evaluate_gate(second, risks=risks())
        assert [r.risk_category for r in verdict.uncovered_risks] == ["concurrency"]
        assert "still uncovered: R-concurrency" in "\n".join(verdict.risk_ledger())

    def test_a_failed_scenario_is_never_inherited_and_stays_blocking(self, run):
        broken = {"gen-other": (OTHER, "not-printed", RiskCategory.SERVICE_UNAVAILABLE)}
        first, _ = run.execute(suite_of(*ALL, overrides=broken))
        assert first.by_id("gen-other").outcome is Outcome.FAILED
        change(run.repo, "docs/notes.md", "# x\n")
        second, _ = run.execute(suite_of(*ALL, overrides=broken))
        # Re-selected because it failed; still failing; still blocking, although
        # every other scenario — and its neighbours — carry valid evidence.
        assert "gen-other" in run.executed
        assert second.by_id("gen-other").outcome is Outcome.FAILED
        verdict = evaluate_gate(second, risks=risks())
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert [c.scenario_id for c in verdict.unverified] == ["gen-other"]

    def test_the_first_iteration_inherits_nothing(self, run):
        first, check = run.execute(suite_of(*ALL))
        assert check.valid == {}
        assert first.lineage is None

    def test_evidence_outside_this_run_is_never_inherited(self, run, tmp_path):
        first, _ = run.execute(suite_of(*ALL))
        other = tmp_path / "elsewhere"
        shutil.copytree(first.by_id("gen-store").evidence_path, other)
        forged = first.model_copy(
            update={
                "outcomes": [
                    o.model_copy(update={"evidence_path": str(other)})
                    if o.scenario_id == "gen-store"
                    else o
                    for o in first.outcomes
                ]
            }
        )
        run.previous = forged
        _, check = run.execute(suite_of(*ALL))
        assert "gen-store" in run.executed
        assert "not inside this run" in check.invalidated["gen-store"].reason


class TestTheLedger:
    def test_fresh_inherited_invalidated_and_uncovered_are_all_named(self, run):
        run.execute(suite_of(*ALL))
        change(run.repo, "scripts/battery.py", "print('battery-ok')  # v2\n")
        second, _ = run.execute(suite_of("gen-store", "gen-battery"))
        verdict = evaluate_gate(second, risks=risks())
        text = verdict.summary_block()
        assert "freshly exercised: 0; inherited from valid earlier same-subject evidence: 1; " \
            "invalidated and re-exercised: 1; still uncovered: 1" in text
        assert "inherited: R-stale_state (iteration 1, gen-store)" in text
        assert "invalidated and re-exercised: R-concurrency (gen-battery)" in text
        assert "still uncovered: R-service_unavailable" in text
        assert "EVIDENCE LINEAGE" in text
        assert "INHERITED from iteration 1" in text

    def test_a_fresh_execution_is_labelled_fresh(self, run):
        first, _ = run.execute(suite_of(*ALL))
        verdict = evaluate_gate(first, risks=risks())
        assert {r.freshness for r in verdict.covered_risks} == {FRESH}


class TestResumeContinuesTheLineage:
    def test_the_accumulated_record_holds_each_scenarios_latest_evidence(self, run):
        run.execute(suite_of(*ALL))
        change(run.repo, "scripts/battery.py", "print('battery-ok')  # v2\n")
        run.execute(suite_of(*ALL))
        merged = accumulated_result(run.store.run_dir, run.repo)
        by_id = {o.scenario_id: o for o in merged.outcomes}
        assert by_id["gen-battery"].evidence_iteration == 2
        assert by_id["gen-store"].evidence_iteration == 1

    def test_a_record_written_before_stamping_is_stamped_from_its_clean_iteration(self, run):
        first, _ = run.execute(suite_of(*ALL))
        legacy = first.model_copy(
            update={
                "outcomes": [
                    o.model_copy(
                        update={"evidence_iteration": 0, "evidence_tree": "", "scenario_digest": ""}
                    )
                    for o in first.outcomes
                ]
            }
        )
        directory = run.store.iteration_dir(1)
        (directory / "suite-result.json").write_text(legacy.model_dump_json())
        head = _git(run.repo, "rev-parse", "--short", "HEAD").strip()
        (directory / "record.json").write_text(
            json.dumps({"git": {"head_commit": head, "status_porcelain": ""}})
        )
        merged = accumulated_result(run.store.run_dir, run.repo)
        assert {o.evidence_tree for o in merged.outcomes} == {worktree_identity(run.repo).tree}
        # And a legacy record is still judged by what it executed.
        run.previous = merged
        run.iteration = 1
        change(run.repo, "docs/notes.md", "# x\n")
        run.execute(suite_of(*ALL))
        assert run.executed == []

    def test_a_record_from_a_dirty_iteration_stays_unstamped(self, run):
        first, _ = run.execute(suite_of(*ALL))
        legacy = first.model_copy(
            update={"outcomes": [o.model_copy(update={"evidence_iteration": 0, "evidence_tree": ""}) for o in first.outcomes]}
        )
        directory = run.store.iteration_dir(1)
        (directory / "suite-result.json").write_text(legacy.model_dump_json())
        (directory / "record.json").write_text(
            json.dumps({"git": {"head_commit": "HEAD", "status_porcelain": " M app/util.py"}})
        )
        merged = accumulated_result(run.store.run_dir, run.repo)
        assert {o.evidence_tree for o in merged.outcomes} == {""}


def test_subject_of_a_pytest_invocation_includes_its_conftests(tmp_path):
    repo = product(tmp_path)
    change(repo, "tests/conftest.py", "")
    change(repo, "tests/test_x.py", "import app.other\n")
    scenario = Scenario(
        name="t", commands=[{"name": "t", "run": f"{PY} -m pytest tests/test_x.py -q"}]
    )
    subject = SubjectIndex(repo).subject(scenario)
    assert {"tests/test_x.py", "tests/conftest.py", "app/other.py"} <= subject.files
    assert subject.touched_by("app/other.py")
    assert not subject.touched_by("app/util.py")
    assert subject.touched_by("tests/conftest.py")


def test_docstrings_do_not_widen_a_subject(tmp_path):
    repo = product(tmp_path)
    change(repo, "app/other.py", '"""Guarded by scripts/battery.py."""\n\n\ndef ok():\n    return 1\n')
    scenario = Scenario(name="t", commands=[{"name": "t", "run": OTHER}])
    subject = SubjectIndex(repo).subject(scenario)
    assert not subject.touched_by("scripts/battery.py")
    change(repo, "app/other.py", "PATH = 'scripts/battery.py'\n\n\ndef ok():\n    return 1\n")
    subject = SubjectIndex(repo).subject(scenario)
    assert subject.touched_by("scripts/battery.py")


# ==========================================================================
# 4 — through the real control loop
# ==========================================================================


class LoopExecutor:
    """Records executions; passes or fails by a per-iteration script."""

    def __init__(self, artifact_dir: Path, verdicts: dict[str, bool], log: list[str]) -> None:
        self.verdicts = verdicts
        self.log = log
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        key = scenario.name.split(":", 1)[-1]
        self.log.append(key)
        passing = self.verdicts.get(key, True)
        return ScenarioResult(
            scenario_name=scenario.name,
            assertions=[
                AssertionResult(kind="expect_state", target=key, passed=passing)
            ],
        )


def _loop_payload():
    return raw_payload(
        raw_scenario("gen-a", risk_category="idempotency"),
        raw_scenario(
            "gen-b",
            risk_category="authorization",
            expected_observations=["denied"],
            generating_risk="a non-owner could approve",
        ),
        risks=[
            {"id": "R1", "description": "approval may not be idempotent", "risk_category": "idempotency", "severity": "P0"},
            {"id": "R2", "description": "a non-owner could approve", "risk_category": "authorization", "severity": "P0"},
        ],
    )


async def _two_iterations(driver_config, change_in_iteration_two, gen_b_fixed=True):
    repo = driver_config.neyma_repo
    (repo / "probe.sh").write_text("#!/bin/sh\necho ok\n")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "notes.md").write_text("# notes\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "probe")

    driver_config.max_iterations = 2
    driver_config.scenario_generation = ScenarioGenerationConfig(enabled=True)
    store = EvidenceStore(driver_config.runs_dir, "20260916-000001")
    state = RunState(run_id=store.run_id, task="build supervised approval", max_iterations=2)
    planner = ScenarioPlanner(
        repo=repo,
        config=driver_config.scenario_generation,
        reasoner=ScriptedReasoner([_loop_payload(), raw_payload(), raw_payload(), raw_payload()]),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )
    iteration = {"n": 0}
    log: list[str] = []

    class CommittingBuilder(FakeBuilder):
        async def send(self, prompt: str, timeout_s: int | None = None) -> FakeTurn:
            iteration["n"] += 1
            log.append(f"--- iteration {iteration['n']} ---")
            if iteration["n"] == 2:
                rel, text = change_in_iteration_two
                (repo / rel).write_text(text)
                _git(repo, "add", "-A")
                _git(repo, "commit", "-qm", "builder correction")
            return await super().send(prompt, timeout_s)

    result = await run_control_loop(
        config=driver_config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=CommittingBuilder(),
        evaluator=FakeEvaluator([accept(), accept()]),
        make_executor=lambda d: LoopExecutor(
            d, {"gen-b": iteration["n"] > 1 and gen_b_fixed}, log
        ),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(FakeUnit()),
        planner=planner,
    )
    return result, log


def _executed_in(log: list[str], n: int) -> list[str]:
    start = log.index(f"--- iteration {n} ---")
    end = log.index(f"--- iteration {n + 1} ---") if f"--- iteration {n + 1} ---" in log else len(log)
    return log[start + 1 : end]


class TestThroughTheControlLoop:
    @pytest.mark.asyncio
    async def test_a_narrowed_iteration_accepts_on_valid_inherited_evidence(self, driver_config):
        result, log = await _two_iterations(driver_config, ("docs/notes.md", "# revised\n"))
        assert result.status is RunStatus.ACCEPTED, result.final_decision
        assert _executed_in(log, 1) == ["backend_generic", "gen-a", "gen-b"] or set(
            _executed_in(log, 1)
        ) == {"backend_generic", "gen-a", "gen-b"}
        # gen-a was green on an unchanged subject: it is NOT run again, and no
        # widening pass re-runs the whole suite behind it.
        assert "gen-a" not in _executed_in(log, 2)
        assert result.suite.full_run
        assert result.suite.lineage.inherited_ids() == {"gen-a"}
        assert result.gate.status is GateStatus.VERIFIED
        by_category = {r.risk_category: r.freshness for r in result.gate.covered_risks}
        assert by_category["idempotency"] == INHERITED
        assert by_category["authorization"] == FRESH

    @pytest.mark.asyncio
    async def test_a_change_inside_the_subject_re_executes_it(self, driver_config):
        result, log = await _two_iterations(
            driver_config, ("probe.sh", "#!/bin/sh\necho changed\n")
        )
        assert "gen-a" in _executed_in(log, 2)
        assert result.suite.lineage is None or "gen-a" not in result.suite.lineage.inherited_ids()
        assert result.status is RunStatus.ACCEPTED

    @pytest.mark.asyncio
    async def test_a_still_failing_scenario_blocks_despite_inherited_neighbours(self, driver_config):
        result, log = await _two_iterations(
            driver_config, ("docs/notes.md", "# revised\n"), gen_b_fixed=False
        )
        assert result.status is not RunStatus.ACCEPTED
        assert "gen-b" in _executed_in(log, 2)
        assert result.suite.by_id("gen-b").outcome is Outcome.FAILED
        assert result.gate.status is GateStatus.NOT_VERIFIED
