"""Contracts for the residuals closed after the dynamic-generation remediation.

Each class corresponds to one residual recorded in
``verification-evidence/REMEDIATION.md`` §11, and each test states the property
in the direction that would be lost if the fix were reverted. The hostile tests
at the end of each class are the ones that matter: they are written so that
deleting the behaviour makes them fail, not so that keeping it makes them pass.

No test here consumes Claude usage.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from neyma_product_driver import cli as driver_cli
from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.evidence import (
    FILENAME_LIMIT,
    sanitize_filename,
    shorten_preserving_identity,
)
from neyma_product_driver.models import (
    BrowserObservation,
    BrowserTextExpectation,
    IterationRecord,
    ScenarioResult,
)
from neyma_product_driver import scenario_gate
from neyma_product_driver.prompts import evaluator_prompt
from neyma_product_driver.run_journal import JOURNAL_FILE, SUMMARY_FILE
from neyma_product_driver.scenario_gate import (
    GateStatus,
    evaluate_gate,
    uncovered_required_risks,
)
from neyma_product_driver.scenario_plan import (
    SCENARIO_ID_LIMIT,
    GeneratedAction,
    GeneratedBrowserStep,
    GeneratedScenario,
    IdentifiedRisk,
    Priority,
    RiskCategory,
)
from neyma_product_driver.scenario_suite import (
    Origin,
    Outcome,
    ScenarioOutcome,
    ScenarioSuite,
    SuiteEntry,
    SuiteExecutor,
    SuiteResult,
    build_suite,
    select_rerun,
)
from neyma_product_driver.scenarios import BrowserStep, Scenario, ScenarioExecutor

from scenario_fixtures import make_scenario

# --------------------------------------------------------------------------
# Shared fixtures
# --------------------------------------------------------------------------

#: 63 characters, so two ids built on it agree on their first 64. Model-authored
#: ids are descriptive slugs; two neighbouring restart-recovery cases sharing a
#: long prefix is ordinary, not contrived.
LONG_PREFIX = "gen-approval-survives-restart-and-is-not-double-applied-after-a"
LONG_ID_A = LONG_PREFIX + "-crash-during-the-outbox-flush"
LONG_ID_B = LONG_PREFIX + "-crash-during-the-payment-call"


class PassingRunner:
    """A scenario executor that always passes, so only identity is under test."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        return ScenarioResult(
            scenario_name=scenario.name, phase="verify", readiness_ok=True, assertions=[]
        )


def _long_suite() -> ScenarioSuite:
    models = [make_scenario(LONG_ID_A), make_scenario(LONG_ID_B)]
    return build_suite(
        generated=[(m, Scenario(name=m.id, phase="verify")) for m in models]
    )


def _run_suite(suite: ScenarioSuite, root: Path) -> SuiteResult:
    executor = SuiteExecutor(
        make_executor=PassingRunner,
        artifact_root=root,
        run_id="contract",
        iteration=1,
    )
    return asyncio.run(executor.run(suite))


# --------------------------------------------------------------------------
# D — scenario id collision (r4 F-4)
# --------------------------------------------------------------------------


