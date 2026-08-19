"""The contract the remediation established, stated as behaviour.

Every test here corresponds to a defect that independent verification found and
reproduced (see ``verification-evidence/ADJUDICATION.md``). They are written so
that removing the requirement makes a test fail — that is the point of them.
Where a test looks redundant with an existing one, it is not: the existing test
usually asserted the *reporting* of a condition, and these assert its *effect*.

Every Claude session is faked. Nothing here consumes Claude usage.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from neyma_product_driver.cli import _apply_suite_precedence, run_control_loop
from neyma_product_driver.config import (
    DriverConfig,
    ScenarioGenerationConfig,
    ScenarioRunConfig,
)
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import Decision, RunState, RunStatus
from neyma_product_driver.scenario_gate import GateStatus, evaluate_gate
from neyma_product_driver.scenario_generator import (
    GenerationBrief,
    LLMScenarioReasoner,
    parse_scenarios,
    provenance_for,
)
from neyma_product_driver.scenario_plan import (
    GenerationBasis,
    Priority,
    ScenarioProvenance,
)
from neyma_product_driver.scenario_planner import ScenarioPlanner
from neyma_product_driver.scenario_suite import (
    FailureEvidence,
    Origin,
    Outcome,
    ScenarioOutcome,
    SuiteExecutor,
    SuiteResult,
    build_suite,
    verify_case_evidence,
)
from neyma_product_driver.scenario_validation import (
    ApprovedCommands,
    resolve_http_target,
    validate_scenario,
)
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor

from scenario_fixtures import (
    APPROVED_STATE,
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    base_scenario,
    make_scenario,
    raw_payload,
    raw_scenario,
    validation_context,
)
from test_scenario_loop import (  # reuse the established loop harness
    FakeBuilder,
    FakeEvaluator,
    FakeRepoLoader,
    RecordingExecutor,
    accept,
    make_planner,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def outcome(
    scenario_id: str,
    result: Outcome,
    *,
    origin: Origin = Origin.GENERATED,
    priority: Priority = Priority.P0,
    required: bool = True,
    evidence_verified: bool | None = None,
    **kw,
) -> ScenarioOutcome:
    return ScenarioOutcome(
        scenario_id=scenario_id,
        scenario_name=scenario_id,
        origin=origin,
        outcome=result,
        priority=priority,
        required=required,
        evidence_path=f"/runs/x/{scenario_id}",
        evidence_verified=(
            (result is Outcome.PASSED) if evidence_verified is None else evidence_verified
        ),
        **kw,
    )


def suite_of(*outcomes: ScenarioOutcome, full_run: bool = True) -> SuiteResult:
    return SuiteResult(
        outcomes=list(outcomes),
        full_run=full_run,
        expected_required_ids=[o.scenario_id for o in outcomes if o.required],
    )


def distinct_raw(scenario_id: str, **kw) -> dict:
    """A proposal whose operations differ from every other one here.

    Duplicate detection keys on the operations and expectations, so a wave-two
    fixture that reuses the default ones is refused as a repeat — correctly, but
    for a reason that has nothing to do with what these tests are about.
    """
    return raw_scenario(
        scenario_id,
        actions=[
            {
                "kind": "request",
                "name": scenario_id,
                "request": {"method": "POST", "path": f"/approve/{scenario_id}",
                            "expect_status": 200},
            }
        ],
        state_checks=[
            {"name": scenario_id, "command": APPROVED_STATE, "contains": [f"seen={scenario_id}"]}
        ],
        expected_observations=[f"seen={scenario_id}"],
        forbidden_observations=[f"missing={scenario_id}"],
        **kw,
    )


@pytest.fixture
def loop_bits(driver_config: DriverConfig):
    assert driver_config.runs_dir is not None
    store = EvidenceStore(driver_config.runs_dir, "20260809-000000")
    state = RunState(
        run_id=store.run_id,
        task="build supervised approval",
        max_iterations=driver_config.max_iterations,
    )
    return driver_config, store, state


# ==========================================================================
# B4 — the authoritative acceptance gate
# ==========================================================================


class TestAcceptanceContract:
    """Fifteen attempts to reach ACCEPT without the evidence to support it."""

    def test_1_a_failed_required_generated_scenario_blocks(self):
        result = suite_of(outcome("gen-dup", Outcome.FAILED))
        assert evaluate_gate(result).status is GateStatus.NOT_VERIFIED
        assert (
            _apply_suite_precedence(result, accept(), "backend_generic", lambda _m: None).decision
            is not Decision.ACCEPT
        )

    def test_2_a_failed_permanent_regression_scenario_blocks(self):
        result = suite_of(outcome("backend_generic", Outcome.FAILED, origin=Origin.PERMANENT))
        verdict = evaluate_gate(result)
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.permanent_unverified

    def test_3_a_skipped_required_scenario_blocks(self):
        """The defect that let a run accept having executed nothing at all."""
        result = suite_of(
            outcome("gen-dup", Outcome.SKIPPED, skip_reason="execution budget exhausted"),
            outcome("backend_generic", Outcome.SKIPPED, origin=Origin.PERMANENT,
                    skip_reason="browser support is disabled"),
        )
        verdict = evaluate_gate(result)
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.executed == 0
        assert len(verdict.unverified) == 2
        decision = _apply_suite_precedence(result, accept(), "backend_generic", lambda _m: None)
        assert decision.decision is Decision.BLOCKED

    def test_4_a_scenario_that_errored_blocks(self):
        result = suite_of(outcome("gen-dup", Outcome.BLOCKED, error="readiness never achieved"))
        assert evaluate_gate(result).status is GateStatus.NOT_VERIFIED

    def test_5_a_required_scenario_that_never_executed_blocks(self):
        """No outcome at all — the case that previously vanished from the counts."""
        result = SuiteResult(
            outcomes=[outcome("gen-a", Outcome.PASSED)],
            full_run=True,
            expected_required_ids=["gen-a", "gen-never-ran"],
        )
        verdict = evaluate_gate(result)
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert [c.scenario_id for c in verdict.unverified] == ["gen-never-ran"]
        assert "no result was recorded" in verdict.unverified[0].reason

    def test_6_a_pass_whose_evidence_is_missing_blocks(self):
        result = suite_of(
            outcome("gen-a", Outcome.PASSED, evidence_verified=False,
                    evidence_problem="the cited evidence directory does not exist")
        )
        verdict = evaluate_gate(result)
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert "does not exist" in verdict.unverified[0].reason

    def test_7_fabricated_evidence_does_not_satisfy_the_gate(self, tmp_path):
        """Evidence belonging to another scenario is worse than none: it reads as proof."""
        directory = tmp_path / "scenarios" / "gen-a"
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(
            json.dumps({"scenario_id": "some-other-scenario", "run_id": "r1", "iteration": 1})
        )
        problem = verify_case_evidence(
            str(directory), scenario_id="gen-a", run_id="r1", iteration=1
        )
        assert "belongs to scenario" in problem

    @pytest.mark.asyncio
    async def test_8_a_builder_completion_claim_cannot_override_a_failed_scenario(self, loop_bits):
        config, store, state = loop_bits
        planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])

        result = await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=FakeBuilder(),  # claims "done."
            evaluator=FakeEvaluator([accept(), accept(), accept()]),
            make_executor=lambda d: RecordingExecutor(d, {"gen-dup": False}, []),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            planner=planner,
        )

        assert result.status is not RunStatus.ACCEPTED

    def test_9_a_generation_runtime_error_is_not_a_clean_bill_of_health(self):
        """A dead generator produced no coverage AND no information."""
        result = suite_of(outcome("backend_generic", Outcome.PASSED, origin=Origin.PERMANENT))
        assert evaluate_gate(result).status is GateStatus.VERIFIED

        verdict = evaluate_gate(
            result, generation_problems=["generation wave 1 failed: RuntimeError: boom"]
        )
        assert verdict.status is GateStatus.NOT_VERIFIED
        decision = _apply_suite_precedence(
            result, accept(), "backend_generic", lambda _m: None,
            generation_problems=["generation wave 1 failed: RuntimeError: boom"],
        )
        assert decision.decision is Decision.BLOCKED

    def test_10_malformed_generated_output_produces_no_executable_scenario(self):
        parsed, malformed = parse_scenarios(
            {"scenarios": [{"id": "x", "risk_category": "not-a-category"}]},
            provenance=ScenarioProvenance(),
        )
        assert parsed == []
        assert malformed

    def test_11_an_unsafe_generated_operation_is_refused(self):
        scenario = make_scenario(
            state_checks=[{"command": "./probe.sh payments\nrm -rf /", "contains": ["x"]}]
        )
        assert validate_scenario(scenario, validation_context())

    def test_12_an_external_url_attempt_is_refused(self):
        scenario = make_scenario(
            actions=[{"kind": "request",
                      "request": {"method": "POST", "path": "https://api.stripe.com/v1/charges"}}]
        )
        reasons = validate_scenario(scenario, validation_context())
        assert any("external" in r.lower() or "scheme" in r.lower() for r in reasons)

    def test_13_a_scenario_without_provenance_is_refused(self):
        scenario = make_scenario(provenance=ScenarioProvenance())
        reasons = validate_scenario(scenario, validation_context())
        assert any("provenance" in r.lower() for r in reasons)

    @pytest.mark.asyncio
    async def test_14_completion_auditor_precedence_is_unchanged(self, loop_bits):
        """The audit still outranks the product evaluator, and still runs first."""
        from neyma_product_driver.completion_auditor import AuditDecision

        config, store, state = loop_bits

        class ContradictingAuditor:
            def audit(self, report, unit=None, run_commands=None, evidence_dir=""):
                from neyma_product_driver.completion_auditor import CompletionAudit

                return CompletionAudit(
                    decision=AuditDecision.CONTRADICTED,
                    headline="the registry does not support this claim",
                    correction_prompt="Correct the status surfaces.",
                )

        result = await run_control_loop(
            config=config,
            scenario=Scenario(name="backend_generic"),
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept(), accept(), accept()]),
            make_executor=lambda d: RecordingExecutor(d, {}, []),
            emit=lambda _m: None,
            repo_loader=FakeRepoLoader(),
            auditor=ContradictingAuditor(),
        )
        assert result.status is not RunStatus.ACCEPTED

    def test_15_measurement_still_precedes_every_layer_that_judges_claims(self):
        """The gate reads before the audit, and protocol no longer outranks either.

        Two orderings live here. The one that must not move: what the suite
        *measured* is folded in before any layer that judges a *claim*, because
        an ordering that ran the audit first let the audit reach a terminal state
        and return before the gate had ever been consulted — a required scenario
        could fail, the run could stop, and no report anywhere said so.

        The one that deliberately changed: the protocol step used to be a
        precedence layer above both, so a stale receipt or a commit-shape
        difference ended a run that had just demonstrated working behaviour. It
        is now a policy question with one answer — does clearing this need
        founder authority — and everything else it finds is recorded.
        """
        import inspect

        from neyma_product_driver import cli

        source = inspect.getsource(cli.run_control_loop)
        assert "_apply_protocol_precedence" not in source, (
            "the protocol precedence table is back"
        )
        assert source.index("_apply_protocol_policy") < source.index("_apply_suite_precedence")
        assert source.index("_apply_suite_precedence") < source.index("blocks_acceptance")

    def test_15b_the_only_protocol_terminal_is_founder_authority(self):
        """The policy has exactly one way to stop a run, and it is the safe one."""
        import inspect

        from neyma_product_driver import cli

        source = inspect.getsource(cli._apply_protocol_policy)
        terminals = [
            line for line in source.splitlines() if "RunStatus." in line and "return" in line
        ]
        assert len(terminals) == 1, terminals
        assert "REQUIRES_APPROVAL" in terminals[0]


class TestConvenienceFlagsCannotOverrideTheGate:
    def test_everything_required_passed_is_false_when_a_required_scenario_failed(self):
        """Asserted in the negative direction, which is what a forced True breaks."""
        result = suite_of(outcome("gen-dup", Outcome.FAILED))
        assert result.everything_required_passed is False

    def test_everything_required_passed_is_false_when_nothing_executed(self):
        result = suite_of(outcome("gen-dup", Outcome.SKIPPED, skip_reason="budget"))
        assert result.everything_required_passed is False

    def test_the_gate_does_not_consult_the_convenience_flag(self):
        """Forcing the flag True must not change the gate's answer."""
        result = suite_of(outcome("gen-dup", Outcome.FAILED))

        class Forced(SuiteResult):
            @property
            def everything_required_passed(self) -> bool:
                return True

        forced = Forced(**result.model_dump())
        assert forced.everything_required_passed is True
        assert evaluate_gate(forced).status is GateStatus.NOT_VERIFIED


