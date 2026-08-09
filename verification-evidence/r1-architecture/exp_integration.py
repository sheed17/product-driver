"""INDEPENDENT REVIEWER 1 experiments — architecture / integration.

Not part of the product test suite. Run with:
    .venv/bin/python -m pytest verification-evidence/r1-architecture/exp_integration.py -q -p no:cacheprovider

Every experiment drives the REAL run_control_loop with a REAL ScenarioPlanner
(scripted reasoner, no model calls). Builder/evaluator are fakes because they
are Claude sessions; everything under review is real code.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO))

from neyma_product_driver.cli import (  # noqa: E402
    _apply_suite_precedence,
    _make_planner,
    run_control_loop,
)
from neyma_product_driver.config import DriverConfig, ScenarioGenerationConfig  # noqa: E402
from neyma_product_driver.context import ActiveUnit, RepositoryContext  # noqa: E402
from neyma_product_driver.evidence import EvidenceStore  # noqa: E402
from neyma_product_driver.models import (  # noqa: E402
    AssertionResult,
    Decision,
    EvaluatorDecision,
    RunState,
    RunStatus,
    ScenarioResult,
)
from neyma_product_driver.scenario_plan import Priority  # noqa: E402
from neyma_product_driver.scenario_planner import ScenarioPlanner  # noqa: E402
from neyma_product_driver.scenario_suite import (  # noqa: E402
    Origin,
    Outcome,
    ScenarioOutcome,
    SuiteResult,
)
from neyma_product_driver.scenarios import Scenario  # noqa: E402

from scenario_fixtures import (  # noqa: E402
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    base_scenario,
    raw_payload,
    raw_scenario,
)

EVIDENCE = Path(__file__).resolve().parent


def _log(name: str, payload: object) -> None:
    (EVIDENCE / f"{name}.json").write_text(json.dumps(payload, indent=2, default=str))


# --------------------------------------------------------------------------
# fakes (Claude sessions only)
# --------------------------------------------------------------------------


@dataclass
class FakeTurn:
    text: str = "done."
    session_id: str | None = "builder-1"
    tool_uses: list[str] = field(default_factory=list)
    denied_requests: list[str] = field(default_factory=list)
    is_error: bool = False
    error_detail: str = ""


class FakeBuilder:
    def __init__(self) -> None:
        self.session_id = "builder-1"
        self.prompts: list[str] = []

    async def send(self, prompt: str, timeout_s: int | None = None) -> FakeTurn:
        self.prompts.append(prompt)
        return FakeTurn()


class FakeRepoLoader:
    def __init__(self) -> None:
        self.unit = FakeUnit()

    def resolve_active_unit(self):
        return self.unit

    def load(self, topics=None) -> RepositoryContext:
        return RepositoryContext(
            head_commit="abc1234",
            branch="main",
            dirty_file_count=0,
            active_unit=ActiveUnit(
                unit_id=self.unit.unit_id,
                name=self.unit.name,
                status="READY",
                acceptance_criteria=self.unit.acceptance_criteria,
            ),
            authority_excerpt="",
            current_excerpt="",
            topic_excerpts={},
            files_consulted=[],
            fingerprint="fp",
        )


class AlwaysAcceptEvaluator:
    """The adversarial case: the evaluator ALWAYS says ACCEPT."""

    def __init__(self) -> None:
        self.session_id = "evaluator-1"
        self.prompts: list[str] = []

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision:
        self.prompts.append(prompt)
        return EvaluatorDecision(
            decision=Decision.ACCEPT, summary="looks good to me", observed_behavior=["saw it"]
        )


class SpyExecutor:
    """Records the actual Scenario OBJECTS it was handed, not just names."""

    seen: list[tuple[str, str, int, str]] = []

    def __init__(self, artifact_dir: Path, outcomes: dict[str, bool]) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.outcomes = outcomes
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        key = scenario.name.split(":", 1)[-1]
        SpyExecutor.seen.append(
            (scenario.name, scenario.mode, len(scenario.steps), str(self.artifact_dir))
        )
        passing = self.outcomes.get(key, True)
        return ScenarioResult(
            scenario_name=scenario.name,
            assertions=[
                AssertionResult(
                    kind="expect_state",
                    target=f"{key}: payments row count",
                    passed=passing,
                    detail="" if passing else "got 2, want 1",
                )
            ],
        )


@pytest.fixture
def bits(driver_config: DriverConfig):
    assert driver_config.runs_dir is not None
    store = EvidenceStore(driver_config.runs_dir, "r1-exp")
    state = RunState(
        run_id=store.run_id,
        task="build supervised approval",
        max_iterations=driver_config.max_iterations,
    )
    SpyExecutor.seen = []
    return driver_config, store, state


def make_planner(config: DriverConfig, store: EvidenceStore, payloads: list, **cfg) -> ScenarioPlanner:
    config.scenario_generation = ScenarioGenerationConfig(enabled=True, **cfg)
    return ScenarioPlanner(
        repo=config.neyma_repo,
        config=config.scenario_generation,
        reasoner=ScriptedReasoner(payloads),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )


# ==========================================================================
# CLAIM 1 — generated scenarios participate in the real control loop
# ==========================================================================


@pytest.mark.asyncio
async def test_c1_generated_scenarios_execute_through_the_real_loop(bits):
    config, store, state = bits
    config.max_iterations = 1
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])

    result = await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=AlwaysAcceptEvaluator(),
        make_executor=lambda d: SpyExecutor(d, {}),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        planner=planner,
    )

    names = [s[0] for s in SpyExecutor.seen]
    _log("c1_executed_scenarios", SpyExecutor.seen)
    # The generated scenario was COMPILED and handed to the same executor
    # factory the single-scenario path uses.
    assert "generated:gen-dup" in names, names
    assert "backend_generic" in names, names
    # It carried real executable steps, not an empty shell.
    gen = next(s for s in SpyExecutor.seen if s[0] == "generated:gen-dup")
    assert gen[2] > 0, gen
    # And the run's own record carries the suite.
    assert result.suite is not None
    assert {o.scenario_id for o in result.suite.outcomes} == {"backend_generic", "gen-dup"}
    assert result.state.iterations[0].suite is not None


# ==========================================================================
# CLAIM 2 + 8 + 11 — a failed required generated scenario prevents ACCEPT,
# and its correction reaches the builder
# ==========================================================================


@pytest.mark.asyncio
async def test_c2_failed_generated_scenario_prevents_accept_and_corrects_builder(bits):
    config, store, state = bits
    config.max_iterations = 2
    planner = make_planner(
        config, store, [raw_payload(raw_scenario("gen-dup")), raw_payload(), raw_payload()]
    )
    builder = FakeBuilder()

    result = await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=builder,
        evaluator=AlwaysAcceptEvaluator(),  # always ACCEPT
        make_executor=lambda d: SpyExecutor(d, {"gen-dup": False}),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        planner=planner,
    )

    _log(
        "c2_result",
        {
            "status": result.status.value,
            "decision": result.final_decision.decision.value,
            "summary": result.final_decision.summary,
            "builder_prompt_count": len(builder.prompts),
        },
    )
    assert result.status is not RunStatus.ACCEPTED
    assert result.final_decision.decision is Decision.FIX
    # The correction actually reached the builder as the next prompt.
    assert len(builder.prompts) >= 2
    second = builder.prompts[1]
    (EVIDENCE / "c2_builder_second_prompt.txt").write_text(second)
    assert "SCENARIO SUITE FAILURES" in second
    assert "gen-dup" in second
    assert "CORRECTION" in second.upper()


# ==========================================================================
# CLAIM 3 — permanent vs generated distinguishable in models and results
# ==========================================================================


@pytest.mark.asyncio
async def test_c3_origin_is_distinguishable_in_models_and_on_disk(bits):
    config, store, state = bits
    config.max_iterations = 1
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])

    result = await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=AlwaysAcceptEvaluator(),
        make_executor=lambda d: SpyExecutor(d, {}),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        planner=planner,
    )

    by_id = {o.scenario_id: o for o in result.suite.outcomes}
    assert by_id["backend_generic"].origin is Origin.PERMANENT
    assert by_id["gen-dup"].origin is Origin.GENERATED
    assert by_id["gen-dup"].generated_because
    assert by_id["backend_generic"].generated_because == ""

    disk = json.loads((store.iteration_dir(1) / "suite-result.json").read_text())
    origins = {o["scenario_id"]: o["origin"] for o in disk["outcomes"]}
    _log("c3_origins_on_disk", origins)
    assert origins == {"backend_generic": "permanent", "gen-dup": "generated"}
    # The permanent scenario directory was never written to.
    assert config.scenarios_dir is None or not list(
        (config.scenarios_dir).glob("*")
    ) if config.scenarios_dir and config.scenarios_dir.exists() else True


# ==========================================================================
# CLAIM 4 — handwritten scenarios unchanged with no planner
# ==========================================================================


@pytest.mark.asyncio
async def test_c4_no_planner_means_exactly_the_old_behaviour(bits):
    config, store, state = bits
    config.max_iterations = 1
    from neyma_product_driver.scenarios import load_scenario

    real = load_scenario(REPO / "scenarios" / "backend_generic.yaml")

    result = await run_control_loop(
        config=config,
        scenario=real,
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=AlwaysAcceptEvaluator(),
        make_executor=lambda d: SpyExecutor(d, {}),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
    )

    _log("c4_executed", SpyExecutor.seen)
    assert [s[0] for s in SpyExecutor.seen] == [real.name]
    assert result.suite is None
    assert result.state.iterations[0].suite is None
    assert result.status is RunStatus.ACCEPTED
    assert not (store.iteration_dir(1) / "suite-result.json").exists()
    # No suite section leaks into the evaluator prompt.
    manifest = list(store.iteration_dir(1).glob("*prompt*"))
    for path in manifest:
        assert "VERIFICATION SUITE" not in path.read_text()


# ==========================================================================
# CLAIM 5 — resume
# ==========================================================================


def test_c5_resume_rebuilds_an_empty_planner(bits, monkeypatch):
    """Does a --resume-run rebuild the plan, or start from zero?"""
    import argparse

    config, store, _state = bits
    config.scenario_generation = ScenarioGenerationConfig(enabled=True)

    # Pretend a previous process already generated and persisted a plan.
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])
    planner.plan_initial(task="t", unit=FakeUnit(), run_id=store.run_id)
    assert [s.id for s in planner.plan.scenarios] == ["gen-dup"]
    assert planner.waves_used == 1
    assert (store.run_dir / "scenario-plan.json").exists()

    # Now the resume path: cli._make_planner is what a resumed run calls.
    monkeypatch.setattr(
        "neyma_product_driver.scenario_generator.LLMScenarioReasoner",
        lambda *a, **k: ScriptedReasoner([]),
    )
    args = argparse.Namespace(auto_scenarios=True)
    resumed = _make_planner(config, args, store, base_scenario(), FakeFounder(), lambda _m: None)

    observed = {
        "persisted_plan_scenarios": json.loads(
            (store.run_dir / "scenario-plan.json").read_text()
        )["scenarios"][0]["id"],
        "resumed_planner_scenarios": [s.id for s in resumed.plan.scenarios],
        "resumed_planner_compiled": list(resumed.compiled),
        "resumed_planner_waves_used": resumed.waves_used,
    }
    _log("c5_resume", observed)
    # DOCUMENTING ACTUAL BEHAVIOUR:
    assert observed["resumed_planner_scenarios"] == []
    assert observed["resumed_planner_waves_used"] == 0


# ==========================================================================
# CLAIM 6 — completion auditor precedence unchanged
# ==========================================================================


@pytest.mark.asyncio
async def test_c6_independent_review_short_circuits_before_the_suite_gate(bits):
    """A required generated failure + REQUIRES_INDEPENDENT_REVIEW + ACCEPT."""
    from neyma_product_driver.completion_auditor import AuditDecision, CompletionAudit

    config, store, state = bits
    config.max_iterations = 1

    class ReviewAuditor:
        def audit(self, report, unit=None, run_commands=None, evidence_dir=""):
            return CompletionAudit(
                decision=AuditDecision.REQUIRES_INDEPENDENT_REVIEW,
                headline="an independent reviewer must confirm this",
                confidence=0.9,
            )

    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])
    result = await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=AlwaysAcceptEvaluator(),
        make_executor=lambda d: SpyExecutor(d, {"gen-dup": False}),  # required P0 FAILS
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        auditor=ReviewAuditor(),
        planner=planner,
    )

    observed = {
        "status": result.status.value,
        "final_decision": result.final_decision.decision.value,
        "suite_on_result": result.suite is not None,
        "promotion_candidates_on_result": len(result.promotion_candidates),
    }
    _log("c6_independent_review", observed)
    assert result.status is RunStatus.NEEDS_INDEPENDENT_REVIEW
    # DOCUMENTING ACTUAL BEHAVIOUR: the recorded decision stays ACCEPT even
    # though a required P0 generated scenario failed, and the suite is dropped
    # from the LoopResult on this path.
    assert observed["final_decision"] == "ACCEPT"
    assert observed["suite_on_result"] is False


@pytest.mark.asyncio
async def test_c6b_contradicted_audit_still_wins_over_the_suite(bits):
    from neyma_product_driver.completion_auditor import (
        AuditDecision,
        CompletionAudit,
        Contradiction,
    )

    config, store, state = bits
    config.max_iterations = 1

    class ContradictingAuditor:
        def audit(self, report, unit=None, run_commands=None, evidence_dir=""):
            return CompletionAudit(
                decision=AuditDecision.CONTRADICTED,
                headline="the registry does not support the claim",
                contradictions=[
                    Contradiction(what="claimed COMPLETE", claimed="COMPLETE", observed="READY")
                ],
                correction_prompt="AUDIT-CORRECTION-MARKER: fix the status surfaces.",
                confidence=0.9,
            )

    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])
    result = await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=AlwaysAcceptEvaluator(),
        make_executor=lambda d: SpyExecutor(d, {"gen-dup": False}),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        auditor=ContradictingAuditor(),
        planner=planner,
    )
    assert result.final_decision.decision is Decision.FIX
    assert "AUDIT-CORRECTION-MARKER" in result.final_decision.correction_prompt
    assert "SCENARIO SUITE FAILURES" not in result.final_decision.correction_prompt


# ==========================================================================
# CLAIM 7 — protocol resolver precedence unchanged
# ==========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_name,expect_status",
    [
        ("BLOCKED_AUTHORITY", RunStatus.BLOCKED),
        ("REQUIRES_APPROVAL", RunStatus.REQUIRES_APPROVAL),
        ("DEADLOCK", RunStatus.BLOCKED),
    ],
)
async def test_c7_terminal_protocol_states_outrank_the_suite(bits, status_name, expect_status):
    from neyma_product_driver.protocol_resolver import ProtocolResolution, ProtocolStatus
    from neyma_product_driver.protocol_resolver import Deadlock  # type: ignore

    config, store, state = bits
    config.max_iterations = 1

    class Resolver:
        def resolve(self, run_commands=None):
            kwargs = {"status": getattr(ProtocolStatus, status_name), "next_safe_action": "ask"}
            if status_name == "DEADLOCK":
                kwargs["deadlocks"] = [Deadlock(root_cause="circular gate")]
            return ProtocolResolution(**kwargs)

    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])
    result = await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=AlwaysAcceptEvaluator(),
        make_executor=lambda d: SpyExecutor(d, {"gen-dup": False}),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        protocol_resolver=Resolver(),
        planner=planner,
    )
    assert result.status is expect_status


@pytest.mark.asyncio
async def test_c7b_protocol_violation_correction_is_not_replaced_by_the_suite(bits):
    from neyma_product_driver.protocol_resolver import (
        ProtocolResolution,
        ProtocolStatus,
        ProtocolViolation,
    )

    config, store, state = bits
    config.max_iterations = 1

    class Resolver:
        def resolve(self, run_commands=None):
            return ProtocolResolution(
                status=ProtocolStatus.VIOLATION,
                violations=[
                    ProtocolViolation(
                        rule_id="R-1",
                        rule_citation="CLAUDE.md",
                        detail="worktree not owned",
                        observed_state="detached",
                        expected_state="owned",
                    )
                ],
                next_safe_action="fix the worktree",
            )

    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])
    result = await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=AlwaysAcceptEvaluator(),
        make_executor=lambda d: SpyExecutor(d, {"gen-dup": False}),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        protocol_resolver=Resolver(),
        planner=planner,
    )
    assert result.final_decision.decision is Decision.FIX
    assert "Repository protocol violation" in result.final_decision.summary
    assert "SCENARIO SUITE FAILURES" not in result.final_decision.correction_prompt


# ==========================================================================
# CLAIM 8 — holes in the suite gate
# ==========================================================================


def test_c8_skipped_required_p0_scenario_does_not_block_accept():
    """A required P0 scenario that never RAN does not stop an ACCEPT."""
    suite = SuiteResult(
        outcomes=[
            ScenarioOutcome(
                scenario_id="gen-critical",
                scenario_name="gen-critical",
                origin=Origin.GENERATED,
                outcome=Outcome.SKIPPED,
                priority=Priority.P0,
                required=True,
                skip_reason="the suite's execution budget was exhausted before this ran",
            )
        ],
        full_run=True,
    )
    accept = EvaluatorDecision(decision=Decision.ACCEPT, summary="fine")
    decision = _apply_suite_precedence(suite, accept, "backend_generic", lambda _m: None)
    _log(
        "c8_skipped_hole",
        {
            "blocking_failures": len(suite.blocking_failures()),
            "full_run": suite.full_run,
            "decision": decision.decision.value,
        },
    )
    assert decision.decision is Decision.ACCEPT  # DOCUMENTING THE HOLE


@pytest.mark.asyncio
async def test_c8b_budget_exhaustion_in_the_real_loop_still_accepts(bits):
    """End to end: an execution budget of 0s skips everything, run ACCEPTs."""
    config, store, state = bits
    config.max_iterations = 1
    planner = make_planner(
        config, store, [raw_payload(raw_scenario("gen-dup"))], execution_budget_s=0
    )

    result = await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=AlwaysAcceptEvaluator(),
        make_executor=lambda d: SpyExecutor(d, {"gen-dup": False}),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        planner=planner,
    )
    observed = {
        "status": result.status.value,
        "executed": [s[0] for s in SpyExecutor.seen],
        "outcomes": [(o.scenario_id, o.outcome.value) for o in result.suite.outcomes],
        "full_run": result.suite.full_run,
    }
    _log("c8b_budget_zero", observed)
    assert observed["executed"] == []  # nothing ran at all
    assert result.status is RunStatus.ACCEPTED  # DOCUMENTING THE HOLE


def test_c8c_p2_generated_failure_does_not_block():
    suite = SuiteResult(
        outcomes=[
            ScenarioOutcome(
                scenario_id="gen-p2",
                scenario_name="gen-p2",
                origin=Origin.GENERATED,
                outcome=Outcome.FAILED,
                priority=Priority.P2,
                required=True,
            )
        ]
    )
    decision = _apply_suite_precedence(
        suite, EvaluatorDecision(decision=Decision.ACCEPT, summary="fine"), "x", lambda _m: None
    )
    assert decision.decision is Decision.ACCEPT  # documented design choice


# ==========================================================================
# CLAIM 10 — adaptive expansion is driven by real failures
# ==========================================================================


@pytest.mark.asyncio
async def test_c10_expand_after_failures_receives_real_failure_data(bits):
    config, store, state = bits
    config.max_iterations = 2
    planner = make_planner(
        config, store, [raw_payload(raw_scenario("gen-dup")), raw_payload(), raw_payload()]
    )

    captured: list[dict] = []
    real_expand = planner.expand_after_failures

    def spy(**kwargs):
        captured.append(
            {
                "failures": list(kwargs.get("failures") or []),
                "clusters": [c.render() for c in (kwargs.get("clusters") or [])],
                "evaluator_requests": list(kwargs.get("evaluator_requests") or []),
                "diff_files": list(kwargs.get("diff_files") or []),
            }
        )
        return real_expand(**kwargs)

    planner.expand_after_failures = spy  # type: ignore[assignment]

    await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=AlwaysAcceptEvaluator(),
        make_executor=lambda d: SpyExecutor(d, {"gen-dup": False}),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        planner=planner,
    )

    _log("c10_expand_calls", captured)
    assert captured, "expand_after_failures was never called"
    failures = captured[0]["failures"]
    assert any("gen-dup" in f for f in failures), failures
    assert any("got 2, want 1" in f for f in failures), failures
    # The scripted reasoner really was asked for another wave with that basis.
    briefs = planner.reasoner.briefs
    adaptive = [b for b in briefs if b.stage == "adaptive"]
    assert adaptive, [b.stage for b in briefs]
    assert any("gen-dup" in f for f in adaptive[0].basis.prior_failures)


@pytest.mark.asyncio
async def test_c10b_evaluator_scenario_requests_reach_the_generator(bits):
    config, store, state = bits
    config.max_iterations = 2
    planner = make_planner(
        config, store, [raw_payload(raw_scenario("gen-dup")), raw_payload(), raw_payload()]
    )

    class RequestingEvaluator:
        session_id = "e"

        def __init__(self):
            self.n = 0

        async def evaluate(self, prompt, timeout_s=None):
            self.n += 1
            return EvaluatorDecision(
                decision=Decision.FIX,
                summary="needs more",
                correction_prompt=(
                    "On the approval surface, invoice LD56001 shows no accountable owner. "
                    "Add a single named owner beside each open obligation, rendered as "
                    "'Owner: <name>', so an operator can tell who moves it next."
                ),
                requirement_reference="U-042",
                product_principle_reference="ownership",
                scenario="backend_generic",
                observed_result="no owner",
                expected_result="an owner",
                preserve="everything",
                retest="rerun",
                evidence_paths=["/x"],
                additional_verification_needed=True,
                scenario_requests=["approve while another operator approves the same invoice"],
                confidence=0.9,
            )

    await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=RequestingEvaluator(),
        make_executor=lambda d: SpyExecutor(d, {}),  # everything PASSES
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        planner=planner,
    )

    adaptive = [b for b in planner.reasoner.briefs if b.stage == "adaptive"]
    _log(
        "c10b_requests",
        {"stages": [b.stage for b in planner.reasoner.briefs],
         "requests": [list(b.basis.evaluator_requests) for b in adaptive]},
    )
    assert adaptive
    assert "approve while another operator approves the same invoice" in adaptive[0].basis.evaluator_requests


# ==========================================================================
# CLAIM 9 — investigator responsibility not duplicated
# ==========================================================================


def test_c9_no_import_edges_between_investigator_and_generator():
    import subprocess

    src = (REPO / "neyma_product_driver").glob("*.py")
    edges = {}
    for path in src:
        text = path.read_text()
        if path.name == "investigator.py":
            edges["investigator_mentions_generator"] = "scenario_generator" in text
            edges["investigator_mentions_planner"] = "scenario_planner" in text
        if path.name in ("scenario_generator.py", "scenario_planner.py"):
            edges[f"{path.stem}_mentions_investigator"] = (
                "from .investigator" in text or "import investigator" in text
            )
    tracked = subprocess.run(
        ["git", "diff", "--name-only"], cwd=REPO, capture_output=True, text=True
    ).stdout.split()
    edges["investigator_py_modified"] = "neyma_product_driver/investigator.py" in tracked
    edges["completion_auditor_py_modified"] = (
        "neyma_product_driver/completion_auditor.py" in tracked
    )
    edges["protocol_resolver_py_modified"] = (
        "neyma_product_driver/protocol_resolver.py" in tracked
    )
    edges["evaluator_py_modified"] = "neyma_product_driver/evaluator.py" in tracked
    _log("c9_boundaries", edges)
    assert edges["investigator_mentions_generator"] is False
    assert edges["investigator_mentions_planner"] is False
    assert edges["investigator_py_modified"] is False
    assert edges["completion_auditor_py_modified"] is False
    assert edges["protocol_resolver_py_modified"] is False


# ==========================================================================
# EXTRA — the widening pass really re-executes; realistic budget exhaustion;
# CLI flag wiring
# ==========================================================================


@pytest.mark.asyncio
async def test_x1_narrowed_then_widened_pass_really_re_executes(bits):
    """Iteration 2 narrows, goes green, then re-runs the FULL set for real."""
    config, store, state = bits
    config.max_iterations = 2
    planner = make_planner(
        config,
        store,
        [
            raw_payload(
                raw_scenario("gen-dup"),
                raw_scenario(
                    "gen-other",
                    risk_category="boundary",
                    priority="P1",
                    actions=[
                        {
                            "kind": "request",
                            "name": "zero-amount approval",
                            "request": {
                                "method": "POST",
                                "path": "/approve?amount=0",
                                "expect_status": 400,
                            },
                        }
                    ],
                ),
            ),
            raw_payload(),
            raw_payload(),
        ],
    )
    iteration = {"n": 0}
    marks: list[str] = []

    class Counting(FakeBuilder):
        async def send(self, prompt, timeout_s=None):
            iteration["n"] += 1
            marks.append(f"--- iteration {iteration['n']} ---")
            return await super().send(prompt, timeout_s)

    def make_executor(d: Path):
        class E(SpyExecutor):
            async def execute(self, scenario):
                marks.append(scenario.name)
                return await super().execute(scenario)

        return E(d, {"gen-dup": iteration["n"] > 1})

    result = await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=Counting(),
        evaluator=AlwaysAcceptEvaluator(),
        make_executor=make_executor,
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        planner=planner,
    )

    _log("x1_execution_trace", marks)
    assert result.status is RunStatus.ACCEPTED
    assert result.suite.full_run is True
    second = marks[marks.index("--- iteration 2 ---") :]
    # The widening pass is a genuine second execution, not a relabel.
    assert second.count("backend_generic") >= 2, second
    assert second.count("generated:gen-other") >= 1, second


@pytest.mark.asyncio
async def test_x2_realistic_budget_exhaustion_still_accepts(bits):
    """Budget runs out after the first scenario; a required P0 never runs."""
    import asyncio

    config, store, state = bits
    config.max_iterations = 1
    planner = make_planner(
        config,
        store,
        [raw_payload(raw_scenario("gen-dup"))],
        execution_budget_s=1,  # int field; the sleep below exceeds it
    )

    class Slow(SpyExecutor):
        async def execute(self, scenario):
            await asyncio.sleep(1.2)
            return await super().execute(scenario)

    result = await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=AlwaysAcceptEvaluator(),
        make_executor=lambda d: Slow(d, {"gen-dup": False}),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        planner=planner,
    )
    observed = {
        "status": result.status.value,
        "outcomes": [(o.scenario_id, o.outcome.value, o.priority.value) for o in result.suite.outcomes],
        "full_run": result.suite.full_run,
        "blocking": len(result.suite.blocking_failures()),
    }
    _log("x2_realistic_budget", observed)
    assert ("gen-dup", "SKIPPED", "P0") in observed["outcomes"]
    assert result.status is RunStatus.ACCEPTED  # DOCUMENTING THE HOLE


def test_x3_cli_flag_is_wired_and_is_the_only_opt_in(tmp_path, monkeypatch):
    import argparse

    from neyma_product_driver.cli import build_parser  # type: ignore[attr-defined]

    parser = build_parser()
    args = parser.parse_args(["run", "--task", "x", "--auto-scenarios"])
    assert args.auto_scenarios is True
    plain = parser.parse_args(["run", "--task", "x"])
    assert plain.auto_scenarios is False

    from neyma_product_driver.config import DriverConfig

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("x")
    config = DriverConfig(
        neyma_repo=repo,
        driver_root=tmp_path / "d",
        runs_dir=tmp_path / "d" / "runs",
        scenarios_dir=tmp_path / "d" / "scenarios",
        task="t",
    )
    store = EvidenceStore(config.runs_dir, "flagcheck")
    monkeypatch.setattr(
        "neyma_product_driver.scenario_generator.LLMScenarioReasoner",
        lambda *a, **k: ScriptedReasoner([]),
    )
    off = _make_planner(
        config, argparse.Namespace(auto_scenarios=False), store, base_scenario(),
        FakeFounder(), lambda _m: None,
    )
    on = _make_planner(
        config, argparse.Namespace(auto_scenarios=True), store, base_scenario(),
        FakeFounder(), lambda _m: None,
    )
    _log("x3_flag", {"off": off is None, "on": type(on).__name__,
                     "config_enabled_after": config.scenario_generation.enabled})
    assert off is None
    assert isinstance(on, ScenarioPlanner)


@pytest.mark.asyncio
async def test_x4_resume_clobbers_the_previous_scenario_plan_evidence(bits, monkeypatch):
    """A resumed run overwrites scenario-plan.json / wave-01.json in place."""
    import argparse

    config, store, state = bits
    config.max_iterations = 1

    # First process: generates gen-dup and persists the plan + wave 1.
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])
    await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=AlwaysAcceptEvaluator(),
        make_executor=lambda d: SpyExecutor(d, {}),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        planner=planner,
    )
    before = json.loads((store.run_dir / "scenario-plan.json").read_text())
    wave_before = json.loads((store.run_dir / "scenario-generation" / "wave-01.json").read_text())

    # Second process resumes the SAME run id and generates something else.
    resumed_planner = make_planner(
        config, store, [raw_payload(raw_scenario("gen-different", risk_category="boundary"))]
    )
    state2 = RunState(run_id=store.run_id, task=state.task, max_iterations=1)
    await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state2,
        builder=FakeBuilder(),
        evaluator=AlwaysAcceptEvaluator(),
        make_executor=lambda d: SpyExecutor(d, {}),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        planner=resumed_planner,
    )
    after = json.loads((store.run_dir / "scenario-plan.json").read_text())
    wave_after = json.loads((store.run_dir / "scenario-generation" / "wave-01.json").read_text())

    observed = {
        "plan_before": [s["id"] for s in before["scenarios"]],
        "plan_after": [s["id"] for s in after["scenarios"]],
        "wave01_accepted_before": wave_before["accepted_ids"],
        "wave01_accepted_after": wave_after["accepted_ids"],
    }
    _log("x4_resume_clobber", observed)
    # DOCUMENTING THE DEFECT: the first process's plan evidence is gone.
    assert observed["plan_before"] == ["gen-dup"]
    assert "gen-dup" not in observed["plan_after"]
    assert observed["wave01_accepted_before"] != observed["wave01_accepted_after"]
