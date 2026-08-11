"""ADJ-G: how the clean path differs from the resume path.

A separate reviewer reported that on the clean (non-resume) path a narrowed
approved-command list "correctly drops scenarios with full reasons and
compiled_ids: []". This measures exactly what "correctly" buys: whether the
refusal is *recorded*, and whether it is *visible to evaluate_gate*.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    driver = Path(args.driver).resolve()
    sys.path.insert(0, str(driver))
    sys.path.insert(0, str(driver / "tests"))
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.scenario_planner import ScenarioPlanner
    from neyma_product_driver.scenarios import Scenario, ServiceSpec

    from scenario_fixtures import (
        APPROVED_SETUP,
        APPROVED_STATE,
        FakeFounder,
        FakeUnit,
        ScriptedReasoner,
        raw_payload,
        raw_scenario,
    )

    root = out / "g01clean"
    if root.exists():
        shutil.rmtree(root)

    props = []
    for i in range(6):
        p = raw_scenario(f"gen-clean-{i}")
        p["title"] = f"clean case {i}"
        p["actions"][0]["request"]["path"] = f"/invoices/{i}/approve"
        props.append(p)

    # The narrowed repository: no teardown anywhere, so './probe.sh reset' —
    # which every one of these scenarios uses for cleanup — is not approved.
    narrowed = Scenario(
        name="backend_generic",
        mode="backend",
        setup=[APPROVED_SETUP],
        services=[ServiceSpec(name="api", command="./serve.sh")],
        readiness=[{"tcp": "127.0.0.1:8931"}],
        app_url="http://127.0.0.1:8931",
        commands=[{"name": "smoke", "run": APPROVED_STATE}],
        expect_state=[{"name": "payments", "command": APPROVED_STATE, "contains": ["ok"]}],
    )

    planner = ScenarioPlanner(
        repo=root / "repo",
        config=ScenarioGenerationConfig(enabled=True, max_initial_scenarios=12),
        reasoner=ScriptedReasoner([raw_payload(*props)]),
        store=EvidenceStore(root / "runs", "run-1"),
        base_scenario=narrowed,
        permanent_scenarios=[narrowed],
        founder=FakeFounder(),
        emit=lambda _m: None,
    )
    planner.plan_initial(task="t", unit=FakeUnit(), run_id="run-1")
    planner.persist()
    wave = json.loads(
        (root / "runs" / "run-1" / "scenario-generation" / "wave-01.json").read_text()
    )
    report = {
        "admitted": len(planner.plan.scenarios),
        "compiled_ids": sorted(planner.compiled),
        "wave_proposed": wave["proposed"],
        "wave_accepted_ids": wave["accepted_ids"],
        "wave_rejected_count": len(wave["rejected"]),
        "wave_rejected_first_reason": wave["rejected"][0]["reasons"][0][:160]
        if wave["rejected"]
        else "",
        "risks_still_in_plan": [r.risk_category for r in planner.plan.risks],
        "coverage_summary_uncovered": json.loads(
            (root / "runs" / "run-1" / "scenario-plan.json").read_text()
        )["coverage_summary"]["uncovered_risks"],
        "generation_problems_seen_by_gate": planner.generation_problems(),
    }
    print(json.dumps(report, indent=2, default=str))
    (out / "adj-g01-cleanpath.json").write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
