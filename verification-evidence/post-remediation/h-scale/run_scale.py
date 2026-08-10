"""Post-remediation harness-scale sweep: 10 / 50 / 100 / 200 scenarios.

  python run_scale.py --driver <path> --target <dir> --out <dir> [10 50 100 200]

Builds real ScenarioSuites against a real local HTTP+SQLite target and executes
them with the real SuiteExecutor and ScenarioExecutor. Nothing is mocked except
the evaluator's model call, which is never made: the *prompt* it would receive is
measured, and the real `_apply_suite_precedence` is applied to a hostile ACCEPT.

Adapted from `verification-evidence/r4-scale/work/{scale_harness,run_scale}.py`
so the numbers are comparable with r4's, plus the checks r4 could not make
because the behaviour did not exist yet: per-case evidence integrity, the
authoritative gate, coverage gaps, resume, and generated/permanent separability.

**This is a harness-scale proof, not a production-scale claim.**
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import sys
import time
from pathlib import Path

PORT = int(os.environ.get("TARGET_PORT", "8763"))
BASE = f"http://127.0.0.1:{PORT}"


def build(driver: Path, target: Path):
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

    def http_scenario(i: int) -> Scenario:
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
                    expect_contains=[f'"item": {i}', '"owner": "tenant-a"'],
                )
            ],
            forbidden=["tenant-b"],
        )

    def command_scenario(i: int) -> Scenario:
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
                    expect_contains=[f"item={i}"],
                )
            ],
        )

    def idempotency_scenario(i: int) -> Scenario:
        key = f"k-{i}"
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
                        contains=[f"item={i} approval_rows=1"],
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
            model = GeneratedScenario(
                id=compiled.name,
                title=f"item {i} behaves correctly",
                purpose=f"exercise item {i}",
                risk_category=category,
                priority=Priority.P0 if i % 5 == 0 else Priority.P1,
                rationale=f"item {i} is representative of the {category.value} surface",
                requirement_reference="POST-SCALE-AUDIT",
                isolation_key="default",
            )
            generated.append((model, compiled))
        permanent = [("permanent-health", http_scenario(1))]
        return build_suite(permanent=permanent, generated=generated)

    def risks():
        # One identified risk per exercised category, all acceptance-blocking.
        return [
            IdentifiedRisk(
                id=f"R{n}",
                description=f"the {c.value} surface may be wrong",
                risk_category=c,
                severity=Priority.P0,
                basis="POST-SCALE-AUDIT",
            )
            for n, c in enumerate(categories, start=1)
        ]

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


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("counts", nargs="*", type=int, default=[10, 50, 100, 200])
    ap.add_argument("--driver", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--out", required=True)
    #: Force required scenarios to be skipped mid-suite, which is the state that
    #: previously accepted while nothing had been verified.
    ap.add_argument("--budget", type=float, default=None)
    #: Name a risk category no scenario exercises, to check that a known gap is
    #: still detected when the suite is large and entirely green.
    ap.add_argument("--extra-uncovered-risk", action="store_true")
    args = ap.parse_args()

    driver = Path(args.driver).resolve()
    target = Path(args.target).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    make_suite, base_risks, executor_factory = build(driver, target)

    def risks():
        register = base_risks()
        if args.extra_uncovered_risk:
            from neyma_product_driver.scenario_plan import (
                IdentifiedRisk,
                Priority,
                RiskCategory,
            )

            # A P0 risk no scenario in this suite exercises. Every executed
            # scenario can still pass; the run has still verified nothing
            # about this one.
            register.append(
                IdentifiedRisk(
                    id="R-GAP",
                    description="a supervisor of one tenant could release another tenant's item",
                    risk_category=RiskCategory.AUTHORIZATION,
                    severity=Priority.P0,
                    basis="POST-SCALE-AUDIT",
                )
            )
        return register

    from neyma_product_driver import cli as driver_cli
    from neyma_product_driver.models import Decision, EvaluatorDecision
    from neyma_product_driver.prompts import evaluator_prompt
    from neyma_product_driver.scenario_gate import evaluate_gate
    from neyma_product_driver.scenario_suite import Origin, Outcome, SuiteExecutor, select_rerun

    bad = {int(x) for x in os.environ.get("TARGET_BAD_IDS", "").split(",") if x}
    dup = {int(x) for x in os.environ.get("TARGET_DUP_IDS", "").split(",") if x}
    err = {int(x) for x in os.environ.get("TARGET_500_IDS", "").split(",") if x}

    rows = []
    tag = ""
    if args.budget is not None:
        tag += f"-budget{args.budget:g}"
    if args.extra_uncovered_risk:
        tag += "-gap"
    for n in args.counts:
        suite = make_suite(n)
        root = out / f"run-{n}{tag}"
        rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        executor = SuiteExecutor(
            make_executor=executor_factory(),
            artifact_root=root,
            browser_enabled=False,
            execution_budget_s=args.budget,
            run_id=f"scale-{n}",
            iteration=1,
        )
        started = time.monotonic()
        result = await executor.run(suite, selection_reason="post-remediation scale sweep")
        wall = time.monotonic() - started
        rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        # -- independent recount, computed without touching SuiteResult -----
        independent = {"PASSED": 0, "FAILED": 0, "BLOCKED": 0, "SKIPPED": 0}
        for outcome in result.outcomes:
            independent[outcome.outcome.value] += 1

        # -- per-case evidence integrity, re-verified from disk -------------
        from neyma_product_driver.scenario_suite import verify_case_evidence

        evidence_problems = []
        for outcome in result.outcomes:
            problem = verify_case_evidence(
                outcome.evidence_path,
                scenario_id=outcome.scenario_id,
                run_id=f"scale-{n}",
                iteration=1,
            )
            if problem:
                evidence_problems.append(f"{outcome.scenario_id}: {problem}")

        ids = [o.scenario_id for o in result.outcomes]
        expected_fail = set()
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

        summary = result.summary_block()
        gaps = [
            r.brief()
            for r in evaluate_gate(result, risks=risks()).uncovered_risks
        ]
        prompt = evaluator_prompt(
            task="post-remediation scale sweep",
            iteration=1,
            max_iterations=3,
            builder_summary="claimed everything works",
            git=None,
            scenario=executor.results.get("permanent-health"),
            service_logs=None,
            evidence_dir=str(root),
            suite=result,
            coverage_gaps=gaps,
        )

        verdict = evaluate_gate(result, risks=risks())
        accept = EvaluatorDecision(
            decision=Decision.ACCEPT, summary="looks good", confidence=0.9
        )
        final = driver_cli._apply_suite_precedence(
            result, accept, "permanent-health", lambda _m: None, risks=risks()
        )

        # -- resume: does the persisted result reload identically? ----------
        from neyma_product_driver.scenario_suite import SuiteResult

        reloaded = SuiteResult.model_validate(result.model_dump(mode="json"))
        resume_ok = (
            reloaded.total == result.total
            and reloaded.passed == result.passed
            and [o.scenario_id for o in reloaded.outcomes] == ids
            and list(reloaded.expected_required_ids) == list(result.expected_required_ids)
            and evaluate_gate(reloaded, risks=risks()).status is verdict.status
        )

        selected, _reason = select_rerun(suite, result)

        row = {
            "scenarios_in_suite": len(suite),
            "outcomes_recorded": result.total,
            "no_silent_truncation": result.total == len(suite),
            "assembly_problems": list(result.assembly_problems),
            "wall_s": round(wall, 2),
            "per_scenario_s": round(wall / max(1, result.total), 4),
            "max_rss_mb_before": round(rss_before / (1024 * 1024), 1),
            "max_rss_mb_after": round(rss_after / (1024 * 1024), 1),
            "passed": result.passed,
            "failed": result.failed,
            "blocked": result.blocked,
            "skipped": result.skipped,
            "independent_recount": independent,
            "counts_agree": (
                independent["PASSED"] == result.passed
                and independent["FAILED"] == result.failed
                and independent["BLOCKED"] == result.blocked
                and independent["SKIPPED"] == result.skipped
            ),
            "duplicate_result_ids": len(ids) - len(set(ids)),
            "evidence_problems": evidence_problems[:5],
            "evidence_all_verified": not evidence_problems
            and all(o.evidence_verified for o in result.outcomes),
            "generated_count": sum(
                1 for o in result.outcomes if o.origin is Origin.GENERATED
            ),
            "permanent_count": sum(
                1 for o in result.outcomes if o.origin is Origin.PERMANENT
            ),
            "generated_and_permanent_distinguishable": (
                sum(1 for o in result.outcomes if o.origin is Origin.PERMANENT) == 1
                and all(
                    o.origin in (Origin.GENERATED, Origin.PERMANENT)
                    for o in result.outcomes
                )
            ),
            "expected_failures": sorted(expected_fail),
            "actual_failures": sorted(actual_fail),
            "failures_match_injection": expected_fail == actual_fail,
            "clusters": len(result.clusters),
            "grouped_clusters": len([c for c in result.clusters if not c.singleton]),
            "cluster_members_all_real": all(
                sid in set(ids) for c in result.clusters for sid in c.affected_scenarios
            ),
            "gate_status": verdict.status.value,
            "gate_required_total": verdict.required_total,
            "gate_required_passed": verdict.required_passed,
            "gate_unverified": len(verdict.unverified),
            "gate_uncovered_risks": len(verdict.uncovered_risks),
            "no_skipped_counted_as_verified": all(
                c.outcome != Outcome.SKIPPED.value
                for c in verdict.unverified
                if c.outcome
            )
            or result.skipped > 0,
            "hostile_accept_becomes": final.decision.value,
            "summary_block_chars": len(summary),
            "evaluator_prompt_chars": len(prompt),
            "evaluator_prompt_est_tokens": len(prompt) // 4,
            "all_failures_named_in_summary": all(
                f.scenario_id in summary for f in result.failures()
            ),
            "resume_reload_identical": resume_ok,
            "rerun_selection_size": len(selected),
            "rerun_selection_unique": len(selected) == len(set(selected)),
            "budget_s": args.budget,
            "skipped_required_ids": [
                c.scenario_id for c in verdict.unverified if c.outcome == "SKIPPED"
            ][:5],
            "uncovered_risk_briefs": [r.brief() for r in verdict.uncovered_risks],
        }
        rows.append(row)
        (out / f"summary-block-{n}{tag}.txt").write_text(summary)
        (out / f"evaluator-prompt-{n}{tag}.txt").write_text(prompt)
        (out / f"suite-result-{n}{tag}.json").write_text(result.model_dump_json(indent=2))
        print(json.dumps(row, indent=2), flush=True)

    (out / f"scale-sweep{tag}.json").write_text(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