# ==========================================================================
# B1 — the real reasoner path
# ==========================================================================


class TestRealReasonerPath:
    def test_the_real_reasoner_runs_inside_an_already_running_event_loop(self):
        """The production call site is inside ``async def run_control_loop``."""
        calls: list[str] = []

        class Stub(LLMScenarioReasoner):
            async def _session(self, prompt):
                calls.append(prompt)
                return {"risks": [], "scenarios": []}

        reasoner = Stub(Path("."))
        brief = GenerationBrief(
            stage="initial", wave=1, basis=GenerationBasis(task="t"),
            max_scenarios=3, available_commands=[], available_services=[],
            app_url="", browser_enabled=False,
        )

        async def as_the_driver_does():
            return reasoner.propose(brief)

        assert asyncio.run(as_the_driver_does()) == {"risks": [], "scenarios": []}
        assert len(calls) == 1

    def test_a_generated_wave_really_reaches_the_plan_through_the_async_path(self, tmp_path):
        class Stub(LLMScenarioReasoner):
            async def _session(self, prompt):
                return raw_payload(raw_scenario("gen-a"))

        planner = ScenarioPlanner(
            repo=tmp_path, config=ScenarioGenerationConfig(enabled=True),
            reasoner=Stub(tmp_path), base_scenario=base_scenario(),
            permanent_scenarios=[base_scenario()], founder=FakeFounder(),
        )

        async def drive():
            return planner.plan_initial(task="approval", unit=FakeUnit())

        plan = asyncio.run(drive())
        assert [s.id for s in plan.scenarios] == ["gen-a"]
        assert plan.waves[0].reasoner_error == ""

    def test_a_reasoner_failure_is_recorded_not_silently_an_empty_wave(self, tmp_path):
        class Dying(LLMScenarioReasoner):
            async def _session(self, prompt):
                raise RuntimeError("the model session died")

        planner = ScenarioPlanner(
            repo=tmp_path, config=ScenarioGenerationConfig(enabled=True),
            reasoner=Dying(tmp_path), base_scenario=base_scenario(),
            permanent_scenarios=[base_scenario()], founder=FakeFounder(),
        )
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert plan.scenarios == []
        assert "the model session died" in plan.waves[0].reasoner_error
        # And that failure must reach the acceptance decision.
        assert planner.generation_problems()

    def test_an_honestly_empty_wave_is_not_a_generation_problem(self, tmp_path):
        """"Nothing to add" and "I broke" are different facts."""
        planner = ScenarioPlanner(
            repo=tmp_path, config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner([{"risks": [], "scenarios": []}]),
            base_scenario=base_scenario(), permanent_scenarios=[base_scenario()],
            founder=FakeFounder(),
        )
        planner.plan_initial(task="t", unit=FakeUnit())
        assert planner.generation_problems() == []


