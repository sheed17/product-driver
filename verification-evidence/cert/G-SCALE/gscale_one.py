"""G-SCALE: one scale point, executed in its own process.

Written by the INDEPENDENT certification reviewer. It deliberately does NOT
trust any total the driver computes. Everything is recounted from either
(a) the raw outcome list, or (b) the evidence directories on disk.

Differences from the implementer's verification-evidence/post-remediation/
h-scale/run_scale.py, which this replaces:

  * evidence is recounted by WALKING THE FILESYSTEM, not by calling the
    driver's own verify_case_evidence over the driver's own outcome list;
  * evidence-directory UNIQUENESS is checked (two cases sharing one directory
    was not checked at all before);
  * the outcome state is re-derived from each on-disk result.json rather than
    read off ScenarioOutcome;
  * every failure id is checked to appear in the EVALUATOR PROMPT, not merely
    in summary_block();
  * resume is a genuinely fresh process reading JSON from disk (see
    gscale_resume.py), not model_validate(model_dump()) in the same process;
  * run_id/iteration attribution is exercised with iteration != 1 and with a
    deliberately wrong run_id/iteration, so the check is shown to have teeth;
  * counts are cross-checked against a plain `find`-style directory count.

Usage:
  python gscale_one.py --driver <repo> --target <dir> --out <dir> --n 200 \
      [--budget S] [--gap] [--all-fail] [--long-messages] [--noncritical]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import sys
import time
from collections import Counter
from pathlib import Path

PORT = int(os.environ.get("TARGET_PORT", "8791"))
BASE = f"http://127.0.0.1:{PORT}"


def build(driver: Path, target: Path, args):
    sys.path.insert(0, str(driver))

    from neyma_product_driver.config import ScenarioRunConfig
    from neyma_product_driver.scenario_plan import (
        GeneratedScenario,
        IdentifiedRisk,
        Priority,
        RiskCategory,
    )
    from neyma_product_driver.scenario_suite import build_suite
    from neyma_product_driver.scenarios import (
        CommandSpec,
        RequestSpec,
        Scenario,
        ScenarioExecutor,
        ScenarioStep,
        StateCheckSpec,
    )

    py = sys.executable

    # When --long-messages is set the expectation strings are padded so that the
    # failure detail the driver carries into the prompt is long. This is the
    # attack on "the evaluator prompt stays bounded".
    pad = ("X" * 400) if args.long_messages else ""

    def http_scenario(i: int) -> Scenario:
        want_owner = "tenant-zzz" if args.all_fail else "tenant-a"
        return Scenario(
            name=f"case-{i:03d}-http",
            description=f"GET /item/{i} returns the item owned by tenant-a",
            mode="backend",
            app_url=BASE,
            readiness=[{"http": f"{BASE}/health", "expect_status": 200}],
            requests=[
                RequestSpec(
                    name=f"read item {i}",
                    method="GET",
                    path=f"/item/{i}",
                    expect_status=200,
                    expect_contains=[f'"item": {i}', f'"owner": "{want_owner}"{pad}'],
                )
            ],
            forbidden=["tenant-b"],
        )

    def command_scenario(i: int) -> Scenario:
        want = f"item={i}" if not args.all_fail else f"item=NOPE{i}{pad}"
        return Scenario(
            name=f"case-{i:03d}-cmd",
            description=f"the persisted-state probe reports item {i}",
            mode="backend",
            app_url=BASE,
            readiness=[{"http": f"{BASE}/health", "expect_status": 200}],
            env={"TARGET_DB": os.environ["TARGET_DB"]},
            commands=[
                CommandSpec(
                    name=f"probe {i}",
                    run=f"{py} {target}/probe.py {i}",
                    expect_exit_code=0,
                    expect_contains=[want],
                )
            ],
        )

    def idempotency_scenario(i: int) -> Scenario:
        key = f"k-{i}"
        want = f"item={i} approval_rows=1"
        if args.all_fail:
            want = f"item={i} approval_rows=9999{pad}"
        return Scenario(
            name=f"case-{i:03d}-idem",
            description=f"approving item {i} twice with one key stores exactly one row",
            mode="backend",
            app_url=BASE,
            readiness=[{"http": f"{BASE}/health", "expect_status": 200}],
            env={"TARGET_DB": os.environ["TARGET_DB"]},
            steps=[
                ScenarioStep(
                    kind="request",
                    name="first approve",
                    request=RequestSpec(
                        method="POST",
                        path="/approve",
                        json={"item": i, "key": key},
                        expect_status=200,
                    ),
                ),
                ScenarioStep(
                    kind="request",
                    name="repeat approve, same key",
                    request=RequestSpec(
                        method="POST",
                        path="/approve",
                        json={"item": i, "key": key},
                        expect_status=200,
                    ),
                ),
                ScenarioStep(
                    kind="state_check",
                    name="exactly one approval row persisted",
                    state_check=StateCheckSpec(
                        command=f"{py} {target}/probe.py {i}",
                        contains=[want],
                        not_contains=["approval_rows=2"],
                    ),
                ),
            ],
        )

    kinds = [http_scenario, command_scenario, idempotency_scenario]
    categories = [
        RiskCategory.CROSS_TENANT,
        RiskCategory.PERSISTENCE_FAILURE,
        RiskCategory.IDEMPOTENCY,
    ]

    def make_suite(n: int):
        generated = []
        for i in range(1, n + 1):
            compiled = kinds[i % len(kinds)](i)
            category = categories[i % len(categories)]
            if args.noncritical and i % 7 == 0:
                priority = Priority.P2
            elif i % 5 == 0:
                priority = Priority.P0
            else:
                priority = Priority.P1
            model = GeneratedScenario(
                id=compiled.name,
                title=f"item {i} behaves correctly",
                purpose=f"exercise item {i}",
                risk_category=category,
                priority=priority,
                rationale=f"item {i} is representative of the {category.value} surface",
                requirement_reference="G-SCALE-CERT",
                isolation_key="default",
            )
            generated.append((model, compiled))
        permanent = [("permanent-health", http_scenario(1))]
        return build_suite(permanent=permanent, generated=generated)

    def risks():
        register = [
            IdentifiedRisk(
                id=f"R{n}",
                description=f"the {c.value} surface may be wrong",
                risk_category=c,
                severity=Priority.P0,
                basis="G-SCALE-CERT",
            )
            for n, c in enumerate(categories, start=1)
        ]
        if args.gap:
            register.append(
                IdentifiedRisk(
                    id="R-GAP",
                    description=(
                        "a supervisor of one tenant could release another tenant's item"
                    ),
                    risk_category=RiskCategory.AUTHORIZATION,
                    severity=Priority.P0,
                    basis="G-SCALE-CERT",
                )
            )
        return register

    def executor_factory():
        cfg = ScenarioRunConfig(
            command_timeout_s=30,
            readiness_timeout_s=20,
            readiness_poll_interval_s=0.2,
            http_timeout_s=15,
            browser_enabled=False,
            headless=True,
            capture_trace=False,
        )
        return lambda artifact_dir: ScenarioExecutor(target, cfg, artifact_dir)

    return make_suite, risks, executor_factory


# --------------------------------------------------------------------------
# Independent recounting — nothing below reads a driver-computed total.
# --------------------------------------------------------------------------


def recount_from_disk(root: Path, run_id: str, iteration: int) -> dict:
    """Walk the evidence tree and recount, without asking the driver anything."""
    scen_root = root / "scenarios"
    dirs = sorted(p for p in scen_root.iterdir() if p.is_dir()) if scen_root.exists() else []
    records: dict[str, dict] = {}
    problems: list[str] = []
    dirs_without_record = []
    ids_seen = Counter()
    for d in dirs:
        rec_path = d / "result.json"
        if not rec_path.exists():
            dirs_without_record.append(str(d))
            continue
        try:
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{d}: unreadable ({exc})")
            continue
        sid = str(rec.get("scenario_id", ""))
        ids_seen[sid] += 1
        records[str(d)] = rec
        if str(rec.get("run_id", "")) != run_id:
            problems.append(f"{d}: run_id {rec.get('run_id')!r} != {run_id!r}")
        if int(rec.get("iteration", -1)) != iteration:
            problems.append(f"{d}: iteration {rec.get('iteration')!r} != {iteration}")
    return {
        "evidence_dirs_on_disk": len(dirs),
        "evidence_dirs_with_record": len(records),
        "evidence_dirs_without_record": dirs_without_record[:5],
        "distinct_scenario_ids_on_disk": len(ids_seen),
        "scenario_ids_appearing_more_than_once_on_disk": [
            s for s, c in ids_seen.items() if c > 1
        ][:10],
        "attribution_problems": problems[:10],
        "attribution_problem_count": len(problems),
        "records": records,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--budget", type=float, default=None)
    ap.add_argument("--gap", action="store_true")
    ap.add_argument("--all-fail", action="store_true")
    ap.add_argument("--long-messages", action="store_true")
    ap.add_argument("--noncritical", action="store_true")
    ap.add_argument("--tag", default="")
    ap.add_argument("--iteration", type=int, default=3)
    args = ap.parse_args()

    driver = Path(args.driver).resolve()
    target = Path(args.target).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    make_suite, risks, executor_factory = build(driver, target, args)

    from neyma_product_driver import cli as driver_cli
    from neyma_product_driver.models import Decision, EvaluatorDecision
    from neyma_product_driver.prompts import evaluator_prompt
    from neyma_product_driver.scenario_gate import evaluate_gate
    from neyma_product_driver.scenario_suite import (
        Origin,
        Outcome,
        SuiteExecutor,
        select_rerun,
        verify_case_evidence,
    )

    bad = {int(x) for x in os.environ.get("TARGET_BAD_IDS", "").split(",") if x}
    dup = {int(x) for x in os.environ.get("TARGET_DUP_IDS", "").split(",") if x}
    err = {int(x) for x in os.environ.get("TARGET_500_IDS", "").split(",") if x}

    n = args.n
    tag = args.tag
    run_id = f"gscale-{n}{tag}"
    iteration = args.iteration

    t_build = time.monotonic()
    suite = make_suite(n)
    build_s = time.monotonic() - t_build

    root = out / f"run-{n}{tag}"
    executor = SuiteExecutor(
        make_executor=executor_factory(),
        artifact_root=root,
        browser_enabled=False,
        execution_budget_s=args.budget,
        run_id=run_id,
        iteration=iteration,
    )
    started = time.monotonic()
    result = await executor.run(suite, selection_reason="G-SCALE certification sweep")
    wall = time.monotonic() - started
    rss_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # ---- recount 1: from the raw outcome list, not from result.passed --------
    tally = Counter(o.outcome.value for o in result.outcomes)
    ids = [o.scenario_id for o in result.outcomes]
    id_counts = Counter(ids)
    ev_paths = [o.evidence_path for o in result.outcomes if o.evidence_path]
    ev_counts = Counter(ev_paths)
    shared_evidence_dirs = {p: c for p, c in ev_counts.items() if c > 1}

    # ---- recount 2: from the filesystem -------------------------------------
    disk = recount_from_disk(root, run_id, iteration)
    records = disk.pop("records")

    executed_ids = {
        o.scenario_id for o in result.outcomes if o.outcome is not Outcome.SKIPPED
    }
    disk_ids = {str(r.get("scenario_id", "")) for r in records.values()}

    # ---- recount 3: re-derive pass/fail from the on-disk record -------------
    # NOTE: ScenarioResult.passed is a *property* and is therefore NOT present in
    # the persisted record. The verdict has to be re-derived from the raw facts,
    # replicating models.ScenarioResult.passed exactly.
    def rederive_passed(rec: dict) -> bool:
        return (
            rec.get("error") is None
            and bool(rec.get("readiness_ok"))
            and all(bool(a.get("passed")) for a in (rec.get("assertions") or []))
        )

    disk_state = Counter()
    state_disagreements = []
    for path, rec in records.items():
        sid = str(rec.get("scenario_id", ""))
        outcome = result.by_id(sid)
        disk_passed = rederive_passed(rec)
        disk_state["disk_passed" if disk_passed else "disk_not_passed"] += 1
        if outcome is None:
            state_disagreements.append(f"{sid}: on disk but no outcome recorded")
            continue
        recorded_pass = outcome.outcome is Outcome.PASSED
        if recorded_pass != disk_passed:
            state_disagreements.append(
                f"{sid}: outcome={outcome.outcome.value} but result.json passed={disk_passed}"
            )

    # ---- teeth check: does verify_case_evidence actually reject bad stamps? --
    teeth = {}
    if result.outcomes:
        probe = next((o for o in result.outcomes if o.evidence_path), None)
        if probe is not None:
            teeth = {
                "correct_stamp_accepted": verify_case_evidence(
                    probe.evidence_path,
                    scenario_id=probe.scenario_id,
                    run_id=run_id,
                    iteration=iteration,
                )
                == "",
                "wrong_run_id_rejected": bool(
                    verify_case_evidence(
                        probe.evidence_path,
                        scenario_id=probe.scenario_id,
                        run_id=run_id + "-OTHER",
                        iteration=iteration,
                    )
                ),
                "wrong_iteration_rejected": bool(
                    verify_case_evidence(
                        probe.evidence_path,
                        scenario_id=probe.scenario_id,
                        run_id=run_id,
                        iteration=iteration + 1,
                    )
                ),
                "wrong_scenario_rejected": bool(
                    verify_case_evidence(
                        probe.evidence_path,
                        scenario_id=probe.scenario_id + "-OTHER",
                        run_id=run_id,
                        iteration=iteration,
                    )
                ),
            }

    # ---- injected-defect expectation, recomputed here -----------------------
    expected_fail = set()
    if not args.all_fail:
        for i in range(1, n + 1):
            kind = i % 3
            name = {
                0: f"case-{i:03d}-http",
                1: f"case-{i:03d}-cmd",
                2: f"case-{i:03d}-idem",
            }[kind]
            if kind == 0 and (i in bad or i in err):
                expected_fail.add(name)
            if kind == 2 and i in dup:
                expected_fail.add(name)
    actual_fail = {o.scenario_id for o in result.failures()}

    # ---- gate, independently recomputed -------------------------------------
    register = risks()
    t0 = time.monotonic()
    verdict = evaluate_gate(result, risks=register)
    gate_s = time.monotonic() - t0

    my_required_ids = [e.scenario_id for e in suite.entries if e.required]
    my_required_passed = sum(
        1
        for sid in my_required_ids
        if (o := result.by_id(sid)) is not None
        and o.outcome is Outcome.PASSED
        and o.evidence_verified
    )

    summary = result.summary_block()
    gaps = [r.brief() for r in verdict.uncovered_risks]
    t0 = time.monotonic()
    prompt = evaluator_prompt(
        task="G-SCALE certification sweep",
        iteration=iteration,
        max_iterations=3,
        builder_summary="claimed everything works",
        git=None,
        scenario=executor.results.get("permanent-health"),
        service_logs=None,
        evidence_dir=str(root),
        suite=result,
        coverage_gaps=gaps,
    )
    prompt_s = time.monotonic() - t0

    failures = result.failures()
    missing_from_prompt = [f.scenario_id for f in failures if f.scenario_id not in prompt]
    missing_from_summary = [f.scenario_id for f in failures if f.scenario_id not in summary]

    accept = EvaluatorDecision(
        decision=Decision.ACCEPT, summary="looks good", confidence=0.9
    )
    final = driver_cli._apply_suite_precedence(
        result, accept, "permanent-health", lambda _m: None, risks=register
    )

    t0 = time.monotonic()
    selected, _reason = select_rerun(suite, result)
    rerun_s = time.monotonic() - t0

    row = {
        "n_generated": n,
        "tag": tag,
        "suite_size": len(suite),
        "outcomes_recorded": len(result.outcomes),
        "counts_match_suite_size": len(result.outcomes) == len(suite),
        "assembly_problems": list(result.assembly_problems),
        "build_suite_s": round(build_s, 4),
        "wall_s": round(wall, 3),
        "per_scenario_s": round(wall / max(1, len(result.outcomes)), 4),
        "gate_s": round(gate_s, 5),
        "prompt_s": round(prompt_s, 5),
        "rerun_select_s": round(rerun_s, 5),
        "cluster_s_included_in_wall": True,
        "max_rss_mb": round(rss_peak / (1024 * 1024), 1),
        # counts, recounted
        "tally_from_outcomes": dict(tally),
        "driver_reported": {
            "passed": result.passed,
            "failed": result.failed,
            "blocked": result.blocked,
            "skipped": result.skipped,
            "total": result.total,
        },
        "counts_agree": (
            tally.get("PASSED", 0) == result.passed
            and tally.get("FAILED", 0) == result.failed
            and tally.get("BLOCKED", 0) == result.blocked
            and tally.get("SKIPPED", 0) == result.skipped
            and sum(tally.values()) == result.total
        ),
        # identity
        "duplicate_result_ids": sum(c - 1 for c in id_counts.values() if c > 1),
        "duplicate_result_id_examples": [s for s, c in id_counts.items() if c > 1][:5],
        # evidence
        "evidence_paths_recorded": len(ev_paths),
        "distinct_evidence_paths": len(ev_counts),
        "shared_evidence_dirs": shared_evidence_dirs,
        "evidence_dirs_on_disk": disk["evidence_dirs_on_disk"],
        "evidence_dirs_with_record": disk["evidence_dirs_with_record"],
        "evidence_dirs_without_record": disk["evidence_dirs_without_record"],
        "disk_ids_equal_executed_ids": disk_ids == executed_ids,
        "disk_only_ids": sorted(disk_ids - executed_ids)[:5],
        "executed_only_ids": sorted(executed_ids - disk_ids)[:5],
        "attribution_problem_count": disk["attribution_problem_count"],
        "attribution_problems": disk["attribution_problems"],
        "disk_rederived_states": dict(disk_state),
        "record_states_verdict_explicitly": bool(records) and all(
            "passed" in r for r in records.values()
        ),
        "disk_state_disagreements": state_disagreements[:5],
        "disk_state_disagreement_count": len(state_disagreements),
        "evidence_all_verified_flag": all(o.evidence_verified for o in result.outcomes),
        "verify_case_evidence_teeth": teeth,
        # origin separability
        "generated_count": sum(1 for o in result.outcomes if o.origin is Origin.GENERATED),
        "permanent_count": sum(1 for o in result.outcomes if o.origin is Origin.PERMANENT),
        # failures
        "expected_failures": sorted(expected_fail),
        "actual_failure_count": len(actual_fail),
        "actual_failures_sample": sorted(actual_fail)[:10],
        "failures_match_injection": (
            (expected_fail == actual_fail) if not args.all_fail else None
        ),
        # clusters
        "clusters": len(result.clusters),
        "grouped_clusters": len([c for c in result.clusters if not c.singleton]),
        "cluster_members_all_real": all(
            sid in set(ids) for c in result.clusters for sid in c.affected_scenarios
        ),
        "cluster_membership_disjoint": (
            len([sid for c in result.clusters for sid in c.affected_scenarios])
            == len({sid for c in result.clusters for sid in c.affected_scenarios})
        ),
        "cluster_covers_all_failures": (
            {sid for c in result.clusters for sid in c.affected_scenarios}
            == {f.scenario_id for f in failures}
        ),
        # gate
        "gate_status": verdict.status.value,
        "gate_required_total": verdict.required_total,
        "gate_required_passed": verdict.required_passed,
        "my_required_total": len(my_required_ids),
        "my_required_passed": my_required_passed,
        "gate_required_total_agrees": verdict.required_total == len(my_required_ids),
        "gate_required_passed_agrees": verdict.required_passed == my_required_passed,
        "gate_unverified": len(verdict.unverified),
        "gate_uncovered_risks": len(verdict.uncovered_risks),
        "uncovered_risk_briefs": [r.brief() for r in verdict.uncovered_risks],
        "hostile_accept_becomes": final.decision.value,
        # prompt
        "summary_block_chars": len(summary),
        "evaluator_prompt_chars": len(prompt),
        "evaluator_prompt_est_tokens": len(prompt) // 4,
        "failure_count": len(failures),
        "failures_missing_from_prompt": missing_from_prompt[:10],
        "failures_missing_from_prompt_count": len(missing_from_prompt),
        "failures_missing_from_summary_count": len(missing_from_summary),
        # rerun
        "rerun_selection_size": len(selected),
        "rerun_selection_unique": len(selected) == len(set(selected)),
        "rerun_selection_all_in_suite": set(selected)
        <= {e.scenario_id for e in suite.entries},
        "budget_s": args.budget,
        "skipped_required_sample": [
            c.scenario_id for c in verdict.unverified if c.outcome == "SKIPPED"
        ][:5],
    }

    (out / f"summary-block-{n}{tag}.txt").write_text(summary)
    (out / f"evaluator-prompt-{n}{tag}.txt").write_text(prompt)
    (out / f"suite-result-{n}{tag}.json").write_text(result.model_dump_json(indent=2))
    (out / f"suite-{n}{tag}.json").write_text(suite.model_dump_json(indent=2))
    (out / f"row-{n}{tag}.json").write_text(json.dumps(row, indent=2, default=str))
    print(json.dumps(row, indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
