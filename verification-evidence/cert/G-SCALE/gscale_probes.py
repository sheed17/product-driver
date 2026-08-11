"""G-SCALE hostile probes: ceilings, caps, identity, evidence sharing, cost.

Every probe here is designed to make the system fail, not to confirm it works.
Run:  python gscale_probes.py --driver <repo> --out <dir> [probe ...]
"""

from __future__ import annotations

import argparse
import json
import random
import string
import sys
import time
from collections import Counter
from pathlib import Path

RESULTS: dict[str, dict] = {}


def probe(fn):
    RESULTS[fn.__name__] = None  # registration order
    return fn


# --------------------------------------------------------------------------
# P1 — is the 200 ceiling enforced, or does it silently truncate?
# --------------------------------------------------------------------------


@probe
def p1_config_ceiling(_ctx) -> dict:
    from neyma_product_driver.config import ScenarioGenerationConfig

    out = {}
    for n in (200, 201, 500, 10_000):
        try:
            cfg = ScenarioGenerationConfig(enabled=True, max_total_scenarios=n)
            out[str(n)] = {"accepted": True, "stored": cfg.max_total_scenarios}
        except Exception as exc:  # noqa: BLE001
            out[str(n)] = {"accepted": False, "error": str(exc)[:120]}
    # A ceiling that silently clamps instead of refusing would show up as
    # accepted=True with stored < n.
    out["silently_clamped"] = any(
        v.get("accepted") and v.get("stored") != int(k)
        for k, v in out.items()
        if k.isdigit()
    )
    return out


@probe
def p2_planner_total_ceiling(ctx) -> dict:
    """Ask for 500 in one wave with a 200 total. Nothing may vanish silently."""
    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.scenario_planner import ScenarioPlanner

    from scenario_fixtures import (
        FakeFounder,
        FakeUnit,
        ScriptedReasoner,
        base_scenario,
        raw_payload,
        raw_scenario,
    )

    proposals = [
        raw_scenario(
            f"gen-total-{i:04d}",
            risk_category=[
                "idempotency",
                "persistence_failure",
                "cross_tenant",
                "authorization",
                "concurrency",
            ][i % 5],
        )
        for i in range(500)
    ]
    for i, p in enumerate(proposals):
        # make each one a distinct coverage signature
        p["title"] = f"proposal {i}"
        p["actions"][0]["request"]["path"] = f"/invoices/{i}/approve"

    cfg = ScenarioGenerationConfig(
        enabled=True,
        max_initial_scenarios=500,
        max_total_scenarios=200,
        max_scenarios_per_risk_category=200,
        max_waves=3,
    )
    planner = ScenarioPlanner(
        repo=ctx["tmp"],
        config=cfg,
        reasoner=ScriptedReasoner([raw_payload(*proposals)]),
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )
    t0 = time.monotonic()
    plan = planner.plan_initial(task="approve invoices", unit=FakeUnit(), run_id="r-ceiling")
    elapsed = time.monotonic() - t0
    wave = plan.waves[0]
    return {
        "proposed": wave.proposed,
        "admitted": len(plan.scenarios),
        "accepted_ids": len(wave.accepted_ids),
        "rejected_records": len(wave.rejected),
        "budget_notes": wave.budget_notes[:4],
        "budget_notes_count": len(wave.budget_notes),
        "ceiling_respected": len(plan.scenarios) <= 200,
        "loss_is_accounted_for": wave.proposed
        == len(wave.accepted_ids) + len(wave.rejected),
        "silent_loss": wave.proposed - len(wave.accepted_ids) - len(wave.rejected),
        "seconds": round(elapsed, 3),
        "unique_ids": len({s.id for s in plan.scenarios}) == len(plan.scenarios),
    }