# ==========================================================================
# B2 — generated execution safety
# ==========================================================================


class TestCommandBoundary:
    @pytest.mark.parametrize(
        "vector",
        [
            "echo payments\necho INJECTED",
            "echo payments\recho INJECTED",
            "echo payments\x0becho INJECTED",
            "echo payments\x0cecho INJECTED",
            "echo payments\x00echo INJECTED",
            "echo payments && echo INJECTED",
            "echo payments; echo INJECTED",
            "echo payments | tee /tmp/x",
            "echo payments > /tmp/x",
            "echo payments >> /tmp/x",
            "echo payments $(echo INJECTED)",
            "echo payments `echo INJECTED`",
            "echo payments \"$(echo INJECTED)\"",
            "echo payments (echo INJECTED)",
        ],
    )
    def test_composition_outside_quotes_is_refused(self, vector):
        approved = ApprovedCommands(["echo payments"])
        ok, why = approved.approves(vector)
        assert not ok, f"{vector!r} was admitted"
        assert why

    @pytest.mark.parametrize(
        "probe",
        [
            'sqlite3 db "SELECT key FROM grants GROUP BY key HAVING count(*) > 1"',
            "sqlite3 db \"SELECT 'DUP:'||key FROM grants\"",
            'sqlite3 db "SELECT * FROM runs WHERE created < updated"',
            'sqlite3 db \'SELECT $(not_a_substitution) FROM t\'',
            'python3 -c "import json; print(json.dumps({}))"',
        ],
    )
    def test_quoted_payloads_are_accepted(self, probe):
        """The effect-family rule requires a persisted-state oracle; SQL is how
        one is written, and quoting is what makes it safe."""
        approved = ApprovedCommands(["sqlite3 db", "python3 -c"])
        ok, why = approved.approves(probe)
        assert ok, f"{probe!r} was wrongly refused: {why}"

    def test_unbalanced_quoting_is_refused(self):
        approved = ApprovedCommands(["sqlite3 db"])
        ok, why = approved.approves('sqlite3 db "SELECT 1')
        assert not ok
        assert "quoting" in why

    def test_the_existing_command_guard_still_outranks_the_approved_set(self):
        """A human may not approve their way past a hard-blocked action."""
        approved = ApprovedCommands(["git push origin main"])
        ok, why = approved.approves("git push origin main")
        assert not ok
        assert "hard-blocked" in why


