"""E-RESUME Part 1 (second battery) — scope the resume failure and attack budgets.

A. baseline: is resume correct at all when no scenario is in the `authorization`
   risk category?
B. can resume regain a spent wave budget (escape max_waves)?
C. does a scenario whose command is no longer approved come back to life?
D. SIGKILL mid-persist: can a half-written scenario-plan.json really be produced?
E. is promotion candidacy (DefectMemory) carried across the process boundary?

Every child is a separate interpreter. Run:
    .venv/bin/python verification-evidence/cert/E-RESUME/resume_probe2.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(HERE))
PY = str(REPO / ".venv" / "bin" / "python")

import resume_probe as rp  # noqa: E402
from neyma_product_driver.evidence import EvidenceStore  # noqa: E402
from neyma_product_driver.scenario_planner import PLAN_FILENAME  # noqa: E402
from scenario_fixtures import (  # noqa: E402
    APPROVED_CLEANUP,
    APPROVED_STATE,
    FakeFounder,
    FakeUnit,
    base_scenario,
    raw_payload,
)
from neyma_product_driver.scenarios import Scenario, ServiceSpec  # noqa: E402

RUN_ID = rp.RUN_ID


def payload(cat_b: str) -> dict:
    return raw_payload(
        rp.probe_scenario("gen-alpha-idempotency", "alpha"),
        rp.probe_scenario("gen-beta-other", "beta", risk_category=cat_b),
    )


SECOND_WAVE = raw_payload(
    rp.probe_scenario("gen-delta-boundary", "delta", risk_category="boundary"),
    risks=[],
)


def narrowed_base() -> Scenario:
    """A base scenario that no longer approves the state-probe command."""
    return Scenario(
        name="backend_generic",
        mode="backend",
        services=[ServiceSpec(name="api", command="./serve.sh")],
        readiness=[{"tcp": "127.0.0.1:8931"}],
        app_url="http://127.0.0.1:8931",
        teardown=[APPROVED_CLEANUP],
    )


# --------------------------------------------------------------------------
# children
# --------------------------------------------------------------------------


def make_planner(work: Path, *, max_waves: int, payloads: list[Any], base=None):
    from neyma_product_driver.scenario_planner import ScenarioPlanner

    config = rp.make_config(work, max_waves=max_waves)
    store = EvidenceStore(config.runs_dir, RUN_ID)
    planner = ScenarioPlanner(
        repo=config.neyma_repo,
        config=config.scenario_generation,
        reasoner=rp.HookedReasoner(payloads),
        store=store,
        base_scenario=base or base_scenario(),
        permanent_scenarios=[base or base_scenario()],
        founder=FakeFounder(),
        emit=lambda m: print(m, flush=True),
    )
    return config, store, planner


def child_generate(work: Path, cat_b: str, max_waves: int) -> None:
    config, store, planner = make_planner(work, max_waves=max_waves, payloads=[payload(cat_b)])
    planner.plan_initial(task=config.task, unit=FakeUnit(), run_id=RUN_ID)
    planner.note_executed([s.id for s in planner.plan.scenarios])
    (work / "expected.json").write_text(
        json.dumps(rp.snapshot(planner, store, "process1"), indent=2), encoding="utf-8"
    )
    sys.stdout.flush()
    os._exit(9)


def child_resume(work: Path, out: str, *, narrow: bool = False) -> None:
    """The production resume path: cli._make_planner -> restore_from_store."""
    from neyma_product_driver import cli

    config = rp.make_config(work)
    store = EvidenceStore(config.runs_dir, RUN_ID)
    lines: list[str] = []
    base = narrowed_base() if narrow else base_scenario()
    planner = cli._make_planner(
        config, argparse.Namespace(auto_scenarios=True), store, base, FakeFounder(),
        lambda m: lines.append(m),
    )
    snap = rp.snapshot(planner, store, "process2")
    snap["restore_emissions"] = lines
    (work / out).write_text(json.dumps(snap, indent=2), encoding="utf-8")


def child_resume_and_generate(work: Path, max_waves: int, out: str) -> None:
    """Resume, then ask for another wave. Does the spent budget still bind?"""
    config, store, planner = make_planner(work, max_waves=max_waves, payloads=[SECOND_WAVE])
    note = planner.restore_from_store()
    before = planner.waves_used
    planner.plan_initial(task=config.task, unit=FakeUnit(), run_id=RUN_ID)
    snap = rp.snapshot(planner, store, "process2+generate")
    snap["restore_note"] = note
    snap["waves_used_before_generate"] = before
    (work / out).write_text(json.dumps(snap, indent=2), encoding="utf-8")


def child_persist_loop(work: Path) -> None:
    """Persist the real plan in a tight loop until SIGKILLed."""
    config, store, planner = make_planner(work, max_waves=3, payloads=[payload("boundary")])
    planner.plan_initial(task=config.task, unit=FakeUnit(), run_id=RUN_ID)
    # Fatten the plan so a single write is many syscalls.
    for i in range(400):
        planner.plan.assumptions.append(f"assumption {i}: " + "y" * 900)
    print("READY", flush=True)
    while True:
        planner.persist()


# --------------------------------------------------------------------------
# orchestrator
# --------------------------------------------------------------------------


def run(work: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, __file__, "--child", str(work), *args],
                          capture_output=True, text=True, cwd=str(REPO))


def fresh(root: Path, name: str) -> Path:
    work = root / name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    rp.build_world(work)
    return work


def case_baseline(root: Path, cat_b: str) -> dict:
    work = fresh(root, f"baseline-{cat_b}")
    p1 = run(work, "generate", cat_b, "3")
    plan_path = work / "driver" / "runs" / RUN_ID / PLAN_FILENAME
    raw = json.loads(plan_path.read_text())
    p2 = run(work, "resume", "restored.json")
    expected = json.loads((work / "expected.json").read_text())
    restored = json.loads((work / "restored.json").read_text())
    return {
        "risk_category_b": cat_b,
        "p1_rc": p1.returncode,
        "persisted_by_risk_category": raw["coverage_summary"]["by_risk_category"],
        "restore_emissions": restored.get("restore_emissions"),
        "lost": [
            {"field": r["field"], "p1": r["process1"], "p2": r["process2"]}
            for r in rp.compare(expected, restored)
            if not r["survived"]
        ],
    }


def case_wave_budget(root: Path, cat_b: str) -> dict:
    work = fresh(root, f"budget-{cat_b}")
    run(work, "generate", cat_b, "1")          # max_waves = 1, spent
    run(work, "resume_generate", "1", "after.json")
    after = json.loads((work / "after.json").read_text())
    return {
        "risk_category_b": cat_b,
        "restore_note": after["restore_note"],
        "waves_used_after_restore": after["waves_used_before_generate"],
        "waves_used_after_second_generate": after["waves_used"],
        "scenario_ids_after": after["plan"]["scenario_ids"],
        "wave_records_after": [
            {"wave": w["wave"], "stage": w["stage"], "accepted": w["accepted_ids"],
             "budget_notes": w["budget_notes"]}
            for w in after["plan"]["waves"]
        ],
        "escaped_max_waves": after["waves_used"] > 1,
        "regenerated_from_scratch": "gen-delta-boundary" in after["plan"]["scenario_ids"]
        and "gen-alpha-idempotency" not in after["plan"]["scenario_ids"],
    }


def case_command_narrowed(root: Path) -> dict:
    work = fresh(root, "narrowed")
    run(work, "generate", "boundary", "3")
    run(work, "resume", "restored.json", "narrow")
    restored = json.loads((work / "restored.json").read_text())
    return {
        "restore_emissions": restored["restore_emissions"],
        "scenario_ids_after_resume": restored["plan"]["scenario_ids"],
        "compiled_after_resume": restored["compiled_ids"],
        "approved_commands_after_resume": restored["approved_commands"],
        "scenario_came_back_to_life": bool(restored["compiled_ids"]),
    }


def case_sigkill_persist(root: Path) -> dict:
    from neyma_product_driver.scenario_plan import GeneratedScenarioPlan

    attempts, corrupt, sizes = 0, 0, []
    detail = []
    for attempt in range(40):
        work = fresh(root, f"sigkill-{attempt}")
        proc = subprocess.Popen(
            [PY, __file__, "--child", str(work), "persist_loop"],
            stdout=subprocess.PIPE, text=True, cwd=str(REPO),
        )
        assert proc.stdout is not None
        ready = False
        for _ in range(200):  # planner emissions come first
            line = proc.stdout.readline()
            if not line:
                break
            if "READY" in line:
                ready = True
                break
        if not ready:
            proc.kill()
            continue
        time.sleep(0.01 + (attempt % 20) * 0.0037)
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait()
        attempts += 1
        path = work / "driver" / "runs" / RUN_ID / PLAN_FILENAME
        size = path.stat().st_size if path.exists() else None
        sizes.append(size)
        ok, err = True, ""
        try:
            GeneratedScenarioPlan.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            ok, err = False, f"{type(exc).__name__}: {str(exc)[:120]}"
        if not ok:
            corrupt += 1
        detail.append({"attempt": attempt, "bytes": size, "parses": ok, "error": err})
    return {
        "attempts": attempts,
        "half_written_plans": corrupt,
        "detail": detail,
        "note": "EvidenceStore.write_json uses Path.write_text — truncate-then-write, not atomic",
    }


async def _loop(work: Path, *, resume: bool, failing: set[str], payloads: list[Any]) -> dict:
    """One process's worth of the real control loop, one iteration."""
    import argparse as _ap

    from neyma_product_driver import cli
    from neyma_product_driver.scenario_planner import PromotionLedger, ScenarioPlanner
    from neyma_product_driver.models import RunState

    config = rp.make_config(work)
    config.max_iterations = 1
    store = EvidenceStore(config.runs_dir, RUN_ID)
    state = RunState(run_id=RUN_ID, task=config.task, max_iterations=1)

    planner = ScenarioPlanner(
        repo=config.neyma_repo,
        config=config.scenario_generation,
        reasoner=rp.HookedReasoner(payloads),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
        emit=lambda m: print(m, flush=True),
    )
    if resume:
        # exactly what cli._make_planner does on a resumed run
        planner.restore_from_store()
    log: list[str] = []
    await cli.run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=rp.FakeBuilder(),
        evaluator=rp.ExitingEvaluator(),
        make_executor=lambda d: rp.ScriptedExecutor(d, failing, log),
        emit=lambda m: print(m, flush=True),
        founder=None,
        repo_loader=rp.FakeRepoLoader(),
        planner=planner,
    )
    return {
        "executed": log,
        "promotion_candidates": [
            {"scenario_id": c.scenario_id, "discovered_in_iteration": c.discovered_in_iteration,
             "fixed_in_iteration": c.fixed_in_iteration}
            for c in PromotionLedger(store.run_dir).load()
        ],
        "executed_scenario_ids": list(planner.plan.executed_scenario_ids),
        "waves_used": planner.waves_used,
    }


