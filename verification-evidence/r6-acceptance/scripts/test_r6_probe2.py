"""REVIEWER 6 — round 2: narrowed-rerun widening, skip semantics, full_run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neyma_product_driver.cli import _apply_suite_precedence, run_control_loop
from neyma_product_driver.config import DriverConfig, ScenarioGenerationConfig
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
    ScenarioSuite,
    SuiteEntry,
    SuiteExecutor,
    SuiteResult,
    build_suite,
    select_rerun,
)
from neyma_product_driver.scenarios import Scenario, ServiceSpec

from scenario_fixtures import FakeFounder, base_scenario, raw_payload, raw_scenario, ScriptedReasoner
from test_r6_false_accept import (
    FakeBuilder,
    FakeEvaluator,
    FakeRepoLoader,
    ScriptedExecutor,
    accept,
    make_planner,
)

LOG: list[str] = []


def say(msg: str) -> None:
    LOG.append(msg)
    print(msg)


@pytest.fixture
def loop_bits(driver_config: DriverConfig, request):
    store = EvidenceStore(driver_config.runs_dir, f"r6b-{request.node.name[:40]}")
    state = RunState(run_id=store.run_id, task="build approval", max_iterations=driver_config.max_iterations)
    return driver_config, store, state


# --------------------------------------------------------------------------
# P1: does the narrowed rerun actually widen?
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p1_narrowed_rerun_detail(loop_bits):
    config, store, state = loop_bits
    config.max_iterations = 2
    planner = make_planner(
        config,
        store,
        [
            raw_payload(
                raw_scenario("gen-a", risk_category="idempotency"),
                raw_scenario("gen-b", risk_category="persistence",
                             state_checks=[{"name": "restart", "command": "./probe.sh payments",
                                            "contains": ["survived"]}]),
                raw_scenario("gen-c", risk_category="authorization",
                             state_checks=[{"name": "authz", "command": "./probe.sh payments",
                                            "contains": ["denied"]}]),
            ),
            None,
        ],
    )

    executed: list[str] = []

    class Phase(ScriptedExecutor):
        async def execute(self, scenario):
            key = scenario.name.split(":", 1)[-1]
            executed.append(f"i{state.iteration}:{key}")
            failing = state.iteration == 1 and key == "gen-a"
            return ScenarioResult(
                scenario_name=scenario.name,
                assertions=[AssertionResult(kind="expect_state", target=key, passed=not failing,
                                            detail="" if not failing else "got 2, want 1")],
            )

    result = await run_control_loop(
        config=config, scenario=base_scenario(), store=store, state=state,
        builder=FakeBuilder(), evaluator=FakeEvaluator(),
        make_executor=lambda d: Phase(d, {}, []), emit=lambda _m: None,
        repo_loader=FakeRepoLoader(), planner=planner,
    )
    say(f"P1 suite entries compiled = {[s.id for s in planner.plan.scenarios]} "
        f"compiled={sorted(planner.compiled)}")
    say(f"P1 executed = {executed}")
    say(f"P1 status={result.status.value} full_run={result.suite.full_run} "
        f"reason={result.suite.selection_reason!r}")
    say(f"P1 outcomes={[(o.scenario_id, o.outcome.value) for o in result.suite.outcomes]}")


def test_p2_select_rerun_and_full_run_semantics():
    """Unit-level: what does select_rerun narrow to, and is full_run honest?"""
    suite = build_suite(
        permanent=[("perm", Scenario(name="perm"))],
        generated=[],
    )
    for sid, cat in (("gen-a", RiskCategory.IDEMPOTENCY), ("gen-b", RiskCategory.AUTHORIZATION)):
        suite.add(SuiteEntry(scenario_id=sid, scenario=Scenario(name=sid), origin=Origin.GENERATED,
                             priority=Priority.P0, risk_category=cat, required=True))
    prev = SuiteResult(outcomes=[
        ScenarioOutcome(scenario_id="perm", scenario_name="perm", origin=Origin.PERMANENT,
                        outcome=Outcome.PASSED, priority=Priority.P0),
        ScenarioOutcome(scenario_id="gen-a", scenario_name="gen-a", origin=Origin.GENERATED,
                        outcome=Outcome.FAILED, priority=Priority.P0, risk_category="idempotency"),
        ScenarioOutcome(scenario_id="gen-b", scenario_name="gen-b", origin=Origin.GENERATED,
                        outcome=Outcome.PASSED, priority=Priority.P0, risk_category="authorization"),
    ])
    only, reason = select_rerun(suite, prev)
    say(f"P2 select_rerun -> {only} ({reason})")

    # Now: what full_run does a run with only=that produce?
    order = suite.execution_order(only)
    say(f"P2 order for only -> {[e.scenario_id for e in order]}; len(suite)={len(suite)}")
    say(f"P2 full_run would be: {only is None or len(order) == len(suite)}")


@pytest.mark.asyncio
async def test_p3_full_run_true_while_everything_skipped():
    """A 'full' run in which nothing at all executed still reports full_run=True."""
    suite = build_suite(permanent=[("perm", Scenario(name="perm", mode="browser"))])
    ex = SuiteExecutor(make_executor=lambda d: None, artifact_root=Path("/tmp/r6"),
                       browser_enabled=False)
    res = await ex.run(suite, only=None, selection_reason="full")
    say(f"P3 full_run={res.full_run} outcomes={[(o.scenario_id, o.outcome.value) for o in res.outcomes]} "
        f"everything_required_passed={res.everything_required_passed} "
        f"blocking={res.blocking_failures()}")
    d = _apply_suite_precedence(res, accept(), "perm", lambda _m: None)
    say(f"P3 decision after precedence = {d.decision.value}")
    say(f"P3 summary_block:\n{res.summary_block()}")


def test_p4_widening_branch_condition():
    """cli.py:358 widens only when everything_required_passed. A narrowed run
    with a blocking failure never widens — check what happens then."""
    suite = SuiteResult(
        full_run=False,
        selection_reason="narrowed",
        outcomes=[ScenarioOutcome(scenario_id="gen-a", scenario_name="gen-a",
                                  origin=Origin.GENERATED, outcome=Outcome.FAILED,
                                  priority=Priority.P0)],
    )
    d = _apply_suite_precedence(suite, accept(), "perm", lambda _m: None)
    say(f"P4 narrowed+failing -> {d.decision.value}")


@pytest.mark.asyncio
async def test_p5_permanent_skipped_in_widened_pass(driver_config, request):
    """The exact case-8 shape, with the decision path spelled out."""
    config = driver_config
    store = EvidenceStore(config.runs_dir, "r6b-p5")
    state = RunState(run_id=store.run_id, task="t", max_iterations=1)
    config.run.browser_enabled = False
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-ok"))])
    scenario = Scenario(name="browser_generic", mode="browser", app_url="http://127.0.0.1:8931",
                        services=[ServiceSpec(name="api", command="./serve.sh")])
    result = await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=FakeBuilder(), evaluator=FakeEvaluator(),
        make_executor=lambda d: ScriptedExecutor(d, {"gen-ok": {"pass": True}}, []),
        emit=lambda _m: None, repo_loader=FakeRepoLoader(), planner=planner,
    )
    say(f"P5 status={result.status.value}")
    say(f"P5 outcomes={[(o.scenario_id, o.outcome.value, o.priority.value, o.required, o.blocks_acceptance) for o in result.suite.outcomes]}")
    say(f"P5 everything_required_passed={result.suite.everything_required_passed} full_run={result.suite.full_run}")
    say(f"P5 primary scenario result recorded: passed={result.state.iterations[-1].scenario.passed} "
        f"error={result.state.iterations[-1].scenario.error!r}")
    say(f"P5 suite summary_block:\n{result.suite.summary_block()}")


def test_p6_validation_rejects_assertionless(loop_bits):
    """Does plan-level validation stop a scenario that asserts nothing?"""
    from neyma_product_driver.scenario_validation import validate_scenario
    from scenario_fixtures import make_scenario, validation_context

    s = make_scenario("gen-empty", state_checks=[], **{"expected_observations": [],
                                                       "forbidden_observations": []})
    verdict = validate_scenario(s, validation_context())
    say(f"P6 assertionless scenario verdict = {verdict}")


def test_zz_dump():
    dest = Path("/Users/sammyfammy/neyma-product-driver/verification-evidence/r6-acceptance")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "probe2.txt").write_text("\n".join(LOG), encoding="utf-8")
