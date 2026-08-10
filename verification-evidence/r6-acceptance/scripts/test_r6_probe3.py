"""REVIEWER 6 — round 3: the narrowed-rerun widening branch (cli.py:358),
driven with a planner whose plan is fully controlled."""

from __future__ import annotations

from pathlib import Path

import pytest

from neyma_product_driver.cli import run_control_loop
from neyma_product_driver.config import DriverConfig, ScenarioGenerationConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import AssertionResult, RunState, RunStatus, ScenarioResult
from neyma_product_driver.scenario_plan import GeneratedScenarioPlan, Priority, RiskCategory
from neyma_product_driver.scenarios import Scenario

from scenario_fixtures import make_scenario
from test_r6_false_accept import FakeBuilder, FakeEvaluator, FakeRepoLoader, accept

LOG: list[str] = []


def say(m: str) -> None:
    LOG.append(m)
    print(m)


class FakePlanner:
    """Implements exactly the planner contract run_control_loop uses."""

    def __init__(self, models):
        self.plan = GeneratedScenarioPlan()
        self.plan.scenarios = list(models)
        self.compiled = {m.id: Scenario(name=m.id) for m in models}
        self.expansions = 0

    def plan_initial(self, task, unit, run_id):
        return self.plan

    def refine_for_diff(self, **kw):
        return self.plan

    def expand_after_failures(self, **kw):
        self.expansions += 1
        return self.plan

    def budget_exhausted(self):
        return True


@pytest.fixture
def bits(driver_config: DriverConfig, request):
    driver_config.scenario_generation = ScenarioGenerationConfig(enabled=True)
    store = EvidenceStore(driver_config.runs_dir, f"r6c-{request.node.name[:36]}")
    state = RunState(run_id=store.run_id, task="t", max_iterations=driver_config.max_iterations)
    return driver_config, store, state


def models():
    return [
        make_scenario("gen-a", risk_category=RiskCategory.IDEMPOTENCY, priority=Priority.P0),
        make_scenario("gen-b", risk_category=RiskCategory.AUTHORIZATION, priority=Priority.P0),
        make_scenario("gen-c", risk_category=RiskCategory.RESTART_RECOVERY, priority=Priority.P0),
    ]


class Phase:
    def __init__(self, d, state, failing_by_iter, executed):
        self.service_logs = {}
        self.state = state
        self.failing_by_iter = failing_by_iter
        self.executed = executed

    async def execute(self, scenario):
        key = scenario.name.split(":", 1)[-1]
        self.executed.append(f"i{self.state.iteration}:{key}")
        bad = key in self.failing_by_iter.get(self.state.iteration, set())
        return ScenarioResult(
            scenario_name=scenario.name,
            assertions=[AssertionResult(kind="expect_state", target=key, passed=not bad,
                                        detail="" if not bad else "wrong")],
        )


@pytest.mark.asyncio
async def test_widening_happens_before_accept(bits):
    """i1: gen-a fails -> FIX. i2: narrowed set is green -> must widen and run
    gen-b/gen-c before ACCEPT."""
    config, store, state = bits
    config.max_iterations = 2
    executed: list[str] = []
    planner = FakePlanner(models())

    result = await run_control_loop(
        config=config, scenario=Scenario(name="perm"), store=store, state=state,
        builder=FakeBuilder(), evaluator=FakeEvaluator(),
        make_executor=lambda d: Phase(d, state, {1: {"gen-a"}}, executed),
        emit=lambda _m: None, repo_loader=FakeRepoLoader(), planner=planner,
    )
    i2 = [e.split(":", 1)[1] for e in executed if e.startswith("i2:")]
    say(f"W1 status={result.status.value} full_run={result.suite.full_run}")
    say(f"W1 i2 executed (in order) = {i2}")
    say(f"W1 widened? {'yes' if i2.count('gen-b') else 'NO'} ; "
        f"selection_reason={result.suite.selection_reason!r}")
    say(f"W1 outcomes={[(o.scenario_id, o.outcome.value) for o in result.suite.outcomes]}")


@pytest.mark.asyncio
async def test_widening_catches_a_regression_outside_the_narrowed_set(bits):
    """i1: gen-a fails. i2: gen-a fixed but gen-c (outside the narrowed set)
    is now broken. Does the widened pass catch it, or does ACCEPT slip out?"""
    config, store, state = bits
    config.max_iterations = 2
    executed: list[str] = []
    planner = FakePlanner(models())

    result = await run_control_loop(
        config=config, scenario=Scenario(name="perm"), store=store, state=state,
        builder=FakeBuilder(), evaluator=FakeEvaluator(),
        make_executor=lambda d: Phase(d, state, {1: {"gen-a"}, 2: {"gen-c"}}, executed),
        emit=lambda _m: None, repo_loader=FakeRepoLoader(), planner=planner,
    )
    i2 = [e.split(":", 1)[1] for e in executed if e.startswith("i2:")]
    say(f"W2 status={result.status.value} i2={i2}")
    say(f"W2 outcomes={[(o.scenario_id, o.outcome.value) for o in result.suite.outcomes]}")
    say(f"W2 final decision={result.final_decision.decision.value}")


@pytest.mark.asyncio
async def test_widening_with_budget_zero_skips_everything_and_accepts(bits):
    """The widened pass runs under a spent execution budget: everything is
    SKIPPED, so 'the full required regression set' proves nothing."""
    config, store, state = bits
    config.max_iterations = 2
    config.scenario_generation.execution_budget_s = 0
    executed: list[str] = []
    planner = FakePlanner(models())

    result = await run_control_loop(
        config=config, scenario=Scenario(name="perm"), store=store, state=state,
        builder=FakeBuilder(), evaluator=FakeEvaluator(),
        make_executor=lambda d: Phase(d, state, {}, executed),
        emit=lambda _m: None, repo_loader=FakeRepoLoader(), planner=planner,
    )
    say(f"W3 status={result.status.value} executed={executed}")
    say(f"W3 outcomes={[(o.scenario_id, o.outcome.value) for o in result.suite.outcomes]}")
    say(f"W3 full_run={result.suite.full_run} everything_required_passed={result.suite.everything_required_passed}")


@pytest.mark.asyncio
async def test_all_required_skipped_but_one_generated_passes(bits):
    """The permanent scenario and 2 of 3 generated are skipped (browser);
    a single generated backend case passes; ACCEPT?"""
    config, store, state = bits
    config.max_iterations = 1
    config.run.browser_enabled = False
    executed: list[str] = []
    ms = models()
    planner = FakePlanner(ms)
    # make gen-b and gen-c browser scenarios so they are skipped
    planner.compiled["gen-b"] = Scenario(name="gen-b", mode="browser")
    planner.compiled["gen-c"] = Scenario(name="gen-c", mode="browser")

    result = await run_control_loop(
        config=config, scenario=Scenario(name="perm", mode="browser"), store=store, state=state,
        builder=FakeBuilder(), evaluator=FakeEvaluator(),
        make_executor=lambda d: Phase(d, state, {}, executed),
        emit=lambda _m: None, repo_loader=FakeRepoLoader(), planner=planner,
    )
    say(f"W4 status={result.status.value} executed={executed}")
    say(f"W4 outcomes={[(o.scenario_id, o.outcome.value) for o in result.suite.outcomes]}")


def test_zz_dump():
    dest = Path("/Users/sammyfammy/neyma-product-driver/verification-evidence/r6-acceptance")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "probe3.txt").write_text("\n".join(LOG), encoding="utf-8")
