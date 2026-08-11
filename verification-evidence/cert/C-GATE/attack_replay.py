#!/usr/bin/env python3
"""C-GATE attack set D — the replay path ``scenarios run-generated``.

Drives the real CLI entry point (``python -m neyma_product_driver scenarios
run-generated``) against fabricated-but-valid run directories, and records the
process exit code. Exit 0 on this command means "the gate did not block".

Everything is local and disposable: a throwaway git repo, a throwaway driver
root, no network, no credentials.

    .venv/bin/python verification-evidence/cert/C-GATE/attack_replay.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from scenario_fixtures import (  # noqa: E402
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    base_scenario,
    raw_payload,
    raw_scenario,
)

from neyma_product_driver.config import ScenarioGenerationConfig  # noqa: E402
from neyma_product_driver.evidence import EvidenceStore  # noqa: E402
from neyma_product_driver.scenario_planner import ScenarioPlanner  # noqa: E402

RESULTS: list[dict] = []
PY = str(REPO / ".venv" / "bin" / "python")


def make_repo(root: Path) -> Path:
    repo = root / "neyma"
    repo.mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("# fake authority\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


PERMANENT_YAML = """\
name: backend_generic
mode: backend
app_url: http://127.0.0.1:8931
setup:
  - ./probe.sh seed
services:
  - name: api
    command: ./serve.sh
readiness:
  - tcp: 127.0.0.1:8931
commands:
  - name: smoke
    run: ./probe.sh payments
expect_state:
  - name: payments
    command: ./probe.sh payments
    contains: [ok]
teardown:
  - ./probe.sh reset
"""


def build_plan(root: Path, repo: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    """Generate a REAL plan with the real planner, then persist it in a run dir."""
    driver_root = root / "driver"
    runs_dir = driver_root / "runs"
    scenarios_dir = driver_root / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    (scenarios_dir / "backend_generic.yaml").write_text(PERMANENT_YAML, encoding="utf-8")

    store = EvidenceStore(runs_dir, "2026-cgate-replay")
    planner = ScenarioPlanner(
        repo=repo,
        config=ScenarioGenerationConfig(enabled=True),
        reasoner=ScriptedReasoner([payload]),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )
    planner.plan_initial(task="build supervised approval", unit=FakeUnit(), run_id=store.run_id)
    planner.persist()
    return driver_root, store.run_dir


def config_yaml(repo: Path, driver_root: Path) -> str:
    return (
        f"neyma_repo: {repo}\n"
        f"driver_root: {driver_root}\n"
        f"runs_dir: {driver_root / 'runs'}\n"
        f"scenarios_dir: {driver_root / 'scenarios'}\n"
        "task: replay\n"
        "scenario: backend_generic\n"
        "allow_dirty_tree: true\n"
    )


def attack(ident: str, what: str, payload: dict[str, Any], expect_zero: bool) -> None:
    root = Path(tempfile.mkdtemp(prefix=f"cgate-{ident}-"))
    try:
        repo = make_repo(root)
        driver_root, run_dir = build_plan(root, repo, payload)
        plan = json.loads((run_dir / "scenario-plan.json").read_text())
        cfg = root / "driver.config.yaml"
        cfg.write_text(config_yaml(repo, driver_root), encoding="utf-8")

        proc = subprocess.run(
            [PY, "-m", "neyma_product_driver", "scenarios", "run-generated",
             "--config", str(cfg), "--run", run_dir.name,
             "--scenario", "backend_generic"],
            cwd=str(REPO), capture_output=True, text=True, timeout=600,
        )
        tail = proc.stdout[-2500:]
        row = {
            "id": ident,
            "attack": what,
            "plan_scenarios": [s["id"] for s in plan["scenarios"]],
            "plan_priorities": [s["priority"] for s in plan["scenarios"]],
            "plan_risks": [(r["id"], r["severity"], r["risk_category"]) for r in plan["risks"]],
            "exit_code": proc.returncode,
            "expected_zero": expect_zero,
            "stdout_tail": tail,
            "OUTCOME": (
                "EXPECTED"
                if (proc.returncode == 0) == expect_zero
                else ("FALSE GREEN (exit 0)" if proc.returncode == 0 else "FALSE REFUSAL")
            ),
        }
        RESULTS.append(row)
        print(f"[{row['OUTCOME']:>16}] {ident}: {what}")
        print(f"                    exit={proc.returncode} scenarios={row['plan_scenarios']} "
              f"priorities={row['plan_priorities']} risks={row['plan_risks']}")
        gate_lines = [l for l in tail.splitlines() if "scenario gate" in l]
        for line in gate_lines:
            print(f"                    {line.strip()}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# D1 — a P0 plan: nothing can actually run here (no product, no services), so
#      every scenario is BLOCKED. Exit must be non-zero.
attack(
    "D1",
    "replay a P0 plan against a repo with no product: every scenario is BLOCKED",
    raw_payload(raw_scenario("gen-idem")),
    expect_zero=False,
)

# D2 — the same plan with every scenario demoted to P2. `build_suite` sets
#      required = priority.blocks_acceptance, so expected_required_ids is empty
#      and the gate has nothing to hold. Does a replay in which NOTHING ran
#      still exit 0?
attack(
    "D2",
    "replay a P2-ONLY plan with NO risks: every scenario BLOCKED, nothing required",
    raw_payload(raw_scenario("gen-idem", priority="P2"), risks=[]),
    expect_zero=False,
)

# D3 — P2-only scenarios but the plan still declares a P0 risk.
attack(
    "D3",
    "replay a P2-only plan that declares a P0 risk",
    raw_payload(raw_scenario("gen-idem", priority="P2")),
    expect_zero=False,
)

out_path = Path(__file__).with_name("attack_replay.json")
out_path.write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
bad = [r for r in RESULTS if r["OUTCOME"] != "EXPECTED"]
print("\n" + "=" * 72)
print(f"{len(RESULTS)} replay attacks; deviations: {len(bad)}")
for r in bad:
    print(f"  {r['OUTCOME']}: {r['id']} — {r['attack']}")
print(f"raw: {out_path}")
