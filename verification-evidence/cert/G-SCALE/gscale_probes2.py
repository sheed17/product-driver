"""G-SCALE hostile probes, part 2: resume-time coverage loss and stale evidence."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

RESULTS: dict[str, None] = {}


def probe(fn):
    RESULTS[fn.__name__] = None
    return fn


@probe
def p10_resume_drops_uncompilable_scenarios(ctx) -> dict:
    """A 200-scenario plan is restored into a run whose approved commands shrank.

    The scenarios stop compiling. `restore_from_store` removes them from the
    plan. The question this probe answers is whether the loss is visible to the
    machinery that decides acceptance, or only to whoever was watching stdout.
    """
    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.scenario_gate import evaluate_gate
    from neyma_product_driver.scenario_planner import ScenarioPlanner
    from neyma_product_driver.scenarios import Scenario, ServiceSpec

    from scenario_fixtures import (
        APPROVED_CLEANUP,
        APPROVED_STATE,
        FakeFounder,
        FakeUnit,
        ScriptedReasoner,
        base_scenario,
        raw_payload,
        raw_scenario,
    )

    n = 200
    proposals = []
    for i in range(n):
        p = raw_scenario(f"gen-drop-{i:04d}")
        p["title"] = f"drop {i}"
        p["actions"][0]["request"]["path"] = f"/invoices/{i}/approve"
        proposals.append(p)

    cfg = ScenarioGenerationConfig(
        enabled=True,
        max_initial_scenarios=n,
        max_total_scenarios=200,
        max_scenarios_per_risk_category=200,
    )
    root = Path(ctx["out"]) / "p10"
    if root.exists():
        shutil.rmtree(root)
    store = ScenarioPlanner  # placeholder to keep linters quiet
    store = EvidenceStore(root / "runs", "run-1")
    writer = ScenarioPlanner(
        repo=root / "repo",
        config=cfg,
        reasoner=ScriptedReasoner([raw_payload(*proposals)]),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )
    writer.plan_initial(task="t", unit=FakeUnit(), run_id="run-1")
    before = len(writer.plan.scenarios)
    writer.persist()

    # A second process/run whose repository no longer approves the state and
    # cleanup commands the plan depends on.
    shrunken = Scenario(
        name="backend_generic",
        mode="backend",
        services=[ServiceSpec(name="api", command="./serve.sh")],
        readiness=[{"tcp": "127.0.0.1:8931"}],
        app_url="http://127.0.0.1:8931",
    )
    reader = ScenarioPlanner(
        repo=root / "repo",
        config=cfg,
        reasoner=ScriptedReasoner([]),
        store=EvidenceStore(root / "runs", "run-1"),
        base_scenario=shrunken,
        permanent_scenarios=[shrunken],
        founder=FakeFounder(),
    )
    emitted: list[str] = []
    reader.emit = emitted.append
    note = reader.restore_from_store()
    after = len(reader.plan.scenarios)

    verdict = evaluate_gate(None, generation_problems=reader.generation_problems())
    return {
        "scenarios_before_resume": before,
        "scenarios_after_resume": after,
        "dropped": before - after,
        "restore_note": note,
        "dropped_mentioned_in_restore_note": "drop" in note or "no longer compile" in note,
        "emitted_lines": emitted[:3],
        "generation_problems_after_resume": reader.generation_problems(),
        "wave_records_mention_the_drop": [
            n2 for w in reader.plan.waves for n2 in w.budget_notes if "compile" in n2
        ],
        "rejected_records_mention_the_drop": [
            r.id for w in reader.plan.waves for r in w.rejected if "compile" in " ".join(r.reasons)
        ][:3],
        "gate_sees_a_problem": verdict.blocks_acceptance,
        "gate_generation_problems": verdict.generation_problems,
        "persisted_plan_still_lists_them": len(
            json.loads((root / "runs" / "run-1" / "scenario-plan.json").read_text())["scenarios"]
        ),
    }


@probe
def p11_gate_trusts_stale_evidence_flag(ctx) -> dict:
    """Reload a green 200-suite result, delete its evidence, re-run the gate."""
    from neyma_product_driver.scenario_gate import evaluate_gate
    from neyma_product_driver.scenario_suite import SuiteResult, verify_case_evidence

    src = Path(ctx["suite_result"])
    raw = json.loads(src.read_text())
    result = SuiteResult.model_validate(raw)
    before = evaluate_gate(result)

    # Move the whole evidence tree aside. Nothing about the recorded outcomes
    # changes; only the proof they cite stops existing.
    evidence_root = Path(result.outcomes[0].evidence_path).parent
    stash = Path(ctx["out"]) / "p11-stashed-evidence"
    if stash.exists():
        shutil.rmtree(stash)
    shutil.move(str(evidence_root), str(stash))
    try:
        after = evaluate_gate(result)
        still_resolves = sum(
            1
            for o in result.outcomes
            if not verify_case_evidence(o.evidence_path, scenario_id=o.scenario_id)
        )
    finally:
        shutil.move(str(stash), str(evidence_root))

    return {
        "outcomes": result.total,
        "gate_with_evidence_present": before.status.value,
        "gate_with_evidence_deleted": after.status.value,
        "required_passed_with_evidence_deleted": after.required_passed,
        "evidence_dirs_that_still_resolve": still_resolves,
        "gate_rechecks_evidence_on_reload": after.status.value != before.status.value,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--suite-result", default="")
    ap.add_argument("probes", nargs="*")
    args = ap.parse_args()
    driver = Path(args.driver).resolve()
    sys.path.insert(0, str(driver))
    sys.path.insert(0, str(driver / "tests"))
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    ctx = {"out": out, "suite_result": args.suite_result}

    results = {}
    for name in (args.probes or list(RESULTS)):
        try:
            results[name] = globals()[name](ctx)
        except Exception as exc:  # noqa: BLE001
            import traceback

            results[name] = {
                "PROBE_ERROR": f"{type(exc).__name__}: {exc}",
                "tb": traceback.format_exc()[-1500:],
            }
        print(f"### {name}\n{json.dumps(results[name], indent=2, default=str)}\n", flush=True)
    (out / "probes2.json").write_text(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