def child_loop(work: Path, which: str) -> None:
    import asyncio

    resume = which == "second"
    failing = set() if resume else {"gen-alpha-idempotency"}
    payloads = [payload("boundary")] if not resume else [{"risks": [], "scenarios": []}]
    res = asyncio.run(_loop(work, resume=resume, failing=failing, payloads=payloads))
    (work / f"loop-{which}.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    sys.stdout.flush()
    os._exit(9)


def case_promotion_across_resume(root: Path) -> dict:
    work = fresh(root, "promotion")
    run(work, "loop", "first")
    run(work, "loop", "second")
    first = json.loads((work / "loop-first.json").read_text())
    second = json.loads((work / "loop-second.json").read_text())
    return {
        "iteration1_failing": ["gen-alpha-idempotency"],
        "iteration1_promotion_candidates": first["promotion_candidates"],
        "iteration2_all_pass": True,
        "iteration2_promotion_candidates": second["promotion_candidates"],
        "iteration2_executed": second["executed"],
        "defect_memory_survived": bool(second["promotion_candidates"]),
    }


def case_partial_window(root: Path) -> dict:
    """Is a half-written scenario-plan.json ever observable on disk?

    A concurrent reader is the decisive test for whether the write has a window
    at all: `Path.write_text` truncates on open and then streams, so any reader
    (or any crash) landing inside that window sees an incomplete file. Compare
    `write_case_evidence`, which stages to a temp name and `replace`s.
    """
    from neyma_product_driver.scenario_plan import GeneratedScenarioPlan

    work = fresh(root, "partial-window")
    proc = subprocess.Popen(
        [PY, __file__, "--child", str(work), "persist_loop"],
        stdout=subprocess.PIPE, text=True, cwd=str(REPO),
    )
    assert proc.stdout is not None
    for _ in range(200):
        line = proc.stdout.readline()
        if not line or "READY" in line:
            break
    path = work / "driver" / "runs" / RUN_ID / PLAN_FILENAME
    sizes, bad, reads = set(), 0, 0
    deadline = time.time() + 6.0
    while time.time() < deadline:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        reads += 1
        sizes.add(len(raw))
        try:
            GeneratedScenarioPlan.model_validate_json(raw)
        except Exception:  # noqa: BLE001
            bad += 1
    proc.kill()
    proc.wait()
    return {
        "reads": reads,
        "incomplete_observations": bad,
        "distinct_sizes_seen": sorted(sizes)[:12],
        "window_exists": bad > 0,
    }


def case_repersist_clean(root: Path) -> dict:
    """After a SUCCESSFUL resume, does persist() preserve the earlier record?"""
    work = fresh(root, "repersist-clean")
    run(work, "generate", "boundary", "3")
    path = work / "driver" / "runs" / RUN_ID / PLAN_FILENAME
    before = json.loads(path.read_text())
    run(work, "resume_persist")
    after = json.loads(path.read_text())
    return {
        "scenarios_before": [s["id"] for s in before["scenarios"]],
        "scenarios_after": [s["id"] for s in after["scenarios"]],
        "waves_before": len(before["waves"]),
        "waves_after": len(after["waves"]),
        "executed_before": before["executed_scenario_ids"],
        "executed_after": after["executed_scenario_ids"],
        "record_destroyed": [s["id"] for s in after["scenarios"]] != [s["id"] for s in before["scenarios"]],
    }


def child_resume_persist(work: Path) -> None:
    from neyma_product_driver import cli

    config = rp.make_config(work)
    store = EvidenceStore(config.runs_dir, RUN_ID)
    planner = cli._make_planner(
        config, argparse.Namespace(auto_scenarios=True), store, base_scenario(),
        FakeFounder(), lambda _m: None,
    )
    planner.persist()


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="e-resume-part1b-"))
    out: dict[str, Any] = {"workdir": str(root)}
    out["A_baseline_clean"] = case_baseline(root, "boundary")
    out["A_baseline_authorization"] = case_baseline(root, "authorization")
    out["B_wave_budget_clean"] = case_wave_budget(root, "boundary")
    out["B_wave_budget_authorization"] = case_wave_budget(root, "authorization")
    out["C_command_narrowed"] = case_command_narrowed(root)
    out["D_sigkill_mid_persist"] = case_sigkill_persist(root)
    out["E_promotion_across_resume"] = case_promotion_across_resume(root)
    out["F_partial_write_window"] = case_partial_window(root)
    out["G_repersist_after_clean_resume"] = case_repersist_clean(root)
    path = HERE / "resume_probe2.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "D_sigkill_mid_persist"}, indent=2)[:9000])
    print(json.dumps(out["D_sigkill_mid_persist"], indent=2)[:3000])
    print(json.dumps(out["E_promotion_across_resume"], indent=2))
    print(json.dumps(out["F_partial_write_window"], indent=2))
    print(json.dumps(out["G_repersist_after_clean_resume"], indent=2))
    print(f"\nwrote {path}\nworkdir {root}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        work = Path(sys.argv[2])
        what = sys.argv[3]
        if what == "generate":
            child_generate(work, sys.argv[4], int(sys.argv[5]))
        elif what == "resume":
            child_resume(work, sys.argv[4], narrow=len(sys.argv) > 5 and sys.argv[5] == "narrow")
        elif what == "resume_generate":
            child_resume_and_generate(work, int(sys.argv[4]), sys.argv[5])
        elif what == "persist_loop":
            child_persist_loop(work)
        elif what == "loop":
            child_loop(work, sys.argv[4])
        elif what == "resume_persist":
            child_resume_persist(work)
        else:
            raise SystemExit(f"unknown child {what}")
    else:
        main()
