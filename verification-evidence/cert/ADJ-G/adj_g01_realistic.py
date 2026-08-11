"""ADJ-G: is the resume drop reachable at the *shipped default* budgets?

Everything here uses ScenarioGenerationConfig() as shipped (30 total, 6 per
category, 3 waves). Five risk categories, every risk P0, six scenarios each.
Between the two processes one approved command disappears from the permanent
scenario's teardown — an ordinary maintenance edit, not sabotage. Five of the
thirty scenarios never used it and survive: one per category, so the
uncovered-risk net stays satisfied and has nothing to say.
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
        APPROVED_SETUP,
        APPROVED_STATE,
        FakeFounder,
        FakeUnit,
        ScriptedReasoner,
        base_scenario,
        raw_payload,
        raw_scenario,
    )

    cats = [
        "idempotency",
        "authorization",
        "concurrency",
        "boundary",
        "retry_safety",
    ]
    proposals = []
    for c, cat in enumerate(cats):
        for i in range(6):
            p = raw_scenario(f"gen-{cat}-{i}", risk_category=cat)
            p["title"] = f"{cat} case {i}"
            p["actions"][0]["request"]["path"] = f"/invoices/{c}{i}/approve"
            if i == 0:
                # One scenario per category needs no cleanup command. Ordinary:
                # a read-only check does not have to reset anything.
                p["cleanup"] = []
                p["isolation_note"] = (
                    "reads only; it mutates nothing, so it cannot contaminate the "
                    "next scenario"
                )
            proposals.append(p)

    risks = [
        {
            "id": f"R{i}",
            "description": f"{cat} may be handled incorrectly",
            "risk_category": cat,
            "severity": "P0",
            "basis": "the diff touched the approval path",
        }
        for i, cat in enumerate(cats)
    ]

    # SHIPPED DEFAULTS, only enabled + the initial-wave allowance raised so one
    # wave can express a full 30-scenario plan. Budgets untouched.
    cfg = ScenarioGenerationConfig(enabled=True, max_initial_scenarios=30)
    assert cfg.max_total_scenarios == 30 and cfg.max_scenarios_per_risk_category == 6

    root = out / "g01r"
    if root.exists():
        shutil.rmtree(root)

    writer = ScenarioPlanner(
        repo=root / "repo",
        config=cfg,
        reasoner=ScriptedReasoner([raw_payload(*proposals, risks=risks)]),
        store=EvidenceStore(root / "runs", "run-1"),
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
        emit=lambda _m: None,
    )
    writer.plan_initial(task="t", unit=FakeUnit(), run_id="run-1")
    before = len(writer.plan.scenarios)
    writer.persist()

    # The maintenance edit: the permanent scenario no longer has a teardown, so
    # './probe.sh reset' is no longer an approved command anywhere.
    edited = Scenario(
        name="backend_generic",
        mode="backend",
        setup=[APPROVED_SETUP],
        services=[ServiceSpec(name="api", command="./serve.sh")],
        readiness=[{"tcp": "127.0.0.1:8931"}],
        app_url="http://127.0.0.1:8931",
        commands=[{"name": "smoke", "run": APPROVED_STATE}],
        expect_state=[{"name": "payments", "command": APPROVED_STATE, "contains": ["ok"]}],
    )
    reader = ScenarioPlanner(
        repo=root / "repo",
        config=cfg,
        reasoner=ScriptedReasoner([]),
        store=EvidenceStore(root / "runs", "run-1"),
        base_scenario=edited,
        permanent_scenarios=[edited],
        founder=FakeFounder(),
    )
    emitted: list[str] = []
    reader.emit = emitted.append
    reader.restore_from_store()
    after = len(reader.plan.scenarios)

    suite = build_suite(
        permanent=[("backend_generic", edited)],
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
    reader.note_executed([o.scenario_id for o in result.outcomes])
    plan_risks = list(reader.plan.risks)
    verdict = evaluate_gate(
        result, generation_problems=reader.generation_problems(), risks=plan_risks
    )
    final = driver_cli._apply_suite_precedence(
        result,
        EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9),
        "backend_generic",
        lambda _m: None,
        generation_problems=reader.generation_problems(),
        risks=plan_risks,
    )
    plan_after = json.loads((root / "runs" / "run-1" / "scenario-plan.json").read_text())

    report = {
        "config": "shipped defaults (30 total / 6 per category / 3 waves)",
        "trigger": "one approved command removed from a permanent scenario between processes",
        "planned_before_resume": before,
        "after_resume": after,
        "silently_dropped": before - after,
        "p0_risks_carried": len(plan_risks),
        "generation_problems": reader.generation_problems(),
        "assembly_problems": list(result.assembly_problems),
        "gate_status": verdict.status.value,
        "gate_required": f"{verdict.required_passed}/{verdict.required_total}",
        "gate_uncovered_risks": len(verdict.uncovered_risks),
        "hostile_accept_becomes": final.decision.value,
        "plan_on_disk_after_persist": len(plan_after["scenarios"]),
        "coverage_summary_uncovered_risks_on_disk": plan_after["coverage_summary"][
            "uncovered_risks"
        ],
        "console_line": emitted[0][:180] if emitted else "",
        "loss_visible_in_gate_inputs": bool(
            reader.generation_problems() or result.assembly_problems or verdict.uncovered_risks
        ),
    }
    print(json.dumps(report, indent=2, default=str))
    (out / "adj-g01-realistic.json").write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