class TestScenarioIdsDoNotCollide:
    def test_two_long_ids_sharing_64_characters_stay_distinct(self) -> None:
        assert LONG_ID_A[:SCENARIO_ID_LIMIT] == LONG_ID_B[:SCENARIO_ID_LIMIT]
        a, b = make_scenario(LONG_ID_A), make_scenario(LONG_ID_B)
        assert a.id != b.id
        assert len(a.id) <= SCENARIO_ID_LIMIT
        assert len(b.id) <= SCENARIO_ID_LIMIT

    def test_the_shortened_id_keeps_a_readable_prefix(self) -> None:
        scenario = make_scenario(LONG_ID_A)
        assert scenario.id.startswith("gen-approval-survives-restart")

    def test_the_original_id_stays_recoverable(self) -> None:
        scenario = make_scenario(LONG_ID_A)
        assert scenario.proposed_id == LONG_ID_A

    def test_a_short_id_is_untouched_and_records_no_proposed_id(self) -> None:
        scenario = make_scenario("gen-approve-twice")
        assert scenario.id == "gen-approve-twice"
        assert scenario.proposed_id == ""

    def test_the_derivation_is_deterministic_across_instances(self) -> None:
        assert make_scenario(LONG_ID_A).id == make_scenario(LONG_ID_A).id

    def test_a_round_trip_through_json_does_not_shorten_twice(self) -> None:
        scenario = make_scenario(LONG_ID_A)
        restored = GeneratedScenario.model_validate(scenario.model_dump(mode="json"))
        assert restored.id == scenario.id
        assert restored.proposed_id == LONG_ID_A

    def test_distinct_ids_get_distinct_filesystem_components(self) -> None:
        a, b = make_scenario(LONG_ID_A), make_scenario(LONG_ID_B)
        assert sanitize_filename(a.id) != sanitize_filename(b.id)

    def test_sanitize_filename_never_merges_two_long_names(self) -> None:
        left = "x" * FILENAME_LIMIT + "-left"
        right = "x" * FILENAME_LIMIT + "-right"
        assert sanitize_filename(left) != sanitize_filename(right)
        assert len(sanitize_filename(left)) <= FILENAME_LIMIT

    def test_both_scenarios_reach_the_suite(self) -> None:
        suite = _long_suite()
        assert len(suite) == 2
        assert suite.assembly_conflicts == []

    def test_both_scenarios_execute_with_separately_attributable_evidence(
        self, tmp_path: Path
    ) -> None:
        result = _run_suite(_long_suite(), tmp_path)
        assert len(result.outcomes) == 2
        paths = {o.evidence_path for o in result.outcomes}
        assert len(paths) == 2
        for outcome in result.outcomes:
            assert outcome.evidence_verified, outcome.evidence_problem
            record = Path(outcome.evidence_path) / "result.json"
            assert record.exists()

    def test_aggregation_counts_both(self, tmp_path: Path) -> None:
        result = _run_suite(_long_suite(), tmp_path)
        assert result.total == 2
        assert result.passed == 2
        assert len(set(result.expected_required_ids)) == 2

    def test_the_gate_accounts_for_both(self, tmp_path: Path) -> None:
        verdict = evaluate_gate(_run_suite(_long_suite(), tmp_path))
        assert verdict.required_total == 2
        assert verdict.required_passed == 2

    def test_a_narrowed_rerun_preserves_both(self, tmp_path: Path) -> None:
        suite = _long_suite()
        result = _run_suite(suite, tmp_path)
        # Make one of them fail, so the rerun narrows rather than selecting all.
        result.outcomes[0].outcome = Outcome.FAILED
        result.outcomes[0].failed_assertions = ["expect_state: payments — payments=2"]
        selected, _reason = select_rerun(suite, result)
        assert len(set(selected)) == len(selected), "a rerun must not list one id twice"
        assert result.outcomes[0].scenario_id in selected

    def test_resume_preserves_both_through_the_persisted_plan(self) -> None:
        from neyma_product_driver.scenario_plan import GeneratedScenarioPlan

        plan = GeneratedScenarioPlan(
            run_id="r", scenarios=[make_scenario(LONG_ID_A), make_scenario(LONG_ID_B)]
        )
        restored = GeneratedScenarioPlan.model_validate(plan.model_dump(mode="json"))
        assert len({s.id for s in restored.scenarios}) == 2
        assert {s.proposed_id for s in restored.scenarios} == {LONG_ID_A, LONG_ID_B}

    def test_promotion_candidates_preserve_both(self, tmp_path: Path) -> None:
        """Two candidates that collapsed to one id would hide one from a human.

        `PromotionLedger.record` refuses a second candidate with an id it has
        already seen, so a truncation collision here does not merely mislabel
        the second scenario — it deletes the suggestion entirely.
        """
        from neyma_product_driver.scenario_plan import GeneratedScenarioPlan
        from neyma_product_driver.scenario_planner import (
            DefectMemory,
            PromotionLedger,
            record_promotion_candidates,
        )

        models = [make_scenario(LONG_ID_A), make_scenario(LONG_ID_B)]
        plan = GeneratedScenarioPlan(run_id="r", scenarios=models)
        result = _run_suite(
            build_suite(
                generated=[(m, Scenario(name=m.id, phase="verify")) for m in models]
            ),
            tmp_path,
        )
        memory = DefectMemory()
        for model in models:
            memory.note_failure(model.id, 1, "payments=2 where payments=1 was expected")

        recorded = record_promotion_candidates(
            ledger=PromotionLedger(tmp_path),
            memory=memory,
            plan=plan,
            outcomes=result.outcomes,
            iteration=2,
        )
        assert len(recorded) == 2
        assert {c.scenario_id for c in recorded} == {m.id for m in models}
        assert len({c.scenario_id for c in recorded}) == 2

    # -- hostile ---------------------------------------------------------

    def test_a_duplicate_id_is_recorded_rather_than_dropped(self) -> None:
        """The mutation this catches: `add` silently ignoring the second entry."""
        model = make_scenario("gen-same")
        suite = build_suite(
            generated=[
                (model, Scenario(name="a", phase="verify")),
                (model, Scenario(name="b", phase="verify")),
            ]
        )
        assert len(suite) == 1
        assert suite.assembly_conflicts, "a dropped scenario must leave a record"
        assert "gen-same" in suite.assembly_conflicts[0]

    def test_suite_add_reports_whether_it_admitted_the_entry(self) -> None:
        suite = ScenarioSuite()
        entry = SuiteEntry(
            scenario_id="gen-x",
            scenario=Scenario(name="x", phase="verify"),
            origin=Origin.GENERATED,
        )
        assert suite.add(entry) is True
        assert suite.add(entry) is False

    def test_a_dropped_scenario_blocks_acceptance(self, tmp_path: Path) -> None:
        model = make_scenario("gen-same")
        suite = build_suite(
            generated=[
                (model, Scenario(name="a", phase="verify")),
                (model, Scenario(name="b", phase="verify")),
            ]
        )
        result = _run_suite(suite, tmp_path)
        assert result.assembly_problems, "the conflict must reach the result"
        verdict = evaluate_gate(result)
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.blocks_acceptance

    def test_shortening_is_injective_over_a_dense_family(self) -> None:
        base = "gen-" + "a" * SCENARIO_ID_LIMIT
        produced = {
            shorten_preserving_identity(base + f"-{n}", SCENARIO_ID_LIMIT)
            for n in range(500)
        }
        assert len(produced) == 500


# --------------------------------------------------------------------------
# C — uncovered risk reaches the evaluator and the gate (r6 F-3)
# --------------------------------------------------------------------------


def _risk(
    risk_id: str,
    category: RiskCategory,
    severity: Priority = Priority.P0,
) -> IdentifiedRisk:
    return IdentifiedRisk(
        id=risk_id,
        description=f"something could go wrong with {category.value}",
        risk_category=category,
        severity=severity,
        basis="an acceptance criterion",
    )


def _outcome(
    scenario_id: str,
    category: RiskCategory,
    outcome: Outcome = Outcome.PASSED,
    *,
    evidence_verified: bool = True,
) -> ScenarioOutcome:
    return ScenarioOutcome(
        scenario_id=scenario_id,
        scenario_name=scenario_id,
        origin=Origin.GENERATED,
        outcome=outcome,
        priority=Priority.P0,
        risk_category=category.value,
        required=True,
        evidence_path=f"/runs/x/scenarios/{scenario_id}",
        evidence_verified=evidence_verified,
    )