class TestHttpBoundary:
    @pytest.mark.parametrize(
        "target",
        [
            "https://api.stripe.com/v1/charges",
            "http://evil.example/exfil",
            "//evil.example/exfil",
            "http://127.0.0.1@evil.example/",
            "http://2130706433/",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "http://[2001:db8::1]/",
            "\\\\evil.example\\share",
        ],
    )
    def test_a_generated_path_cannot_leave_the_approved_base(self, target):
        _resolved, problem = resolve_http_target(
            app_url="http://127.0.0.1:8931", url="", path=target,
            local_hosts=frozenset({"127.0.0.1", "localhost", "::1"}),
        )
        assert problem, f"{target!r} was allowed"

    def test_a_relative_path_still_resolves_against_the_base(self):
        resolved, problem = resolve_http_target(
            app_url="http://127.0.0.1:8931", url="", path="/approve",
            local_hosts=frozenset({"127.0.0.1"}),
        )
        assert problem == ""
        assert resolved == "http://127.0.0.1:8931/approve"

    def test_naming_both_a_url_and_a_path_is_refused(self):
        _resolved, problem = resolve_http_target(
            app_url="http://127.0.0.1:8931", url="http://127.0.0.1:8931/a", path="/b",
            local_hosts=frozenset({"127.0.0.1"}),
        )
        assert "only one may decide the target" in problem


