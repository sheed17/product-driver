"""EXPERIMENT 1 — scale sweep: 10 / 50 / 100 scenarios against the real target."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scale_harness import (  # noqa: E402
    EVID, Decision, EvaluatorDecision, Outcome, Origin,
    make_suite, run_suite, prompt_size, driver_cli,
)

BAD = {int(x) for x in os.environ.get("TARGET_BAD_IDS", "").split(",") if x}
DUP = {int(x) for x in os.environ.get("TARGET_DUP_IDS", "").split(",") if x}
ERR = {int(x) for x in os.environ.get("TARGET_500_IDS", "").split(",") if x}


async def main() -> None:
    counts = [int(x) for x in sys.argv[1:]] or [10, 50, 100]
    rows = []
    for n in counts:
        suite = make_suite(n)
        root = EVID / f"run-{n}"
        ex, result, wall = await run_suite(suite, root, browser=False)

        primary = ex.results.get("permanent-health")
        prompt = prompt_size(result, primary)
        summary = result.summary_block()

        # aggregate cross-check, computed independently of SuiteResult
        independent = {"PASSED": 0, "FAILED": 0, "BLOCKED": 0, "SKIPPED": 0}
        for o in result.outcomes:
            independent[o.outcome.value] += 1

        # per-case evidence check: does each outcome's evidence dir belong to it?
        evidence_ok = True
        evidence_problems = []
        for o in result.outcomes:
            p = Path(o.evidence_path) if o.evidence_path else None
            if p is None:
                continue
            if p.name != o.scenario_id:
                evidence_ok = False
                evidence_problems.append(f"{o.scenario_id} -> {p}")

        # unique ids?
        ids = [o.scenario_id for o in result.outcomes]
        dup_ids = len(ids) - len(set(ids))

        # do injected defects map exactly onto the failing cases?
        # KINDS = [http, cmd, idem] indexed by i % 3
        expected_fail = set()
        for i in range(1, n + 1):
            kind = i % 3
            name = {0: f"case-{i:03d}-http", 1: f"case-{i:03d}-cmd", 2: f"case-{i:03d}-idem"}[kind]
            if kind == 0 and (i in BAD or i in ERR):
                expected_fail.add(name)
            if kind == 2 and i in DUP:
                expected_fail.add(name)
        actual_fail = {o.scenario_id for o in result.failures()}

        # would an evaluator ACCEPT survive?
        accept = EvaluatorDecision(decision=Decision.ACCEPT, summary="looks good", confidence=0.9)
        final = driver_cli._apply_suite_precedence(result, accept, "permanent-health", lambda _m: None)

        row = {
            "scenarios": len(suite),
            "executed": result.total,
            "wall_s": round(wall, 2),
            "per_scenario_s": round(wall / max(1, result.total), 3),
            "passed": result.passed,
            "failed": result.failed,
            "blocked": result.blocked,
            "skipped": result.skipped,
            "independent_recount": independent,
            "counts_agree": independent["PASSED"] == result.passed
            and independent["FAILED"] == result.failed
            and independent["BLOCKED"] == result.blocked
            and independent["SKIPPED"] == result.skipped,
            "total_equals_suite": result.total == len(suite),
            "duplicate_result_ids": dup_ids,
            "evidence_paths_correct": evidence_ok,
            "evidence_problems": evidence_problems[:5],
            "blocking_failures": len(result.blocking_failures()),
            "expected_failures": sorted(expected_fail),
            "actual_failures": sorted(actual_fail),
            "failures_match_injection": expected_fail == actual_fail,
            "clusters": len(result.clusters),
            "grouped_clusters": len([c for c in result.clusters if not c.singleton]),
            "summary_block_chars": len(summary),
            "evaluator_prompt_chars": len(prompt),
            "evaluator_prompt_est_tokens": len(prompt) // 4,
            "all_failures_named_in_summary": all(
                f.scenario_id in summary for f in result.failures()
            ),
            "accept_survives": final.decision.value,
            "results_dict_size": len(ex.results),
            "outcome_durations_all_zero": all(o.duration_s == 0.0 for o in result.outcomes),
        }
        rows.append(row)
        (EVID / f"summary-block-{n}.txt").write_text(summary)
        (EVID / f"evaluator-prompt-{n}.txt").write_text(prompt)
        (EVID / f"suite-result-{n}.json").write_text(result.model_dump_json(indent=2))
        print(json.dumps(row, indent=2), flush=True)

    (EVID / "scale-sweep.json").write_text(json.dumps(rows, indent=2))


asyncio.run(main())
