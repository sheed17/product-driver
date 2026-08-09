"""EXPERIMENT 6 — id integrity at scale, executor reuse, and cross-attribution."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scale_harness import (  # noqa: E402
    EVID, Priority, RiskCategory, GeneratedScenario, build_suite, run_suite,
    make_suite, http_scenario, select_rerun, executor_factory,
)
from neyma_product_driver.scenario_suite import SuiteExecutor  # noqa: E402

OUT: dict = {}


async def main():
    # --- id vs name separation at 200 scenarios --------------------------
    suite = make_suite(200)
    ex, r, wall = await run_suite(suite, EVID / "int-200")
    perm = r.by_id("permanent-health")
    mismatch = [
        {"id": o.scenario_id, "name": o.scenario_name}
        for o in r.outcomes
        if o.origin.value == "generated" and o.scenario_id != o.scenario_name
    ]
    # every failing outcome's evidence dir must be named after ITS id
    wrong_dir = [
        o.scenario_id for o in r.failures()
        if Path(o.evidence_path).name != o.scenario_id
    ]
    # failure detail must reference the item number in the scenario id
    misattributed = []
    for o in r.failures():
        num = o.scenario_id.split("-")[1].lstrip("0") or "0"
        blob = " ".join(o.failed_assertions) + o.error
        if f"/item/{num}" not in blob and f"probe.py {num}" not in blob:
            misattributed.append({"id": o.scenario_id, "detail": blob[:160]})
    OUT["1_id_integrity_200"] = {
        "suite": len(suite), "outcomes": r.total, "wall_s": round(wall, 2),
        "unique_ids": len({o.scenario_id for o in r.outcomes}),
        "permanent_entry": {
            "scenario_id": perm.scenario_id, "scenario_name": perm.scenario_name,
            "origin": perm.origin.value, "outcome": perm.outcome.value,
        },
        "generated_id_name_mismatches": len(mismatch),
        "evidence_dir_wrong_case": wrong_dir,
        "failure_detail_misattributed": misattributed,
        "results_dict_keys_match_outcomes": sorted(ex.results) == sorted(
            o.scenario_id for o in r.outcomes),
    }

    # --- executor reuse across two run() calls ---------------------------
    s2 = make_suite(10)
    reuse = SuiteExecutor(make_executor=executor_factory(False),
                          artifact_root=EVID / "int-reuse", max_parallel=1)
    first = await reuse.run(s2, only=None)
    ids_a = sorted(reuse.results)
    subset = [e.scenario_id for e in s2.entries][:3]
    second = await reuse.run(s2, only=subset, selection_reason="narrowed")
    OUT["2_executor_reuse"] = {
        "first_run_outcomes": first.total,
        "results_after_first": len(ids_a),
        "second_run_outcomes": second.total,
        "results_after_second": len(reuse.results),
        "second_result_ids": [o.scenario_id for o in second.outcomes],
        "stale_results_retained": len(reuse.results) > second.total,
        "second_full_run_flag": second.full_run,
        "clusters_only_from_current_run": all(
            any(o.scenario_id == a for o in second.outcomes)
            for c in second.clusters for a in c.affected_scenarios),
    }

    # --- select_rerun at scale -------------------------------------------
    only, reason = select_rerun(suite, r)
    failed_ids = {o.scenario_id for o in r.failures()}
    OUT["3_select_rerun_200"] = {
        "selected": len(only), "of": len(suite),
        "all_failures_reselected": failed_ids <= set(only),
        "all_permanent_reselected": all(
            e.scenario_id in only for e in suite.permanent()),
        "reason": reason,
    }

    (EVID / "integrity.json").write_text(json.dumps(OUT, indent=2, default=str))
    print(json.dumps(OUT, indent=2, default=str))


asyncio.run(main())
