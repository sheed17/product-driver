"""Reproduce r4 F-4: two distinct scenario ids that collapse to one identity.

Run against any driver checkout:  python reproduce.py --driver <path> [--out f]

Drives the real production path — model identity, suite assembly, suite
execution with a stub runner, per-case evidence, aggregation, targeted rerun
selection and the acceptance gate — and reports where a second scenario stops
existing.

The fixture is not exotic. Model-authored ids are descriptive slugs; two
neighbouring restart-recovery cases naturally share a long prefix.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

PREFIX = "gen-approval-survives-restart-and-is-not-double-applied-after-a"
ID_A = PREFIX + "-crash-during-the-outbox-flush"
ID_B = PREFIX + "-crash-during-the-payment-call"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    sys.path.insert(0, str(Path(args.driver).resolve()))

    from neyma_product_driver.evidence import sanitize_filename
    from neyma_product_driver.models import ScenarioResult
    from neyma_product_driver.scenario_gate import evaluate_gate
    from neyma_product_driver.scenario_plan import (
        GeneratedScenario,
        Priority,
        RiskCategory,
    )
    from neyma_product_driver.scenario_suite import (
        SuiteExecutor,
        build_suite,
        select_rerun,
    )
    from neyma_product_driver.scenarios import Scenario

    assert len(PREFIX) == 63
    assert ID_A[:64] == ID_B[:64], "fixture must share the first 64 characters"

    f: dict[str, object] = {
        "fixture": {"id_a": ID_A, "id_b": ID_B, "shared_prefix_chars": 64},
    }

    def make(sid: str) -> GeneratedScenario:
        return GeneratedScenario(
            id=sid,
            title="restart " + sid[-12:],
            purpose="probe",
            risk_category=RiskCategory.RESTART_RECOVERY,
            priority=Priority.P0,
            actions=[],
        )

    a, b = make(ID_A), make(ID_B)
    f["probe1_model_identity"] = {
        "stored_id_a": a.id,
        "stored_id_b": b.id,
        "collide": a.id == b.id,
    }

    # -- 2. the planner's compiled map is keyed by id -----------------------
    compiled = {s.id: s for s in (a, b)}
    f["probe2_compiled_map"] = {"entries": len(compiled), "lost": 2 - len(compiled)}

    # -- 3. suite assembly (the real build_suite) ---------------------------
    suite = build_suite(
        generated=[
            (a, Scenario(name=a.id, phase="verify")),
            (b, Scenario(name=b.id, phase="verify")),
        ]
    )
    f["probe3_suite"] = {
        "entries": len(suite),
        "ids": [e.scenario_id for e in suite.entries],
        "dropped_silently": len(suite) < 2,
    }

    # -- 4. filesystem identity --------------------------------------------
    f["probe4_filesystem"] = {
        "dir_a": sanitize_filename(ID_A),
        "dir_b": sanitize_filename(ID_B),
        "same_directory": sanitize_filename(ID_A) == sanitize_filename(ID_B),
    }

    # -- 5. execution + evidence + aggregation + gate -----------------------
    class StubRunner:
        def __init__(self, directory: Path) -> None:
            self.directory = directory
            self.service_logs: dict[str, str] = {}

        async def execute(self, scenario: Scenario) -> ScenarioResult:
            return ScenarioResult(
                scenario_name=scenario.name,
                phase=scenario.phase,
                readiness_ok=True,
                assertions=[],
            )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        executor = SuiteExecutor(
            make_executor=StubRunner,
            artifact_root=root,
            run_id="dcollide",
            iteration=1,
        )
        result = asyncio.run(executor.run(suite))

    f["probe5_execution"] = {
        "executed": len([o for o in result.outcomes]),
        "outcome_ids": [o.scenario_id for o in result.outcomes],
        "expected_required_ids": list(result.expected_required_ids),
        "evidence_paths": sorted({o.evidence_path for o in result.outcomes}),
        "distinct_evidence_dirs": len({o.evidence_path for o in result.outcomes}),
    }

    verdict = evaluate_gate(result)
    f["probe6_gate"] = {
        "status": verdict.status.value,
        "required_total": verdict.required_total,
        "required_passed": verdict.required_passed,
        "unverified": [c.scenario_id for c in verdict.unverified],
        "gate_sees_the_lost_scenario": ID_B in {
            c.scenario_id for c in verdict.unverified
        },
    }

    ids, reason = select_rerun(suite, result)
    f["probe7_rerun_selection"] = {"selected": sorted(ids), "reason": reason}

    lost_at_identity = a.id == b.id
    lost_in_suite = len(suite) < 2
    shared_dir = sanitize_filename(a.id) == sanitize_filename(b.id)
    f["REPRODUCED"] = bool(lost_at_identity or lost_in_suite or shared_dir)
    f["SUMMARY"] = (
        "two distinct proposed scenarios collapse into one execution identity; "
        f"suite holds {len(suite)} of 2, gate accounts for {verdict.required_total} of 2"
        if f["REPRODUCED"]
        else "both scenarios keep distinct identity, evidence and gate accounting"
    )

    text = json.dumps(f, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
