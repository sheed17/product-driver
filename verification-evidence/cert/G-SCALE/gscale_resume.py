"""G-SCALE resume at scale, in genuinely separate OS processes.

The implementer's harness checked "resume" with
``SuiteResult.model_validate(result.model_dump(mode="json"))`` inside the same
process — a pydantic round-trip, not a resume. This writes state to disk in one
process and reads it back in another, exercising the real persistence path
(``EvidenceStore.write_json`` -> ``redact_obj`` -> JSON -> ``restore()``).

  python gscale_resume.py write --driver R --out D --n 200
  python gscale_resume.py read  --driver R --out D --n 200
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def imports(driver: Path):
    sys.path.insert(0, str(driver))
    sys.path.insert(0, str(driver / "tests"))


def make_planner(driver: Path, out: Path, n: int):
    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.scenario_planner import ScenarioPlanner

    from scenario_fixtures import (
        FakeFounder,
        ScriptedReasoner,
        base_scenario,
        raw_payload,
        raw_scenario,
    )

    proposals = []
    for i in range(n):
        p = raw_scenario(
            f"gen-resume-{i:04d}",
            risk_category=["idempotency", "persistence_failure", "cross_tenant"][i % 3],
        )
        p["title"] = f"resume proposal {i}"
        p["priority"] = "P0" if i % 5 == 0 else "P1"
        p["actions"][0]["request"]["path"] = f"/invoices/{i}/approve"
        proposals.append(p)

    cfg = ScenarioGenerationConfig(
        enabled=True,
        max_initial_scenarios=n,
        max_total_scenarios=200,
        max_scenarios_per_risk_category=200,
        max_waves=3,
    )
    store = EvidenceStore(out / "runs", "resume-run")
    planner = ScenarioPlanner(
        repo=out / "repo",
        config=cfg,
        reasoner=ScriptedReasoner([raw_payload(*proposals)]),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )
    return planner, store


def fingerprint(planner) -> dict:
    from neyma_product_driver.scenario_suite import build_suite

    plan = planner.plan
    suite = build_suite(
        permanent=[("permanent-anchor", planner.base_scenario)],
        generated=[
            (m, planner.compiled[m.id]) for m in plan.scenarios if m.id in planner.compiled
        ],
    )
    ids = [s.id for s in plan.scenarios]
    entry_ids = [e.scenario_id for e in suite.entries]
    required = [e.scenario_id for e in suite.entries if e.required]
    blob = json.dumps(
        {
            "ids": ids,
            "priorities": [s.priority.value for s in plan.scenarios],
            "categories": [s.risk_category.value for s in plan.scenarios],
            "requirements": [s.requirement_reference for s in plan.scenarios],
            "signatures": sorted(plan.signatures()),
            "entry_ids": entry_ids,
            "required": required,
            "isolation": [e.isolation_key for e in suite.entries],
            "compiled_names": [planner.compiled[i].name for i in ids],
            "compiled_requests": [
                [f"{r.method}:{r.path or r.url}" for r in planner.compiled[i].requests]
                for i in ids
            ],
            "executed": list(plan.executed_scenario_ids),
            "observed_failures": sorted(planner._observed_failure_ids),
            "waves": [(w.wave, w.stage, len(w.accepted_ids), len(w.rejected)) for w in plan.waves],
            "coverage": plan.coverage_summary.model_dump(mode="json"),
            "risks": sorted(r.description for r in plan.risks),
        },
        sort_keys=True,
        default=str,
    )
    return {
        "scenario_count": len(ids),
        "suite_entries": len(suite),
        "required_count": len(required),
        "compiled_count": len(planner.compiled),
        "waves_used": planner.waves_used,
        "digest": hashlib.sha256(blob.encode()).hexdigest(),
        "blob": blob,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["write", "read"])
    ap.add_argument("--driver", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--suite-result", default="")
    args = ap.parse_args()

    driver = Path(args.driver).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    imports(driver)

    from scenario_fixtures import FakeUnit  # noqa: E402

    planner, store = make_planner(driver, out, args.n)
    report: dict = {"mode": args.mode, "pid": __import__("os").getpid()}

    if args.mode == "write":
        planner.plan_initial(task="approve invoices", unit=FakeUnit(), run_id="resume-run")
        planner.note_executed([s.id for s in planner.plan.scenarios[: args.n // 2]])
        planner.persist()
        fp = fingerprint(planner)
        report["fingerprint"] = {k: v for k, v in fp.items() if k != "blob"}
        (out / "fingerprint-write.json").write_text(fp["blob"])
        report["plan_file_bytes"] = (store.run_dir / "scenario-plan.json").stat().st_size
    else:
        note = planner.restore_from_store()
        fp = fingerprint(planner)
        report["restore_note"] = note
        report["fingerprint"] = {k: v for k, v in fp.items() if k != "blob"}
        (out / "fingerprint-read.json").write_text(fp["blob"])
        written = (out / "fingerprint-write.json").read_text()
        report["identical_state"] = written == fp["blob"]
        if not report["identical_state"]:
            a = json.loads(written)
            b = json.loads(fp["blob"])
            report["differing_keys"] = [k for k in a if a[k] != b.get(k)]

        # ---- gate verdict identity across processes, from the suite result ---
        if args.suite_result:
            from neyma_product_driver.scenario_gate import evaluate_gate
            from neyma_product_driver.scenario_suite import SuiteResult

            raw = json.loads(Path(args.suite_result).read_text())
            reloaded = SuiteResult.model_validate(raw)
            v = evaluate_gate(reloaded)
            report["suite_result_reload"] = {
                "total": reloaded.total,
                "passed": reloaded.passed,
                "failed": reloaded.failed,
                "blocked": reloaded.blocked,
                "skipped": reloaded.skipped,
                "expected_required_ids": len(reloaded.expected_required_ids),
                "outcome_ids": len({o.scenario_id for o in reloaded.outcomes}),
                "gate_status": v.status.value,
                "gate_required_total": v.required_total,
                "gate_required_passed": v.required_passed,
                "gate_unverified": len(v.unverified),
                "clusters": len(reloaded.clusters),
                "evidence_verified_flags": sum(
                    1 for o in reloaded.outcomes if o.evidence_verified
                ),
            }
            # Does the reloaded result still have evidence on disk? A resume that
            # believes a stored `evidence_verified: true` without rechecking is
            # trusting a claim, not a fact.
            from neyma_product_driver.scenario_suite import verify_case_evidence

            run_id = raw.get("__run_id__", "")
            problems = [
                verify_case_evidence(
                    o.evidence_path, scenario_id=o.scenario_id, run_id="", iteration=0
                )
                for o in reloaded.outcomes
            ]
            report["suite_result_reload"]["evidence_still_resolves_on_disk"] = sum(
                1 for p in problems if not p
            )
            report["suite_result_reload"]["evidence_now_broken"] = [
                p for p in problems if p
            ][:3]

    print(json.dumps(report, indent=2, default=str))
    (out / f"resume-{args.mode}.json").write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