def _result(*outcomes: ScenarioOutcome) -> SuiteResult:
    return SuiteResult(
        full_run=True,
        expected_required_ids=[o.scenario_id for o in outcomes],
        outcomes=list(outcomes),
    )


class TestUncoveredRisksReachAcceptance:
    def test_a_required_risk_with_no_scenario_is_a_gap(self) -> None:
        gaps = uncovered_required_risks(
            [_risk("R1", RiskCategory.IDEMPOTENCY), _risk("R2", RiskCategory.CROSS_TENANT)],
            _result(_outcome("gen-1", RiskCategory.IDEMPOTENCY)),
        )
        assert [g.risk_id for g in gaps] == ["R2"]
        assert "no scenario exercising this risk was executed" in gaps[0].reason

    def test_every_executed_scenario_passing_does_not_close_the_gap(self) -> None:
        """The exact false-ACCEPT this residual describes."""
        verdict = evaluate_gate(
            _result(_outcome("gen-1", RiskCategory.IDEMPOTENCY)),
            risks=[_risk("R2", RiskCategory.CROSS_TENANT)],
        )
        assert verdict.required_passed == verdict.required_total
        assert verdict.status is GateStatus.NOT_VERIFIED
        assert verdict.blocks_acceptance

    def test_a_covered_and_passing_risk_is_not_a_gap(self) -> None:
        verdict = evaluate_gate(
            _result(_outcome("gen-1", RiskCategory.IDEMPOTENCY)),
            risks=[_risk("R1", RiskCategory.IDEMPOTENCY)],
        )
        assert verdict.uncovered_risks == []
        assert verdict.status is GateStatus.VERIFIED

    @pytest.mark.parametrize(
        "outcome",
        [Outcome.FAILED, Outcome.BLOCKED, Outcome.SKIPPED],
        ids=["failed", "blocked", "skipped"],
    )
    def test_a_risk_whose_only_scenario_did_not_pass_is_a_gap(
        self, outcome: Outcome
    ) -> None:
        gaps = uncovered_required_risks(
            [_risk("R1", RiskCategory.IDEMPOTENCY)],
            _result(_outcome("gen-1", RiskCategory.IDEMPOTENCY, outcome)),
        )
        assert len(gaps) == 1
        assert "none established a pass" in gaps[0].reason

    def test_a_pass_without_resolvable_evidence_does_not_close_a_gap(self) -> None:
        gaps = uncovered_required_risks(
            [_risk("R1", RiskCategory.IDEMPOTENCY)],
            _result(
                _outcome("gen-1", RiskCategory.IDEMPOTENCY, evidence_verified=False)
            ),
        )
        assert len(gaps) == 1

    def test_a_non_blocking_risk_is_not_a_gap(self) -> None:
        for severity in (Priority.P2, Priority.P3):
            gaps = uncovered_required_risks(
                [_risk("R9", RiskCategory.STALE_STATE, severity)], _result()
            )
            assert gaps == [], severity

    def test_detection_is_deterministic_and_consults_no_model(self) -> None:
        risks = [_risk("R2", RiskCategory.CROSS_TENANT)]
        result = _result(_outcome("gen-1", RiskCategory.IDEMPOTENCY))
        first = [g.brief() for g in uncovered_required_risks(risks, result)]
        for _ in range(5):
            assert [g.brief() for g in uncovered_required_risks(risks, result)] == first
        # The whole resolution path, not just the entry point: a wrapper that
        # consults nothing is worth nothing if what it delegates to does.
        source = "".join(
            inspect.getsource(fn)
            for fn in (
                uncovered_required_risks,
                scenario_gate.risk_coverage,
                scenario_gate._satisfying_outcome,
                scenario_gate._gap_reason,
            )
        )
        for forbidden in ("Claude", "reasoner", "propose", "prompt", "evaluator"):
            assert forbidden not in source, forbidden

    def test_no_risk_register_leaves_behaviour_unchanged(self) -> None:
        """Generation is opt-in; a run without a planner must gate as before."""
        result = _result(_outcome("gen-1", RiskCategory.IDEMPOTENCY))
        assert evaluate_gate(result).status is GateStatus.VERIFIED
        assert evaluate_gate(result, risks=[]).status is GateStatus.VERIFIED

    def test_the_evaluator_is_shown_the_gap(self) -> None:
        prompt = evaluator_prompt(
            task="t",
            iteration=1,
            max_iterations=3,
            builder_summary="done",
            git=None,
            scenario=None,
            service_logs=None,
            evidence_dir="/runs/x",
            suite=_result(_outcome("gen-1", RiskCategory.IDEMPOTENCY)),
            coverage_gaps=["[P0] cross_tenant — nobody checked this"],
        )
        assert "KNOWN COVERAGE GAPS" in prompt
        assert "cross_tenant" in prompt
        assert "was the coverage sufficient" in prompt

    def test_the_control_loop_supplies_the_gaps_it_computed(self) -> None:
        """A parameter nothing fills is not evidence reaching an evaluator.

        Asserted against the whole expression, not the keyword: `coverage_gaps=`
        alone is equally satisfied by `coverage_gaps=[]`, which is the mutation
        this test exists to catch.
        """
        source = inspect.getsource(driver_cli.run_control_loop)
        assert "coverage_gaps=_coverage_gap_briefs(planner, suite_result)" in source

    def test_the_gap_briefs_helper_reads_the_planner_risk_register(self) -> None:
        class Plan:
            risks = [_risk("R2", RiskCategory.CROSS_TENANT)]

        class Planner:
            plan = Plan()

        briefs = driver_cli._coverage_gap_briefs(
            Planner(), _result(_outcome("gen-1", RiskCategory.IDEMPOTENCY))
        )
        assert briefs and "cross_tenant" in briefs[0]

    # -- hostile ---------------------------------------------------------

    def test_the_gate_is_not_persuadable_by_a_convenience_flag(self) -> None:
        result = _result(_outcome("gen-1", RiskCategory.IDEMPOTENCY))
        verdict = evaluate_gate(result, risks=[_risk("R2", RiskCategory.CROSS_TENANT)])
        assert verdict.blocks_acceptance
        # `everything_required_passed` is a display convenience that consults the
        # gate without the register; it must not be what acceptance reads.
        assert result.everything_required_passed is True
        assert verdict.blocks_acceptance

    def test_dropping_the_risk_register_at_the_call_site_is_caught(self) -> None:
        """Mutation: `_apply_suite_precedence` stops passing `risks=`."""
        source = inspect.getsource(driver_cli._apply_suite_precedence)
        assert "risks=risks" in source
        assert "risks=_identified_risks(planner)" in inspect.getsource(
            driver_cli.run_control_loop
        )

    def test_an_accept_is_overridden_when_only_a_coverage_gap_remains(self) -> None:
        from neyma_product_driver.models import Decision, EvaluatorDecision

        decision = driver_cli._apply_suite_precedence(
            _result(_outcome("gen-1", RiskCategory.IDEMPOTENCY)),
            EvaluatorDecision(decision=Decision.ACCEPT, summary="looks good"),
            "backend_generic",
            lambda _m: None,
            risks=[_risk("R2", RiskCategory.CROSS_TENANT)],
        )
        assert decision.decision is Decision.BLOCKED
        assert any("cross_tenant" in p for p in decision.problems)

    def test_an_accept_survives_when_no_gap_exists(self) -> None:
        from neyma_product_driver.models import Decision, EvaluatorDecision

        decision = driver_cli._apply_suite_precedence(
            _result(_outcome("gen-1", RiskCategory.IDEMPOTENCY)),
            EvaluatorDecision(decision=Decision.ACCEPT, summary="looks good"),
            "backend_generic",
            lambda _m: None,
            risks=[_risk("R1", RiskCategory.IDEMPOTENCY)],
        )
        assert decision.decision is Decision.ACCEPT