# ==========================================================================
# B3 — adaptive generation actually uses the failure evidence
# ==========================================================================


class TestAdaptiveUsesFailureEvidence:
    def _planner(self, tmp_path, payloads):
        return ScenarioPlanner(
            repo=tmp_path, config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner(payloads), base_scenario=base_scenario(),
            permanent_scenarios=[base_scenario()], founder=FakeFounder(),
        )

    def test_the_observed_value_reaches_the_generator(self, tmp_path):
        """Not "an expectation failed" — the value that was actually wrong."""
        planner = self._planner(
            tmp_path,
            [raw_payload(raw_scenario("gen-dup")),
             raw_payload(distinct_raw("gen-conc", risk_category="concurrency",
                                      source_failures=["gen-dup"]))],
        )
        planner.plan_initial(task="t", unit=FakeUnit())
        planner.expand_after_failures(
            task="t", unit=FakeUnit(),
            failures=[
                FailureEvidence(
                    scenario_id="gen-dup", risk_category="idempotency",
                    expected=["payments=1"], forbidden=["payments=2"],
                    failed_assertions=["expect_state: exactly one payment — not found"],
                    observed="payments=2\ninvoices=INV-1,INV-1",
                    evidence_path="/runs/x/iteration-01/scenarios/gen-dup",
                    cluster_id="C01",
                )
            ],
        )
        brief = planner.reasoner.briefs[1].render()
        assert "payments=2" in brief          # what actually happened
        assert "payments=1" in brief          # what was expected
        assert "gen-dup" in brief             # which case
        assert "idempotency" in brief         # which risk
        assert "/runs/x/iteration-01" in brief  # where the evidence is

    def test_an_adaptive_scenario_must_name_the_failure_that_caused_it(self, tmp_path):
        planner = self._planner(
            tmp_path,
            [raw_payload(raw_scenario("gen-dup")),
             raw_payload(distinct_raw("gen-orphan", risk_category="concurrency"))],
        )
        planner.plan_initial(task="t", unit=FakeUnit())
        planner.expand_after_failures(
            task="t", unit=FakeUnit(),
            failures=[FailureEvidence(scenario_id="gen-dup", observed="payments=2")],
        )

        assert "gen-orphan" not in [s.id for s in planner.plan.scenarios]
        refusals = [r for w in planner.plan.waves for r in w.rejected]
        assert any("source failure" in reason for r in refusals for reason in r.reasons)

    def test_an_adaptive_scenario_cannot_invent_a_failure_that_never_happened(self, tmp_path):
        planner = self._planner(
            tmp_path,
            [raw_payload(raw_scenario("gen-dup")),
             raw_payload(distinct_raw("gen-liar", risk_category="concurrency",
                                      source_failures=["gen-never-failed"]))],
        )
        planner.plan_initial(task="t", unit=FakeUnit())
        planner.expand_after_failures(
            task="t", unit=FakeUnit(),
            failures=[FailureEvidence(scenario_id="gen-dup", observed="payments=2")],
        )

        assert "gen-liar" not in [s.id for s in planner.plan.scenarios]
        refusals = [r for w in planner.plan.waves for r in w.rejected]
        assert any("never observed" in reason for r in refusals for reason in r.reasons)

    def test_the_link_survives_into_the_persisted_plan(self, tmp_path):
        planner = self._planner(
            tmp_path,
            [raw_payload(raw_scenario("gen-dup")),
             raw_payload(distinct_raw("gen-conc", risk_category="concurrency",
                                      source_failures=["gen-dup"]))],
        )
        planner.plan_initial(task="t", unit=FakeUnit())
        planner.expand_after_failures(
            task="t", unit=FakeUnit(),
            failures=[FailureEvidence(scenario_id="gen-dup", observed="payments=2")],
        )
        adaptive = planner.plan.by_id("gen-conc")
        assert adaptive is not None
        assert adaptive.provenance.source_failures == ["gen-dup"]
        assert adaptive.provenance.stage == "adaptive"


