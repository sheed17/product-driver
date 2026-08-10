"""EXPERIMENT 2 — adversarial: budgets, skips, gate holes, ID collisions, races."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scale_harness import (  # noqa: E402
    EVID, Decision, EvaluatorDecision, Origin, Outcome, Priority, RiskCategory,
    GeneratedScenario, Scenario, ScenarioSuite, SuiteEntry, build_suite,
    make_suite, run_suite, prompt_size, select_rerun, driver_cli,
    http_scenario, command_scenario, idempotency_scenario, browser_scenario,
)

OUT: dict = {}


def precedence(result, name="permanent-health"):
    accept = EvaluatorDecision(decision=Decision.ACCEPT, summary="looks good", confidence=0.9)
    msgs: list[str] = []
    d = driver_cli._apply_suite_precedence(result, accept, name, msgs.append)
    return d.decision.value, msgs


async def t_budget_skip():
    """A tiny execution budget must not let an ACCEPT through on 3 of 60 cases."""
    suite = make_suite(60)
    ex, result, wall = await run_suite(suite, EVID / "adv-budget", budget=0.4)
    decision, msgs = precedence(result)
    return {
        "suite_size": len(suite),
        "executed_not_skipped": result.total - result.skipped,
        "skipped": result.skipped,
        "passed": result.passed,
        "failed": result.failed,
        "full_run_flag": result.full_run,
        "blocking_failures": len(result.blocking_failures()),
        "everything_required_passed": result.everything_required_passed,
        "ACCEPT_becomes": decision,
        "precedence_messages": msgs,
        "summary_block_chars": len(result.summary_block()),
        "summary_head": result.summary_block()[:400],
    }


async def t_browser_disabled_skip():
    """Every browser scenario skipped for lack of a browser: does ACCEPT survive?"""
    from scale_harness import BrowserSpec, BrowserStep, GeneratedScenario as GS
    generated = []
    for i in range(1, 21):
        compiled = browser_scenario(i)
        generated.append((GS(
            id=compiled.name, title=f"ui {i}", risk_category=RiskCategory.UI_BACKEND_DISAGREEMENT,
            priority=Priority.P0, requirement_reference="R4"), compiled))
    suite = build_suite(permanent=[("permanent-health", http_scenario(1))], generated=generated)
    ex, result, wall = await run_suite(suite, EVID / "adv-nobrowser", browser=False)
    decision, msgs = precedence(result)
    return {
        "suite_size": len(suite),
        "skipped": result.skipped,
        "passed": result.passed,
        "failed": result.failed,
        "full_run_flag": result.full_run,
        "blocking_failures": len(result.blocking_failures()),
        "ACCEPT_becomes": decision,
        "precedence_messages": msgs,
    }


async def t_all_fail_prompt_growth():
    """100 cases, every one failing: how large does the evaluator prompt get?"""
    generated = []
    for i in range(1, 101):
        s = http_scenario(i)
        # expect something the target never returns -> every case fails
        s.requests[0].expect_contains = [f'"owner": "tenant-{i}"']
        s.forbidden = ["tenant-a"]
        generated.append((GeneratedScenario(
            id=s.name, title=f"t{i}", risk_category=RiskCategory.CROSS_TENANT,
            priority=Priority.P0, requirement_reference="R4",
            rationale="x" * 200), s))
    suite = build_suite(generated=generated)
    ex, result, wall = await run_suite(suite, EVID / "adv-allfail")
    summary = result.summary_block()
    primary = next(iter(ex.results.values()))
    prompt = prompt_size(result, primary)
    (EVID / "adv-allfail-summary.txt").write_text(summary)
    (EVID / "adv-allfail-prompt.txt").write_text(prompt)
    corr = driver_cli._suite_correction(result)
    (EVID / "adv-allfail-correction.txt").write_text(corr)
    return {
        "failed": result.failed,
        "clusters": len(result.clusters),
        "grouped": len([c for c in result.clusters if not c.singleton]),
        "summary_block_chars": len(summary),
        "evaluator_prompt_chars": len(prompt),
        "evaluator_prompt_est_tokens": len(prompt) // 4,
        "correction_prompt_chars": len(corr),
        "correction_est_tokens": len(corr) // 4,
    }


async def t_duplicate_ids():
    """Two distinct scenarios claiming the same id."""
    a = http_scenario(1)
    b = http_scenario(2)
    b.name = "case-001-http"  # collide deliberately
    gen = [
        (GeneratedScenario(id="dup-id", title="a", risk_category=RiskCategory.BOUNDARY,
                           priority=Priority.P0), a),
        (GeneratedScenario(id="dup-id", title="b", risk_category=RiskCategory.BOUNDARY,
                           priority=Priority.P0), b),
    ]
    suite = build_suite(generated=gen)
    ex, result, wall = await run_suite(suite, EVID / "adv-dup")
    return {
        "entries_offered": 2,
        "entries_in_suite": len(suite),
        "outcomes": result.total,
        "results_dict_size": len(ex.results),
        "silently_dropped": 2 - len(suite),
        "any_warning": "none — build_suite/ScenarioSuite.add drop silently",
    }


def t_id_truncation():
    """GeneratedScenario._safe_id truncates to 64 chars: do long ids collide?"""
    long_a = "x" * 60 + "-persistence-after-restart"
    long_b = "x" * 60 + "-persistence-after-crash"
    a = GeneratedScenario(id=long_a, title="a", risk_category=RiskCategory.BOUNDARY)
    b = GeneratedScenario(id=long_b, title="b", risk_category=RiskCategory.BOUNDARY)
    suite = build_suite(generated=[(a, http_scenario(1)), (b, http_scenario(2))])
    return {
        "input_ids_distinct": long_a != long_b,
        "sanitised_a": a.id,
        "sanitised_b": b.id,
        "collide_after_truncation": a.id == b.id,
        "entries_in_suite": len(suite),
        "second_scenario_dropped": len(suite) == 1,
    }


async def t_narrowed_rerun():
    """A narrowed rerun that goes green must not be accepted on its own."""
    suite = make_suite(20)
    ex1, first, _ = await run_suite(suite, EVID / "adv-rerun-1")
    only, reason = select_rerun(suite, first)
    ex2, second, _ = await run_suite(suite, EVID / "adv-rerun-2", only=only, reason=reason)
    d_first, _ = precedence(first)
    d_second, msgs = precedence(second)
    return {
        "first_pass_failed": first.failed,
        "first_ACCEPT_becomes": d_first,
        "rerun_selected": len(only),
        "rerun_of_total": len(suite),
        "rerun_reason": reason,
        "rerun_full_run_flag": second.full_run,
        "rerun_failed": second.failed,
        "rerun_ACCEPT_becomes": d_second,
        "rerun_messages": msgs,
    }


async def t_dependency_cascade():
    """A failure must skip only its declared dependants, never unrelated cases."""
    suite = ScenarioSuite()
    bad = http_scenario(9)   # injected BAD id -> fails
    good = http_scenario(3)
    dep = http_scenario(6)
    dep.name = "dependant"
    unrelated = http_scenario(12)
    unrelated.name = "unrelated"
    for sid, sc, deps in (
        ("leader", bad, []),
        ("dependant", dep, ["leader"]),
        ("unrelated", unrelated, []),
        ("other", good, []),
    ):
        suite.add(SuiteEntry(scenario_id=sid, scenario=sc, origin=Origin.GENERATED,
                             priority=Priority.P0, depends_on=deps))
    ex, result, _ = await run_suite(suite, EVID / "adv-deps")
    return {
        o.scenario_id: {"outcome": o.outcome.value, "skip_reason": o.skip_reason}
        for o in result.outcomes
    }


async def t_one_pass_cannot_override():
    """Interleave 1 fail among 99 passes at the END; confirm nothing overrides it."""
    suite = make_suite(100)
    ex, result, _ = await run_suite(suite, EVID / "adv-late-fail")
    late = result.by_id("case-087-http")
    summary = result.summary_block()
    d, _ = precedence(result)
    return {
        "case_087_outcome": late.outcome.value if late else None,
        "case_087_blocks": late.blocks_acceptance if late else None,
        "case_087_named_in_summary": "case-087-http" in summary,
        "position_in_summary_pct": round(summary.index("case-087-http") / len(summary) * 100, 1)
        if "case-087-http" in summary else None,
        "everything_required_passed": result.everything_required_passed,
        "ACCEPT_becomes": d,
    }


def t_max_parallel_config():
    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.scenario_suite import SuiteExecutor
    out = {}
    try:
        ScenarioGenerationConfig(max_parallel=4)
        out["config_rejects_parallel_4"] = False
    except Exception as exc:
        out["config_rejects_parallel_4"] = True
        out["config_error"] = str(exc)[:120]
    ex = SuiteExecutor(make_executor=lambda p: None, artifact_root=EVID, max_parallel=32)
    out["SuiteExecutor_accepts_max_parallel_32"] = ex.max_parallel
    import inspect
    src = inspect.getsource(SuiteExecutor.run)
    out["run_references_max_parallel"] = "max_parallel" in src
    return out


async def main():
    OUT["1_budget_exhaustion"] = await t_budget_skip()
    OUT["2_browser_disabled_skip"] = await t_browser_disabled_skip()
    OUT["3_all_100_fail_prompt_growth"] = await t_all_fail_prompt_growth()
    OUT["4_duplicate_scenario_ids"] = await t_duplicate_ids()
    OUT["5_id_truncation_collision"] = t_id_truncation()
    OUT["6_narrowed_rerun"] = await t_narrowed_rerun()
    OUT["7_dependency_cascade"] = await t_dependency_cascade()
    OUT["8_late_failure_visibility"] = await t_one_pass_cannot_override()
    OUT["9_max_parallel"] = t_max_parallel_config()
    (EVID / "adversarial.json").write_text(json.dumps(OUT, indent=2, default=str))
    print(json.dumps(OUT, indent=2, default=str))


asyncio.run(main())