# --------------------------------------------------------------------------
# E — max_parallel honesty (r4 F-5)
# --------------------------------------------------------------------------


class TestParallelismIsNotClaimedFalsely:
    def test_the_executor_states_its_only_concurrency(self) -> None:
        assert SuiteExecutor.MAX_PARALLEL == 1

    @pytest.mark.parametrize("value", [0, 2, 8, 64])
    def test_the_executor_refuses_any_other_value(self, value: int) -> None:
        with pytest.raises(ValueError, match="max_parallel must be 1"):
            SuiteExecutor(
                make_executor=PassingRunner, artifact_root=Path("."), max_parallel=value
            )

    def test_the_config_refuses_it_one_layer_earlier(self) -> None:
        with pytest.raises(ValueError, match="max_parallel must be 1"):
            ScenarioGenerationConfig(max_parallel=4)

    def test_execution_is_in_fact_sequential(self, tmp_path: Path) -> None:
        live = {"now": 0, "peak": 0}

        class Counting:
            def __init__(self, directory: Path) -> None:
                self.service_logs: dict[str, str] = {}

            async def execute(self, scenario: Scenario) -> ScenarioResult:
                live["now"] += 1
                live["peak"] = max(live["peak"], live["now"])
                await asyncio.sleep(0.01)
                live["now"] -= 1
                return ScenarioResult(
                    scenario_name=scenario.name,
                    phase="verify",
                    readiness_ok=True,
                    assertions=[],
                )

        suite = ScenarioSuite()
        for index in range(4):
            suite.add(
                SuiteEntry(
                    scenario_id=f"s{index}",
                    scenario=Scenario(name=f"s{index}", phase="verify"),
                    origin=Origin.GENERATED,
                    # Distinct keys: even a runner that honoured the isolation
                    # partition would be free to overlap these.
                    isolation_key=f"key-{index}",
                )
            )
        executor = SuiteExecutor(
            make_executor=Counting, artifact_root=tmp_path, run_id="seq", iteration=1
        )
        asyncio.run(executor.run(suite))
        assert live["peak"] == 1

    # -- hostile ---------------------------------------------------------

    def test_the_refusal_cannot_be_softened_into_a_silent_coercion(self) -> None:
        """Mutation: going back to `max(1, int(max_parallel))`."""
        source = inspect.getsource(SuiteExecutor.__init__)
        assert "max(1, int(max_parallel))" not in source
        assert "raise ValueError" in source


# --------------------------------------------------------------------------
# F — the run journal (r1 F7)
# --------------------------------------------------------------------------


