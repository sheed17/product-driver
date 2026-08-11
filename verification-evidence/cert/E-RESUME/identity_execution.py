"""E-RESUME Part 2 — execute near-identical scenario identities for real.

Reviewer-authored. Uses the real SuiteExecutor + real ScenarioExecutor against a
disposable temp repo (local shell only, no services, no network), then the real
acceptance gate.

Run:  .venv/bin/python verification-evidence/cert/E-RESUME/identity_execution.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from neyma_product_driver.config import ScenarioRunConfig  # noqa: E402
from neyma_product_driver.scenario_gate import evaluate_gate  # noqa: E402
from neyma_product_driver.scenario_plan import compile_to_scenario  # noqa: E402
from neyma_product_driver.scenario_suite import (  # noqa: E402
    SuiteExecutor,
    build_suite,
)
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor  # noqa: E402

from scenario_fixtures import make_scenario  # noqa: E402

OUT: dict[str, object] = {}
WORK = Path(tempfile.mkdtemp(prefix="e-resume-ident-"))
PROBE = "./probe.sh"


def build_repo() -> Path:
    repo = WORK / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "probe.sh").write_text("#!/bin/sh\necho \"marker=$1\"\n", encoding="utf-8")
    (repo / "probe.sh").chmod(0o755)
    return repo


def base() -> Scenario:
    """A permanent scenario that approves the probe commands. No services."""
    return Scenario(
        name="local_probe",
        mode="backend",
        commands=[{"name": "smoke", "run": f"{PROBE} smoke"}],
        expect_state=[
            {"name": "alpha", "command": f"{PROBE} alpha", "contains": ["marker=alpha"]},
            {"name": "beta", "command": f"{PROBE} beta", "contains": ["marker=beta"]},
            {"name": "dot", "command": f"{PROBE} dot", "contains": ["marker=dot"]},
        ],
    )


APPROVED = {f"{PROBE} smoke", f"{PROBE} alpha", f"{PROBE} beta", f"{PROBE} dot"}


def gen(scenario_id: str, marker: str):
    """A generated scenario whose only action reads a distinct marker."""
    return make_scenario(
        scenario_id,
        actions=[
            {
                "kind": "state_check",
                "name": marker,
                "state_check": {
                    "name": marker,
                    "command": f"{PROBE} {marker}",
                    "contains": [f"marker={marker}"],
                },
            }
        ],
        state_checks=[],
        expected_observations=[f"marker={marker}"],
        cleanup=[],
        service_refs=[],
        isolation_key=marker,
    )


async def run_case(label: str, pairs, repo: Path, root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    compiled = []
    for model, _marker in pairs:
        compiled.append((model, compile_to_scenario(model, base=base(), approved_commands=APPROVED)))
    suite = build_suite(generated=compiled)
    executor = SuiteExecutor(
        make_executor=lambda d: ScenarioExecutor(repo, ScenarioRunConfig(), d),
        artifact_root=root,
        run_id="RUNX",
        iteration=1,
    )
    result = await executor.run(suite)
    verdict = evaluate_gate(result)

    dirs = {}
    for p in sorted(root.rglob("result.json")):
        try:
            rec = json.loads(p.read_text())
        except Exception as exc:  # noqa: BLE001
            rec = {"_unreadable": str(exc)}
        dirs[str(p.relative_to(root))] = {
            "scenario_id_in_record": rec.get("scenario_id"),
            "scenario_name": rec.get("scenario_name"),
        }
    return {
        "label": label,
        "suite_size": len(suite),
        "assembly_conflicts": suite.assembly_conflicts,
        "outcomes": [
            {
                "id": o.scenario_id,
                "outcome": o.outcome.value,
                "evidence_path": str(Path(o.evidence_path).resolve()),
                "evidence_verified": o.evidence_verified,
                "evidence_problem": o.evidence_problem,
            }
            for o in result.outcomes
        ],
        "distinct_evidence_dirs": len({Path(o.evidence_path).resolve() for o in result.outcomes}),
        "result_json_files": dirs,
        "gate": verdict.status.value,
        "gate_problems": verdict.generation_problems,
    }


async def main() -> None:
    repo = build_repo()

    # 1. two ids differing ONLY in case
    OUT["case_only"] = await run_case(
        "ids differing only in case",
        [(gen("Gen-Case-Probe", "alpha"), "alpha"), (gen("gen-case-probe", "beta"), "beta")],
        repo,
        WORK / "case",
    )

    # 2. two long ids sharing an 80-char prefix (the advertised protection)
    long_a = "gen-" + "p" * 90 + "-alpha"
    long_b = "gen-" + "p" * 90 + "-beta"
    OUT["long_prefix"] = await run_case(
        "ids sharing a 94-char prefix",
        [(gen(long_a, "alpha"), "alpha"), (gen(long_b, "beta"), "beta")],
        repo,
        WORK / "long",
    )

    # 3. a model-authored id that is a path component: ".."  and "."
    OUT["dotdot"] = await run_case(
        "ids '..' and '.'",
        [(gen("..", "alpha"), "alpha"), (gen(".", "dot"), "dot")],
        repo,
        WORK / "dots",
    )

    # 4. a genuinely duplicate id offered to the suite: does it BLOCK?
    dup_a = gen("gen-dup", "alpha")
    dup_b = gen("gen-dup", "beta")
    OUT["duplicate_admission"] = await run_case(
        "same id offered twice",
        [(dup_a, "alpha"), (dup_b, "beta")],
        repo,
        WORK / "dup",
    )

    # 5. case-only collision where the SECOND scenario FAILS.
    #    Does the first still count as VERIFIED while the evidence on disk is
    #    the second one's failing record?
    good = gen("Gen-Clash", "alpha")
    bad = gen("gen-clash", "alpha")
    bad = bad.model_copy(update={"expected_observations": ["marker=NEVER-APPEARS"]})
    OUT["case_only_asymmetric"] = await run_case(
        "case-variant ids, second one fails",
        [(good, "alpha"), (bad, "alpha")],
        repo,
        WORK / "clash",
    )
    clash_dir = WORK / "clash" / "scenarios"
    OUT["case_only_asymmetric"]["directories_on_disk"] = sorted(
        p.name for p in clash_dir.iterdir()
    ) if clash_dir.exists() else []

    path = Path(__file__).with_name("identity_execution.json")
    path.write_text(json.dumps(OUT, indent=2), encoding="utf-8")
    print(json.dumps(OUT, indent=2))
    print(f"\nwrote {path}\nworkdir {WORK}")


asyncio.run(main())
