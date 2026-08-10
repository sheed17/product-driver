"""Reproduce r6 F-3: a known required coverage gap reaches neither the evaluator
nor the acceptance gate.

Run against any driver checkout:  python reproduce.py --driver <path> [--out f]

The fixture is the case that matters: **every executed scenario passes**, and
the run's own risk register names a P0 risk that no scenario ever exercised.

  probe 1  does the evaluator prompt state the gap, while asking
           "was the coverage sufficient for the risk surface?"
  probe 2  does the acceptance gate report VERIFIED anyway?
  probe 3  is the gap detected deterministically, without a model?
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    sys.path.insert(0, str(Path(args.driver).resolve()))

    from neyma_product_driver.prompts import evaluator_prompt
    from neyma_product_driver.scenario_gate import evaluate_gate
    from neyma_product_driver.scenario_plan import (
        IdentifiedRisk,
        Priority,
        RiskCategory,
    )
    from neyma_product_driver.scenario_suite import (
        Origin,
        Outcome,
        ScenarioOutcome,
        SuiteResult,
    )

    f: dict[str, object] = {}

    # The run identified two risks. It generated coverage for one.
    risks = [
        IdentifiedRisk(
            id="R1",
            description="a duplicate approval could pay the carrier twice",
            risk_category=RiskCategory.IDEMPOTENCY,
            severity=Priority.P0,
            basis="AC: an approved invoice is paid exactly once",
        ),
        IdentifiedRisk(
            id="R2",
            description="an operator of another tenant could approve this invoice",
            risk_category=RiskCategory.CROSS_TENANT,
            severity=Priority.P0,
            basis="AC: an invoice belonging to another tenant is never approvable",
        ),
    ]

    passed = ScenarioOutcome(
        scenario_id="gen-idempotency-01",
        scenario_name="gen-idempotency-01",
        origin=Origin.GENERATED,
        outcome=Outcome.PASSED,
        priority=Priority.P0,
        risk_category=RiskCategory.IDEMPOTENCY.value,
        required=True,
        evidence_path="/runs/x/scenarios/gen-idempotency-01",
        evidence_verified=True,
    )
    result = SuiteResult(
        full_run=True,
        expected_required_ids=[passed.scenario_id],
        outcomes=[passed],
    )

    # -- probe 1: what the evaluator is shown, through the production wiring --
    from neyma_product_driver import cli as driver_cli

    class Plan:
        pass

    class Planner:
        """Stands in for ScenarioPlanner: the loop only reads `plan.risks`."""

    plan_stub = Plan()
    plan_stub.risks = risks  # type: ignore[attr-defined]
    planner_stub = Planner()
    planner_stub.plan = plan_stub  # type: ignore[attr-defined]

    kwargs: dict[str, object] = dict(
        task="Add supervised carrier invoice approval.",
        iteration=1,
        max_iterations=3,
        builder_summary="implemented",
        git=None,
        scenario=None,
        service_logs=None,
        evidence_dir="/runs/x/iteration-01",
        suite=result,
    )
    takes_gaps = "coverage_gaps" in inspect.signature(evaluator_prompt).parameters
    briefs: list[str] = []
    if hasattr(driver_cli, "_coverage_gap_briefs"):
        briefs = driver_cli._coverage_gap_briefs(planner_stub, result)
    if takes_gaps:
        kwargs["coverage_gaps"] = briefs
    prompt = evaluator_prompt(**kwargs)  # type: ignore[arg-type]

    # …and that the control loop actually passes them, rather than merely being
    # able to. A parameter nothing fills is not evidence reaching an evaluator.
    loop_source = inspect.getsource(driver_cli.run_control_loop)
    f["probe1_evaluator"] = {
        "asks_whether_coverage_was_sufficient": "was the coverage sufficient" in prompt,
        "states_the_cross_tenant_gap": "cross_tenant" in prompt,
        "has_a_coverage_gap_section": "KNOWN COVERAGE GAPS" in prompt,
        "accepts_a_coverage_gaps_argument": takes_gaps,
        "control_loop_supplies_the_gaps": "coverage_gaps=" in loop_source,
        "gap_briefs_computed": briefs,
    }

    # -- probe 2: what the acceptance gate concludes -------------------------
    try:
        verdict = evaluate_gate(result, risks=risks)
        gate_takes_risks = True
    except TypeError:
        verdict = evaluate_gate(result)
        gate_takes_risks = False
    f["probe2_gate"] = {
        "gate_accepts_a_risk_register": gate_takes_risks,
        "status": verdict.status.value,
        "blocks_acceptance": verdict.blocks_acceptance,
        "required_total": verdict.required_total,
        "required_passed": verdict.required_passed,
        "uncovered_risks": [
            r.brief() for r in getattr(verdict, "uncovered_risks", [])
        ],
    }

    # -- probe 3: is detection deterministic? --------------------------------
    try:
        from neyma_product_driver.scenario_gate import uncovered_required_risks

        gaps = uncovered_required_risks(risks, result)
        f["probe3_deterministic_detector"] = {
            "exists": True,
            "gaps": [g.model_dump(mode="json") for g in gaps],
            "stable_across_calls": [g.brief() for g in gaps]
            == [g.brief() for g in uncovered_required_risks(risks, result)],
        }
    except ImportError:
        f["probe3_deterministic_detector"] = {"exists": False}

    evaluator = f["probe1_evaluator"]
    gap_hidden_from_evaluator = not (
        evaluator["has_a_coverage_gap_section"]  # type: ignore[index]
        and evaluator["control_loop_supplies_the_gaps"]  # type: ignore[index]
    )
    gate_said_verified = not verdict.blocks_acceptance
    f["REPRODUCED"] = bool(gap_hidden_from_evaluator or gate_said_verified)
    f["SUMMARY"] = (
        "the evaluator is asked whether coverage was sufficient without being shown "
        "the known gap, and the gate reports the run verified"
        if f["REPRODUCED"]
        else "the gap is stated to the evaluator and the gate refuses to call the run verified"
    )

    text = json.dumps(f, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