class TestRunJournalIsWritten:
    @staticmethod
    def _write(tmp_path: Path, record: IterationRecord) -> tuple[Path, list[str]]:
        from neyma_product_driver.evidence import EvidenceStore
        from neyma_product_driver.models import RunState, RunStatus

        store = EvidenceStore(tmp_path / "runs", run_id="journal")
        state = RunState(run_id="journal", task="t", status=RunStatus.RUNNING)
        state.iterations.append(record)

        class Config:
            neyma_repo = tmp_path

        warnings: list[str] = []
        original = driver_cli.warn
        driver_cli.warn = lambda m: warnings.append(str(m))  # type: ignore[assignment]
        try:
            driver_cli._write_run_journal(store, state, Config())  # type: ignore[arg-type]
        finally:
            driver_cli.warn = original  # type: ignore[assignment]
        return store.run_dir, warnings

    def test_a_run_with_one_iteration_writes_a_journal(self, tmp_path: Path) -> None:
        run_dir, warnings = self._write(tmp_path, IterationRecord(iteration=1))
        assert warnings == []
        assert (run_dir / JOURNAL_FILE).exists()
        assert (run_dir / SUMMARY_FILE).exists()

    def test_scenario_commands_reach_the_journal_with_honest_provenance(
        self, tmp_path: Path
    ) -> None:
        import json

        from neyma_product_driver.models import CommandResult

        record = IterationRecord(iteration=1)
        record.scenario = ScenarioResult(
            scenario_name="backend_generic",
            phase="verify",
            setup=[CommandResult(command="./probe.sh seed", exit_code=0)],
            commands=[CommandResult(command="./probe.sh payments", exit_code=0)],
            teardown=[CommandResult(command="./probe.sh reset", exit_code=0)],
        )
        run_dir, warnings = self._write(tmp_path, record)
        assert warnings == []
        journal = json.loads((run_dir / JOURNAL_FILE).read_text())
        sources = {c["source"] for c in journal["commands"]}
        assert sources == {"scenario:setup", "scenario:commands", "scenario:teardown"}
        assert len(journal["commands"]) == 3
        # Never relabelled as the builder's: the builder did not run these.
        assert "builder" not in sources

    def test_an_iteration_with_no_scenario_contributes_no_commands(self) -> None:
        assert driver_cli._journalled_commands(IterationRecord(iteration=1)) == []

    # -- hostile ---------------------------------------------------------

    def test_the_journal_no_longer_reads_a_field_that_does_not_exist(self) -> None:
        assert "commands" not in IterationRecord.model_fields
        assert "record.commands" not in inspect.getsource(driver_cli._write_run_journal)


# --------------------------------------------------------------------------
# G — a generated browser scenario's expect_text is a real oracle
# --------------------------------------------------------------------------


class TestBrowserTextExpectationsAreScored:
    """Found by running the generated-browser path end to end for the first time.

    `expect_text` was executed, compared against the page, and written into the
    observation's narration as ``NOT FOUND`` — and nothing scored it. A browser
    scenario whose only oracle was `expect_text` therefore could not fail, while
    the generator's schema advertises it as a browser oracle and
    `has_observable_outcome` accepts it as one.
    """

    @staticmethod
    def _result() -> ScenarioResult:
        return ScenarioResult(scenario_name="s", phase="verify", readiness_ok=True)

    @staticmethod
    def _observation(*pairs: tuple[str, bool]) -> BrowserObservation:
        return BrowserObservation(
            url="http://127.0.0.1/",
            # A session that genuinely reached the product. `page_loaded` is
            # the floor under every browser oracle — a session that never
            # loaded a page produces no oracles at all, and `all([])` is True —
            # so an observation standing in for a working session must say so.
            page_loaded=True,
            visible_text="status: open",
            text_expectations=[
                BrowserTextExpectation(text=text, present=present, step=index, label="step-1")
                for index, (text, present) in enumerate(pairs, start=1)
            ],
        )

    def test_a_satisfied_expectation_produces_a_passing_assertion(self) -> None:
        result = self._result()
        ScenarioExecutor._assert_browser_text(result, self._observation(("open", True)))
        assert len(result.assertions) == 1
        assert result.assertions[0].passed
        assert result.passed

    def test_an_unsatisfied_expectation_fails_the_scenario(self) -> None:
        result = self._result()
        ScenarioExecutor._assert_browser_text(
            result, self._observation(("status: resolved", False))
        )
        assert len(result.assertions) == 1
        assert not result.assertions[0].passed
        assert not result.passed
        assert "status: resolved" in result.assertions[0].target

    def test_every_expectation_is_scored_not_just_the_last(self) -> None:
        result = self._result()
        ScenarioExecutor._assert_browser_text(
            result, self._observation(("a", True), ("b", False), ("c", True))
        )
        assert [a.passed for a in result.assertions] == [True, False, True]

    def test_the_assertion_names_the_step_it_came_from(self) -> None:
        result = self._result()
        ScenarioExecutor._assert_browser_text(result, self._observation(("x", False)))
        assert "browser step 1" in result.assertions[0].target

    def test_a_scenario_whose_only_oracle_is_expect_text_is_observable(self) -> None:
        """The validator already believed this; now the executor makes it true."""
        scenario = make_scenario(
            "gen-browser-only-text",
            mode="browser",
            actions=[
                GeneratedAction(
                    kind="browser",
                    browser_steps=[
                        GeneratedBrowserStep(goto="/"),
                        GeneratedBrowserStep(expect_text="next action"),
                    ],
                )
            ],
            state_checks=[],
            expected_observations=[],
            cleanup=[],
        )
        assert scenario.has_observable_outcome()

    # -- hostile ---------------------------------------------------------

    def test_narration_alone_is_not_accepted_as_an_oracle(self) -> None:
        """Mutation: dropping `_assert_browser_text` from the execution paths."""
        step_source = inspect.getsource(ScenarioExecutor._run_steps)
        phase_source = inspect.getsource(ScenarioExecutor.execute)
        assert "_assert_browser_text" in step_source
        assert "_assert_browser_text" in phase_source

    def test_the_observation_records_expectations_structurally(self) -> None:
        """Driven, not grepped.

        Grepping for `BrowserTextExpectation(` is satisfied by constructing one
        and appending it to a throwaway list — which is the mutation. So this
        runs the real `_run_step` against a stub page and reads the observation
        it was handed.
        """
        assert "text_expectations" in BrowserObservation.model_fields

        class StubPage:
            async def evaluate(self, _script: str) -> str:
                return "status: open\nowner: Neyma"

        executor = object.__new__(ScenarioExecutor)
        observation = BrowserObservation(url="http://127.0.0.1/", page_loaded=True)
        asyncio.run(
            ScenarioExecutor._run_step(
                executor,
                StubPage(),
                BrowserStep(expect_text="status: open"),
                observation,
                Path("."),
                3,
                "http://127.0.0.1",
                "step-1-",
            )
        )
        asyncio.run(
            ScenarioExecutor._run_step(
                executor,
                StubPage(),
                BrowserStep(expect_text="status: resolved"),
                observation,
                Path("."),
                4,
                "http://127.0.0.1",
                "step-1-",
            )
        )
        assert [(e.text, e.present, e.step) for e in observation.text_expectations] == [
            ("status: open", True, 3),
            ("status: resolved", False, 4),
        ]

        # …and that those recorded expectations become scored assertions.
        result = self._result()
        ScenarioExecutor._assert_browser_text(result, observation)
        assert [a.passed for a in result.assertions] == [True, False]
        assert not result.passed

    def test_a_step_that_raised_is_scored_as_a_failure(self) -> None:
        """A scenario whose every step blew up produced no assertions at all,
        and `ScenarioResult.passed` reads no assertions as success."""
        result = self._result()
        observation = BrowserObservation(
            url="http://127.0.0.1/",
            page_loaded=True,
            step_failures=["step 2 FAILED: TimeoutError: locator '#resolve' not found"],
        )
        ScenarioExecutor._assert_browser_text(result, observation)
        assert len(result.assertions) == 1
        assert not result.assertions[0].passed
        assert not result.passed

    def test_a_clean_run_scores_no_step_failures(self) -> None:
        result = self._result()
        ScenarioExecutor._assert_browser_text(result, self._observation(("open", True)))
        assert [a.passed for a in result.assertions] == [True]

    def test_merging_two_browser_observations_keeps_both_sets(self) -> None:
        result = self._result()
        first = self._observation(("a", True))
        second = self._observation(("b", False))
        executor = object.__new__(ScenarioExecutor)
        ScenarioExecutor._merge_browser(executor, result, first)
        ScenarioExecutor._merge_browser(executor, result, second)
        assert result.browser is not None
        assert [e.text for e in result.browser.text_expectations] == ["a", "b"]

    def test_the_executor_scores_step_failures_and_records_them(self) -> None:
        assert "step_failures" in BrowserObservation.model_fields
        run_step = inspect.getsource(ScenarioExecutor._run_step)
        assert "obs.step_failures.append" in run_step
        assert "step_failures" in inspect.getsource(ScenarioExecutor._assert_browser_text)


