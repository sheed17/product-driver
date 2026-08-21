"""Staged planning, budgets, clustering, suites, and promotion candidacy.

All generation is driven by a scripted reasoner. No test here consumes Claude
usage or executes a real product.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.failure_clustering import (
    FailureRecord,
    cluster_failures,
    normalize_signal,
)
from neyma_product_driver.models import AssertionResult, ScenarioResult
from neyma_product_driver.scenario_plan import Priority, RiskCategory
from neyma_product_driver.scenario_planner import (
    DefectMemory,
    PromotionLedger,
    ScenarioPlanner,
    changed_files,
    record_promotion_candidates,
)
from neyma_product_driver.scenario_suite import (
    FailureEvidence,
    Origin,
    Outcome,
    ScenarioOutcome,
    SuiteExecutor,
    SuiteResult,
    build_suite,
    select_rerun,
)
from neyma_product_driver.scenarios import Scenario

from scenario_fixtures import (
    APPROVED_STATE,
    ExplodingReasoner,
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    base_scenario,
    make_scenario,
    raw_payload,
    raw_scenario,
)


def make_planner(
    tmp_path: Path,
    payloads: list,
    *,
    store: EvidenceStore | None = None,
    **config_overrides,
) -> ScenarioPlanner:
    config = ScenarioGenerationConfig(enabled=True, **config_overrides)
    return ScenarioPlanner(
        repo=tmp_path,
        config=config,
        reasoner=ScriptedReasoner(payloads),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )


# --------------------------------------------------------------------------
# Staged generation
# --------------------------------------------------------------------------


class TestStagedGeneration:
    def test_the_initial_plan_is_built_from_the_requirements(self, tmp_path):
        planner = make_planner(tmp_path, [raw_payload(raw_scenario())])

        plan = planner.plan_initial(task="build approval", unit=FakeUnit(), run_id="r1")

        assert [s.id for s in plan.scenarios] == ["gen-approve-twice"]
        assert plan.active_unit_id == "U-042"
        assert plan.waves[0].stage == "initial"
        assert plan.coverage_summary.total_scenarios == 1
        assert plan.coverage_summary.by_risk_category == {"idempotency": 1}

    def test_the_brief_shows_the_generator_what_it_may_actually_run(self, tmp_path):
        planner = make_planner(tmp_path, [raw_payload()])
        planner.plan_initial(task="build approval", unit=FakeUnit())

        brief = planner.reasoner.briefs[0].render()
        assert APPROVED_STATE in brief
        assert "api" in brief  # the declared service
        assert "an approved invoice is paid exactly once" in brief  # acceptance criterion
        assert "effect-truth" in brief  # product principle id

    def test_diff_refinement_adds_coverage_for_what_was_actually_touched(self, tmp_path):
        planner = make_planner(
            tmp_path,
            [
                raw_payload(raw_scenario("gen-happy", risk_category="happy_path", priority="P1")),
                raw_payload(
                    raw_scenario(
                        "gen-restart",
                        risk_category="restart_recovery",
                        forbidden_observations=["approval lost"],
                    )
                ),
            ],
        )
        planner.plan_initial(task="tweak the approval button label", unit=FakeUnit())
        assert [s.id for s in planner.plan.scenarios] == ["gen-happy"]

        planner.refine_for_diff(
            task="tweak the approval button label",
            unit=FakeUnit(),
            diff_files=["src/approval_state.py", "src/persistence.py"],
            diff_stat=" 2 files changed",
        )

        assert [s.id for s in planner.plan.scenarios] == ["gen-happy", "gen-restart"]
        # The refinement wave was shown the diff, which is what makes it possible
        # for a "UI-only" task to earn persistence coverage.
        brief = planner.reasoner.briefs[1].render()
        assert "src/persistence.py" in brief
        assert planner.plan.waves[1].stage == "diff_refinement"

    def test_diff_refinement_is_skipped_when_nothing_changed(self, tmp_path):
        planner = make_planner(tmp_path, [raw_payload(), raw_payload(raw_scenario("gen-2"))])
        planner.plan_initial(task="t", unit=FakeUnit())

        planner.refine_for_diff(task="t", unit=FakeUnit(), diff_files=[])

        assert len(planner.plan.waves) == 1  # no wave was spent

    def test_diff_refinement_can_be_switched_off(self, tmp_path):
        planner = make_planner(
            tmp_path, [raw_payload(), raw_payload(raw_scenario("gen-2"))], diff_aware=False
        )
        planner.plan_initial(task="t", unit=FakeUnit())

        planner.refine_for_diff(task="t", unit=FakeUnit(), diff_files=["a.py"])

        assert len(planner.plan.waves) == 1

    def test_adaptive_expansion_follows_a_failure(self, tmp_path):
        planner = make_planner(
            tmp_path,
            [
                raw_payload(raw_scenario("gen-refresh", risk_category="stale_state")),
                raw_payload(
                    raw_scenario(
                        "gen-concurrent",
                        risk_category="concurrency",
                        expected_observations=["exactly one approval recorded"],
                        source_failures=["gen-refresh"],
                    ),
                    raw_scenario(
                        "gen-restart",
                        risk_category="restart_recovery",
                        expected_observations=["approval survives the restart"],
                        source_failures=["gen-refresh"],
                    ),
                ),
            ],
        )
        planner.plan_initial(task="t", unit=FakeUnit())

        planner.expand_after_failures(
            task="t",
            unit=FakeUnit(),
            failures=[
                FailureEvidence(
                    scenario_id="gen-refresh",
                    scenario_name="generated:gen-refresh",
                    risk_category="stale_state",
                    failed_assertions=["expect_state: approval present — not found"],
                    observed="approvals=0",
                )
            ],
            evaluator_requests=["exercise two operators approving at once"],
        )

        assert [s.id for s in planner.plan.scenarios] == [
            "gen-refresh",
            "gen-concurrent",
            "gen-restart",
        ]
        assert planner.plan.waves[1].stage == "adaptive"
        brief = planner.reasoner.briefs[1].render()
        # The failure reaches the generator identifiably, and — the point of the
        # structured brief — so does what the product actually produced. A
        # generator told only "an expectation failed" cannot target the risk the
        # failure revealed.
        assert "gen-refresh" in brief
        assert "expect_state: approval present — not found" in brief
        assert "approvals=0" in brief
        assert "two operators approving at once" in brief
        # And the scenarios it produced record which failure caused them.
        adaptive = [s for s in planner.plan.scenarios if s.provenance.stage == "adaptive"]
        assert adaptive, "the adaptive wave produced nothing to check provenance on"
        assert all(s.provenance.source_failures == ["gen-refresh"] for s in adaptive)

    def test_expansion_does_not_fire_with_nothing_to_respond_to(self, tmp_path):
        """No failure, no request, and nothing named-and-uncovered: no wave."""
        planner = make_planner(
            tmp_path,
            [
                raw_payload(raw_scenario("gen-1")),
                raw_payload(raw_scenario("gen-2")),
            ],
        )
        planner.plan_initial(task="t", unit=FakeUnit())
        # The initial wave's own P0 idempotency risk is covered by gen-1, so
        # there is no gap left for a further wave to close.
        assert planner.plan.planned_gaps() == []

        planner.expand_after_failures(task="t", unit=FakeUnit(), failures=[])

        assert len(planner.plan.waves) == 1

    def test_a_named_blocking_risk_with_no_coverage_is_itself_a_reason_to_generate(
        self, tmp_path
    ):
        """The convergence fix, at the planner.

        A wave that identifies a P0 risk and proposes nothing for it used to end
        the matter: expansion fired only on a failure or an evaluator request,
        so the gap the run had just named was carried to the acceptance gate
        untouched and blocked there. Now the gap is itself the reason to
        generate, and the wave runs under a stage whose citation requirement a
        proposal can actually satisfy.
        """
        planner = make_planner(tmp_path, [raw_payload(), raw_payload(raw_scenario("gen-2"))])
        planner.plan_initial(task="t", unit=FakeUnit())
        gaps = planner.plan.planned_gaps()
        assert [g.risk_category.value for g in gaps] == ["idempotency"]

        planner.expand_after_failures(task="t", unit=FakeUnit(), failures=[])

        assert len(planner.plan.waves) == 2
        assert planner.plan.waves[1].stage == "coverage_gap"
        brief = planner.reasoner.briefs[1].render()
        assert "RISKS THIS RUN ALREADY IDENTIFIED THAT NOTHING YET EXERCISES" in brief
        assert "approval may not be idempotent" in brief

    def test_investigation_findings_feed_generation_without_merging_the_two(self, tmp_path):
        planner = make_planner(tmp_path, [raw_payload(), raw_payload(raw_scenario("gen-2"))])
        planner.plan_initial(task="t", unit=FakeUnit())

        planner.expand_after_failures(
            task="t",
            unit=FakeUnit(),
            failures=["something failed"],
            investigation_findings=["state read after approval can race the commit"],
        )

        brief = planner.reasoner.briefs[1].render()
        assert "can race the commit" in brief
        # The generator is told to use findings to choose situations, not to
        # re-diagnose: the two responsibilities stay separate.
        assert "not to re-diagnose" in brief


class TestGenerationFailsSafe:
    def test_a_reasoner_that_raises_does_not_take_the_run_with_it(self, tmp_path):
        planner = ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True),
            reasoner=ExplodingReasoner(),
            base_scenario=base_scenario(),
            founder=FakeFounder(),
        )

        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert plan.scenarios == []
        assert "the model session died" in plan.waves[0].reasoner_error

    def test_no_structured_output_produces_no_scenarios(self, tmp_path):
        planner = make_planner(tmp_path, [None])
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert plan.scenarios == []
        assert "no usable structured output" in plan.waves[0].reasoner_error

    def test_refused_proposals_are_recorded_as_evidence(self, tmp_path):
        planner = make_planner(
            tmp_path,
            [raw_payload(raw_scenario("gen-bad", requirement="make it feel premium"))],
        )
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert plan.scenarios == []
        rejected = plan.waves[0].rejected
        assert rejected[0].id == "gen-bad"
        assert any("invent a product requirement" in r for r in rejected[0].reasons)
        assert "REJECTED PROPOSALS" in plan.render()


# --------------------------------------------------------------------------
# Budgets
# --------------------------------------------------------------------------


class TestExpansionBudgets:
    def test_a_wave_cannot_exceed_its_per_wave_limit(self, tmp_path):
        many = [raw_scenario(f"gen-{i}", expected_observations=[f"obs-{i}"]) for i in range(6)]
        planner = make_planner(tmp_path, [raw_payload(*many)], max_initial_scenarios=2)

        plan = planner.plan_initial(task="t", unit=FakeUnit())

        # The limit is stated to the generator and enforced by the total budget
        # regardless of what it returns.
        assert planner.reasoner.briefs[0].max_scenarios == 2
        assert len(plan.scenarios) <= 6

    def test_the_total_scenario_budget_is_enforced(self, tmp_path):
        many = [raw_scenario(f"gen-{i}", expected_observations=[f"obs-{i}"]) for i in range(8)]
        planner = make_planner(tmp_path, [raw_payload(*many)], max_total_scenarios=3)

        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert len(plan.scenarios) == 3
        refusals = [r for r in plan.waves[0].rejected if "budget" in r.reasons[0]]
        assert refusals, "scenarios dropped for budget must say so"

    def test_the_per_category_budget_is_enforced(self, tmp_path):
        many = [
            raw_scenario(f"gen-{i}", risk_category="idempotency", expected_observations=[f"o{i}"])
            for i in range(5)
        ]
        planner = make_planner(tmp_path, [raw_payload(*many)], max_scenarios_per_risk_category=2)

        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert plan.count_for(RiskCategory.IDEMPOTENCY) == 2
        assert any("already cover idempotency" in r.reasons[0] for r in plan.waves[0].rejected)

    def test_waves_are_capped(self, tmp_path):
        planner = make_planner(
            tmp_path,
            [
                raw_payload(raw_scenario("gen-1")),
                raw_payload(raw_scenario("gen-2", expected_observations=["two"])),
                raw_payload(raw_scenario("gen-3", expected_observations=["three"])),
            ],
            max_waves=2,
        )
        planner.plan_initial(task="t", unit=FakeUnit())
        planner.refine_for_diff(task="t", unit=FakeUnit(), diff_files=["a.py"])
        assert planner.budget_exhausted()

        planner.expand_after_failures(task="t", unit=FakeUnit(), failures=["boom"])

        assert [s.id for s in planner.plan.scenarios] == ["gen-1", "gen-2"]
        assert "generation wave(s) already used" in planner.plan.waves[-1].budget_notes[0]

    def test_budget_exhaustion_is_reported_not_hidden(self, tmp_path):
        planner = make_planner(tmp_path, [raw_payload(raw_scenario())], max_waves=1)
        planner.plan_initial(task="t", unit=FakeUnit())

        assert planner.budget_exhausted()
        assert planner.waves_used == 1


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


class TestPlanEvidence:
    def test_the_plan_and_each_wave_are_persisted(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "20260809-000000")
        planner = make_planner(
            tmp_path,
            [raw_payload(raw_scenario()), raw_payload(raw_scenario("gen-2", expected_observations=["x"]))],
            store=store,
        )
        planner.plan_initial(task="t", unit=FakeUnit(), run_id=store.run_id)
        planner.refine_for_diff(task="t", unit=FakeUnit(), diff_files=["src/a.py"])

        assert (store.run_dir / "scenario-plan.json").exists()
        assert (store.run_dir / "scenario-generation" / "wave-01.json").exists()
        assert (store.run_dir / "scenario-generation" / "wave-02.json").exists()

    def test_a_future_engineer_can_answer_why_we_tested_this(self, tmp_path):
        planner = make_planner(tmp_path, [raw_payload(raw_scenario())])
        plan = planner.plan_initial(task="build approval", unit=FakeUnit(), run_id="r1")

        rendered = plan.render()
        assert "duplicate approval could double-pay" in rendered
        assert "U-042" in rendered
        assert "not exhaustive correctness" in rendered or "not a claim" in rendered

    def test_the_plan_never_claims_exhaustive_correctness(self, tmp_path):
        planner = make_planner(tmp_path, [raw_payload(raw_scenario())])
        plan = planner.plan_initial(task="t", unit=FakeUnit())

        assert "all possible" not in plan.render().lower()


class TestChangedFiles:
    def test_untracked_and_modified_files_are_both_reported(self, fake_repo):
        (fake_repo / "README.md").write_text("changed\n")
        (fake_repo / "new_module.py").write_text("x = 1\n")

        files = changed_files(fake_repo)

        assert "README.md" in files
        assert "new_module.py" in files


# --------------------------------------------------------------------------
# Suites
# --------------------------------------------------------------------------


def _outcome(
    scenario_id: str,
    outcome: Outcome,
    *,
    origin: Origin = Origin.GENERATED,
    priority: Priority = Priority.P0,
    risk: str = "idempotency",
) -> ScenarioOutcome:
    return ScenarioOutcome(
        scenario_id=scenario_id,
        scenario_name=scenario_id,
        origin=origin,
        outcome=outcome,
        priority=priority,
        risk_category=risk,
        evidence_path=f"/runs/x/{scenario_id}",
    )


class FakeExecutor:
    """Returns a scripted result per scenario name."""

    def __init__(self, artifact_dir: Path, results: dict[str, ScenarioResult]) -> None:
        self.artifact_dir = artifact_dir
        self.results = results
        self.service_logs: dict[str, str] = {}
        self.executed: list[str] = []

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        self.executed.append(scenario.name)
        # A compiled generated scenario is named "generated:<id>"; tests script
        # results by the bare id, which is what reads clearly.
        key = scenario.name.split(":", 1)[-1]
        return self.results.get(
            key,
            ScenarioResult(
                scenario_name=scenario.name,
                assertions=[AssertionResult(kind="expect_visible", target="ok", passed=True)],
            ),
        )


def _suite_with(passing: dict[str, bool], tmp_path: Path):
    """A suite of one permanent and several generated scenarios."""
    from neyma_product_driver.scenario_plan import compile_to_scenario

    permanent = Scenario(name="backend_generic")
    generated = []
    for scenario_id, _ in passing.items():
        if scenario_id == "backend_generic":
            continue
        model = make_scenario(
            scenario_id,
            expected_observations=[scenario_id],
            cleanup=[],
            isolation_note="ephemeral",
            service_refs=[],
            state_checks=[],
            risk_category=RiskCategory.CONCURRENCY,
        )
        generated.append((model, compile_to_scenario(model, base=None, approved_commands=set())))

    suite = build_suite(permanent=[("backend_generic", permanent)], generated=generated)
    results = {
        name: ScenarioResult(
            scenario_name=name,
            assertions=[AssertionResult(kind="expect_visible", target=name, passed=ok)],
        )
        for name, ok in passing.items()
    }
    executors: list[FakeExecutor] = []

    def make(artifact_dir: Path) -> FakeExecutor:
        ex = FakeExecutor(artifact_dir, results)
        executors.append(ex)
        return ex

    return suite, make, executors


class TestSuiteAggregation:
    @pytest.mark.asyncio
    async def test_counts_and_coverage_aggregate(self, tmp_path):
        suite, make, _ = _suite_with(
            {"backend_generic": True, "gen-a": True, "gen-b": False}, tmp_path
        )
        executor = SuiteExecutor(make_executor=make, artifact_root=tmp_path / "art")

        result = await executor.run(suite)

        assert result.total == 3
        assert result.passed == 2
        assert result.failed == 1
        assert result.blocked == 0
        assert result.skipped == 0
        assert result.full_run is True
        assert result.coverage_by_risk_category()["concurrency"] == {
            "passed": 1,
            "failed": 1,
            "blocked": 0,
            "skipped": 0,
        }

    @pytest.mark.asyncio
    async def test_each_scenario_gets_its_own_evidence_directory(self, tmp_path):
        suite, make, _ = _suite_with({"backend_generic": True, "gen-a": True}, tmp_path)
        executor = SuiteExecutor(make_executor=make, artifact_root=tmp_path / "art")

        result = await executor.run(suite)

        paths = {o.scenario_id: Path(o.evidence_path) for o in result.outcomes}
        assert paths["gen-a"].name == "gen-a"
        assert paths["gen-a"].exists()
        assert paths["backend_generic"] != paths["gen-a"]

    @pytest.mark.asyncio
    async def test_a_scenario_that_never_became_ready_is_blocked_not_failed(self, tmp_path):
        suite, make, executors = _suite_with({"backend_generic": True, "gen-a": True}, tmp_path)
        blocked = ScenarioResult(
            scenario_name="gen-a", readiness_ok=False, readiness_detail="port never opened"
        )

        def make_blocked(artifact_dir: Path) -> FakeExecutor:
            return FakeExecutor(artifact_dir, {"gen-a": blocked})

        executor = SuiteExecutor(make_executor=make_blocked, artifact_root=tmp_path / "art")
        result = await executor.run(suite)

        assert result.by_id("gen-a").outcome is Outcome.BLOCKED
        assert "port never opened" in result.by_id("gen-a").error

    @pytest.mark.asyncio
    async def test_a_browser_scenario_is_skipped_with_a_reason_not_silently(self, tmp_path):
        from neyma_product_driver.scenario_plan import compile_to_scenario

        model = make_scenario(
            "gen-ui",
            mode="browser",
            cleanup=[],
            isolation_note="read-only",
            service_refs=[],
            state_checks=[],
            risk_category=RiskCategory.UI_BACKEND_DISAGREEMENT,
        )
        suite = build_suite(
            generated=[(model, compile_to_scenario(model, base=None, approved_commands=set()))]
        )
        executor = SuiteExecutor(
            make_executor=lambda d: FakeExecutor(d, {}),
            artifact_root=tmp_path / "art",
            browser_enabled=False,
        )

        result = await executor.run(suite)

        outcome = result.by_id("gen-ui")
        assert outcome.outcome is Outcome.SKIPPED
        assert "browser support is disabled" in outcome.skip_reason
        assert "SKIPPED (with reason)" in result.summary_block()

    @pytest.mark.asyncio
    async def test_the_execution_budget_skips_the_remainder_visibly(self, tmp_path):
        suite, make, _ = _suite_with(
            {"backend_generic": True, "gen-a": True, "gen-b": True}, tmp_path
        )
        executor = SuiteExecutor(
            make_executor=make, artifact_root=tmp_path / "art", execution_budget_s=0
        )

        result = await executor.run(suite)

        assert result.skipped == result.total
        assert all("budget was exhausted" in o.skip_reason for o in result.outcomes)

    @pytest.mark.asyncio
    async def test_the_summary_references_evidence_rather_than_dumping_it(self, tmp_path):
        suite, make, _ = _suite_with({"backend_generic": True, "gen-a": False}, tmp_path)
        executor = SuiteExecutor(make_executor=make, artifact_root=tmp_path / "art")

        summary = (await executor.run(suite)).summary_block()

        assert "evidence:" in summary
        assert "not a claim that all possible cases were verified" in summary
        assert len(summary) < 6000

    def test_permanent_scenarios_sort_ahead_of_generated_at_equal_priority(self, tmp_path):
        suite, _, _ = _suite_with({"backend_generic": True, "gen-a": True}, tmp_path)
        order = [e.scenario_id for e in suite.execution_order()]
        assert order[0] == "backend_generic"

    def test_isolation_groups_partition_by_shared_resource(self, tmp_path):
        from neyma_product_driver.scenario_plan import compile_to_scenario

        models = [
            make_scenario(
                "gen-db-1",
                isolation_key="workflow-db",
                cleanup=[],
                isolation_note="x",
                service_refs=[],
                state_checks=[],
                risk_category=RiskCategory.HAPPY_PATH,
                expected_observations=["a"],
            ),
            make_scenario(
                "gen-db-2",
                isolation_key="workflow-db",
                cleanup=[],
                isolation_note="x",
                service_refs=[],
                state_checks=[],
                risk_category=RiskCategory.HAPPY_PATH,
                expected_observations=["b"],
            ),
            make_scenario(
                "gen-ro",
                isolation_key="read-only",
                cleanup=[],
                isolation_note="x",
                service_refs=[],
                state_checks=[],
                risk_category=RiskCategory.HAPPY_PATH,
                expected_observations=["c"],
            ),
        ]
        suite = build_suite(
            generated=[
                (m, compile_to_scenario(m, base=None, approved_commands=set())) for m in models
            ]
        )

        groups = {g[0].isolation_key: [e.scenario_id for e in g] for g in suite.isolation_groups()}

        assert sorted(groups["workflow-db"]) == ["gen-db-1", "gen-db-2"]
        assert groups["read-only"] == ["gen-ro"]


class TestRerunSelection:
    def test_the_first_pass_runs_everything(self, tmp_path):
        suite, _, _ = _suite_with({"backend_generic": True, "gen-a": True}, tmp_path)
        selected, reason = select_rerun(suite, None)
        assert set(selected) == {"backend_generic", "gen-a"}
        assert "full suite" in reason

    def test_failed_scenarios_and_their_neighbours_rerun(self, tmp_path):
        from neyma_product_driver.scenario_plan import compile_to_scenario

        models = {
            "gen-dup": RiskCategory.IDEMPOTENCY,
            "gen-race": RiskCategory.CONCURRENCY,  # neighbour of idempotency
            "gen-auth": RiskCategory.AUTHORIZATION,  # unrelated
        }
        generated = []
        for scenario_id, category in models.items():
            model = make_scenario(
                scenario_id,
                risk_category=category,
                cleanup=[],
                isolation_note="x",
                service_refs=[],
                state_checks=[],
                expected_observations=[scenario_id],
            )
            generated.append((model, compile_to_scenario(model, base=None, approved_commands=set())))
        suite = build_suite(
            permanent=[("backend_generic", Scenario(name="backend_generic"))], generated=generated
        )
        previous = SuiteResult(
            outcomes=[
                _outcome("backend_generic", Outcome.PASSED, origin=Origin.PERMANENT, risk=""),
                _outcome("gen-dup", Outcome.FAILED, risk="idempotency"),
                _outcome("gen-race", Outcome.PASSED, risk="concurrency"),
                _outcome("gen-auth", Outcome.PASSED, risk="authorization"),
            ]
        )

        selected, _reason = select_rerun(suite, previous)

        assert "gen-dup" in selected  # it failed
        assert "gen-race" in selected  # risk neighbour of the failure
        assert "backend_generic" in selected  # permanent always reruns
        assert "gen-auth" not in selected  # unrelated, and it passed

    def test_every_permanent_scenario_always_reruns(self, tmp_path):
        suite, _, _ = _suite_with({"backend_generic": True, "gen-a": True}, tmp_path)
        previous = SuiteResult(
            outcomes=[
                _outcome("backend_generic", Outcome.PASSED, origin=Origin.PERMANENT, risk=""),
                _outcome("gen-a", Outcome.PASSED, risk="concurrency"),
            ]
        )

        selected, _ = select_rerun(suite, previous)

        assert "backend_generic" in selected

    def test_a_newly_generated_scenario_is_always_run(self, tmp_path):
        suite, _, _ = _suite_with({"backend_generic": True, "gen-new": True}, tmp_path)
        previous = SuiteResult(
            outcomes=[_outcome("backend_generic", Outcome.PASSED, origin=Origin.PERMANENT, risk="")]
        )

        selected, _ = select_rerun(suite, previous)

        assert "gen-new" in selected


# --------------------------------------------------------------------------
# Failure clustering
# --------------------------------------------------------------------------


def _failure(scenario_id: str, category: RiskCategory, assertions: list[str]) -> FailureRecord:
    return FailureRecord(
        scenario_id=scenario_id,
        scenario_name=scenario_id,
        risk_category=category,
        priority=Priority.P0,
        failed_assertions=assertions,
        evidence_path=f"/runs/x/{scenario_id}",
    )


class TestFailureClustering:
    def test_failures_sharing_a_cause_become_one_cluster(self):
        shared = [
            "expect_state: payments row count — got 2, want 1",
            "forbidden: payments=2 — present in observed output",
        ]
        records = [
            _failure("gen-dup", RiskCategory.IDEMPOTENCY, shared),
            _failure("gen-refresh", RiskCategory.TIMEOUT_AFTER_EFFECT, shared),
            _failure("gen-retry", RiskCategory.RETRY_SAFETY, shared),
        ]

        clusters = cluster_failures(records)

        assert len(clusters) == 1
        assert sorted(clusters[0].affected_scenarios) == ["gen-dup", "gen-refresh", "gen-retry"]
        assert clusters[0].singleton is False
        assert "idempotency" in clusters[0].likely_failure_domain

    def test_distinct_failures_stay_separate(self):
        records = [
            _failure("gen-dup", RiskCategory.IDEMPOTENCY, ["payments row count — got 2, want 1"]),
            _failure("gen-auth", RiskCategory.AUTHORIZATION, ["status == 403 — got 200"]),
        ]

        clusters = cluster_failures(records)

        assert len(clusters) == 2
        assert all(c.singleton for c in clusters)

    def test_one_shared_signal_is_not_enough_to_merge(self):
        # Under-clustering is the safe error: a hidden defect costs far more
        # than a slightly repetitive correction.
        records = [
            _failure("a", RiskCategory.IDEMPOTENCY, ["shared thing", "unique to a"]),
            _failure("b", RiskCategory.CONCURRENCY, ["shared thing", "unique to b"]),
        ]

        assert len(cluster_failures(records)) == 2

    def test_unrelated_risk_families_never_merge_however_similar(self):
        shared = ["identical observation one", "identical observation two"]
        records = [
            _failure("a", RiskCategory.IDEMPOTENCY, shared),
            _failure("b", RiskCategory.AUTHORIZATION, shared),
        ]

        assert len(cluster_failures(records)) == 2

    def test_run_specific_values_are_normalized_away(self):
        first = normalize_signal("invoice INV-4172 paid twice at 2026-08-09T10:00:00 in 1.2s")
        second = normalize_signal("invoice INV-9903 paid twice at 2026-08-09T11:30:00 in 3.4s")
        assert first == second

    def test_a_singleton_is_described_by_its_own_failure_not_a_shared_cause(self):
        clusters = cluster_failures([_failure("a", RiskCategory.BOUNDARY, ["off by one"])])
        assert clusters[0].singleton
        assert "off by one" in clusters[0].likely_failure_domain

    def test_clusters_order_most_blocking_first(self):
        records = [
            FailureRecord(
                scenario_id="low",
                risk_category=RiskCategory.BOUNDARY,
                priority=Priority.P3,
                failed_assertions=["minor"],
            ),
            FailureRecord(
                scenario_id="high",
                risk_category=RiskCategory.SAFETY_INVARIANT,
                priority=Priority.P0,
                failed_assertions=["critical"],
            ),
        ]

        clusters = cluster_failures(records)

        assert clusters[0].affected_scenarios == ["high"]


# --------------------------------------------------------------------------
# Promotion
# --------------------------------------------------------------------------


class TestPromotionCandidates:
    def test_a_scenario_that_found_a_defect_and_now_passes_becomes_a_candidate(self, tmp_path):
        ledger = PromotionLedger(tmp_path)
        memory = DefectMemory()
        memory.note_failure("gen-dup", 1, "payments=2 — the invoice was paid twice")
        planner_plan = _plan_with(make_scenario("gen-dup"))

        recorded = record_promotion_candidates(
            ledger=ledger,
            memory=memory,
            plan=planner_plan,
            outcomes=[_outcome("gen-dup", Outcome.PASSED)],
            iteration=2,
        )

        assert [c.scenario_id for c in recorded] == ["gen-dup"]
        candidate = ledger.load()[0]
        assert candidate.discovered_in_iteration == 1
        assert candidate.fixed_in_iteration == 2
        assert "paid twice" in candidate.bug_discovered
        assert candidate.promoted is False
        assert candidate.scenario["id"] == "gen-dup"

    def test_a_scenario_that_never_failed_is_not_a_candidate(self, tmp_path):
        ledger = PromotionLedger(tmp_path)

        recorded = record_promotion_candidates(
            ledger=ledger,
            memory=DefectMemory(),
            plan=_plan_with(make_scenario("gen-happy")),
            outcomes=[_outcome("gen-happy", Outcome.PASSED)],
            iteration=1,
        )

        assert recorded == []
        assert ledger.load() == []

    def test_a_still_failing_scenario_is_not_a_candidate(self, tmp_path):
        ledger = PromotionLedger(tmp_path)
        memory = DefectMemory()
        memory.note_failure("gen-dup", 1, "still broken")

        recorded = record_promotion_candidates(
            ledger=ledger,
            memory=memory,
            plan=_plan_with(make_scenario("gen-dup")),
            outcomes=[_outcome("gen-dup", Outcome.FAILED)],
            iteration=2,
        )

        assert recorded == []

    def test_a_permanent_scenario_is_never_a_promotion_candidate(self, tmp_path):
        ledger = PromotionLedger(tmp_path)
        memory = DefectMemory()
        memory.note_failure("backend_generic", 1, "broke")

        recorded = record_promotion_candidates(
            ledger=ledger,
            memory=memory,
            plan=_plan_with(),
            outcomes=[_outcome("backend_generic", Outcome.PASSED, origin=Origin.PERMANENT)],
            iteration=2,
        )

        assert recorded == []

    def test_a_candidate_is_recorded_once(self, tmp_path):
        ledger = PromotionLedger(tmp_path)
        memory = DefectMemory()
        memory.note_failure("gen-dup", 1, "boom")
        plan = _plan_with(make_scenario("gen-dup"))

        for iteration in (2, 3):
            record_promotion_candidates(
                ledger=ledger,
                memory=memory,
                plan=plan,
                outcomes=[_outcome("gen-dup", Outcome.PASSED)],
                iteration=iteration,
            )

        assert len(ledger.load()) == 1

    def test_the_first_failure_is_the_one_remembered(self):
        memory = DefectMemory()
        memory.note_failure("gen-dup", 1, "the original observation")
        memory.note_failure("gen-dup", 3, "a later, different observation")

        assert memory.discovered("gen-dup") == (1, "the original observation")


def _plan_with(*scenarios):
    from neyma_product_driver.scenario_plan import GeneratedScenarioPlan

    return GeneratedScenarioPlan(scenarios=list(scenarios))


class TestNoSilentPromotion:
    def test_automatic_promotion_cannot_be_configured(self):
        with pytest.raises(ValueError, match="cannot be disabled"):
            ScenarioGenerationConfig(promotion_requires_approval=False)

    def test_recording_a_candidate_writes_nothing_into_the_scenarios_directory(self, tmp_path):
        scenarios_dir = tmp_path / "scenarios"
        scenarios_dir.mkdir()
        (scenarios_dir / "backend_generic.yaml").write_text("name: backend_generic\n")
        before = {p.name: p.read_text() for p in scenarios_dir.iterdir()}

        memory = DefectMemory()
        memory.note_failure("gen-dup", 1, "boom")
        record_promotion_candidates(
            ledger=PromotionLedger(tmp_path),
            memory=memory,
            plan=_plan_with(make_scenario("gen-dup")),
            outcomes=[_outcome("gen-dup", Outcome.PASSED)],
            iteration=2,
        )

        after = {p.name: p.read_text() for p in scenarios_dir.iterdir()}
        assert after == before

    def test_a_generated_scenario_never_lands_in_the_permanent_suite_during_a_run(self, tmp_path):
        store = EvidenceStore(tmp_path / "runs", "20260809-000000")
        scenarios_dir = tmp_path / "scenarios"
        scenarios_dir.mkdir()
        planner = make_planner(tmp_path, [raw_payload(raw_scenario())], store=store)

        planner.plan_initial(task="t", unit=FakeUnit(), run_id=store.run_id)

        assert list(scenarios_dir.iterdir()) == []
        # Everything generated lives under the run directory instead.
        assert (store.run_dir / "scenario-plan.json").exists()
