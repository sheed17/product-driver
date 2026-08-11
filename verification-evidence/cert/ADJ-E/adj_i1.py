"""ADJ-E I1: do two scenario ids differing only in case share one evidence dir?

Drives the real SuiteExecutor, real ScenarioExecutor (local `sh` only),
real build_suite and real evaluate_gate. No network, no services.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from neyma_product_driver.evidence import sanitize_filename
from neyma_product_driver.config import ScenarioRunConfig
from neyma_product_driver.scenarios import ScenarioExecutor
from neyma_product_driver.scenario_gate import evaluate_gate
from neyma_product_driver.scenario_plan import (
    GeneratedAction,
    GeneratedScenario,
    Priority,
    RiskCategory,
    compile_to_scenario,
)
from neyma_product_driver.scenario_suite import SuiteExecutor, build_suite


def gen(sid: str, *, ok: bool) -> GeneratedScenario:
    """A generated scenario running one approved local command."""
    return GeneratedScenario(
        id=sid,
        title=f"probe {sid}",
        risk_category=RiskCategory.AUTHORIZATION,
        priority=Priority.P1,
        actions=[
            GeneratedAction(
                kind="command",
                name="probe",
                command="echo hello",
                expect_exit_code=0,
                expect_contains=["hello"],
            )
        ],
        expected_observations=["hello"] if ok else ["never-appears-in-output"],
    )


async def case(label: str, a: tuple[str, bool], b: tuple[str, bool]) -> dict:
    root = Path(tempfile.mkdtemp(prefix=f"adje-i1-{label}-"))
    models = [gen(a[0], ok=a[1]), gen(b[0], ok=b[1])]
    approved = {"echo hello"}
    pairs = []
    for m in models:
        compiled = compile_to_scenario(m, base=None, approved_commands=approved)
        pairs.append((m, compiled))
    suite = build_suite(generated=pairs)

    executor = SuiteExecutor(
        make_executor=lambda d: ScenarioExecutor(Path.cwd(), ScenarioRunConfig(), d),
        artifact_root=root,
        run_id="adj-e",
        iteration=1,
        emit=lambda _m: None,
    )
    result = await executor.run(suite)
    verdict = evaluate_gate(result)

    scen_dir = root / "scenarios"
    dirs = sorted(p.name for p in scen_dir.iterdir()) if scen_dir.exists() else []
    records = {}
    for d in dirs:
        rp = scen_dir / d / "result.json"
        if rp.exists():
            raw = json.loads(rp.read_text())
            records[d] = {
                "scenario_id": raw.get("scenario_id"),
                "passed": raw.get("passed"),
            }
    return {
        "suite_entries": [e.scenario_id for e in suite.entries],
        "assembly_conflicts": list(suite.assembly_conflicts),
        "sanitized": {m.id: sanitize_filename(m.id) for m in models},
        "outcomes": [
            {
                "id": o.scenario_id,
                "outcome": o.outcome.value,
                "required": o.required,
                "evidence_verified": o.evidence_verified,
                "evidence_problem": o.evidence_problem,
                "evidence_path": o.evidence_path.replace(str(root), "<root>"),
            }
            for o in result.outcomes
        ],
        "directories_on_disk": dirs,
        "result_json_on_disk": records,
        "gate": verdict.status.value,
        "gate_required_total": verdict.required_total,
        "gate_required_passed": verdict.required_passed,
        "gate_problems": verdict.generation_problems,
        "gate_unverified": [u.scenario_id for u in verdict.unverified],
    }


async def main() -> None:
    out = {}
    out["control_distinct_ids"] = await case(
        "control", ("gen-clash-a", True), ("gen-clash-b", True)
    )
    out["exact_duplicate_id"] = await case(
        "exact", ("gen-clash", True), ("gen-clash", True)
    )
    out["case_only_both_pass"] = await case(
        "case", ("gen-clash", True), ("Gen-Clash", True)
    )
    out["case_only_asymmetric_first_fails"] = await case(
        "asym", ("gen-clash", False), ("Gen-Clash", True)
    )
    out["case_only_asymmetric_second_fails"] = await case(
        "asym2", ("gen-clash", True), ("Gen-Clash", False)
    )
    print(json.dumps(out, indent=2))


asyncio.run(main())