# --------------------------------------------------------------------------
# A — the fields the generator is judged on are described to it
# --------------------------------------------------------------------------


class TestTheGeneratorIsToldWhatItIsJudgedOn:
    """Measured, not assumed: see `verification-evidence/post-remediation/a-*`.

    Re-running r2's generation-quality campaign against the merged baseline
    showed the same failure class the remediation engineer named — a rule the
    generator is not told about produces silence, not compliance. Two causes were
    measured, and only those two were changed:

      * `persisted_state_checks` was declared as a bare `{"type": "object"}`.
        The model wrote `{"name", "description", "expect"}`, omitted the required
        `command`, and six proposals in one wave were discarded for
        `persisted_state_checks.0.command: Field required` — taking with them the
        only oracle an EFFECT_FAMILY scenario is allowed to have.
      * nothing said that invoking an existing broad test suite is not a
        scenario, so for a durable-state task the model proposed running the
        repository's own pytest files and asserting they passed.
    """

    @staticmethod
    def _state_check_schema() -> dict:
        from neyma_product_driver.scenario_generator import PLAN_SCHEMA

        scenario = PLAN_SCHEMA["properties"]["scenarios"]["items"]["properties"]
        return scenario["persisted_state_checks"]

    def test_persisted_state_checks_describes_its_required_command(self) -> None:
        schema = self._state_check_schema()
        assert "description" in schema
        item = schema["items"]
        assert item.get("required") == ["command"]
        assert "command" in item["properties"]
        assert item["properties"]["command"]["description"]

    def test_the_schema_agrees_with_the_model_it_is_parsed_into(self) -> None:
        """A described field that the parser then rejects is worse than silence."""
        from neyma_product_driver.scenario_plan import GeneratedStateCheck

        described = set(self._state_check_schema()["items"]["properties"])
        assert described <= set(GeneratedStateCheck.model_fields)
        required = {
            name
            for name, field in GeneratedStateCheck.model_fields.items()
            if field.is_required()
        }
        assert required == set(self._state_check_schema()["items"]["required"])

    def test_the_fields_that_do_not_exist_are_named_as_not_existing(self) -> None:
        from neyma_product_driver.scenario_generator import GENERATOR_SYSTEM

        schema_text = json.dumps(self._state_check_schema())
        assert "description" in schema_text and "expect" in schema_text
        assert "discarded" in schema_text
        assert "`description` or `expect`" in GENERATOR_SYSTEM

    def test_the_effect_family_rule_shows_a_worked_oracle(self) -> None:
        from neyma_product_driver.scenario_generator import GENERATOR_SYSTEM

        assert "persisted_state_checks" in GENERATOR_SYSTEM
        assert "sqlite3" in GENERATOR_SYSTEM
        # Every category the validator holds to the rule must be named in the
        # rule the generator is shown, or the refusal is a surprise.
        for category in (
            "idempotency",
            "repeated requests",
            "retries",
            "partial failure",
            "persistence",
            "stale state",
            "restart",
            "crash",
            "unexpected state transition",
        ):
            assert category in GENERATOR_SYSTEM, category

    def test_running_an_existing_suite_is_stated_not_to_be_a_scenario(self) -> None:
        from neyma_product_driver.scenario_generator import GENERATOR_SYSTEM

        flat = " ".join(GENERATOR_SYSTEM.split())
        assert "Running somebody else's existing test suite is not that" in flat
        assert "does not make invoking it a scenario" in flat
        assert "EXERCISE THE PRODUCT YOURSELF" in flat

    def test_every_category_the_validator_gates_is_offered_to_the_generator(self) -> None:
        """A category the model may not use is a category it should not be shown."""
        from neyma_product_driver.scenario_generator import GENERATOR_SYSTEM

        for category in RiskCategory:
            assert category.value in GENERATOR_SYSTEM, category.value


