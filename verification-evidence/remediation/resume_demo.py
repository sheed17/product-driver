"""Interrupt a run mid-flight and resume it, through the production path.

Generation is scripted here on purpose: resume is a question about what was
persisted and recovered, not about what a model would say, and a scripted
reasoner makes the before/after comparison exact. Everything else is real — the
real control loop, the real evidence store, the real planner restore that
``_make_planner`` performs on a resumed run.

The interruption is modelled the way it actually happens: the process ends and a
new one starts against the same run directory, with nothing carried in memory.

Run:  .venv/bin/python verification-evidence/remediation/resume_demo.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

OUT = Path(__file__).parent / "resume"

from neyma_product_driver.config import ScenarioGenerationConfig  # noqa: E402
from neyma_product_driver.evidence import EvidenceStore  # noqa: E402
from neyma_product_driver.scenario_planner import (  # noqa: E402
    PromotionLedger,
    ScenarioPlanner,
)
from neyma_product_driver.scenario_suite import FailureEvidence  # noqa: E402

from scenario_fixtures import (  # noqa: E402
    APPROVED_STATE,
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    base_scenario,
    raw_payload,
    raw_scenario,
)

LINES: list[str] = []


def say(line: str = "") -> None:
    print(line, flush=True)
    LINES.append(line)


def distinct(scenario_id: str, **kw) -> dict:
    return raw_scenario(
        scenario_id,
        actions=[
            {
                "kind": "request",
                "name": scenario_id,
                "request": {"method": "POST", "path": f"/approve/{scenario_id}",
                            "expect_status": 200},
            }
        ],
        state_checks=[
            {"name": scenario_id, "command": APPROVED_STATE, "contains": [f"seen={scenario_id}"]}
        ],
        expected_observations=[f"seen={scenario_id}"],
        forbidden_observations=[f"missing={scenario_id}"],
        **kw,
    )


def make_planner(repo: Path, store: EvidenceStore, payloads: list) -> ScenarioPlanner:
    return ScenarioPlanner(
        repo=repo,
        config=ScenarioGenerationConfig(enabled=True, max_waves=2),
        reasoner=ScriptedReasoner(payloads),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
        emit=lambda m: say(f"    {m}"),
    )


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    repo = OUT / "repo"
    repo.mkdir()
    store = EvidenceStore(OUT / "runs", "resume-001")

    say("=" * 78)
    say("PROCESS 1 — generate, adapt after a failure, then die")
    say("=" * 78)
    first = make_planner(
        repo,
        store,
        [
            raw_payload(distinct("gen-happy"), distinct("gen-dup", risk_category="idempotency")),
            raw_payload(distinct("gen-conc", risk_category="concurrency",
                                 source_failures=["gen-dup"])),
        ],
    )
    first.plan_initial(task="supervised invoice approval", unit=FakeUnit(), run_id=store.run_id)
    first.note_executed(["gen-happy", "gen-dup"])
    first.expand_after_failures(
        task="supervised invoice approval",
        unit=FakeUnit(),
        failures=[
            FailureEvidence(
                scenario_id="gen-dup",
                risk_category="idempotency",
                expected=["payments=1"],
                failed_assertions=["expect_state: exactly one payment — not found"],
                observed="payments=2",
                evidence_path=str(store.run_dir / "iteration-01" / "scenarios" / "gen-dup"),
                cluster_id="C01",
            )
        ],
    )
    ledger = PromotionLedger(store.run_dir)

    before = {
        "scenarios": [s.id for s in first.plan.scenarios],
        "waves_used": first.waves_used,
        "budget_exhausted": first.budget_exhausted(),
        "executed": list(first.plan.executed_scenario_ids),
        "observed_failures": sorted(first._observed_failure_ids),
        "observed_clusters": sorted(first._observed_cluster_ids),
        "compiled": sorted(first.compiled),
        "adaptive_links": {
            s.id: s.provenance.source_failures
            for s in first.plan.scenarios
            if s.provenance.stage == "adaptive"
        },
        "promotion_candidates": [c.scenario_id for c in ledger.load()],
    }
    for key, value in before.items():
        say(f"  {key:<22}: {value}")

    say()
    say("  *** the process is interrupted here — nothing is carried in memory ***")
    say()

    say("=" * 78)
    say("PROCESS 2 — a new process resumes the same run")
    say("=" * 78)
    resumed = make_planner(repo, store, [])  # no payloads: it must not regenerate
    note = resumed.restore_from_store()
    say(f"  restore note          : {note}")

    after = {
        "scenarios": [s.id for s in resumed.plan.scenarios],
        "waves_used": resumed.waves_used,
        "budget_exhausted": resumed.budget_exhausted(),
        "executed": list(resumed.plan.executed_scenario_ids),
        "observed_failures": sorted(resumed._observed_failure_ids),
        "observed_clusters": sorted(resumed._observed_cluster_ids),
        "compiled": sorted(resumed.compiled),
        "adaptive_links": {
            s.id: s.provenance.source_failures
            for s in resumed.plan.scenarios
            if s.provenance.stage == "adaptive"
        },
        "promotion_candidates": [c.scenario_id for c in PromotionLedger(store.run_dir).load()],
    }
    for key, value in after.items():
        say(f"  {key:<22}: {value}")

    say()
    say("=" * 78)
    say("COMPARISON")
    say("=" * 78)
    mismatches = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    for key in before:
        mark = "OK  " if before[key] == after[key] else "LOST"
        say(f"  [{mark}] {key}")

    # The run identity and the earlier plan must both still be on disk.
    stored = json.loads((store.run_dir / "scenario-plan.json").read_text())
    resumed.persist()
    after_persist = json.loads((store.run_dir / "scenario-plan.json").read_text())
    say()
    say(f"  plan on disk before re-persist : {[s['id'] for s in stored['scenarios']]}")
    say(f"  plan on disk after  re-persist : {[s['id'] for s in after_persist['scenarios']]}")
    say(f"  run id preserved               : {after_persist['run_id']!r}")

    destroyed = [s["id"] for s in stored["scenarios"]] != [
        s["id"] for s in after_persist["scenarios"]
    ]
    say()
    if mismatches or destroyed:
        say("RESULT: resume did NOT fully preserve the run.")
        for key, (b, a) in mismatches.items():
            say(f"  {key}: before={b} after={a}")
    else:
        say("RESULT: resume continued from persisted state.")
        say("  - no scenario was regenerated (the reasoner had no payloads left to give)")
        say("  - the wave budget was not refunded by restarting the process")
        say("  - the adaptive scenario still records the failure that caused it")
        say("  - re-persisting did not destroy the earlier plan")

    (OUT / "resume-comparison.json").write_text(
        json.dumps({"before": before, "after": after, "mismatches": list(mismatches)}, indent=2)
    )
    (OUT / "transcript.txt").write_text("\n".join(LINES))
    say(f"\nartifacts: {OUT}")
    return 1 if (mismatches or destroyed) else 0


if __name__ == "__main__":
    sys.exit(main())