# ==========================================================================
# B5 — provenance is derived, required, and meaningful
# ==========================================================================


class TestProvenanceEnforced:
    def test_provenance_is_derived_from_the_run_not_invented(self):
        basis = GenerationBasis(
            task="add supervised approval", task_hash="abc123",
            repository_head="deadbeef", active_unit_id="U-042",
        )
        stamp = provenance_for(basis, stage="initial", wave=1, model="opus", session_id="s1")
        assert stamp.task_hash == "abc123"
        assert stamp.repository_head == "deadbeef"
        assert stamp.active_unit_id == "U-042"
        assert stamp.stage == "initial" and stamp.wave == 1
        assert stamp.model == "opus"

    def test_a_planner_generated_scenario_carries_real_provenance(self, tmp_path):
        """Fails if ``provenance_for`` stops deriving anything."""
        planner = ScenarioPlanner(
            repo=tmp_path, config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner([raw_payload(raw_scenario("gen-a"))]),
            base_scenario=base_scenario(), permanent_scenarios=[base_scenario()],
            founder=FakeFounder(),
        )
        plan = planner.plan_initial(task="add supervised approval", unit=FakeUnit())

        assert [s.id for s in plan.scenarios] == ["gen-a"]
        stamp = plan.scenarios[0].provenance
        assert stamp.task_hash
        assert stamp.stage == "initial"
        assert stamp.wave == 1
        assert stamp.model or stamp.session_id

    @pytest.mark.parametrize(
        "missing", ["task_hash", "stage", "model_and_session", "wave", "risk"]
    )
    def test_each_required_provenance_field_is_enforced(self, missing):
        scenario = make_scenario()
        update: dict = {}
        if missing == "model_and_session":
            update = {"model": "", "session_id": ""}
        elif missing == "wave":
            update = {"wave": 0}
        elif missing == "risk":
            update = {"generating_risk": ""}
            scenario.rationale = ""
        else:
            update = {missing: ""}
        scenario.provenance = scenario.provenance.model_copy(update=update)

        assert validate_scenario(scenario, validation_context())

    def test_an_unknown_generation_stage_is_refused(self):
        scenario = make_scenario()
        scenario.provenance = scenario.provenance.model_copy(update={"stage": "vibes"})
        reasons = validate_scenario(scenario, validation_context())
        assert any("stage" in r for r in reasons)