@probe
def p3_per_category_and_per_wave_caps(ctx) -> dict:
    """Per-category cap and per-wave cap, both attacked with a flood."""
    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.scenario_planner import ScenarioPlanner

    from scenario_fixtures import (
        FakeFounder,
        FakeUnit,
        ScriptedReasoner,
        base_scenario,
        raw_payload,
        raw_scenario,
    )

    def flood(n: int, category: str, prefix: str):
        out = []
        for i in range(n):
            p = raw_scenario(f"{prefix}-{i:04d}", risk_category=category)
            p["title"] = f"{prefix} {i}"
            p["actions"][0]["request"]["path"] = f"/{prefix}/{i}/approve"
            out.append(p)
        return out

    # -- per-category: 100 proposals in one category, cap of 6 ---------------
    cfg = ScenarioGenerationConfig(
        enabled=True,
        max_initial_scenarios=100,
        max_total_scenarios=200,
        max_scenarios_per_risk_category=6,
        max_waves=3,
    )
    planner = ScenarioPlanner(
        repo=ctx["tmp"],
        config=cfg,
        reasoner=ScriptedReasoner([raw_payload(*flood(100, "idempotency", "cat"))]),
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )
    plan = planner.plan_initial(task="t", unit=FakeUnit(), run_id="r-cat")
    cat_wave = plan.waves[0]
    per_cat = Counter(s.risk_category.value for s in plan.scenarios)

    # -- per-wave: 100 proposals with a per-wave limit of 8 ------------------
    cfg2 = ScenarioGenerationConfig(
        enabled=True,
        max_initial_scenarios=8,
        max_total_scenarios=200,
        max_scenarios_per_risk_category=200,
        max_waves=3,
    )
    planner2 = ScenarioPlanner(
        repo=ctx["tmp"],
        config=cfg2,
        reasoner=ScriptedReasoner([raw_payload(*flood(100, "idempotency", "wave"))]),
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )
    plan2 = planner2.plan_initial(task="t", unit=FakeUnit(), run_id="r-wave")
    wave_wave = plan2.waves[0]

    # -- max_waves: a fourth wave must be refused ---------------------------
    cfg3 = ScenarioGenerationConfig(
        enabled=True,
        max_initial_scenarios=2,
        max_adaptive_scenarios_per_wave=2,
        max_total_scenarios=200,
        max_scenarios_per_risk_category=200,
        max_waves=2,
    )
    payloads = [raw_payload(*flood(2, "idempotency", f"w{k}")) for k in range(4)]
    planner3 = ScenarioPlanner(
        repo=ctx["tmp"],
        config=cfg3,
        reasoner=ScriptedReasoner(payloads),
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )
    planner3.plan_initial(task="t", unit=FakeUnit(), run_id="r-waves")
    for _ in range(3):
        planner3.expand_after_failures(
            task="t",
            unit=FakeUnit(),
            failures=[type("F", (), {"scenario_id": "x", "render": lambda self: "f"})()],
        )
    plan3 = planner3.plan

    return {
        "per_category": {
            "proposed": cat_wave.proposed,
            "admitted": len(plan.scenarios),
            "counts_by_category": dict(per_cat),
            "cap_respected": max(per_cat.values()) <= 6 if per_cat else True,
            "budget_notes_count": len(cat_wave.budget_notes),
            "loss_is_accounted_for": cat_wave.proposed
            == len(cat_wave.accepted_ids) + len(cat_wave.rejected),
        },
        "per_wave": {
            "proposed": wave_wave.proposed,
            "admitted": len(plan2.scenarios),
            "cap_respected": len(plan2.scenarios) <= 8,
            "budget_notes": wave_wave.budget_notes[:3],
            "loss_is_accounted_for": wave_wave.proposed
            == len(wave_wave.accepted_ids) + len(wave_wave.rejected),
        },
        "max_waves": {
            "waves_recorded": len(plan3.waves),
            "waves_used": planner3.waves_used,
            "cap_respected": planner3.waves_used <= 2,
            "refusal_notes": [
                n for w in plan3.waves for n in w.budget_notes if "refused" in n
            ][:3],
        },
    }


# --------------------------------------------------------------------------
# P4 — identity under adversarially similar long ids
# --------------------------------------------------------------------------


