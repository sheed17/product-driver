"""ADJ-G independent reproduction of G-SCALE-01.

Deliberately NOT the reviewer's trigger. The reviewer narrowed the approved
command set. This uses the trigger an ordinary operator produces by accident:
the run is resumed against a *different base scenario*, because `--resume-run`
re-loads the scenario from `--scenario`/config and never from the saved
`state.scenario_name`.

Three things are measured that the reviewer did not measure:
  1. whether the loss reaches any machine-readable acceptance input,
  2. whether a later persist() erases the plan's record of the loss on disk,
  3. what the *wave* records on disk still say afterwards.
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
        APPROVED_CLEANUP,
        APPROVED_SETUP,
        APPROVED_STATE,
        FakeFounder,
        FakeUnit,
        ScriptedReasoner,
        base_scenario,
        raw_payload,
        raw_scenario,
    )

    n = 60
    proposals = []
    for i in range(n):
        p = raw_scenario(f"gen-case-{i:04d}")
        p["title"] = f"case {i}"
        p["actions"][0]["request"]["path"] = f"/invoices/{i}/approve"
        if i == 0:
            # One survivor that names no service, so it still compiles against
            # any base. Every risk category in this plan is the same one, so the
            # uncovered-risk net is satisfied by this single survivor.
            p["service_refs"] = []
        proposals.append(p)

    cfg = ScenarioGenerationConfig(
        enabled=True,
        max_initial_scenarios=n,
        max_total_scenarios=200,
        max_scenarios_per_risk_category=200,
    )
    root = out / "g01"
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
        emit=lambda _m: None,
    )
    writer.plan_initial(task="t", unit=FakeUnit(), run_id="run-1")
    before = len(writer.plan.scenarios)
    writer.persist()
    plan_on_disk_before = len(
        json.loads((root / "runs" / "run-1" / "scenario-plan.json").read_text())["scenarios"]
    )

    # The resumed run: same approved commands, same everything, EXCEPT the base
    # scenario the operator named. It declares a service called "web", not
    # "api". Nothing here is sabotage: `--resume-run X` without repeating
    # `--scenario` re-reads the config default, and `state.scenario_name` is
    # ignored on the resume path (cli.py loads the scenario before it opens the
    # run) and then overwritten.
    other_base = Scenario(
        name="other_generic",
        mode="backend",
        setup=[APPROVED_SETUP],
        services=[ServiceSpec(name="web", command="./serve.sh")],
        readiness=[{"tcp": "127.0.0.1:8931"}],
        app_url="http://127.0.0.1:8931",
        commands=[{"name": "smoke", "run": APPROVED_STATE}],
        expect_state=[{"name": "payments", "command": APPROVED_STATE, "contains": ["ok"]}],
        teardown=[APPROVED_CLEANUP],
    )
    reader = ScenarioPlanner(
        repo=root / "repo",
        config=cfg,
        reasoner=ScriptedReasoner([]),
        store=EvidenceStore(root / "runs", "run-1"),
        base_scenario=other_base,
        # unchanged: the approved command set is identical
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )
    emitted: list[str] = []
    reader.emit = emitted.append
    note = reader.restore_from_store()
    after = len(reader.plan.scenarios)

    suite = build_suite(
        permanent=[("permanent-anchor", other_base)],
        generated=[
            (m, reader.compiled[m.id]) for m in reader.plan.scenarios if m.id in reader.compiled
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
        iteration=2,
    )
    result = asyncio.run(ex.run(suite, selection_reason="resumed run"))

    # What the real loop does immediately after executing a suite.
    reader.note_executed(
        [o.scenario_id for o in result.outcomes if o.outcome.value != "SKIPPED"]
    )

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

    plan_after = json.loads((root / "runs" / "run-1" / "scenario-plan.json").read_text())
    wave_after = json.loads(
        (root / "runs" / "run-1" / "scenario-generation" / "wave-01.json").read_text()
    )

    report = {
        "trigger": "resumed with a different base scenario (--scenario not repeated)",
        "planned_before_resume": before,
        "plan_on_disk_before_resume": plan_on_disk_before,
        "after_resume": after,
        "silently_dropped": before - after,
        "restore_note": note,
        "console_line": emitted[0][:300] if emitted else "",
        "generation_problems_after_resume": reader.generation_problems(),
        "assembly_problems": list(result.assembly_problems),
        "suite_size": len(suite),
        "expected_required_ids": len(result.expected_required_ids),
        "outcomes": result.total,
        "gate_status": verdict.status.value,
        "gate_required_total": verdict.required_total,
        "gate_required_passed": verdict.required_passed,
        "gate_uncovered_risks": len(verdict.uncovered_risks),
        "hostile_accept_becomes": final.decision.value,
        "PLAN_ON_DISK_AFTER_ONE_PERSIST": len(plan_after["scenarios"]),
        "plan_coverage_summary_after": plan_after.get("coverage_summary", {}).get(
            "total_scenarios"
        ),
        "wave01_accepted_ids_on_disk": len(wave_after.get("accepted_ids", [])),
        "wave01_rejected_on_disk": len(wave_after.get("rejected", [])),
        "executed_scenario_ids_on_disk": len(plan_after.get("executed_scenario_ids", [])),
        "loss_visible_in_gate_inputs": bool(
            reader.generation_problems() or result.assembly_problems or verdict.uncovered_risks
        ),
    }
    print(json.dumps(report, indent=2, default=str))
    (out / "adj-g01.json").write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
