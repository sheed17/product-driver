"""Drive the ACTUAL Product Driver builder against a disposable fixture repository.

  python run_live_builder.py --driver <path> --work <dir> [--max-iterations 4]

Nothing about the driver is stubbed. This invokes `neyma-product-driver run
--auto-scenarios` exactly as an operator would: a live builder session, a live
scenario generator, live scenario execution against the fixture's own HTTP
service, and a live evaluator, with the acceptance gate deciding the outcome.

The fixture (see `make_fixture.py`) has no git remote, binds loopback only,
needs no credential and depends on nothing outside the standard library, so the
run has no way to reach anything outside its own directory.

Afterwards `analyse()` reads the run's own artifacts and reports, link by link,
which parts of the chain the evidence actually shows.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def make_config(driver: Path, fixture: Path, runs: Path, work: Path) -> Path:
    template = (HERE / "fixture.config.yaml").read_text()
    header = (
        f"neyma_repo: {fixture}\n"
        f"runs_dir: {runs}\n"
        f"scenarios_dir: {fixture / 'scenarios'}\n"
        f"preservation_dir: {work / 'preservation'}\n"
        f"temp_workspace_root: {work / 'tmp'}\n"
    )
    path = work / "driver.config.yaml"
    path.write_text(header + template, encoding="utf-8")
    return path


def analyse(run_dir: Path, fixture: Path) -> dict:
    """Read the run's own artifacts and say which links the evidence shows."""
    findings: dict[str, object] = {"run_dir": str(run_dir)}

    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    findings["status"] = state.get("status", "(no state)")
    iterations = state.get("iterations", [])
    findings["iterations"] = len(iterations)

    plan_path = run_dir / "scenario-plan.json"
    plan = json.loads(plan_path.read_text()) if plan_path.exists() else {}
    scenarios = plan.get("scenarios", [])
    waves = plan.get("waves", [])
    findings["plan"] = {
        "scenarios": len(scenarios),
        "waves": [
            {
                "wave": w.get("wave"),
                "stage": w.get("stage"),
                "proposed": w.get("proposed"),
                "accepted": len(w.get("accepted_ids", [])),
                "refused": len(w.get("rejected", [])),
                "reasoner_error": w.get("reasoner_error", ""),
            }
            for w in waves
        ],
        "risks": len(plan.get("risks", [])),
        "observed_failure_ids": plan.get("observed_failure_ids", []),
        "observed_cluster_ids": plan.get("observed_cluster_ids", []),
    }

    stages = {w.get("stage") for w in waves}
    adaptive = [
        s
        for s in scenarios
        if (s.get("provenance") or {}).get("stage") == "adaptive"
    ]
    findings["chain"] = {
        "initial_generation": any(w.get("stage") == "initial" for w in waves)
        and any(len(w.get("accepted_ids", [])) for w in waves if w.get("stage") == "initial"),
        "diff_refinement": "diff_refinement" in stages or "refinement" in stages,
        "adaptive_generation": bool(adaptive),
        "adaptive_scenarios_cite_a_failure": [
            {
                "id": s["id"],
                "source_failures": (s.get("provenance") or {}).get("source_failures", []),
                "source_clusters": (s.get("provenance") or {}).get("source_clusters", []),
                "generating_risk": (s.get("provenance") or {}).get("generating_risk", ""),
            }
            for s in adaptive
        ],
    }

    # Per-iteration suite outcomes, straight from the persisted records.
    per_iteration = []
    for record in iterations:
        suite = record.get("suite") or {}
        outcomes = suite.get("outcomes", [])
        per_iteration.append(
            {
                "iteration": record.get("iteration"),
                "decision": (record.get("decision") or {}).get("decision"),
                "summary": ((record.get("decision") or {}).get("summary") or "")[:200],
                "suite_total": len(outcomes),
                "passed": sum(1 for o in outcomes if o.get("outcome") == "PASSED"),
                "failed": sum(1 for o in outcomes if o.get("outcome") == "FAILED"),
                "blocked": sum(1 for o in outcomes if o.get("outcome") == "BLOCKED"),
                "skipped": sum(1 for o in outcomes if o.get("outcome") == "SKIPPED"),
                "full_run": suite.get("full_run"),
                "selection_reason": suite.get("selection_reason", ""),
                "clusters": len(suite.get("clusters", [])),
                "grouped_clusters": len(
                    [c for c in suite.get("clusters", []) if not c.get("singleton", True)]
                ),
                "failures": [
                    {
                        "id": o["scenario_id"],
                        "risk": o.get("risk_category"),
                        "assertions": o.get("failed_assertions", [])[:3],
                    }
                    for o in outcomes
                    if o.get("outcome") in ("FAILED", "BLOCKED")
                ],
                "evidence_all_verified": all(
                    o.get("evidence_verified") for o in outcomes
                )
                if outcomes
                else None,
                "correction_sent": bool(record.get("correction_prompt_sent")),
                "correction_excerpt": (record.get("correction_prompt_sent") or "")[:600],
                "investigation": bool(record.get("investigation")),
            }
        )
    findings["iterations_detail"] = per_iteration

    detected = [i for i in per_iteration if i["failed"] or i["blocked"]]
    findings["chain"].update(
        {
            "defect_detected_by_a_generated_scenario": bool(
                [
                    f
                    for i in detected
                    for f in i["failures"]
                    if f["id"] not in ("fixture_backend",)
                ]
            ),
            "grounded_correction_sent_to_builder": any(
                i["correction_sent"] for i in per_iteration
            ),
            "builder_remediated": len(per_iteration) > 1,
            "targeted_rerun": any(i["full_run"] is False for i in per_iteration),
            "widened_regression": any(
                i["full_run"] is True and i["iteration"] > 1 for i in per_iteration
            ),
            "clustering_used": any(i["grouped_clusters"] for i in per_iteration),
            "evidence_verified_every_iteration": all(
                i["evidence_all_verified"] in (True, None) for i in per_iteration
            ),
            "acceptance_gate_reached": findings["status"]
            in ("ACCEPTED", "BLOCKED", "NEEDS_INDEPENDENT_REVIEW", "MAX_ITERATIONS"),
        }
    )

    # Did the fixture actually get fixed? Asked of the product, not of the run.
    findings["fixture_diff_stat"] = subprocess.run(
        ["git", "diff", "--stat", "HEAD"],
        cwd=fixture,
        capture_output=True,
        text=True,
    ).stdout.strip() or "(no uncommitted change)"
    findings["fixture_log"] = subprocess.run(
        ["git", "log", "--oneline", "-5"], cwd=fixture, capture_output=True, text=True
    ).stdout.strip()
    findings["fixture_has_no_remote"] = not subprocess.run(
        ["git", "remote"], cwd=fixture, capture_output=True, text=True
    ).stdout.strip()

    journal = run_dir / "journal.json"
    findings["run_journal_written"] = journal.exists()
    findings["founder_summary_written"] = (run_dir / "FOUNDER-SUMMARY.md").exists()
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--max-iterations", type=int, default=4)
    ap.add_argument("--analyse-only", default="")
    args = ap.parse_args()

    driver = Path(args.driver).resolve()
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    fixture = work / "fixture"
    runs = work / "runs"

    if args.analyse_only:
        findings = analyse(Path(args.analyse_only).resolve(), fixture)
        print(json.dumps(findings, indent=2))
        (work / "findings.json").write_text(json.dumps(findings, indent=2))
        return 0

    # A free port, chosen per run. A fixed one lets a stale process from an
    # earlier run answer for the build under test — which the driver detects and
    # refuses to reason from, but which wastes the run.
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    print(f"fixture port: {port}", flush=True)

    subprocess.run(
        [
            sys.executable,
            str(HERE / "make_fixture_stale_tests.py"),
            "--dest",
            str(fixture),
            "--port",
            str(port),
        ],
        check=True,
    )
    config = make_config(driver, fixture, runs, work)

    started = time.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "neyma_product_driver",
            "run",
            "--config",
            str(config),
            "--auto-scenarios",
            "--max-iterations",
            str(args.max_iterations),
        ],
        cwd=str(driver),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        text=True,
    )
    elapsed = time.monotonic() - started

    run_dirs = sorted(runs.glob("*/"), key=lambda p: p.stat().st_mtime)
    if not run_dirs:
        print("no run directory was produced")
        return 1
    findings = analyse(run_dirs[-1], fixture)
    findings["driver_exit_code"] = proc.returncode
    findings["wall_s"] = round(elapsed, 1)
    (work / "findings.json").write_text(json.dumps(findings, indent=2))
    print(json.dumps(findings, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