@probe
def p4_adversarial_ids(ctx) -> dict:
    from neyma_product_driver.evidence import sanitize_filename
    from neyma_product_driver.scenario_plan import (
        GeneratedScenario,
        Priority,
        RiskCategory,
    )
    from neyma_product_driver.scenario_suite import build_suite
    from neyma_product_driver.scenarios import Scenario

    def scen(name: str) -> Scenario:
        return Scenario(name=name[:60] or "x", mode="backend", description="d")

    findings = {}
    for label, prefix_len, count in (
        ("shared-63-char-prefix", 63, 200),
        ("shared-80-char-prefix", 80, 200),
        ("shared-200-char-prefix", 200, 200),
    ):
        prefix = "z" * prefix_len
        raw_ids = [f"{prefix}{i:04d}" for i in range(count)]
        models = [
            GeneratedScenario(
                id=r,
                title=f"t{i}",
                risk_category=RiskCategory.IDEMPOTENCY,
                priority=Priority.P1,
            )
            for i, r in enumerate(raw_ids)
        ]
        ids = [m.id for m in models]
        dirs = [sanitize_filename(i) for i in ids]
        suite = build_suite(generated=[(m, scen(m.id)) for m in models])
        findings[label] = {
            "raw_inputs": count,
            "distinct_derived_ids": len(set(ids)),
            "id_collisions": count - len(set(ids)),
            "distinct_evidence_dir_names": len(set(dirs)),
            "evidence_dir_collisions": count - len(set(dirs)),
            "suite_entries": len(suite),
            "assembly_conflicts": len(suite.assembly_conflicts),
            "max_id_len": max(len(i) for i in ids),
            "proposed_id_kept": all(m.proposed_id for m in models)
            if prefix_len >= 64
            else None,
        }

    # Sanitisation collisions: ids that differ ONLY in characters the filename
    # sanitiser folds away.
    folded = ["case/001/http", "case 001 http", "case-001-http", "case.001.http"]
    findings["sanitiser_folding"] = {
        "inputs": folded,
        "derived_ids": [
            GeneratedScenario(
                id=f, title="t", risk_category=RiskCategory.IDEMPOTENCY
            ).id
            for f in folded
        ],
        "filenames": [sanitize_filename(f) for f in folded],
        "distinct_filenames": len({sanitize_filename(f) for f in folded}),
    }
    return findings


# --------------------------------------------------------------------------
# P5 — can two executed cases be made to share one evidence directory?
# --------------------------------------------------------------------------


@probe
def p5_shared_evidence_dir(ctx) -> dict:
    """A permanent scenario name and a generated id that sanitise to one path.

    Permanent ids come from `Scenario.name`, which is free human text and is NOT
    passed through the id sanitiser (only through sanitize_filename at write
    time). Generated ids ARE sanitised. So a permanent scenario called
    "approve twice" and a generated scenario called "approve-twice" are two
    distinct suite entries that resolve to one evidence directory.
    """
    import asyncio

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
        verify_case_evidence,
    )
    from neyma_product_driver.scenarios import Scenario

    permanent_name = "approve twice"
    generated_id = "approve-twice"

    class FakeExec:
        service_logs: dict[str, str] = {}

        def __init__(self, artifact_dir: Path) -> None:
            self.dir = artifact_dir

        async def execute(self, scenario: Scenario) -> ScenarioResult:
            return ScenarioResult(scenario_name=scenario.name, readiness_ok=True)

    root = Path(ctx["out"]) / "p5-shared-evidence"
    suite = build_suite(
        permanent=[(permanent_name, Scenario(name=permanent_name, mode="backend"))],
        generated=[
            (
                GeneratedScenario(
                    id=generated_id,
                    title="t",
                    risk_category=RiskCategory.IDEMPOTENCY,
                    priority=Priority.P0,
                ),
                Scenario(name=generated_id, mode="backend"),
            )
        ],
    )
    ex = SuiteExecutor(
        make_executor=lambda d: FakeExec(d),
        artifact_root=root,
        run_id="p5",
        iteration=1,
    )
    result = asyncio.get_event_loop().run_until_complete(ex.run(suite))
    paths = [o.evidence_path for o in result.outcomes]
    dirs = sorted(p.name for p in (root / "scenarios").iterdir()) if (root / "scenarios").exists() else []

    # Re-verify every outcome's evidence AFTER the whole run, which is what an
    # auditor (or a resume) does. The executor only verifies immediately after
    # each write, so a later case overwriting an earlier one is invisible to it.
    reverified = {
        o.scenario_id: verify_case_evidence(
            o.evidence_path, scenario_id=o.scenario_id, run_id="p5", iteration=1
        )
        for o in result.outcomes
    }
    return {
        "suite_entries": len(suite),
        "assembly_conflicts": suite.assembly_conflicts,
        "outcomes": len(result.outcomes),
        "evidence_paths": paths,
        "distinct_evidence_paths": len(set(paths)),
        "evidence_dirs_on_disk": dirs,
        "two_cases_one_directory": len(set(paths)) < len(paths),
        "evidence_verified_at_run_time": {
            o.scenario_id: o.evidence_verified for o in result.outcomes
        },
        "reverified_after_the_run": reverified,
        "gate_status_at_run_time": evaluate_gate(result).status.value,
        "gate_required_passed": evaluate_gate(result).required_passed,
    }


