"""EXPERIMENT 5 — measure the two unbounded prompt-growth vectors precisely."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scale_harness import (  # noqa: E402
    EVID, Decision, EvaluatorDecision, Priority, RiskCategory,
    GeneratedScenario, build_suite, run_suite, http_scenario, driver_cli,
)
from neyma_product_driver.prompts import evaluator_prompt, render_correction_for_builder  # noqa

OUT: dict = {}


async def main():
    # --- vector A: service_logs, one entry per scenario, each truncated to 8000
    base = await run_suite(build_suite(generated=[(
        GeneratedScenario(id="one", title="x", risk_category=RiskCategory.BOUNDARY,
                          priority=Priority.P0), http_scenario(200))]),
        EVID / "g2-base")
    ex, result, _ = base
    primary = next(iter(ex.results.values()))

    rows = []
    for n in (0, 1, 10, 50, 100):
        logs = {f"worker-{i:03d}": ("log line filler " * 1200) for i in range(n)}
        p = evaluator_prompt(task="t", iteration=1, max_iterations=3, builder_summary="b",
                             git=None, scenario=primary, service_logs=logs,
                             evidence_dir=str(EVID), suite=result)
        rows.append({"distinct_services": n, "prompt_chars": len(p),
                     "est_tokens": len(p) // 4})
    OUT["A_service_log_prompt_growth"] = {
        "note": "prompts.py:490-493 emits every service_logs entry, tail-truncated to "
                "8000 chars each; SuiteExecutor.service_logs accumulates one entry per "
                "distinct service name across the whole suite (50 distinct keys observed "
                "in a real 50-scenario run).",
        "rows": rows,
    }

    # --- vector B: failure count -> summary_block / prompt / correction
    rows_b = []
    for n in (1, 5, 25, 50, 100):
        gen = []
        for i in range(1, 101):
            s = http_scenario(i)
            if i <= n:
                s.requests[0].expect_contains = [f'"owner": "tenant-{i}"']
            gen.append((GeneratedScenario(
                id=s.name, title="x", risk_category=RiskCategory.CROSS_TENANT,
                priority=Priority.P0, requirement_reference="R4"), s))
        suite = build_suite(generated=gen)
        ex2, r2, wall = await run_suite(suite, EVID / f"g2-fail-{n}")
        summary = r2.summary_block()
        p = evaluator_prompt(task="t", iteration=1, max_iterations=3, builder_summary="b",
                             git=None, scenario=next(iter(ex2.results.values())),
                             service_logs=None, evidence_dir=str(EVID), suite=r2)
        accept = EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9)
        fix = driver_cli._apply_suite_precedence(r2, accept, "x", lambda _m: None)
        builder_prompt = render_correction_for_builder(fix)
        named = sum(1 for o in r2.failures() if o.scenario_id in summary)
        rows_b.append({
            "scenarios": r2.total, "failed": r2.failed, "wall_s": round(wall, 2),
            "summary_block_chars": len(summary),
            "evaluator_prompt_chars": len(p),
            "evaluator_prompt_est_tokens": len(p) // 4,
            "builder_correction_chars": len(builder_prompt),
            "builder_correction_est_tokens": len(builder_prompt) // 4,
            "failures_named_in_summary": f"{named}/{r2.failed}",
            "decision": fix.decision.value,
        })
    OUT["B_failure_count_growth"] = rows_b

    (EVID / "growth2.json").write_text(json.dumps(OUT, indent=2, default=str))
    print(json.dumps(OUT, indent=2, default=str))


asyncio.run(main())