# ==========================================================================
# B6 — resume continues rather than restarting
# ==========================================================================


class TestResumePreservesAdaptiveState:
    def _planner(self, tmp_path, store, payloads):
        return ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True, max_waves=2),
            reasoner=ScriptedReasoner(payloads), store=store,
            base_scenario=base_scenario(), permanent_scenarios=[base_scenario()],
            founder=FakeFounder(),
        )

    def test_a_resumed_planner_continues_from_the_persisted_plan(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "run-1")
        first = self._planner(
            tmp_path, store,
            [raw_payload(raw_scenario("gen-a")),
             raw_payload(distinct_raw("gen-b", risk_category="concurrency",
                                      source_failures=["gen-a"]))],
        )
        first.plan_initial(task="t", unit=FakeUnit(), run_id="run-1")
        first.expand_after_failures(
            task="t", unit=FakeUnit(),
            failures=[FailureEvidence(scenario_id="gen-a", observed="payments=2")],
        )
        before = [s.id for s in first.plan.scenarios]
        assert before == ["gen-a", "gen-b"], "the probe needs both waves to have produced"

        # A fresh process: same run directory, nothing carried in memory.
        resumed = self._planner(tmp_path, store, [])
        note = resumed.restore_from_store()

        assert note
        assert [s.id for s in resumed.plan.scenarios] == before
        assert resumed.waves_used == first.waves_used
        assert resumed.compiled.keys() == first.compiled.keys()
        # The wave budget is not refunded by restarting the process.
        assert resumed.budget_exhausted() is first.budget_exhausted()
        # And the failure the adaptive case cited is still known.
        assert "gen-a" in resumed._observed_failure_ids

    def test_resume_does_not_destroy_the_earlier_plan(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "run-1")
        first = self._planner(tmp_path, store, [raw_payload(raw_scenario("gen-a"))])
        first.plan_initial(task="t", unit=FakeUnit(), run_id="run-1")

        resumed = self._planner(tmp_path, store, [])
        resumed.restore_from_store()
        resumed.persist()

        stored = json.loads((store.run_dir / "scenario-plan.json").read_text())
        assert [s["id"] for s in stored["scenarios"]] == ["gen-a"]

    def test_a_refused_wave_gets_its_own_evidence_file(self, tmp_path):
        """Refused waves keep the previous wave number; files must not collide."""
        store = EvidenceStore(tmp_path / "runs", "run-1")
        planner = ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True, max_waves=1),
            reasoner=ScriptedReasoner([raw_payload(raw_scenario("gen-a"))] * 4),
            store=store, base_scenario=base_scenario(),
            permanent_scenarios=[base_scenario()], founder=FakeFounder(),
        )
        planner.plan_initial(task="t", unit=FakeUnit(), run_id="run-1")
        for _ in range(3):
            planner.expand_after_failures(
                task="t", unit=FakeUnit(),
                failures=[FailureEvidence(scenario_id="gen-a", observed="x")],
            )

        files = sorted(p.name for p in (store.run_dir / "scenario-generation").glob("*.json"))
        assert len(files) == len(planner.plan.waves)


# ==========================================================================
# B7 — per-case evidence integrity
# ==========================================================================


