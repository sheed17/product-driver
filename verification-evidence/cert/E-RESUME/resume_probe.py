"""E-RESUME Part 1 — interrupt/resume across a genuine process boundary.

Reviewer-authored. Not derived from verification-evidence/remediation/resume_demo.py.

Process 1 drives the REAL `cli.run_control_loop` (fake builder/evaluator only —
those are Claude sessions), with the real ScenarioPlanner, the real
EvidenceStore, the real SuiteExecutor and the real acceptance gate. At a chosen
interruption point it snapshots its in-memory state and then ends the process
ABRUPTLY with ``os._exit`` (no atexit, no flush, no unwinding).

Process 2 is a genuinely fresh interpreter. It rebuilds the planner through the
production resume path — ``cli._make_planner``, which calls
``ScenarioPlanner.restore_from_store`` — and snapshots what survived.

Usage:
    .venv/bin/python verification-evidence/cert/E-RESUME/resume_probe.py
    .venv/bin/python verification-evidence/cert/E-RESUME/resume_probe.py --child <work> <case> <phase>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
PY = str(REPO / ".venv" / "bin" / "python")

from neyma_product_driver.config import (  # noqa: E402
    DriverConfig,
    ScenarioGenerationConfig,
)
from neyma_product_driver.evidence import EvidenceStore  # noqa: E402
from neyma_product_driver.models import (  # noqa: E402
    AssertionResult,
    Decision,
    EvaluatorDecision,
    RunState,
    ScenarioResult,
)
from neyma_product_driver.scenario_planner import PromotionLedger  # noqa: E402

from scenario_fixtures import (  # noqa: E402
    APPROVED_CLEANUP,
    APPROVED_STATE,
    FakeFounder,
    FakeUnit,
    base_scenario,
    raw_payload,
    raw_scenario,
)

RUN_ID = "20260810-000000"


# --------------------------------------------------------------------------
# Fakes for the two Claude sessions only
# --------------------------------------------------------------------------


@dataclass
class FakeTurn:
    text: str = "done."
    session_id: str | None = "builder-1"
    tool_uses: list[str] = field(default_factory=list)
    denied_requests: list[str] = field(default_factory=list)
    is_error: bool = False
    error_detail: str = ""


class FakeBuilder:
    session_id = "builder-1"

    async def send(self, prompt: str, timeout_s: int | None = None) -> FakeTurn:
        return FakeTurn()


class FakeRepoLoader:
    def __init__(self) -> None:
        self.unit = FakeUnit()

    def resolve_active_unit(self) -> FakeUnit:
        return self.unit

    def load(self, topics: list[str] | None = None):
        from neyma_product_driver.context import ActiveUnit, RepositoryContext

        return RepositoryContext(
            head_commit="abc1234",
            branch="main",
            dirty_file_count=0,
            active_unit=ActiveUnit(
                unit_id=self.unit.unit_id,
                name=self.unit.name,
                status="READY",
                acceptance_criteria=self.unit.acceptance_criteria,
            ),
            authority_excerpt="",
            current_excerpt="",
            topic_excerpts={},
            files_consulted=[],
            fingerprint="fp",
        )


class ExitingEvaluator:
    """Returns FIX so the loop keeps going; can end the process at this point."""

    session_id = "evaluator-1"

    def __init__(self, hook=None) -> None:
        self.calls = 0
        self.hook = hook

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision:
        self.calls += 1
        if self.hook:
            self.hook(f"evaluator-call-{self.calls}")
        return EvaluatorDecision(
            decision=Decision.FIX,
            summary="a discrepancy",
            correction_prompt=(
                "On the approval surface, invoice LD56001 shows no accountable owner. Add a "
                "single named owner beside each open obligation, rendered as 'Owner: <name>', "
                "so an operator can tell who moves it next. Do not change the ordering."
            ),
            requirement_reference="U-042",
            product_principle_reference="ownership",
            scenario="backend_generic",
            observed_result="no owner shown",
            expected_result="an owner is shown",
            preserve="everything else",
            retest="re-run the suite",
            evidence_paths=["/runs/x/iteration-01"],
            confidence=0.9,
        )


class ScriptedExecutor:
    """A scenario runner whose verdicts are scripted per bare scenario id."""

    def __init__(self, artifact_dir: Path, failing: set[str], log: list[str]) -> None:
        self.artifact_dir = artifact_dir
        self.failing = failing
        self.log = log
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario):
        key = scenario.name.split(":", 1)[-1]
        self.log.append(key)
        passing = key not in self.failing
        return ScenarioResult(
            scenario_name=scenario.name,
            assertions=[
                AssertionResult(
                    kind="expect_state",
                    target=f"{key}: payments row count",
                    passed=passing,
                    detail="" if passing else "got payments=2, want payments=1",
                )
            ],
        )


class HookedReasoner:
    """Serves queued payloads; may raise or end the process at a given wave."""

    session_id = "scripted"

    def __init__(self, payloads: list[Any], hook=None) -> None:
        self.payloads = list(payloads)
        self.calls = 0
        self.hook = hook

    def propose(self, brief) -> Any:
        self.calls += 1
        if self.hook:
            self.hook(f"propose-{self.calls}")
        item = self.payloads.pop(0) if self.payloads else {"risks": [], "scenarios": []}
        if isinstance(item, Exception):
            raise item
        return item


# --------------------------------------------------------------------------
# Scenario payloads
# --------------------------------------------------------------------------


def probe_scenario(sid: str, marker: str, **kw) -> dict:
    return raw_scenario(
        sid,
        actions=[
            {
                "kind": "state_check",
                "name": marker,
                "state_check": {
                    "name": marker,
                    "command": APPROVED_STATE,
                    "contains": [f"payments={marker}"],
                },
            }
        ],
        state_checks=[{"name": marker, "command": APPROVED_STATE, "contains": ["payments=1"]}],
        expected_observations=[f"payments-{marker}=1"],
        forbidden_observations=[f"payments-{marker}=2"],
        cleanup=[APPROVED_CLEANUP],
        isolation_key=marker,
        **kw,
    )


WAVE1 = raw_payload(
    probe_scenario("gen-alpha-idempotency", "alpha"),
    probe_scenario("gen-beta-authorization", "beta", risk_category="authorization"),
)
WAVE_ADAPTIVE = raw_payload(
    probe_scenario(
        "gen-gamma-adaptive",
        "gamma",
        risk_category="retry_safety",
        source_failures=["gen-alpha-idempotency"],
    ),
    risks=[],
)


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------


def snapshot(planner, store: EvidenceStore, label: str) -> dict:
    plan = planner.plan
    evidence: dict[str, Any] = {}
    for p in sorted(store.run_dir.rglob("result.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            evidence[str(p.relative_to(store.run_dir))] = {
                "scenario_id": rec.get("scenario_id"),
                "run_id": rec.get("run_id"),
                "iteration": rec.get("iteration"),
                "passed": rec.get("passed"),
            }
        except Exception as exc:  # noqa: BLE001
            evidence[str(p.relative_to(store.run_dir))] = {"_unreadable": repr(exc)}
    return {
        "label": label,
        "waves_used": planner.waves_used,
        "max_waves": planner.config.max_waves,
        "budget_exhausted": planner.budget_exhausted(),
        "generation_problems": planner.generation_problems(),
        "plan": {
            "run_id": plan.run_id,
            "task": plan.task,
            "scenario_ids": [s.id for s in plan.scenarios],
            "proposed_ids": [s.proposed_id for s in plan.scenarios],
            "titles": [s.title for s in plan.scenarios],
            "risk_categories": [s.risk_category.value for s in plan.scenarios],
            "priorities": [s.priority.value for s in plan.scenarios],
            "signatures": [s.signature() for s in plan.scenarios],
            "source_failures": {s.id: list(s.provenance.source_failures) for s in plan.scenarios},
            "source_clusters": {s.id: list(s.provenance.source_clusters) for s in plan.scenarios},
            "provenance_waves": {s.id: s.provenance.wave for s in plan.scenarios},
            "risks": [r.description for r in plan.risks],
            "coverage_total": plan.coverage_summary.total_scenarios,
            "waves": [
                {
                    "wave": w.wave,
                    "stage": w.stage,
                    "proposed": w.proposed,
                    "accepted_ids": list(w.accepted_ids),
                    "rejected": [r.id for r in w.rejected],
                    "budget_notes": list(w.budget_notes),
                    "reasoner_error": w.reasoner_error,
                }
                for w in plan.waves
            ],
            "observed_failure_ids": list(plan.observed_failure_ids),
            "observed_cluster_ids": list(plan.observed_cluster_ids),
            "executed_scenario_ids": list(plan.executed_scenario_ids),
        },
        "compiled_ids": sorted(planner.compiled),
        "compiled_digest": {
            sid: json.dumps(sc.model_dump(mode="json"), sort_keys=True)
            for sid, sc in sorted(planner.compiled.items())
        },
        "approved_commands": list(planner.approved_commands.entries),
        "promotion_candidates": [
            {
                "scenario_id": c.scenario_id,
                "discovered_in_iteration": c.discovered_in_iteration,
                "fixed_in_iteration": c.fixed_in_iteration,
            }
            for c in PromotionLedger(store.run_dir).load()
        ],
        "evidence_records": evidence,
        "plan_file_bytes": (
            (store.run_dir / "scenario-plan.json").stat().st_size
            if (store.run_dir / "scenario-plan.json").exists()
            else None
        ),
        "wave_files": sorted(
            p.name for p in (store.run_dir / "scenario-generation").glob("*.json")
        )
        if (store.run_dir / "scenario-generation").exists()
        else [],
    }


# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------


def make_config(work: Path, *, max_waves: int = 3, budget: int = 1800) -> DriverConfig:
    repo = work / "repo"
    driver = work / "driver"
    return DriverConfig(
        neyma_repo=repo,
        driver_root=driver,
        runs_dir=driver / "runs",
        scenarios_dir=driver / "scenarios",
        task="build supervised carrier invoice approval",
        max_iterations=2,
        scenario_generation=ScenarioGenerationConfig(
            enabled=True, max_waves=max_waves, execution_budget_s=budget
        ),
    )


def build_world(work: Path) -> None:
    repo = work / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "CLAUDE.md").write_text("# fake authority\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    (work / "driver" / "scenarios").mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Child: process 1 (generate + execute, then die abruptly)
# --------------------------------------------------------------------------


async def phase_one(work: Path, case: str) -> None:
    from neyma_product_driver.cli import run_control_loop
    from neyma_product_driver.scenario_planner import ScenarioPlanner

    budget = 0 if case == "budget_exhausted" else 1800
    config = make_config(work, budget=budget)
    store = EvidenceStore(config.runs_dir, RUN_ID)
    state = RunState(run_id=RUN_ID, task=config.task, max_iterations=config.max_iterations)

    payloads: list[Any] = [WAVE1, WAVE_ADAPTIVE]
    if case == "failed_wave":
        payloads = [WAVE1, RuntimeError("the model session died")]

    log: list[str] = []
    planner_box: dict[str, Any] = {}

    def die(where: str) -> None:
        if where != case_point(case):
            return
        planner = planner_box.get("planner")
        snap = snapshot(planner, store, f"process1@{where}")
        (work / "expected.json").write_text(json.dumps(snap, indent=2), encoding="utf-8")
        sys.stdout.flush()
        os._exit(9)  # abrupt: no atexit, no unwinding, no cleanup

    reasoner = HookedReasoner(payloads, hook=die)
    planner = ScenarioPlanner(
        repo=config.neyma_repo,
        config=config.scenario_generation,
        reasoner=reasoner,
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
        emit=lambda m: print(m, flush=True),
    )
    planner_box["planner"] = planner
    planner.restore_from_store()

    evaluator = ExitingEvaluator(hook=die)

    await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=evaluator,
        make_executor=lambda d: ScriptedExecutor(d, {"gen-alpha-idempotency"}, log),
        emit=lambda m: print(m, flush=True),
        founder=None,
        repo_loader=FakeRepoLoader(),
        planner=planner,
    )
    # Cases that never reach their kill point simply finish; snapshot anyway.
    snap = snapshot(planner, store, f"process1@completed({case})")
    (work / "expected.json").write_text(json.dumps(snap, indent=2), encoding="utf-8")
    print(json.dumps({"executed": log}), flush=True)


def case_point(case: str) -> str:
    return {
        "mid_wave1": "propose-1",
        "after_wave1_exec": "evaluator-call-1",
        "mid_adaptive": "propose-2",
        "failed_wave": "evaluator-call-2",
        "budget_exhausted": "evaluator-call-1",
        "complete": "never",
    }[case]


# --------------------------------------------------------------------------
# Child: process 2 (resume through cli._make_planner)
# --------------------------------------------------------------------------


def phase_two(work: Path, case: str, out_name: str = "restored.json") -> None:
    from neyma_product_driver import cli

    config = make_config(work)
    store = EvidenceStore(config.runs_dir, RUN_ID)
    args = argparse.Namespace(auto_scenarios=True)
    lines: list[str] = []
    planner = cli._make_planner(
        config, args, store, base_scenario(), FakeFounder(), lambda m: lines.append(m)
    )
    snap = snapshot(planner, store, f"process2@resume({case})")
    snap["restore_emissions"] = lines
    (work / out_name).write_text(json.dumps(snap, indent=2), encoding="utf-8")
    print(json.dumps({"emissions": lines}), flush=True)


def phase_two_persist(work: Path, case: str) -> None:
    """Resume, then persist again — the historical B6 destroy-the-record check."""
    from neyma_product_driver import cli

    config = make_config(work)
    store = EvidenceStore(config.runs_dir, RUN_ID)
    args = argparse.Namespace(auto_scenarios=True)
    planner = cli._make_planner(
        config, args, store, base_scenario(), FakeFounder(), lambda _m: None
    )
    planner.persist()
    planner.note_executed(["a-post-resume-marker"])
    snap = snapshot(planner, store, f"process2@resume+persist({case})")
    (work / "after_persist.json").write_text(json.dumps(snap, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------


CRITICAL_KEYS = [
    ("plan.scenario_ids", "the generated plan (scenario ids)"),
    ("plan.proposed_ids", "proposed ids"),
    ("plan.signatures", "scenario signatures"),
    ("plan.source_failures", "causal links (source_failures)"),
    ("plan.source_clusters", "causal links (source_clusters)"),
    ("plan.observed_failure_ids", "observed failures"),
    ("plan.observed_cluster_ids", "failure clusters"),
    ("plan.executed_scenario_ids", "executed scenario ids"),
    ("plan.risks", "identified risks"),
    ("plan.waves", "wave records"),
    ("waves_used", "wave counter"),
    ("max_waves", "max_waves"),
    ("compiled_ids", "compiled forms"),
    ("compiled_digest", "compiled forms (full)"),
    ("promotion_candidates", "promotion candidates"),
    ("evidence_records", "per-case evidence"),
]


def dig(d: dict, path: str):
    cur: Any = d
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def compare(expected: dict, restored: dict) -> list[dict]:
    rows = []
    for key, label in CRITICAL_KEYS:
        a, b = dig(expected, key), dig(restored, key)
        rows.append(
            {
                "field": key,
                "what": label,
                "survived": a == b,
                "process1": a,
                "process2": b,
            }
        )
    return rows


def child(work: Path, case: str, phase: str) -> None:
    if phase == "one":
        asyncio.run(phase_one(work, case))
    elif phase == "two":
        phase_two(work, case)
    elif phase == "two_again":
        phase_two(work, case, out_name="restored_twice.json")
    elif phase == "two_persist":
        phase_two_persist(work, case)
    else:
        raise SystemExit(f"unknown phase {phase}")


def run_case(root: Path, case: str, mutate=None, dirname: str | None = None) -> dict:
    work = root / (dirname or case)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    build_world(work)

    p1 = subprocess.run(
        [PY, __file__, "--child", str(work), case, "one"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    mutation_note = ""
    if mutate:
        mutation_note = mutate(work)

    p2 = subprocess.run(
        [PY, __file__, "--child", str(work), case, "two"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    p3 = subprocess.run(
        [PY, __file__, "--child", str(work), case, "two_again"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    p4 = subprocess.run(
        [PY, __file__, "--child", str(work), case, "two_persist"],
        capture_output=True, text=True, cwd=str(REPO),
    )

    def load(name):
        p = work / name
        return json.loads(p.read_text()) if p.exists() else None

    expected, restored = load("expected.json"), load("restored.json")
    twice, after = load("restored_twice.json"), load("after_persist.json")
    out = {
        "case": case,
        "mutation": mutation_note,
        "p1_returncode": p1.returncode,
        "p1_died_abruptly": p1.returncode == 9,
        "p2_returncode": p2.returncode,
        "p2_stderr_tail": p2.stderr[-800:],
        "restore_emissions": (restored or {}).get("restore_emissions", []),
        "comparison": compare(expected, restored) if expected and restored else None,
        "resume_twice_identical": (
            {k: v for k, v in twice.items() if k != "label"}
            == {k: v for k, v in restored.items() if k != "label"}
            if twice and restored
            else None
        ),
        "after_repersist_scenarios": (after or {}).get("plan", {}).get("scenario_ids"),
        "after_repersist_waves": len((after or {}).get("plan", {}).get("waves") or []),
        "after_repersist_executed": (after or {}).get("plan", {}).get("executed_scenario_ids"),
        "after_repersist_wave_files": (after or {}).get("wave_files"),
        "expected": expected,
        "restored": restored,
    }
    return out


# -- hostile mutations between the two processes ---------------------------


def mutate_truncate_plan(work: Path) -> str:
    """What a SIGKILL during EvidenceStore.write_json leaves behind."""
    p = work / "driver" / "runs" / RUN_ID / "scenario-plan.json"
    raw = p.read_text(encoding="utf-8")
    p.write_text(raw[: len(raw) // 2], encoding="utf-8")
    return f"scenario-plan.json truncated to {len(raw)//2} of {len(raw)} bytes"


def mutate_empty_plan(work: Path) -> str:
    p = work / "driver" / "runs" / RUN_ID / "scenario-plan.json"
    size = p.stat().st_size
    p.write_text("", encoding="utf-8")
    return f"scenario-plan.json truncated to 0 of {size} bytes"


def mutate_move_head(work: Path) -> str:
    repo = work / "repo"
    (repo / "moved.txt").write_text("moved\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "moved"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    return f"repository HEAD moved to {head}"


def mutate_delete_evidence(work: Path) -> str:
    run_dir = work / "driver" / "runs" / RUN_ID
    removed = []
    for d in sorted(run_dir.glob("iteration-*/scenarios/*")):
        shutil.rmtree(d)
        removed.append(str(d.relative_to(run_dir)))
    return f"deleted evidence directories: {removed}"


def mutate_narrow_commands(work: Path) -> str:
    """The approved-command set shrinks between processes.

    Done the way it really happens: the human-written scenario that approved the
    command is what the planner harvests from. Here the *config* is what the
    resume process supplies, so instead remove the command from the base
    scenario by writing a marker the child reads.
    """
    (work / "NARROW_COMMANDS").write_text("1", encoding="utf-8")
    return "approved command set narrowed for the resuming process"


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="e-resume-part1-"))
    results = {}
    plain_cases = [
        "mid_wave1",
        "after_wave1_exec",
        "mid_adaptive",
        "failed_wave",
        "budget_exhausted",
        "complete",
    ]
    for case in plain_cases:
        results[case] = run_case(root, case)
        print(f"[{case}] p1={results[case]['p1_returncode']} p2={results[case]['p2_returncode']}", flush=True)

    for name, mut in (
        ("hostile_truncated_plan", mutate_truncate_plan),
        ("hostile_empty_plan", mutate_empty_plan),
        ("hostile_head_moved", mutate_move_head),
        ("hostile_evidence_deleted", mutate_delete_evidence),
    ):
        results[name] = run_case(root, "after_wave1_exec", mutate=mut, dirname=name)
        results[name]["case"] = name
        print(f"[{name}] {results[name]['mutation']}", flush=True)

    path = Path(__file__).with_name("resume_probe.json")
    path.write_text(json.dumps({"workdir": str(root), "results": results}, indent=2), encoding="utf-8")
    print(f"\nwrote {path}\nworkdir {root}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(Path(sys.argv[2]), sys.argv[3], sys.argv[4])
    else:
        main()