# --------------------------------------------------------------------------
# P6 — where is the quadratic cost?
# --------------------------------------------------------------------------


@probe
def p6_complexity(ctx) -> dict:
    from neyma_product_driver.failure_clustering import FailureRecord, cluster_failures
    from neyma_product_driver.scenario_gate import evaluate_gate
    from neyma_product_driver.scenario_plan import (
        GeneratedScenario,
        Priority,
        RiskCategory,
    )
    from neyma_product_driver.scenario_suite import (
        Origin,
        Outcome,
        ScenarioOutcome,
        SuiteResult,
        build_suite,
        select_rerun,
    )
    from neyma_product_driver.scenarios import Scenario

    sizes = [100, 200, 400, 800, 1600]
    rows = []
    for n in sizes:
        models = [
            GeneratedScenario(
                id=f"s-{i:05d}",
                title=f"t{i}",
                risk_category=RiskCategory.IDEMPOTENCY,
                priority=Priority.P1,
            )
            for i in range(n)
        ]
        scen = [Scenario(name=f"s-{i:05d}", mode="backend") for i in range(n)]

        t0 = time.perf_counter()
        suite = build_suite(generated=list(zip(models, scen)))
        t_build = time.perf_counter() - t0

        outcomes = [
            ScenarioOutcome(
                scenario_id=f"s-{i:05d}",
                scenario_name=f"s-{i:05d}",
                origin=Origin.GENERATED,
                outcome=Outcome.FAILED,
                priority=Priority.P1,
                risk_category="idempotency",
                required=True,
                failed_assertions=[
                    f"expect_visible: GET /invoice/{i} — expected rows=1 got rows=2"
                ],
                error="",
                evidence_path=f"/tmp/e/{i}",
            )
            for i in range(n)
        ]
        result = SuiteResult(
            expected_required_ids=[o.scenario_id for o in outcomes], outcomes=outcomes
        )

        records = [
            FailureRecord(
                scenario_id=o.scenario_id,
                scenario_name=o.scenario_name,
                risk_category=RiskCategory.IDEMPOTENCY,
                priority=Priority.P1,
                failed_assertions=list(o.failed_assertions),
                evidence_path=o.evidence_path,
            )
            for o in outcomes
        ]
        t0 = time.perf_counter()
        clusters = cluster_failures(records)
        t_cluster = time.perf_counter() - t0

        result.clusters = clusters
        t0 = time.perf_counter()
        evaluate_gate(result)
        t_gate = time.perf_counter() - t0

        t0 = time.perf_counter()
        select_rerun(suite, result)
        t_rerun = time.perf_counter() - t0

        t0 = time.perf_counter()
        summary = result.summary_block()
        t_summary = time.perf_counter() - t0

        # dependency chains exercise the topological sort
        dep_models = [
            GeneratedScenario(
                id=f"d-{i:05d}",
                title=f"t{i}",
                risk_category=RiskCategory.IDEMPOTENCY,
                priority=Priority.P1,
            )
            for i in range(n)
        ]
        dep_suite = build_suite(
            generated=[
                (m, Scenario(name=m.id, mode="backend")) for m in dep_models
            ]
        )
        for i, e in enumerate(dep_suite.entries):
            if i:
                e.depends_on = [f"d-{i - 1:05d}"]
        t0 = time.perf_counter()
        dep_suite.execution_order()
        t_topo = time.perf_counter() - t0

        rows.append(
            {
                "n": n,
                "build_suite_ms": round(t_build * 1000, 2),
                "cluster_failures_ms": round(t_cluster * 1000, 2),
                "evaluate_gate_ms": round(t_gate * 1000, 2),
                "select_rerun_ms": round(t_rerun * 1000, 2),
                "summary_block_ms": round(t_summary * 1000, 2),
                "execution_order_chain_deps_ms": round(t_topo * 1000, 2),
                "summary_chars": len(summary),
                "clusters": len(clusters),
            }
        )

    def ratios(key):
        return [
            round(rows[i + 1][key] / rows[i][key], 2) if rows[i][key] else None
            for i in range(len(rows) - 1)
        ]

    return {
        "rows": rows,
        "doubling_ratios (2.0=linear, 4.0=quadratic)": {
            k: ratios(k)
            for k in (
                "build_suite_ms",
                "cluster_failures_ms",
                "evaluate_gate_ms",
                "select_rerun_ms",
                "summary_block_ms",
                "execution_order_chain_deps_ms",
            )
        },
    }


