"""A passing suite with an uncovered blocking risk closes the gap itself.

The shape this file pins down: every executed required scenario passed, the
deterministic gate still names an acceptance-blocking risk with no evidence
behind it, and the run neither accepts (the risk is genuinely unverified) nor
stops (it has the budget and the vocabulary to verify it). It generates the
missing coverage, executes it, and is judged again by the same gate.

Every Claude session is faked. Nothing here consumes Claude usage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neyma_product_driver.cli import (
    _close_coverage_gaps,
    _coverage_gap_only,
    _gap_risks,
    run_control_loop,
)
from neyma_product_driver.config import DriverConfig, ScenarioGenerationConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import Decision, EvaluatorDecision, RunState, RunStatus
from neyma_product_driver.scenario_gate import evaluate_gate
from neyma_product_driver.scenario_plan import IdentifiedRisk, Priority, RiskCategory
from neyma_product_driver.scenario_planner import STAGE_COVERAGE_GAP, ScenarioPlanner
from neyma_product_driver.scenario_suite import (
    Origin,
    Outcome,
    ScenarioOutcome,
    SuiteResult,
)

from scenario_fixtures import (
    APPROVED_CLEANUP,
    APPROVED_STATE,
    FakeFounder,
    ScriptedReasoner,
    base_scenario,
    raw_scenario,
)
from test_scenario_loop import (
    FakeBuilder,
    FakeEvaluator,
    FakeRepoLoader,
    RecordingExecutor,
    accept,
)

RUN_FIXTURE = Path(__file__).parent / "data" / "run-20260821-071958-gate-inputs.json"
LIVE_RUN = Path(__file__).resolve().parents[1] / "runs" / "20260821-071958"


# --------------------------------------------------------------------------
# Payload builders — a run that identifies a P0 risk it does not yet cover
# --------------------------------------------------------------------------

CRASH_RISK = (
    "A process crash after the effect is attempted but before the adapter returns "
    "must land in UNKNOWN_OUTCOME with no orphan and no second effect."
)


def risk_key(description: str, category: RiskCategory) -> str:
    """The identity a coverage-gap scenario must cite, as the planner computes it."""
    return IdentifiedRisk(description=description, risk_category=category).key


def uncovered_risk(
    description: str = CRASH_RISK,
    category: str = "crash_mid_workflow",
    severity: str = "P0",
) -> dict:
    return {
        "id": "R2",
        "description": description,
        "risk_category": category,
        "severity": severity,
        "basis": "the adapter boundary is the only place a second effect can escape",
    }


def first_wave(
    *,
    gap: dict | None = None,
    covered_category: str = "idempotency",
) -> dict:
    """An initial wave that covers one risk and names another it does not."""
    return {
        "risks": [
            {
                "id": "R1",
                "description": "approval may not be idempotent",
                "risk_category": covered_category,
                "severity": "P0",
                "basis": "the diff touched approval state",
            },
            gap if gap is not None else uncovered_risk(),
        ],
        "scenarios": [raw_scenario("gen-idem", risk_category=covered_category)],
        "assumptions": [],
        "unresolved_questions": [],
    }


def gap_wave(
    scenario_id: str = "gen-crash",
    *,
    description: str = CRASH_RISK,
    category: str = "crash_mid_workflow",
) -> dict:
    """A coverage-gap wave that closes the named risk, citing its key."""
    return {
        "risks": [],
        "scenarios": [
            raw_scenario(
                scenario_id,
                risk_category=category,
                source_risks=[risk_key(description, RiskCategory(category))],
                state_checks=[
                    {
                        "name": "effects",
                        "command": APPROVED_STATE,
                        "contains": ["effects=1"],
                    }
                ],
                expected_observations=["effects=1"],
                forbidden_observations=["effects=2"],
                cleanup=[APPROVED_CLEANUP],
            )
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }


def refusing_gap_wave() -> dict:
    """A wave that honestly reports it cannot express the risk it was given."""
    return {
        "risks": [],
        "scenarios": [],
        "assumptions": [],
        "unresolved_questions": [
            "the approved command vocabulary cannot crash the process mid-adapter"
        ],
    }


def make_planner(
    config: DriverConfig,
    store: EvidenceStore,
    payloads: list,
    *,
    generation: ScenarioGenerationConfig | None = None,
) -> ScenarioPlanner:
    config.scenario_generation = generation or ScenarioGenerationConfig(enabled=True)
    return ScenarioPlanner(
        repo=config.neyma_repo,
        config=config.scenario_generation,
        reasoner=ScriptedReasoner(payloads),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )


@pytest.fixture
def loop_bits(driver_config: DriverConfig):
    assert driver_config.runs_dir is not None
    store = EvidenceStore(driver_config.runs_dir, "20260821-000000")
    state = RunState(
        run_id=store.run_id,
        task="build supervised approval",
        max_iterations=driver_config.max_iterations,
    )
    return driver_config, store, state


async def drive(
    config: DriverConfig,
    store: EvidenceStore,
    state: RunState,
    planner: ScenarioPlanner,
    *,
    outcomes: dict[str, bool] | None = None,
    decisions: list[EvaluatorDecision] | None = None,
    log: list[str] | None = None,
    reviewer_factory=None,
    emit=lambda _m: None,
    builder: FakeBuilder | None = None,
):
    log = log if log is not None else []
    return await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=builder or FakeBuilder(),
        evaluator=FakeEvaluator(decisions if decisions is not None else [accept()]),
        make_executor=lambda d: RecordingExecutor(d, outcomes or {}, log),
        emit=emit,
        repo_loader=FakeRepoLoader(),
        planner=planner,
        reviewer_factory=reviewer_factory,
    )


# --------------------------------------------------------------------------
# 1-3. the gap is closed rather than escalated
# --------------------------------------------------------------------------


class TestAnUncoveredRiskIsGeneratedFor:
    async def test_a_passing_suite_with_an_uncovered_p0_does_not_terminate_blocked(
        self, loop_bits
    ):
        """The premature-BLOCKED class itself: budget available, gap closable."""
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(config, store, [first_wave(), gap_wave()])
        log: list[str] = []

        result = await drive(config, store, state, planner, log=log)

        assert result.status is not RunStatus.BLOCKED
        assert result.status is RunStatus.ACCEPTED
        # The generated case really executed; the gap was not waved through.
        assert "gen-crash" in log

    async def test_a_coverage_gap_wave_is_generated_and_no_correction_is_sent(
        self, loop_bits
    ):
        """An absence of coverage is never dressed up as a defect to fix."""
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(config, store, [first_wave(), gap_wave()])
        builder = FakeBuilder()

        result = await drive(config, store, state, planner, builder=builder)

        waves = [w for w in planner.plan.waves if w.stage == STAGE_COVERAGE_GAP]
        assert len(waves) == 1
        assert waves[0].accepted_ids == ["gen-crash"]
        # One builder turn: the opening task. No correction followed, because
        # nothing about the product was observed to be wrong.
        assert len(builder.prompts) == 1
        assert result.state.iterations[0].correction_prompt_sent == ""

    async def test_the_wave_is_aimed_by_the_gate_not_by_evaluator_prose(self, loop_bits):
        """The uncovered set is deterministic; the evaluator does not steer it."""
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(config, store, [first_wave(), gap_wave()])
        asked = accept(
            scenario_requests=["please also test the login page and the CSV export"]
        )

        await drive(config, store, state, planner, decisions=[asked])

        brief = planner.reasoner.briefs[-1]
        assert brief.stage == STAGE_COVERAGE_GAP
        assert len(brief.uncovered_risks) == 1
        assert CRASH_RISK in brief.uncovered_risks[0]
        # The evaluator's wishlist reached neither the brief nor the basis.
        assert brief.basis.evaluator_requests == []
        assert "CSV export" not in brief.render()

    async def test_passing_the_new_scenario_removes_the_risk_from_the_gap_list(
        self, loop_bits
    ):
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(config, store, [first_wave(), gap_wave()])

        result = await drive(config, store, state, planner)

        assert result.status is RunStatus.ACCEPTED
        assert result.gate is not None
        assert result.gate.uncovered_risks == []
        covered = {r.risk_category for r in result.gate.covered_risks}
        assert "crash_mid_workflow" in covered
        # The merged record is one full pass over the widened suite, not a
        # narrowed rerun that happened to be green.
        assert result.suite is not None
        assert result.suite.full_run
        assert "gen-crash" in result.suite.expected_required_ids


# --------------------------------------------------------------------------
# 4, 7. a generated scenario that fails is a defect, and stays one
# --------------------------------------------------------------------------


class TestAFailingGeneratedScenarioIsADefect:
    async def test_it_becomes_a_grounded_builder_fix(self, loop_bits):
        config, store, state = loop_bits
        config.max_iterations = 2
        planner = make_planner(
            config, store, [first_wave(), gap_wave(), {"risks": [], "scenarios": []}]
        )
        builder = FakeBuilder()

        result = await drive(
            config,
            store,
            state,
            planner,
            outcomes={"gen-crash": False},
            decisions=[accept(), accept()],
            builder=builder,
        )

        first = result.state.iterations[0]
        assert first.decision is not None
        assert first.decision.decision is Decision.FIX
        # Grounded in the observed failure, not in the absence of coverage.
        assert "gen-crash" in first.decision.summary
        assert first.decision.correction_prompt
        assert "gen-crash" in first.decision.correction_prompt
        # It reached the same builder session, and the run retested.
        assert len(builder.prompts) == 2
        assert builder.session_id == "builder-1"

    async def test_an_accept_cannot_wave_the_failure_through(self, loop_bits):
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(config, store, [first_wave(), gap_wave()])

        result = await drive(
            config,
            store,
            state,
            planner,
            outcomes={"gen-crash": False},
            decisions=[accept()],
        )

        assert result.status is not RunStatus.ACCEPTED
        assert result.final_decision is not None
        assert result.final_decision.decision is Decision.FIX
        assert result.gate is not None
        assert result.gate.blocks_acceptance

    async def test_closure_stops_generating_once_something_actually_failed(
        self, loop_bits
    ):
        """A failure is evidence; Stage 3 handles it, not another gap wave."""
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(config, store, [first_wave(), gap_wave()])

        await drive(config, store, state, planner, outcomes={"gen-crash": False})

        gap_waves = [w for w in planner.plan.waves if w.stage == STAGE_COVERAGE_GAP]
        assert len(gap_waves) == 1


# --------------------------------------------------------------------------
# 5, 6. the refusals that remain terminal
# --------------------------------------------------------------------------


class TestBlockedIsStillReachable:
    async def test_an_unexpressible_risk_stays_blocked(self, loop_bits):
        """The generator says it cannot express the risk; the run refuses."""
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(config, store, [first_wave(), refusing_gap_wave()])

        result = await drive(config, store, state, planner)

        assert result.status is RunStatus.BLOCKED
        assert result.final_decision is not None
        assert result.final_decision.decision is Decision.BLOCKED
        assert any(CRASH_RISK in p for p in result.final_decision.problems)
        notes = result.state.iterations[0].notes
        assert any("no runnable scenario" in n for n in notes)

    async def test_an_exhausted_generation_budget_stays_blocked(self, loop_bits):
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(
            config,
            store,
            [first_wave(), gap_wave()],
            generation=ScenarioGenerationConfig(enabled=True, max_waves=1),
        )

        result = await drive(config, store, state, planner)

        assert result.status is RunStatus.BLOCKED
        assert planner.waves_used == 1
        notes = result.state.iterations[0].notes
        assert any("budget is spent" in n for n in notes)
        # No second wave was even attempted, so no scenario was invented for it.
        assert [w.stage for w in planner.plan.waves] == ["initial"]

    async def test_a_scenario_the_gate_never_verifies_does_not_loop_forever(
        self, loop_bits
    ):
        """A wave that keeps producing the wrong coverage still terminates."""
        config, store, state = loop_bits
        config.max_iterations = 1
        # Each wave answers with a scenario for a category that is not the gap.
        wrong = [
            {
                "risks": [],
                "scenarios": [
                    raw_scenario(
                        f"gen-wrong-{n}",
                        risk_category="boundary",
                        source_risks=[risk_key(CRASH_RISK, RiskCategory.CRASH_MID_WORKFLOW)],
                    )
                ],
            }
            for n in range(1, 5)
        ]
        planner = make_planner(config, store, [first_wave(), *wrong])

        result = await drive(config, store, state, planner)

        assert result.status is RunStatus.BLOCKED
        assert planner.waves_used <= config.scenario_generation.max_waves

    def test_generation_problems_disqualify_closure_outright(self):
        """A wave that errored can never reach VERIFIED, so closure is futile."""
        outcome = ScenarioOutcome(
            scenario_id="gen-idem",
            scenario_name="gen-idem",
            origin=Origin.GENERATED,
            outcome=Outcome.PASSED,
            priority=Priority.P0,
            risk_category="idempotency",
            required=True,
            evidence_verified=True,
            evidence_path="/runs/x/gen-idem",
        )
        suite_result = SuiteResult(
            outcomes=[outcome], expected_required_ids=["gen-idem"]
        )
        risks = [
            IdentifiedRisk(
                description=CRASH_RISK,
                risk_category=RiskCategory.CRASH_MID_WORKFLOW,
                severity=Priority.P0,
            )
        ]

        clean = evaluate_gate(suite_result, risks=risks)
        broken = evaluate_gate(
            suite_result, risks=risks, generation_problems=["wave 1 failed: session died"]
        )

        assert _coverage_gap_only(clean, suite_result) is True
        assert _coverage_gap_only(broken, suite_result) is False


# --------------------------------------------------------------------------
# 8. review ordering
# --------------------------------------------------------------------------


class TestReviewStillFollowsTheGate:
    async def test_no_review_is_launched_while_the_gate_is_unverified(self, loop_bits):
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(config, store, [first_wave(), refusing_gap_wave()])
        launched: list[str] = []

        def reviewer_factory(*_a, **_k):
            launched.append("review")
            raise AssertionError("a review must not run while the gate refuses")

        result = await drive(
            config, store, state, planner, reviewer_factory=reviewer_factory
        )

        assert result.status is RunStatus.BLOCKED
        assert launched == []
        assert result.reviews == []

    def test_closure_runs_before_the_reviewer_is_even_considered(self):
        """Structural, because the ordering is the guarantee.

        A reviewer shown a suite that is about to grow reviews the wrong
        evidence, and a reviewer launched for a run the gate is about to refuse
        is work nobody asked for. Both are prevented by where closure sits.
        """
        import inspect

        source = inspect.getsource(run_control_loop)
        assert (
            source.index("_close_coverage_gaps")
            < source.index("proportional independent review")
            < source.index("_run_independent_review")
        )

    async def test_the_reviewer_sees_a_verified_gate_when_it_does_run(self, loop_bits):
        config, store, state = loop_bits
        config.max_iterations = 1
        planner = make_planner(config, store, [first_wave(), gap_wave()])
        seen: list[object] = []

        class Reviewer:
            def review(self, **kwargs):
                seen.append(kwargs.get("suite_result"))
                from neyma_product_driver.reviewer import IndependentReview

                return IndependentReview(verdict="SUPPORTED", summary="fine")

        result = await drive(
            config,
            store,
            state,
            planner,
            reviewer_factory=lambda *_a, **_k: Reviewer(),
        )

        assert result.status is RunStatus.ACCEPTED
        # Whether a review was warranted is the risk assessor's call; what this
        # pins is that if one ran, the suite it was shown already contained the
        # coverage-gap scenario rather than the pre-closure record.
        for suite_result in seen:
            assert suite_result is not None
            assert suite_result.by_id("gen-crash") is not None


# --------------------------------------------------------------------------
# 9. nothing here is about M3, P6 or crash_mid_workflow
# --------------------------------------------------------------------------


class TestThisGeneralizes:
    async def test_a_different_risk_category_and_task_behaves_identically(
        self, driver_config: DriverConfig
    ):
        assert driver_config.runs_dir is not None
        store = EvidenceStore(driver_config.runs_dir, "20260821-000001")
        state = RunState(
            run_id=store.run_id,
            task="expose the tenant-scoped shipment list",
            max_iterations=1,
        )
        driver_config.max_iterations = 1
        leak = (
            "A shipment list request carrying tenant A's session must never render a "
            "row belonging to tenant B."
        )
        planner = make_planner(
            driver_config,
            store,
            [
                first_wave(gap=uncovered_risk(leak, "cross_tenant"), covered_category="boundary"),
                gap_wave("gen-tenant", description=leak, category="cross_tenant"),
            ],
        )
        log: list[str] = []

        result = await drive(driver_config, store, state, planner, log=log)

        assert result.status is RunStatus.ACCEPTED
        assert "gen-tenant" in log
        assert result.gate is not None
        assert result.gate.uncovered_risks == []
        assert {r.risk_category for r in result.gate.covered_risks} >= {"cross_tenant"}

    def test_the_closure_path_names_no_product_specific_identifier(self):
        import inspect

        source = inspect.getsource(_close_coverage_gaps) + inspect.getsource(
            _coverage_gap_only
        )
        for forbidden in ("crash_mid_workflow", "M3", "P6", "neyma", "effect_grant"):
            assert forbidden not in source


# --------------------------------------------------------------------------
# 10. the run that produced this finding
# --------------------------------------------------------------------------


def _load_run_fixture() -> dict:
    return json.loads(RUN_FIXTURE.read_text(encoding="utf-8"))


class TestRun20260821071958:
    """The verification run whose control flow this change corrects.

    The fixture is a distillate of that run's own artifacts — its risk register
    and its outcome records, copied verbatim. ``runs/`` is not committed, so the
    distillate is what keeps this regression permanent; the test below checks it
    against the live run whenever that directory is still present.
    """

    def test_the_fixture_still_matches_the_live_run(self):
        if not LIVE_RUN.exists():
            pytest.skip(f"{LIVE_RUN} is not present (runs/ is not committed)")
        fixture = _load_run_fixture()
        plan = json.loads((LIVE_RUN / "scenario-plan.json").read_text(encoding="utf-8"))
        suite = json.loads(
            (LIVE_RUN / "iteration-01" / "suite-result.json").read_text(encoding="utf-8")
        )

        assert [
            (r["risk_category"], r["severity"], r["description"]) for r in plan["risks"]
        ] == [
            (r["risk_category"], r["severity"], r["description"])
            for r in fixture["risks"]
        ]
        assert suite["expected_required_ids"] == fixture["expected_required_ids"]
        assert [
            (o["scenario_id"], o["outcome"], o["risk_category"]) for o in suite["outcomes"]
        ] == [
            (o["scenario_id"], o["outcome"], o["risk_category"])
            for o in fixture["outcomes"]
        ]

    def test_it_reproduces_the_premature_blocked_class(self):
        fixture = _load_run_fixture()
        risks = [IdentifiedRisk.model_validate(r) for r in fixture["risks"]]
        suite_result = SuiteResult(
            expected_required_ids=fixture["expected_required_ids"],
            outcomes=[ScenarioOutcome.model_validate(o) for o in fixture["outcomes"]],
        )

        verdict = evaluate_gate(suite_result, risks=risks)

        # Exactly the situation the run reported: everything that ran passed,
        # nothing is unverified, and one P0 risk has no scenario at all.
        assert suite_result.blocking_failures() == []
        assert verdict.unverified == []
        assert verdict.required_passed == verdict.required_total == 10
        assert [r.risk_category for r in verdict.uncovered_risks] == [
            "crash_mid_workflow"
        ]
        assert verdict.blocks_acceptance

        # That is the class this change addresses: a gap, with a register entry
        # behind it, and therefore a wave that can be aimed at it.
        assert _coverage_gap_only(verdict, suite_result) is True

        class _Planner:
            def __init__(self, risks):
                self.plan = type("_Plan", (), {"risks": risks})()

        gaps = _gap_risks(_Planner(risks), verdict)
        assert [r.risk_category.value for r in gaps] == ["crash_mid_workflow"]
        assert gaps[0].severity.blocks_acceptance

    def test_that_run_still_had_generation_budget_when_it_stopped(self):
        fixture = _load_run_fixture()
        config = ScenarioGenerationConfig(**fixture["scenario_generation_budgets"])

        assert fixture["waves_used"] < config.max_waves
        assert len(fixture["scenario_ids"]) < config.max_total_scenarios