# --------------------------------------------------------------------------
# I — a generated scenario that addresses the local app gets the local app
# --------------------------------------------------------------------------


class TestGeneratedScenariosGetTheServiceTheyAddress:
    """Found by running the real builder loop against a fixture repository.

    Four generated scenarios issued requests against `app_url`, declared no
    `service_refs`, and were compiled with **no services**. Readiness had nothing
    to check and passed; every request then failed with `Connection refused`;
    and four FAILED outcomes were attributed to a product that was never running.
    The evaluator diagnosed it correctly and refused to blame the builder, which
    is the right behaviour — but the iteration was spent on a harness artefact.

    `service_refs` is documented to the generator as the list of services it may
    *operate on* (restart / stop / start). It was also, silently, the list of
    services that got started at all.
    """

    @staticmethod
    def _base() -> Scenario:
        from neyma_product_driver.scenarios import ServiceSpec

        return Scenario(
            name="base",
            app_url="http://127.0.0.1:8931",
            services=[ServiceSpec(name="api", command="./serve.sh")],
            readiness=[{"tcp": "127.0.0.1:8931"}],
        )

    @staticmethod
    def _compile(scenario: GeneratedScenario) -> Scenario:
        from neyma_product_driver.scenario_plan import compile_to_scenario

        return compile_to_scenario(
            scenario,
            base=TestGeneratedScenariosGetTheServiceTheyAddress._base(),
            approved_commands=set(),
        )

    @staticmethod
    def _requesting(**overrides) -> GeneratedScenario:
        from neyma_product_driver.scenario_plan import GeneratedRequest

        return make_scenario(
            "gen-requests",
            actions=[
                GeneratedAction(
                    kind="request",
                    request=GeneratedRequest(method="POST", path="/approve", expect_status=200),
                )
            ],
            state_checks=[],
            cleanup=[],
            **{"service_refs": [], **overrides},
        )

    def test_a_requesting_scenario_starts_the_service_that_serves_it(self) -> None:
        compiled = self._compile(self._requesting())
        assert [s.name for s in compiled.services] == ["api"]
        assert compiled.readiness, "readiness must be checked once a service is started"
        assert compiled.app_url == "http://127.0.0.1:8931"

    def test_a_browser_scenario_starts_the_service_too(self) -> None:
        scenario = make_scenario(
            "gen-browser",
            mode="browser",
            actions=[
                GeneratedAction(
                    kind="browser",
                    browser_steps=[GeneratedBrowserStep(goto="/")],
                )
            ],
            state_checks=[],
            expected_observations=[],
            cleanup=[],
            service_refs=[],
        )
        assert [s.name for s in self._compile(scenario).services] == ["api"]

    def test_an_explicit_service_ref_is_still_honoured_exactly(self) -> None:
        compiled = self._compile(self._requesting(service_refs=["api"]))
        assert [s.name for s in compiled.services] == ["api"]

    def test_a_command_only_scenario_still_starts_nothing(self) -> None:
        """Nothing is started speculatively: only what the scenario addresses."""
        scenario = make_scenario(
            "gen-command-only",
            actions=[],
            state_checks=[],
            expected_observations=["ok"],
            cleanup=[],
            service_refs=[],
        )
        assert scenario.addresses_local_app() is False
        assert self._compile(scenario).services == []

    def test_a_scenario_may_still_not_invent_a_service(self) -> None:
        from neyma_product_driver.scenario_plan import CompilationError

        with pytest.raises(CompilationError, match="does not declare"):
            self._compile(self._requesting(service_refs=["not-a-service"]))

    # -- hostile ---------------------------------------------------------

    def test_the_inheritance_is_conditioned_on_addressing_the_app(self) -> None:
        """Mutation: starting every base service for every generated scenario."""
        source = inspect.getsource(
            __import__(
                "neyma_product_driver.scenario_plan", fromlist=["compile_to_scenario"]
            ).compile_to_scenario
        )
        assert "addresses_local_app()" in source


# --------------------------------------------------------------------------
# A — a generator session that fails says why it failed
# --------------------------------------------------------------------------