class TestEvidenceIntegrity:
    def _run_one(self, tmp_path, command="echo hello"):
        scenario = Scenario(
            name="probe", mode="backend", commands=[{"name": "c", "run": command}]
        )
        suite = build_suite(permanent=[("probe", scenario)])
        executor = SuiteExecutor(
            make_executor=lambda d: ScenarioExecutor(tmp_path, ScenarioRunConfig(), d),
            artifact_root=tmp_path / "iteration-01",
            run_id="run-1", iteration=1, emit=lambda _m: None,
        )
        return asyncio.run(executor.run(suite))

    def test_a_scenario_writes_the_evidence_it_cites(self, tmp_path):
        result = self._run_one(tmp_path)
        case = result.outcomes[0]

        assert case.evidence_verified, case.evidence_problem
        record = Path(case.evidence_path) / "result.json"
        assert record.exists() and record.stat().st_size > 0
        stored = json.loads(record.read_text())
        assert stored["scenario_id"] == "probe"
        assert stored["run_id"] == "run-1"
        assert stored["iteration"] == 1

    @pytest.mark.parametrize(
        "mutate,expected",
        [
            (lambda d: d.joinpath("result.json").unlink(), "holds no result.json"),
            (lambda d: d.joinpath("result.json").write_text(""), "empty"),
            (lambda d: d.joinpath("result.json").write_text("{not json"), "could not be read"),
            (lambda d: d.joinpath("result.json").write_text('{"scenario_id":"other"}'),
             "belongs to scenario"),
        ],
    )
    def test_damaged_evidence_is_detected(self, tmp_path, mutate, expected):
        result = self._run_one(tmp_path)
        directory = Path(result.outcomes[0].evidence_path)
        mutate(directory)

        problem = verify_case_evidence(
            str(directory), scenario_id="probe", run_id="run-1", iteration=1
        )
        assert expected in problem

    def test_evidence_from_another_run_is_rejected(self, tmp_path):
        result = self._run_one(tmp_path)
        directory = str(result.outcomes[0].evidence_path)

        assert "belongs to run" in verify_case_evidence(
            directory, scenario_id="probe", run_id="a-different-run", iteration=1
        )

    def test_a_nonexistent_evidence_path_is_rejected(self):
        assert "does not exist" in verify_case_evidence(
            "/no/such/place", scenario_id="probe", run_id="r", iteration=1
        )

    def test_a_pass_without_evidence_is_downgraded_rather_than_believed(self, tmp_path):
        """The executor confirms evidence; an unconfirmed pass must not stand."""
        scenario = Scenario(name="probe", mode="backend", commands=[{"name": "c", "run": "echo hi"}])
        suite = build_suite(permanent=[("probe", scenario)])
        executor = SuiteExecutor(
            make_executor=lambda d: ScenarioExecutor(tmp_path, ScenarioRunConfig(), d),
            artifact_root=tmp_path / "iteration-01",
            run_id="run-1", iteration=1, emit=lambda _m: None,
        )
        result = asyncio.run(executor.run(suite))
        # Remove the evidence after the fact and re-verify, which is what the
        # gate does with a stale or truncated run directory.
        Path(result.outcomes[0].evidence_path).joinpath("result.json").unlink()
        problem = verify_case_evidence(
            result.outcomes[0].evidence_path, scenario_id="probe", run_id="run-1", iteration=1
        )
        assert problem
        degraded = result.outcomes[0].model_copy(
            update={"evidence_verified": False, "evidence_problem": problem}
        )
        assert evaluate_gate(
            SuiteResult(outcomes=[degraded], expected_required_ids=["probe"])
        ).status is GateStatus.NOT_VERIFIED


# ==========================================================================
# B8 — bounds actually bind
# ==========================================================================


class TestBoundsBind:
    def test_a_wave_cannot_exceed_its_per_wave_limit(self, tmp_path):
        """The bound is enforced on what came back, not merely requested."""
        many = [
            raw_scenario(
                f"gen-{i:02d}",
                risk_category=["idempotency", "concurrency", "restart_recovery"][i % 3],
                actions=[{"kind": "request",
                          "request": {"method": "POST", "path": f"/a/{i}", "expect_status": 200}}],
                state_checks=[{"name": "s", "command": APPROVED_STATE,
                               "contains": [f"payments={i}"]}],
                expected_observations=[f"payments={i}"],
            )
            for i in range(9)
        ]
        planner = ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(
                enabled=True, max_initial_scenarios=3, max_total_scenarios=30,
                max_scenarios_per_risk_category=6,
            ),
            reasoner=ScriptedReasoner([raw_payload(*many)]),
            base_scenario=base_scenario(), permanent_scenarios=[base_scenario()],
            founder=FakeFounder(),
        )
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert len(plan.scenarios) == 3
        assert any("wave budget" in n for n in plan.waves[0].budget_notes)

    def test_the_generator_is_told_the_truth_about_the_browser(self, tmp_path):
        """Promising a browser that the suite will not drive earns skipped coverage."""
        planner = ScenarioPlanner(
            repo=tmp_path, config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner([raw_payload()]),
            base_scenario=base_scenario(), permanent_scenarios=[base_scenario()],
            founder=FakeFounder(), browser_enabled=False,
        )
        planner.plan_initial(task="t", unit=FakeUnit())
        assert "BROWSER available: no" in planner.reasoner.briefs[0].render()
