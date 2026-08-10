"""Suites inside the control loop: precedence, reruns, and backwards compatibility.

Every Claude session is faked. Nothing here consumes Claude usage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from neyma_product_driver.cli import _apply_suite_precedence, run_control_loop
from neyma_product_driver.config import DriverConfig, ScenarioGenerationConfig
from neyma_product_driver.context import ActiveUnit, RepositoryContext
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import (
    AssertionResult,
    Decision,
    EvaluatorDecision,
    RunState,
    RunStatus,
    ScenarioResult,
)
from neyma_product_driver.scenario_plan import Priority, RiskCategory
from neyma_product_driver.scenario_planner import ScenarioPlanner
from neyma_product_driver.scenario_suite import (
    Origin,
    Outcome,
    ScenarioOutcome,
    SuiteResult,
)
from neyma_product_driver.scenarios import Scenario

from scenario_fixtures import (
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    base_scenario,
    raw_payload,
    raw_scenario,
)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


@dataclass
class FakeTurn:
    text: str = "done.\n\nRUNNABLE CHECKPOINT: run `make demo`."
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
    """Supplies the active unit, which is what grounds a generated requirement.

    Without it, `requirement_reference` has nothing to be checked against and
    every proposal is (correctly) refused as inventing a requirement.
    """

    def __init__(self, unit: FakeUnit | None = None) -> None:
        self.unit = unit or FakeUnit()

    def resolve_active_unit(self) -> FakeUnit:
        return self.unit

    def load(self, topics: list[str] | None = None) -> RepositoryContext:
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


class FakeEvaluator:
    def __init__(self, decisions: list[EvaluatorDecision]) -> None:
        self.session_id = "evaluator-1"
        self.decisions = list(decisions)
        self.prompts: list[str] = []

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision:
        self.prompts.append(prompt)
        if self.decisions:
            return self.decisions.pop(0)
        return EvaluatorDecision(decision=Decision.ACCEPT, summary="fine")


class RecordingExecutor:
    """Executes from a per-iteration script keyed by bare scenario id."""

    def __init__(self, artifact_dir: Path, outcomes: dict[str, bool], log: list[str]) -> None:
        self.artifact_dir = artifact_dir
        self.outcomes = outcomes
        self.log = log
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        key = scenario.name.split(":", 1)[-1]
        self.log.append(key)
        passing = self.outcomes.get(key, True)
        return ScenarioResult(
            scenario_name=scenario.name,
            commands=[],
            assertions=[
                AssertionResult(
                    kind="expect_state",
                    target=f"{key}: payments row count",
                    passed=passing,
                    detail="" if passing else "got 2, want 1",
                )
            ],
        )


def accept(**kw) -> EvaluatorDecision:
    return EvaluatorDecision(
        decision=Decision.ACCEPT, summary="looks good", observed_behavior=["saw it"], **kw
    )


def grounded_fix(n: int = 1) -> EvaluatorDecision:
    return EvaluatorDecision(
        decision=Decision.FIX,
        summary="a discrepancy",
        correction_prompt=(
            f"On the approval surface, invoice LD5600{n} shows no accountable owner. Add a "
            "single named owner beside each open obligation, rendered as 'Owner: <name>', so "
            "an operator can tell at a glance who moves it next. Do not change the ordering."
        ),
        requirement_reference="U-042",
        product_principle_reference="ownership",
        scenario="backend_generic",
        observed_result="no owner shown",
        expected_result="an owner is shown",
        preserve="everything else",
        retest="re-run the suite",
        evidence_paths=["/runs/x/iteration-01"],
        confidence=0.9,
    )


@pytest.fixture
def loop_bits(driver_config: DriverConfig):
    assert driver_config.runs_dir is not None
    store = EvidenceStore(driver_config.runs_dir, "20260809-000000")
    state = RunState(
        run_id=store.run_id, task="build supervised approval", max_iterations=driver_config.max_iterations
    )
    return driver_config, store, state


def make_planner(config: DriverConfig, store: EvidenceStore, payloads: list) -> ScenarioPlanner:
    config.scenario_generation = ScenarioGenerationConfig(enabled=True)
    return ScenarioPlanner(
        repo=config.neyma_repo,
        config=config.scenario_generation,
        reasoner=ScriptedReasoner(payloads),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )


# --------------------------------------------------------------------------
# Backwards compatibility
# --------------------------------------------------------------------------


class TestExistingWorkflowsUnchanged:
    @pytest.mark.asyncio
    async def test_without_a_planner_the_loop_runs_exactly_one_scenario(self, loop_bits):
        config, store, state = loop_bits
        log: list[str] = []
        scenario = Scenario(name="backend_generic")

        result = await run_control_loop(
            config=config,
            scenario=scenario,
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            make_executor=lambda d: RecordingExecutor(d, {}, log),
            emit=lambda _m: None,
        )

        assert result.status is RunStatus.ACCEPTED
        assert log == ["backend_generic"]
        assert result.suite is None
        assert result.state.iterations[0].suite is None
        assert result.state.iterations[0].scenario is not None

    @pytest.mark.asyncio
    async def test_a_handwritten_scenario_still_runs_unchanged_alongside_generated_ones(
        self, loop_bits
    ):
        config, store, state = loop_bits
        log: list[str] = []
        planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])

        result = await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            make_executor=lambda d: RecordingExecutor(d, {}, log),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            planner=planner,
        )

        assert result.status is RunStatus.ACCEPTED
        # The explicitly selected scenario ran, first, and is permanent.
        assert log[0] == "backend_generic"
        assert "gen-dup" in log
        assert result.suite is not None
        permanent = [o for o in result.suite.outcomes if o.origin is Origin.PERMANENT]
        assert [o.scenario_id for o in permanent] == ["backend_generic"]

    def test_handwritten_scenario_files_still_parse_and_use_the_phase_form(self):
        from neyma_product_driver.scenarios import load_scenario

        for name in ("backend_generic", "browser_generic"):
            scenario = load_scenario(Path("scenarios") / f"{name}.yaml")
            assert scenario.steps == []
            assert scenario.uses_steps is False


# --------------------------------------------------------------------------
# Suite precedence
# --------------------------------------------------------------------------


def _outcome(
    scenario_id: str,
    outcome: Outcome,
    *,
    origin: Origin = Origin.GENERATED,
    priority: Priority = Priority.P0,
    required: bool | None = None,
    evidence_verified: bool | None = None,
) -> ScenarioOutcome:
    return ScenarioOutcome(
        scenario_id=scenario_id,
        scenario_name=scenario_id,
        origin=origin,
        outcome=outcome,
        priority=priority,
        risk_category="idempotency",
        # A generated case is required exactly when its priority blocks, which
        # is what build_suite records on the real path.
        required=priority.blocks_acceptance if required is None else required,
        evidence_path=f"/runs/x/{scenario_id}",
        # The executor confirms a passing scenario's evidence and downgrades it
        # to BLOCKED when it cannot, so a PASSED outcome that reached the gate
        # always carries verified evidence. Synthesising one without it would be
        # testing a state the executor cannot produce.
        evidence_verified=(
            (outcome is Outcome.PASSED) if evidence_verified is None else evidence_verified
        ),
        failed_assertions=["expect_state: payments row count — got 2, want 1"],
    )


class TestAcceptCannotOverrideAFailedScenario:
    def test_an_accept_becomes_a_fix_when_a_required_scenario_failed(self):
        suite = SuiteResult(outcomes=[_outcome("gen-dup", Outcome.FAILED)])

        decision = _apply_suite_precedence(suite, accept(), "backend_generic", lambda _m: None)

        assert decision.decision is Decision.FIX
        assert "gen-dup" in decision.summary
        assert decision.correction_prompt
        assert decision.evidence_paths == ["/runs/x/gen-dup"]

    def test_a_failed_permanent_scenario_also_overrides_an_accept(self):
        suite = SuiteResult(
            outcomes=[_outcome("backend_generic", Outcome.FAILED, origin=Origin.PERMANENT)]
        )

        decision = _apply_suite_precedence(suite, accept(), "backend_generic", lambda _m: None)

        assert decision.decision is Decision.FIX
        assert "permanent regression coverage" in decision.summary

    def test_a_blocked_scenario_also_prevents_acceptance(self):
        suite = SuiteResult(outcomes=[_outcome("gen-dup", Outcome.BLOCKED)])

        decision = _apply_suite_precedence(suite, accept(), "backend_generic", lambda _m: None)

        assert decision.decision is Decision.FIX

    def test_a_low_priority_failure_does_not_block_acceptance(self):
        suite = SuiteResult(outcomes=[_outcome("gen-nice", Outcome.FAILED, priority=Priority.P3)])

        decision = _apply_suite_precedence(suite, accept(), "backend_generic", lambda _m: None)

        assert decision.decision is Decision.ACCEPT

    def test_an_accept_on_a_partial_suite_becomes_blocked(self):
        suite = SuiteResult(
            outcomes=[_outcome("gen-dup", Outcome.PASSED)],
            full_run=False,
            selection_reason="only the failed scenarios reran",
        )

        decision = _apply_suite_precedence(suite, accept(), "backend_generic", lambda _m: None)

        assert decision.decision is Decision.BLOCKED
        assert "only part of the required scenario suite" in decision.summary

    def test_a_green_full_suite_leaves_an_accept_alone(self):
        suite = SuiteResult(outcomes=[_outcome("gen-dup", Outcome.PASSED)])

        decision = _apply_suite_precedence(suite, accept(), "backend_generic", lambda _m: None)

        assert decision.decision is Decision.ACCEPT

    def test_a_fix_is_not_replaced_by_the_suite(self):
        # The evaluator's reasoning is more informative than the suite's; only
        # an ACCEPT is overridden.
        suite = SuiteResult(outcomes=[_outcome("gen-dup", Outcome.FAILED)])
        original = grounded_fix()

        decision = _apply_suite_precedence(suite, original, "backend_generic", lambda _m: None)

        assert decision is original

    def test_the_correction_groups_clustered_failures_and_separates_distinct_ones(self):
        from neyma_product_driver.failure_clustering import FailureCluster

        suite = SuiteResult(
            outcomes=[
                _outcome("gen-dup", Outcome.FAILED),
                _outcome("gen-race", Outcome.FAILED),
                _outcome("gen-auth", Outcome.FAILED),
            ],
            clusters=[
                FailureCluster(
                    cluster_id="C01",
                    affected_scenarios=["gen-dup", "gen-race"],
                    likely_failure_domain="effect duplication under repetition",
                    singleton=False,
                ),
                FailureCluster(
                    cluster_id="C02",
                    affected_scenarios=["gen-auth"],
                    likely_failure_domain="gen-auth: status 403 expected, got 200",
                    singleton=True,
                ),
            ],
        )

        correction = _apply_suite_precedence(
            suite, accept(), "backend_generic", lambda _m: None
        ).correction_prompt

        assert "SHARE ONE CAUSE" in correction
        assert "effect duplication under repetition" in correction
        assert "THESE FAILURES ARE DISTINCT" in correction
        assert "gen-auth" in correction
        assert "Do not change a scenario" in correction

    @pytest.mark.asyncio
    async def test_in_the_loop_an_accept_with_a_failed_scenario_does_not_accept(self, loop_bits):
        config, store, state = loop_bits
        config.max_iterations = 1
        log: list[str] = []
        planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])

        result = await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            make_executor=lambda d: RecordingExecutor(d, {"gen-dup": False}, log),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            planner=planner,
        )

        assert result.status is not RunStatus.ACCEPTED
        assert result.final_decision.decision is Decision.FIX


class TestPrecedenceOrderUnchanged:
    @pytest.mark.asyncio
    async def test_a_protocol_violation_still_outranks_the_suite(self, loop_bits):
        from neyma_product_driver.protocol_resolver import ProtocolStatus

        config, store, state = loop_bits
        config.max_iterations = 1

        class BlockingResolver:
            def resolve(self, run_commands=None):
                from neyma_product_driver.protocol_resolver import ProtocolResolution

                return ProtocolResolution(
                    status=ProtocolStatus.BLOCKED_AUTHORITY,
                    next_safe_action="ask the founder",
                )

        planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])
        result = await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            make_executor=lambda d: RecordingExecutor(d, {"gen-dup": False}, []),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            protocol_resolver=BlockingResolver(),
            planner=planner,
        )

        # The governance blocker wins: BLOCKED, not the suite's FIX.
        assert result.status is RunStatus.BLOCKED
        assert "authority" in result.final_decision.summary.lower()


# --------------------------------------------------------------------------
# The regression feedback loop
# --------------------------------------------------------------------------


class TestRegressionFeedbackLoop:
    @pytest.mark.asyncio
    async def test_a_failed_scenario_reruns_and_becomes_a_promotion_candidate(self, loop_bits):
        config, store, state = loop_bits
        config.max_iterations = 3
        planner = make_planner(
            config,
            store,
            [
                raw_payload(raw_scenario("gen-dup")),
                raw_payload(),  # diff refinement adds nothing
                raw_payload(),  # adaptive wave adds nothing
            ],
        )
        # Iteration 1 fails; the "builder fixes it" and iteration 2 passes.
        iteration = {"n": 0}

        def make_executor(artifact_dir: Path) -> RecordingExecutor:
            return RecordingExecutor(
                artifact_dir, {"gen-dup": iteration["n"] > 1}, per_iteration_log
            )

        per_iteration_log: list[str] = []

        class CountingBuilder(FakeBuilder):
            async def send(self, prompt: str, timeout_s: int | None = None) -> FakeTurn:
                iteration["n"] += 1
                return await super().send(prompt, timeout_s)

        result = await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=CountingBuilder(),
            evaluator=FakeEvaluator([accept(), accept()]),
            make_executor=make_executor,
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            planner=planner,
        )

        assert result.status is RunStatus.ACCEPTED
        candidates = result.promotion_candidates
        assert [c.scenario_id for c in candidates] == ["gen-dup"]
        assert candidates[0].discovered_in_iteration == 1
        assert candidates[0].fixed_in_iteration == 2
        assert candidates[0].promoted is False
        # And it is on disk, as suggestions rather than as a repository change.
        recorded = json.loads((store.run_dir / "promotion-candidates.json").read_text())
        assert recorded[0]["scenario_id"] == "gen-dup"

    @pytest.mark.asyncio
    async def test_the_permanent_suite_directory_is_never_written_to(self, loop_bits):
        config, store, state = loop_bits
        assert config.scenarios_dir is not None
        config.scenarios_dir.mkdir(parents=True, exist_ok=True)
        planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])

        await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            make_executor=lambda d: RecordingExecutor(d, {}, []),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            planner=planner,
        )

        assert list(config.scenarios_dir.iterdir()) == []

    @pytest.mark.asyncio
    async def test_the_full_required_set_runs_before_acceptance(self, loop_bits):
        config, store, state = loop_bits
        config.max_iterations = 2
        planner = make_planner(
            config, store, [raw_payload(raw_scenario("gen-dup")), raw_payload(), raw_payload()]
        )
        iteration = {"n": 0}
        log: list[str] = []

        class CountingBuilder(FakeBuilder):
            async def send(self, prompt: str, timeout_s: int | None = None) -> FakeTurn:
                iteration["n"] += 1
                log.append(f"--- iteration {iteration['n']} ---")
                return await super().send(prompt, timeout_s)

        result = await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=CountingBuilder(),
            evaluator=FakeEvaluator([accept(), accept()]),
            make_executor=lambda d: RecordingExecutor(
                d, {"gen-dup": iteration["n"] > 1}, log
            ),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            planner=planner,
        )

        assert result.status is RunStatus.ACCEPTED
        assert result.suite.full_run is True
        # The accepted pass covered both the permanent scenario and the
        # generated one, not just the scenario that had failed.
        accepted_ids = {o.scenario_id for o in result.suite.outcomes}
        assert accepted_ids == {"backend_generic", "gen-dup"}


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


class TestSuiteEvidence:
    @pytest.mark.asyncio
    async def test_the_manifest_links_evidence_back_to_scenario_ids(self, loop_bits):
        config, store, state = loop_bits
        planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])

        await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            make_executor=lambda d: RecordingExecutor(d, {}, []),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            planner=planner,
        )

        suite_file = store.iteration_dir(1) / "suite-result.json"
        assert suite_file.exists()
        payload = json.loads(suite_file.read_text())
        by_id = {o["scenario_id"]: o for o in payload["outcomes"]}
        assert set(by_id) == {"backend_generic", "gen-dup"}
        # Each outcome names a real directory holding that scenario's artifacts.
        for scenario_id, outcome in by_id.items():
            assert Path(outcome["evidence_path"]).name == scenario_id
            assert Path(outcome["evidence_path"]).exists()
        # And the scenario plan explains why the generated one exists.
        plan = json.loads((store.run_dir / "scenario-plan.json").read_text())
        generated = plan["scenarios"][0]
        assert generated["id"] == "gen-dup"
        assert generated["provenance"]["generating_risk"]
        assert generated["provenance"]["kind"] == "ephemeral"

    @pytest.mark.asyncio
    async def test_the_evaluator_is_given_a_summary_not_a_dump(self, loop_bits):
        config, store, state = loop_bits
        planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])
        evaluator = FakeEvaluator([accept()])

        await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=evaluator,
            make_executor=lambda d: RecordingExecutor(d, {"gen-dup": False}, []),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            planner=planner,
        )

        prompt = evaluator.prompts[0]
        assert "VERIFICATION SUITE" in prompt
        assert "gen-dup" in prompt
        assert "not a claim that all possible cases were verified" in prompt
        # The failing scenario's evidence directory is cited rather than inlined.
        assert "evidence:" in prompt

    @pytest.mark.asyncio
    async def test_the_builder_is_told_what_will_be_exercised(self, loop_bits):
        config, store, state = loop_bits
        planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])
        builder = FakeBuilder()

        await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=builder,
            evaluator=FakeEvaluator([accept()]),
            make_executor=lambda d: RecordingExecutor(d, {}, []),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            planner=planner,
        )

        prompt = builder.prompts[0]
        assert "generated situations" in prompt
        assert "not additional requirements" in prompt


class TestResumePreservesGeneratedState:
    @pytest.mark.asyncio
    async def test_a_run_can_be_resumed_from_its_persisted_plan(self, loop_bits):
        from neyma_product_driver.scenario_plan import (
            GeneratedScenarioPlan,
            compile_to_scenario,
        )
        from neyma_product_driver.scenario_validation import ApprovedCommands

        config, store, state = loop_bits
        planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])
        await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            make_executor=lambda d: RecordingExecutor(d, {}, []),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            planner=planner,
        )

        # A fresh process reads the plan back and can rebuild the same scenarios.
        reloaded = GeneratedScenarioPlan.model_validate_json(
            (store.run_dir / "scenario-plan.json").read_text()
        )
        assert [s.id for s in reloaded.scenarios] == ["gen-dup"]
        approved = ApprovedCommands.from_sources(scenarios=[base_scenario()])
        model = reloaded.scenarios[0]
        allowed, refusals = approved.resolve(model.command_strings())
        assert not refusals
        rebuilt = compile_to_scenario(model, base=base_scenario(), approved_commands=allowed)
        assert rebuilt.name == "generated:gen-dup"
        assert [s.kind for s in rebuilt.steps] == [s.kind for s in planner.compiled["gen-dup"].steps]

    @pytest.mark.asyncio
    async def test_the_promotion_ledger_survives_a_reload(self, loop_bits):
        from neyma_product_driver.scenario_planner import PromotionLedger, PromotionCandidate

        _config, store, _state = loop_bits
        ledger = PromotionLedger(store.run_dir)
        ledger.record(
            PromotionCandidate(scenario_id="gen-dup", title="t", bug_discovered="boom")
        )

        assert [c.scenario_id for c in PromotionLedger(store.run_dir).load()] == ["gen-dup"]


class TestInvestigatorStaysSeparate:
    def test_the_generator_and_the_investigator_are_different_systems(self):
        import neyma_product_driver.investigator as investigator
        import neyma_product_driver.scenario_generator as generator

        # No import edge in either direction: the generator asks "what should we
        # test", the investigator asks "why did this happen".
        assert "scenario_generator" not in investigator.__doc__.lower().replace(" ", "_")
        source = Path("neyma_product_driver/investigator.py").read_text()
        assert "scenario_generator" not in source
        assert "scenario_planner" not in source
        gen_source = Path("neyma_product_driver/scenario_generator.py").read_text()
        assert "from .investigator import" not in gen_source
        assert "hypothesis_engine" not in gen_source
        assert "not diagnose failures" in generator.GENERATOR_SYSTEM

    def test_findings_flow_from_investigation_into_generation_only(self, tmp_path):
        from neyma_product_driver.cli import _investigation_findings
        from neyma_product_driver.models import IterationRecord

        record = IterationRecord(
            iteration=1,
            investigation={
                "conclusion": "state read after approval can race the commit",
                "hypotheses": [
                    {"statement": "the write is not flushed", "status": "SUPPORTED"},
                    {"statement": "an unrelated guess", "status": "REFUTED"},
                ],
            },
        )

        findings = _investigation_findings(record)

        assert "state read after approval can race the commit" in findings
        assert "the write is not flushed" in findings
        assert "an unrelated guess" not in findings


class TestAuditorPrecedenceUnchanged:
    @pytest.mark.asyncio
    async def test_an_unsupported_completion_claim_still_outranks_the_suite(self, loop_bits):
        """The audit converts ACCEPT→FIX before the suite is consulted.

        The suite gate is deliberately last so that the two older precedence
        layers keep behaving exactly as they did: when the audit already has a
        grounded correction, that is what the builder gets.
        """
        from neyma_product_driver.completion_auditor import (
            AuditDecision,
            CompletionAudit,
            Contradiction,
        )

        config, store, state = loop_bits
        config.max_iterations = 1

        class ContradictingAuditor:
            def audit(self, report, unit=None, run_commands=None, evidence_dir=""):
                return CompletionAudit(
                    decision=AuditDecision.CONTRADICTED,
                    headline="the registry does not support the claimed completion",
                    contradictions=[
                        Contradiction(
                            what="claimed COMPLETE, registry says READY",
                            claimed="COMPLETE",
                            observed="READY",
                        )
                    ],
                    correction_prompt=(
                        "The builder reported the unit complete. The registry still marks it "
                        "READY and no receipt binds to HEAD. Correct the status surfaces so "
                        "they reflect only what the evidence supports."
                    ),
                    confidence=0.9,
                )

        planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])
        result = await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            make_executor=lambda d: RecordingExecutor(d, {"gen-dup": False}, []),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            auditor=ContradictingAuditor(),
            planner=planner,
        )

        decision = result.final_decision
        assert decision.decision is Decision.FIX
        # The audit's correction survived; the suite did not replace it.
        assert "registry still marks it" in decision.correction_prompt
        assert "SCENARIO SUITE FAILURES" not in decision.correction_prompt