class TestAFailedGenerationSessionIsLegible:
    """Measured while re-running r2's campaign: task A produced zero scenarios
    three times running. The recorded reason was "the scenario generator
    returned no usable structured output", which is what the planner records for
    a refusal, a transport failure, and — as it turned out here — a session cut
    off at its turn limit having read the repository and written nothing.

    Probing the session directly returned `is_error=True, subtype=error_max_turns,
    num_turns=17` against `max_turns=16`. The budget was stopping the work before
    it started, and nothing said so.
    """

    def test_a_turn_limit_cutoff_names_itself(self) -> None:
        from neyma_product_driver.scenario_generator import _describe_session_error

        message = _describe_session_error("error_max_turns", 40)
        assert "40" in message
        assert "turns" in message
        assert "generator_max_turns" in message, "the message must name the lever"

    def test_an_unknown_subtype_still_says_something_specific(self) -> None:
        from neyma_product_driver.scenario_generator import _describe_session_error

        assert "weird_new_subtype" in _describe_session_error("weird_new_subtype", 40)
        assert _describe_session_error("", 40)

    def test_a_failed_session_raises_rather_than_returning_empty(self) -> None:
        """An empty wave reads as 'nothing to add'; a raise reads as 'this broke'."""
        from neyma_product_driver.scenario_generator import (
            GenerationSessionError,
            LLMScenarioReasoner,
        )

        assert issubclass(GenerationSessionError, RuntimeError)
        source = inspect.getsource(LLMScenarioReasoner._session)
        assert "raise GenerationSessionError(" in source
        assert "return None" not in source.split("if message.is_error:")[1][:200]

    def test_the_turn_budget_is_configurable_and_wired(self) -> None:
        from neyma_product_driver.scenario_generator import LLMScenarioReasoner

        assert "generator_max_turns" in ScenarioGenerationConfig.model_fields
        assert ScenarioGenerationConfig().generator_max_turns == (
            LLMScenarioReasoner.DEFAULT_MAX_TURNS
        )
        reasoner = LLMScenarioReasoner(Path("."), max_turns=7)
        assert reasoner.max_turns == 7
        assert "max_turns=self.max_turns" in inspect.getsource(
            LLMScenarioReasoner._session
        )

    # -- hostile ---------------------------------------------------------

    def test_the_control_loop_passes_the_configured_budget(self) -> None:
        """Mutation: the config knob exists but nothing reads it."""
        source = inspect.getsource(driver_cli)
        assert source.count("max_turns=config.scenario_generation.generator_max_turns") == 2

    def test_the_planner_records_the_reason_against_the_wave(self) -> None:
        """A raising reasoner must become a *failed* wave, not an empty one."""
        from neyma_product_driver.scenario_generator import GenerationSessionError
        from neyma_product_driver.scenario_planner import ScenarioPlanner

        from scenario_fixtures import FakeFounder, FakeUnit, base_scenario

        class Exploding:
            session_id = "x"

            def propose(self, brief):
                raise GenerationSessionError("the generator used all 40 of its turns")

        planner = ScenarioPlanner(
            repo=Path("."),
            config=ScenarioGenerationConfig(enabled=True),
            reasoner=Exploding(),
            store=None,
            base_scenario=base_scenario(),
            permanent_scenarios=[base_scenario()],
            founder=FakeFounder(),
        )
        planner.plan_initial(task="t", unit=FakeUnit(), run_id="r")
        problems = planner.generation_problems()
        assert problems, "a failed wave must be a recorded generation problem"
        assert any("turns" in p for p in problems), problems


# --------------------------------------------------------------------------
# A — a sub-second deadline is expressible
# --------------------------------------------------------------------------


class TestFractionalTimeoutsAreAccepted:
    """Found in the live-builder run: an entire adaptive wave was refused for

        actions.0.request.timeout_s: Input should be a valid integer,
        got a number with a fractional part

    Three scenarios, all three dead, on a constraint with no runtime basis —
    `timeout_s` reaches `asyncio.wait_for` and `urlopen(timeout=...)`, both of
    which take a float. A scenario probing "timed out *before* the effect
    landed" needs a sub-second deadline, so the integer requirement stopped the
    generator expressing the exact situation the taxonomy has a category for.

    Widening this refuses nothing it used to accept: a deadline is an upper
    bound, and 0.25 is a stricter bound than 1.
    """

    @pytest.mark.parametrize("value", [0.25, 0.5, 1.5, 30])
    def test_a_generated_request_accepts_a_fractional_deadline(self, value) -> None:
        from neyma_product_driver.scenario_plan import GeneratedRequest

        assert GeneratedRequest(method="GET", path="/x", timeout_s=value).timeout_s == value

    @pytest.mark.parametrize("value", [0.25, 2.5])
    def test_a_generated_state_check_and_action_accept_one_too(self, value) -> None:
        from neyma_product_driver.scenario_plan import GeneratedAction, GeneratedStateCheck

        assert GeneratedStateCheck(command="./probe.sh", timeout_s=value).timeout_s == value
        assert GeneratedAction(kind="command", command="x", timeout_s=value).timeout_s == value

    @pytest.mark.parametrize("value", [0.25, 2.5])
    def test_the_executable_specs_accept_it_as_well(self, value) -> None:
        """The generated model is only useful if what it compiles into agrees."""
        from neyma_product_driver.scenarios import CommandSpec, RequestSpec, StateCheckSpec

        assert RequestSpec(method="GET", path="/x", timeout_s=value).timeout_s == value
        assert CommandSpec(run="x", timeout_s=value).timeout_s == value
        assert StateCheckSpec(command="x", timeout_s=value).timeout_s == value

    def test_a_sub_second_deadline_survives_compilation(self) -> None:
        from neyma_product_driver.scenario_plan import (
            GeneratedAction,
            GeneratedRequest,
            compile_to_scenario,
        )

        scenario = make_scenario(
            "gen-timeout-before-effect",
            risk_category=RiskCategory.TIMEOUT_BEFORE_EFFECT,
            actions=[
                GeneratedAction(
                    kind="request",
                    request=GeneratedRequest(
                        method="POST", path="/approve", expect_status=200, timeout_s=0.25
                    ),
                )
            ],
            state_checks=[],
            cleanup=[],
            service_refs=[],
        )
        compiled = compile_to_scenario(scenario, base=None, approved_commands=set())
        step = compiled.steps[0]
        assert step.request is not None
        assert step.request.timeout_s == 0.25