# --------------------------------------------------------------------------
# P7 — is the evaluator prompt actually bounded?
# --------------------------------------------------------------------------


@probe
def p7_prompt_bound(ctx) -> dict:
    from neyma_product_driver.prompts import evaluator_prompt
    from neyma_product_driver.scenario_plan import Priority
    from neyma_product_driver.scenario_suite import (
        Origin,
        Outcome,
        ScenarioOutcome,
        SuiteResult,
    )

    def make(n: int, msg_chars: int) -> SuiteResult:
        pad = "M" * msg_chars
        outcomes = [
            ScenarioOutcome(
                scenario_id=f"s-{i:05d}",
                scenario_name=f"s-{i:05d}",
                origin=Origin.GENERATED,
                outcome=Outcome.FAILED,
                priority=Priority.P0,
                risk_category="idempotency",
                required=True,
                failed_assertions=[f"expect_visible: target-{i} — {pad}"] * 6,
                error=f"boom {i} {pad}",
                evidence_path=f"/runs/r/scenarios/s-{i:05d}",
                generated_because=f"risk {i} {pad}",
                requirement_reference=f"U-{i}: {pad}",
            )
            for i in range(n)
        ]
        return SuiteResult(
            expected_required_ids=[o.scenario_id for o in outcomes], outcomes=outcomes
        )

    rows = []
    for n, msg in [
        (10, 100),
        (200, 100),
        (200, 1000),
        (200, 10_000),
        (500, 10_000),
    ]:
        r = make(n, msg)
        p = evaluator_prompt(
            task="t",
            iteration=1,
            max_iterations=3,
            builder_summary="b",
            git=None,
            scenario=None,
            service_logs=None,
            evidence_dir="/runs/r",
            suite=r,
        )
        named = sum(1 for o in r.outcomes if o.scenario_id in p)
        rows.append(
            {
                "failures": n,
                "assertion_chars": msg,
                "summary_chars": len(r.summary_block()),
                "prompt_chars": len(p),
                "prompt_est_tokens": len(p) // 4,
                "failures_named_in_prompt": named,
                "all_failures_named": named == n,
            }
        )
    return {
        "rows": rows,
        "is_bounded": max(r["prompt_chars"] for r in rows) < 1_000_000,
        "note": "prompt size grows linearly in (failures x assertion length) with no cap",
    }


