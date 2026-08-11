"""H-LOOP: read a run directory RAW and report facts, link by link.

  python analyse_run.py --run-dir <dir> [--transcript <file>]

Deliberately NOT the prior pass's `analyse()`. That one derived
`targeted_rerun` and `adaptive_generation` from fields its own report later
admitted were computed misleadingly. This reads the persisted artifacts and, for
every link, records the artifact or transcript line that proves it — including
re-verifying the evidence directories itself rather than trusting the
`evidence_verified` flag written into the record.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def jload(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def fractional_timeouts(obj, trail=""):
    """Every timeout_s in the plan that has a fractional part."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "timeout_s" and isinstance(v, (int, float)) and float(v) != int(float(v)):
                found.append((trail + "." + k, v))
            else:
                found += fractional_timeouts(v, trail + "." + str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += fractional_timeouts(v, trail + f"[{i}]")
    return found


def all_timeouts(obj, trail=""):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "timeout_s" and isinstance(v, (int, float)):
                found.append((trail + "." + k, v))
            else:
                found += all_timeouts(v, trail + "." + str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += all_timeouts(v, trail + f"[{i}]")
    return found


def verify_evidence(evidence_path: str, scenario_id: str, run_id: str, iteration: int) -> str:
    """Independent re-implementation of the harness's own evidence check."""
    if not evidence_path:
        return "no evidence path cited"
    d = Path(evidence_path)
    if not d.is_dir():
        return f"not a directory: {evidence_path}"
    rec = d / "result.json"
    if not rec.exists() or rec.stat().st_size == 0:
        return f"no usable result.json in {evidence_path}"
    body = jload(rec)
    if not isinstance(body, dict):
        return "result.json is not an object"
    if str(body.get("scenario_id", "")) != scenario_id:
        return f"result.json belongs to {body.get('scenario_id')!r}, not {scenario_id!r}"
    if run_id and str(body.get("run_id", "")) != run_id:
        return f"result.json belongs to run {body.get('run_id')!r}"
    if iteration and int(body.get("iteration", 0) or 0) != iteration:
        return f"result.json was written in iteration {body.get('iteration')}"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--transcript", default="")
    args = ap.parse_args()
    run_dir = Path(args.run_dir).resolve()
    report: dict = {"run_dir": str(run_dir)}

    state = jload(run_dir / "state.json") or {}
    plan = jload(run_dir / "scenario-plan.json") or {}
    report["status"] = state.get("status")
    report["run_id"] = state.get("run_id", "")
    run_id = report["run_id"]

    # ---- generation waves, raw -----------------------------------------
    report["waves"] = [
        {
            "wave": w.get("wave"),
            "stage": w.get("stage"),
            "proposed": w.get("proposed"),
            "accepted_ids": w.get("accepted_ids", []),
            "n_accepted": len(w.get("accepted_ids", [])),
            "n_rejected": len(w.get("rejected", [])),
            "rejected_reasons": [
                (r.get("reasons") or r.get("reason") or r) for r in (w.get("rejected") or [])
            ][:8],
            "reasoner_error": w.get("reasoner_error", ""),
            "budget_notes": w.get("budget_notes", []),
        }
        for w in plan.get("waves", [])
    ]
    report["risks"] = [
        {
            "id": r.get("id"),
            "severity": r.get("severity"),
            "category": r.get("risk_category"),
            "description": (r.get("description") or "")[:160],
        }
        for r in plan.get("risks", [])
    ]
    report["observed_failure_ids"] = plan.get("observed_failure_ids", [])
    report["observed_cluster_ids"] = plan.get("observed_cluster_ids", [])

    scenarios = plan.get("scenarios", [])
    report["scenarios"] = [
        {
            "id": s.get("id"),
            "proposed_id": s.get("proposed_id", ""),
            "stage": (s.get("provenance") or {}).get("stage"),
            "wave": (s.get("provenance") or {}).get("wave"),
            "risk_category": s.get("risk_category"),
            "priority": s.get("priority"),
            "source_failures": (s.get("provenance") or {}).get("source_failures", []),
            "source_clusters": (s.get("provenance") or {}).get("source_clusters", []),
            "generating_risk": ((s.get("provenance") or {}).get("generating_risk") or "")[:200],
            "title": (s.get("title") or "")[:120],
        }
        for s in scenarios
    ]
    report["adaptive_admitted"] = [
        s for s in report["scenarios"] if s["stage"] == "adaptive"
    ]

    # ---- timeout_s widening, live --------------------------------------
    report["plan_timeouts"] = [
        {"where": w, "value": v} for w, v in all_timeouts(plan)
    ]
    report["plan_fractional_timeouts"] = [
        {"where": w, "value": v} for w, v in fractional_timeouts(plan)
    ]

    # ---- per iteration, from the persisted records ----------------------
    iterations = []
    for record in state.get("iterations", []):
        n = record.get("iteration")
        suite = record.get("suite") or {}
        outcomes = suite.get("outcomes", [])
        # independent re-verification of every cited evidence directory
        evidence_problems = []
        for o in outcomes:
            problem = verify_evidence(
                o.get("evidence_path", ""), o.get("scenario_id", ""), run_id, int(n or 0)
            )
            if problem:
                evidence_problems.append({"scenario": o.get("scenario_id"), "problem": problem})
        iterations.append(
            {
                "iteration": n,
                "decision": (record.get("decision") or {}).get("decision"),
                "decision_summary": ((record.get("decision") or {}).get("summary") or "")[:400],
                "suite_full_run": suite.get("full_run"),
                "suite_selection_reason": suite.get("selection_reason", ""),
                "executed": [
                    {
                        "id": o.get("scenario_id"),
                        "origin": o.get("origin"),
                        "outcome": o.get("outcome"),
                        "risk": o.get("risk_category"),
                        "evidence_verified_flag": o.get("evidence_verified"),
                        "failed_assertions": (o.get("failed_assertions") or [])[:4],
                    }
                    for o in outcomes
                ],
                "n_passed": sum(1 for o in outcomes if o.get("outcome") == "PASSED"),
                "n_failed": sum(1 for o in outcomes if o.get("outcome") == "FAILED"),
                "n_blocked": sum(1 for o in outcomes if o.get("outcome") == "BLOCKED"),
                "n_skipped": sum(1 for o in outcomes if o.get("outcome") == "SKIPPED"),
                "clusters": suite.get("clusters", []),
                "correction_sent": bool(record.get("correction_prompt_sent")),
                "correction_excerpt": (record.get("correction_prompt_sent") or "")[:2500],
                "completion_audit": (record.get("completion_audit") or {}).get("decision"),
                "protocol": (record.get("protocol_resolution") or {}).get("status"),
                "notes": record.get("notes", []),
                "evidence_problems_found_independently": evidence_problems,
                "builder_summary_head": (record.get("builder_summary") or "")[:600],
            }
        )
    report["iterations"] = iterations

    # ---- the on-disk per-iteration suite records (not via state.json) ----
    on_disk = []
    for d in sorted(run_dir.glob("iteration-*")):
        sr = jload(d / "suite-result.json")
        on_disk.append(
            {
                "dir": d.name,
                "has_suite_result": sr is not None,
                "full_run": (sr or {}).get("full_run"),
                "selection_reason": (sr or {}).get("selection_reason", ""),
                "n_outcomes": len((sr or {}).get("outcomes", [])),
                "case_dirs": sorted(p.name for p in d.iterdir() if p.is_dir()),
                "files": sorted(p.name for p in d.iterdir() if p.is_file()),
            }
        )
    report["on_disk_iterations"] = on_disk

    report["journal_written"] = (run_dir / "journal.json").exists()
    report["founder_summary_written"] = (run_dir / "FOUNDER-SUMMARY.md").exists()
    report["decision_files"] = sorted(str(p.relative_to(run_dir)) for p in run_dir.rglob("decision.json"))

    # ---- transcript proof lines -----------------------------------------
    if args.transcript and Path(args.transcript).exists():
        text = Path(args.transcript).read_text(errors="replace").splitlines()
        markers = {
            "plan_initial": r"planning verification scenarios",
            "builder": r"builder working",
            "diff_refine": r"refining the scenario plan against what the builder changed",
            "suite_run": r"running scenario suite",
            "narrowed_green_widen": r"narrowed suite is green; running the full required regression set",
            "adaptive": r"expanding verification around what failed",
            "evaluator": r"evaluating observed behaviour",
            "gate_refusal": r"product evaluation ACCEPTed, but",
            "accept": r"^ACCEPT|status: ACCEPTED|ACCEPTED",
            "blocked": r"BLOCKED",
            "wave": r"wave \d+",
            "generation_error": r"generator returned|reasoner_error|no usable structured output",
        }
        hits = {}
        for name, pattern in markers.items():
            rx = re.compile(pattern)
            hits[name] = [
                {"line": i + 1, "text": ln.strip()[:300]}
                for i, ln in enumerate(text)
                if rx.search(ln)
            ][:40]
        report["transcript_hits"] = hits
        report["transcript_lines"] = len(text)

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
