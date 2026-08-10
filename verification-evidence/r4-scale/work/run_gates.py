"""EXPERIMENT 3 — isolate the acceptance-gate behaviour for SKIPPED scenarios."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scale_harness import (  # noqa: E402
    EVID, Decision, EvaluatorDecision, Origin, Outcome, Priority, RiskCategory,
    GeneratedScenario, ScenarioSuite, SuiteEntry, build_suite,
    make_suite, run_suite, select_rerun, driver_cli,
    http_scenario, browser_scenario, Scenario, RequestSpec,
)

OUT: dict = {}


def precedence(result):
    accept = EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9)
    msgs: list[str] = []
    d = driver_cli._apply_suite_precedence(result, accept, "permanent-health", msgs.append)
    return d.decision.value, msgs, d


def clean_suite(n: int) -> ScenarioSuite:
    """n cases that all pass (ids chosen to avoid every injected defect)."""
    gen = []
    for i in range(1, n + 1):
        item = 100 + i * 3  # 103,106,... none are 9/33/5/44/87
        s = http_scenario(item)
        s.name = f"clean-{i:03d}"
        gen.append((GeneratedScenario(
            id=s.name, title=f"clean {i}", risk_category=RiskCategory.CROSS_TENANT,
            priority=Priority.P0, requirement_reference="R4"), s))
    return build_suite(permanent=[("permanent-health", http_scenario(200))], generated=gen)


async def t_budget_all_green():
    """Budget exhausted, everything that DID run passed. Does ACCEPT survive?"""
    suite = clean_suite(60)
    ex, result, _ = await run_suite(suite, EVID / "gate-budget-green", budget=0.05)
    d, msgs, full = precedence(result)
    (EVID / "gate-budget-green-summary.txt").write_text(result.summary_block())
    return {
        "suite_size": len(suite),
        "actually_executed": result.total - result.skipped,
        "skipped_for_budget": result.skipped,
        "passed": result.passed,
        "failed": result.failed,
        "blocked": result.blocked,
        "full_run_flag": result.full_run,
        "everything_required_passed": result.everything_required_passed,
        "blocking_failures": len(result.blocking_failures()),
        "ACCEPT_becomes": d,
        "messages": msgs,
        "VERDICT": ("GATE HOLE: accepted after running "
                    f"{result.total - result.skipped}/{len(suite)}"
                    if d == "ACCEPT" else "gate held"),
    }


async def t_browser_all_green():
    """Every browser case skipped (no browser). Does ACCEPT survive?"""
    gen = []
    for i in range(1, 31):
        c = browser_scenario(200 + i)
        c.name = f"ui-{i:03d}"
        gen.append((GeneratedScenario(
            id=c.name, title=f"ui {i}", risk_category=RiskCategory.UI_BACKEND_DISAGREEMENT,
            priority=Priority.P0, requirement_reference="R4"), c))
    suite = build_suite(permanent=[("permanent-health", http_scenario(200))], generated=gen)
    ex, result, _ = await run_suite(suite, EVID / "gate-nobrowser", browser=False)
    d, msgs, _ = precedence(result)
    (EVID / "gate-nobrowser-summary.txt").write_text(result.summary_block())
    return {
        "suite_size": len(suite),
        "skipped": result.skipped,
        "passed": result.passed,
        "full_run_flag": result.full_run,
        "everything_required_passed": result.everything_required_passed,
        "ACCEPT_becomes": d,
        "messages": msgs,
        "VERDICT": ("GATE HOLE: accepted with 30/31 required P0 scenarios never run"
                    if d == "ACCEPT" else "gate held"),
    }


async def t_dependency_skip_gate():
    """A P0 failure skips its dependants; do those skipped P0s still gate?"""
    suite = ScenarioSuite()
    bad = http_scenario(9)
    suite.add(SuiteEntry(scenario_id="leader", scenario=bad, origin=Origin.GENERATED,
                         priority=Priority.P0))
    for k in range(5):
        d = http_scenario(200 + k)
        suite.add(SuiteEntry(scenario_id=f"dep-{k}", scenario=d, origin=Origin.GENERATED,
                             priority=Priority.P0, depends_on=["leader"]))
    ex, result, _ = await run_suite(suite, EVID / "gate-depskip")
    d, msgs, _ = precedence(result)
    return {
        "failed": result.failed, "skipped": result.skipped,
        "blocking_failures": len(result.blocking_failures()),
        "ACCEPT_becomes": d,
    }


async def t_narrowed_green():
    """A narrowed rerun that goes fully green must not be accepted alone."""
    suite = clean_suite(20)
    ex1, first, _ = await run_suite(suite, EVID / "gate-narrow-1")
    only = [e.scenario_id for e in suite.entries][:5]
    ex2, second, _ = await run_suite(suite, EVID / "gate-narrow-2", only=only,
                                     reason="narrowed to 5")
    d, msgs, dec = precedence(second)
    return {
        "narrowed_to": second.total, "of": len(suite),
        "failed": second.failed, "full_run_flag": second.full_run,
        "ACCEPT_becomes": d, "messages": msgs,
        "evidence_paths_in_decision": len(dec.evidence_paths),
    }


async def t_blocked_vs_failed():
    """Readiness failure -> BLOCKED, distinct from an assertion FAILURE."""
    dead = Scenario(
        name="dead-service", mode="backend", app_url="http://127.0.0.1:9",
        readiness=[{"http": "http://127.0.0.1:9/health", "expect_status": 200}],
        requests=[RequestSpec(method="GET", path="/health", expect_status=200)],
    )
    suite = ScenarioSuite()
    suite.add(SuiteEntry(scenario_id="blocked-one", scenario=dead,
                         origin=Origin.GENERATED, priority=Priority.P0))
    suite.add(SuiteEntry(scenario_id="failed-one", scenario=http_scenario(9),
                         origin=Origin.GENERATED, priority=Priority.P0))
    suite.add(SuiteEntry(scenario_id="passed-one", scenario=http_scenario(200),
                         origin=Origin.GENERATED, priority=Priority.P0))
    ex, result, _ = await run_suite(suite, EVID / "gate-blocked")
    d, _, _ = precedence(result)
    return {
        "outcomes": {o.scenario_id: o.outcome.value for o in result.outcomes},
        "blocked_count": result.blocked, "failed_count": result.failed,
        "blocked_separately_reported": result.blocked == 1 and result.failed == 1,
        "both_block_acceptance": len(result.blocking_failures()) == 2,
        "ACCEPT_becomes": d,
    }


async def t_low_priority_failure():
    """A required P2 failure: reported, but does it gate?"""
    suite = ScenarioSuite()
    suite.add(SuiteEntry(scenario_id="p2-fail", scenario=http_scenario(9),
                         origin=Origin.GENERATED, priority=Priority.P2, required=True))
    suite.add(SuiteEntry(scenario_id="p0-pass", scenario=http_scenario(200),
                         origin=Origin.GENERATED, priority=Priority.P0))
    ex, result, _ = await run_suite(suite, EVID / "gate-p2")
    d, _, _ = precedence(result)
    return {
        "failed": result.failed,
        "in_summary": "p2-fail" in result.summary_block(),
        "blocking_failures": len(result.blocking_failures()),
        "ACCEPT_becomes": d,
        "note": "required=True but priority P2 -> blocks_acceptance is False",
    }


def t_evidence_dir_collision():
    from neyma_product_driver.evidence import sanitize_filename
    pairs = [("a/b", "a-b"), ("case 1", "case_1"), ("Case-1", "case-1")]
    return {f"{a!r} vs {b!r}": {"a": sanitize_filename(a), "b": sanitize_filename(b),
                                "collide": sanitize_filename(a) == sanitize_filename(b)}
            for a, b in pairs}


async def main():
    OUT["A_budget_exhausted_all_green"] = await t_budget_all_green()
    OUT["B_browser_disabled_all_skipped"] = await t_browser_all_green()
    OUT["C_dependency_skip_gate"] = await t_dependency_skip_gate()
    OUT["D_narrowed_green_rerun"] = await t_narrowed_green()
    OUT["E_blocked_vs_failed"] = await t_blocked_vs_failed()
    OUT["F_required_P2_failure"] = await t_low_priority_failure()
    OUT["G_evidence_dir_sanitisation"] = t_evidence_dir_collision()
    (EVID / "gates.json").write_text(json.dumps(OUT, indent=2, default=str))
    print(json.dumps(OUT, indent=2, default=str))


asyncio.run(main())