# --------------------------------------------------------------------------
# P8 — many scenarios in one cluster
# --------------------------------------------------------------------------


@probe
def p8_one_giant_cluster(ctx) -> dict:
    from neyma_product_driver.failure_clustering import FailureRecord, cluster_failures
    from neyma_product_driver.scenario_plan import Priority, RiskCategory

    n = 200
    records = [
        FailureRecord(
            scenario_id=f"g-{i:04d}",
            scenario_name=f"g-{i:04d}",
            risk_category=RiskCategory.IDEMPOTENCY,
            priority=Priority.P0,
            # identical shape after digit normalisation -> one cluster
            failed_assertions=[
                f"expect_state: rows for invoice {i} — expected 1 got 2",
                f"expect_visible: /invoice/{i} — duplicate payment recorded",
            ],
            evidence_path=f"/tmp/e/{i}",
        )
        for i in range(n)
    ]
    t0 = time.perf_counter()
    clusters = cluster_failures(records)
    elapsed = time.perf_counter() - t0
    members = [sid for c in clusters for sid in c.affected_scenarios]
    return {
        "failures": n,
        "clusters": len(clusters),
        "largest_cluster": max(len(c.affected_scenarios) for c in clusters),
        "cluster_seconds": round(elapsed, 4),
        "every_failure_in_exactly_one_cluster": len(members) == len(set(members)) == n,
        "render_chars_of_largest": len(
            max(clusters, key=lambda c: len(c.affected_scenarios)).render()
        ),
        "evidence_paths_listed_in_render": clusters[0].render().count("/tmp/e/"),
    }


# --------------------------------------------------------------------------
# P9 — duplicate ids inside expected_required_ids
# --------------------------------------------------------------------------


@probe
def p9_gate_duplicate_required_ids(ctx) -> dict:
    """Does the gate double-count a required id that appears twice?"""
    from neyma_product_driver.scenario_gate import evaluate_gate
    from neyma_product_driver.scenario_plan import Priority
    from neyma_product_driver.scenario_suite import (
        Origin,
        Outcome,
        ScenarioOutcome,
        SuiteResult,
    )

    outcomes = [
        ScenarioOutcome(
            scenario_id="a",
            scenario_name="a",
            origin=Origin.GENERATED,
            outcome=Outcome.PASSED,
            priority=Priority.P0,
            required=True,
            evidence_verified=True,
        )
    ]
    r = SuiteResult(expected_required_ids=["a", "a", "a"], outcomes=outcomes)
    v = evaluate_gate(r)
    r2 = SuiteResult(expected_required_ids=["a", "b"], outcomes=outcomes)
    v2 = evaluate_gate(r2)
    return {
        "duplicate_required_ids": {
            "required_total": v.required_total,
            "required_passed": v.required_passed,
            "status": v.status.value,
            "double_counted": v.required_total != len(set(r.expected_required_ids)),
        },
        "missing_required_id": {
            "required_total": v2.required_total,
            "required_passed": v2.required_passed,
            "status": v2.status.value,
            "unverified": [c.brief() for c in v2.unverified],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("probes", nargs="*")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(args.driver).resolve()))
    sys.path.insert(0, str(Path(args.driver).resolve() / "tests"))
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "tmp"
    tmp.mkdir(exist_ok=True)
    ctx = {"out": out, "tmp": tmp}

    wanted = args.probes or list(RESULTS)
    results = {}
    for name in wanted:
        fn = globals()[name]
        try:
            results[name] = fn(ctx)
        except Exception as exc:  # noqa: BLE001
            import traceback

            results[name] = {"PROBE_ERROR": f"{type(exc).__name__}: {exc}", "tb": traceback.format_exc()[-2000:]}
        print(f"### {name}\n{json.dumps(results[name], indent=2, default=str)}\n", flush=True)

    (out / "probes.json").write_text(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
