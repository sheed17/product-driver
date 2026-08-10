"""EXPERIMENT 4 — prompt/evidence growth vectors + real browser flows."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scale_harness import (  # noqa: E402
    EVID, TARGET_DIR, PY, BASE, Decision, EvaluatorDecision, Origin, Priority,
    RiskCategory, GeneratedScenario, Scenario, ScenarioSuite, SuiteEntry,
)

OUT: dict = {}


async def main():
    from scale_harness import (
        build_suite, run_suite, prompt_size, http_scenario, browser_scenario,
        driver_cli,
    )
    from neyma_product_driver.scenarios import ServiceSpec
    from neyma_product_driver.prompts import evaluator_prompt

    # ---- 1. real browser flows -----------------------------------------
    gen = []
    for i in (201, 202, 9):
        c = browser_scenario(i)
        c.name = f"ui-{i}"
        gen.append((GeneratedScenario(id=c.name, title=f"ui {i}",
                                      risk_category=RiskCategory.UI_BACKEND_DISAGREEMENT,
                                      priority=Priority.P0,
                                      requirement_reference="R4"), c))
    suite = build_suite(generated=gen)
    ex, result, wall = await run_suite(suite, EVID / "growth-browser", browser=True)
    files = {
        o.scenario_id: sorted(p.name for p in Path(o.evidence_path).rglob("*") if p.is_file())
        for o in result.outcomes
    }
    OUT["1_browser_flows"] = {
        "wall_s": round(wall, 2),
        "outcomes": {o.scenario_id: o.outcome.value for o in result.outcomes},
        "per_case_evidence_files": files,
        "note": "item 9 has the injected wrong-owner defect; ui-9 must FAIL",
    }

    # ---- 2. per-case evidence for backend scenarios ---------------------
    suite2 = build_suite(generated=[
        (GeneratedScenario(id=f"be-{i}", title="x", risk_category=RiskCategory.CROSS_TENANT,
                           priority=Priority.P0), http_scenario(200 + i))
        for i in range(5)
    ])
    ex2, r2, _ = await run_suite(suite2, EVID / "growth-backend-evidence")
    OUT["2_backend_per_case_evidence"] = {
        o.scenario_id: {
            "evidence_path": o.evidence_path,
            "exists": Path(o.evidence_path).exists(),
            "file_count": len([p for p in Path(o.evidence_path).rglob("*") if p.is_file()]),
        }
        for o in r2.outcomes
    }

    # ---- 3. service_logs accumulation across a suite ---------------------
    # Each scenario declares its OWN service name. SuiteExecutor.service_logs
    # is a dict .update()d per scenario, so it grows with the suite.
    chatty = TARGET_DIR / "chatty.py"
    chatty.write_text(
        "import sys,time\n"
        "for i in range(4000): print('service log line %05d %s' % (i,'y'*60), flush=True)\n"
        "time.sleep(30)\n"
    )
    curves = {}
    for n in (5, 20, 50):
        gen3 = []
        for i in range(n):
            s = Scenario(
                name=f"svc-{i:03d}", mode="backend", app_url=BASE,
                services=[ServiceSpec(name=f"worker-{i:03d}", command=f"{PY} {chatty}")],
                readiness=[{"http": f"{BASE}/health", "expect_status": 200}],
                requests=[],
            )
            gen3.append((GeneratedScenario(id=s.name, title="x",
                                           risk_category=RiskCategory.SERVICE_UNAVAILABLE,
                                           priority=Priority.P0), s))
        suite3 = build_suite(generated=gen3)
        ex3, r3, w3 = await run_suite(suite3, EVID / f"growth-svc-{n}")
        primary = next(iter(ex3.results.values()))
        prompt = evaluator_prompt(
            task="t", iteration=1, max_iterations=3, builder_summary="b", git=None,
            scenario=primary, service_logs=ex3.service_logs, evidence_dir=str(EVID),
            suite=r3,
        )
        curves[n] = {
            "distinct_service_log_keys": len(ex3.service_logs),
            "service_log_total_chars": sum(len(v) for v in ex3.service_logs.values()),
            "summary_block_chars": len(r3.summary_block()),
            "evaluator_prompt_chars": len(prompt),
            "evaluator_prompt_est_tokens": len(prompt) // 4,
            "wall_s": round(w3, 2),
        }
        (EVID / f"growth-svc-prompt-{n}.txt").write_text(prompt)
    OUT["3_service_log_growth"] = curves

    # ---- 4. decision payload size with 100 blocking failures -------------
    gen4 = []
    for i in range(1, 101):
        s = http_scenario(i)
        s.requests[0].expect_contains = [f'"owner": "tenant-{i}"']
        gen4.append((GeneratedScenario(id=s.name, title="x",
                                       risk_category=RiskCategory.CROSS_TENANT,
                                       priority=Priority.P0,
                                       requirement_reference="R4"), s))
    suite4 = build_suite(generated=gen4)
    ex4, r4, _ = await run_suite(suite4, EVID / "growth-100fail")
    accept = EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9)
    fix = driver_cli._apply_suite_precedence(r4, accept, "x", lambda _m: None)
    payload = fix.model_dump_json()
    (EVID / "growth-100fail-decision.json").write_text(payload)
    OUT["4_decision_with_100_failures"] = {
        "decision": fix.decision.value,
        "problems_count": len(fix.problems),
        "evidence_paths_count": len(fix.evidence_paths),
        "correction_prompt_chars": len(fix.correction_prompt),
        "decision_json_chars": len(payload),
        "decision_est_tokens": len(payload) // 4,
        "all_100_named_in_correction": sum(
            1 for o in r4.outcomes if o.scenario_id in fix.correction_prompt
        ),
    }

    (EVID / "growth.json").write_text(json.dumps(OUT, indent=2, default=str))
    print(json.dumps(OUT, indent=2, default=str))


asyncio.run(main())
