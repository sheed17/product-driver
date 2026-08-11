"""G-SCALE probe 12: partial resume-time coverage loss that the gate cannot see.

p10 showed a full drop (200 -> 0). A full drop is caught downstream, because the
risk register survives the resume and the uncovered-risk check then fires. This
probe removes that safety net: it drops 199 of 200 scenarios while leaving one
survivor that still covers every identified risk category. The gate then has
nothing to object to.

Runs the whole thing end to end: restore -> assemble suite -> execute (fake,
always-pass executor) -> gate -> hostile ACCEPT.
"""

from __future__ import annotations

import argparse
import asyncio
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

    from neyma_product_driver import cli as driver_cli
    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.models import Decision, EvaluatorDecision, ScenarioResult
    from neyma_product_driver.scenario_gate import evaluate_gate
    from neyma_product_driver.scenario_planner import ScenarioPlanner
    from neyma_product_driver.scenario_suite import SuiteExecutor, build_suite
    from neyma_product_driver.scenarios import Scenario, ServiceSpec

    from scenario_fixtures import (
        FakeFounder,
        FakeUnit,
        ScriptedReasoner,
        base_scenario,
        raw_payload,
        raw_scenario,
    )

    n = 200
    proposals = []
    for i in range(n):
        p = raw_scenario(f"gen-part-{i:04d}")
        p["title"] = f"part {i}"
        p["actions"][0]["request"]["path"] = f"/invoices/{i}/approve"
        if i == 0:
            # The survivor: uses only the state command, which stays approved.
            # Every other scenario also needs the cleanup command, which does
            # not.
            p["cleanup"] = []
            p["isolation_note"] = (
                "writes only to invoice 0, which no other scenario in this plan "
                "touches, so it cannot contaminate the next scenario"
            )
        proposals.append(p)

    cfg = ScenarioGenerationConfig(
        enabled=True,
        max_initial_scenarios=n,
        max_total_scenarios=200,
        max_scenarios_per_risk_category=200,
    )
    root = out / "p12"
    if root.exists():
        shutil.rmtree(root)

    writer = ScenarioPlanner(
        repo=root / "repo",
        config=cfg,
        reasoner=ScriptedReasoner([raw_payload(*proposals)]),
        store=EvidenceStore(root / "runs", "run-1"),
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )
    writer.plan_initial(task="t", unit=FakeUnit(), run_id="run-1")
    before = len(writer.plan.scenarios)
    writer.persist()

    # The resumed run's repository still approves the state command but no
    # longer approves the cleanup command (a scenario file lost its teardown).
    from scenario_fixtures import APPROVED_STATE

    shrunken = Scenario(
        name="backend_generic",
        mode="backend",
        services=[ServiceSpec(name="api", command="./serve.sh")],
        readiness=[{"tcp": "127.0.0.1:8931"}],
        app_url="http://127.0.0.1:8931",
        commands=[{"name": "smoke", "run": APPROVED_STATE}],
        expect_state=[
            {"name": "payments", "command": APPROVED_STATE, "contains": ["ok"]}
        ],
    )
    reader = ScenarioPlanner(
        repo=root / "repo",
        config=cfg,
        reasoner=ScriptedReasoner([]),
        store=EvidenceStore(root / "runs", "run-1"),
        base_scenario=shrunken,
        permanent_scenarios=[shrunken],
        founder=FakeFounder(),
    )
    emitted: list[str] = []
    reader.emit = emitted.append
    note = reader.restore_from_store()
    after = len(reader.plan.scenarios)

    suite = build_suite(
        permanent=[("permanent-anchor", shrunken)],
        generated=[
            (m, reader.compiled[m.id])
            for m in reader.plan.scenarios
            if m.id in reader.compiled
        ],
    )

    class AlwaysPass:
        service_logs: dict[str, str] = {}

        def __init__(self, d: Path) -> None:
            self.d = d

        async def execute(self, scenario: Scenario) -> ScenarioResult:
            return ScenarioResult(scenario_name=scenario.name, readiness_ok=True)

    ex = SuiteExecutor(
        make_executor=lambda d: AlwaysPass(d),
        artifact_root=root / "artifacts",
        run_id="run-1",
        iteration=1,
    )
    result = asyncio.run(ex.run(suite, selection_reason="resumed run"))
    risks = list(reader.plan.risks)
    verdict = evaluate_gate(
        result, generation_problems=reader.generation_problems(), risks=risks
    )
    final = driver_cli._apply_suite_precedence(
        result,
        EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9),
        "permanent-anchor",
        lambda _m: None,
        generation_problems=reader.generation_problems(),
        risks=risks,
    )

    report = {
        "scenarios_planned_before_resume": before,
        "scenarios_after_resume": after,
        "silently_dropped": before - after,
        "restore_note": note,
        "generation_problems_after_resume": reader.generation_problems(),
        "risks_carried_over": [r.risk_category.value for r in risks],
        "suite_size_after_resume": len(suite),
        "assembly_problems": list(result.assembly_problems),
        "outcomes": result.total,
        "passed": result.passed,
        "gate_status": verdict.status.value,
        "gate_required_total": verdict.required_total,
        "gate_required_passed": verdict.required_passed,
        "gate_uncovered_risks": len(verdict.uncovered_risks),
        "gate_generation_problems": verdict.generation_problems,
        "hostile_accept_becomes": final.decision.value,
        "persisted_plan_on_disk_still_lists": len(
            json.loads(
                (root / "runs" / "run-1" / "scenario-plan.json").read_text()
            )["scenarios"]
        ),
        "loss_visible_anywhere_machine_readable": bool(
            reader.generation_problems()
            or result.assembly_problems
            or verdict.uncovered_risks
            or verdict.generation_problems
        ),
        "emitted_first_line": emitted[0][:200] if emitted else "",
        "plan_time_rejections": [
            f"{r.id}: {'; '.join(r.reasons)[:120]}"
            for w in writer.plan.waves
            for r in w.rejected
        ][:3],
        "survivor_ids": [s.id for s in reader.plan.scenarios][:5],
    }
    print(json.dumps(report, indent=2, default=str))
    (out / "probe12.json").write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
