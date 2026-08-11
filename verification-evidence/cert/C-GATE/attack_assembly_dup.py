#!/usr/bin/env python3
"""C-GATE attack set E — assembly loss, and the reachability of the duplicate-id hole.

E1: a generated scenario whose id collides with the permanent scenario's name is
    dropped before execution. Does the run still accept?  (real control loop)

E2: attack A12 found that ``evaluate_gate`` reads ``{o.scenario_id: o for o in
    outcomes}`` — last record wins — so a PASSED record appended after a FAILED
    record for the same id flips the gate to VERIFIED. This asks whether the
    product can actually PRODUCE such a SuiteResult, by running the real
    SuiteExecutor over a suite that has been forced to contain two entries with
    the same id.

    .venv/bin/python verification-evidence/cert/C-GATE/attack_assembly_dup.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenario_fixtures import (  # noqa: E402
    ScriptedReasoner,
    base_scenario,
    raw_payload,
    raw_scenario,
)

import attack_loop_e2e as e2e  # noqa: E402

from neyma_product_driver.cli import run_control_loop  # noqa: E402
from neyma_product_driver.evidence import EvidenceStore  # noqa: E402
from neyma_product_driver.models import (  # noqa: E402
    AssertionResult,
    RunState,
    RunStatus,
    ScenarioResult,
)
from neyma_product_driver.scenario_gate import evaluate_gate  # noqa: E402
from neyma_product_driver.scenario_suite import (  # noqa: E402
    Origin,
    Outcome,
    ScenarioSuite,
    SuiteEntry,
    SuiteExecutor,
    build_suite,
)
from neyma_product_driver.scenarios import Scenario  # noqa: E402

RESULTS: list[dict] = []


def record(ident: str, what: str, observed: Any, expected: str, verdict: str) -> None:
    RESULTS.append({"id": ident, "attack": what, "observed": observed,
                    "expected": expected, "OUTCOME": verdict})
    print(f"[{verdict:>16}] {ident}: {what}")
    print(f"                    observed: {observed}")


# ===========================================================================
# E1 — id collision drops a generated scenario before the suite
# ===========================================================================
async def e1() -> None:
    root = Path(tempfile.mkdtemp(prefix="cgate-E1-"))
    try:
        repo = e2e.make_repo(root)
        config = e2e.make_config(root, repo, enabled=True)
        config.max_iterations = 1
        assert config.runs_dir is not None
        store = EvidenceStore(config.runs_dir, "2026-cgate-E1")
        state = RunState(run_id=store.run_id, task=config.task, max_iterations=1)
        # The generated scenario claims the permanent scenario's id.
        planner = e2e.make_planner(
            config, store,
            ScriptedReasoner([raw_payload(raw_scenario("backend_generic"))]),
        )
        log: list[str] = []
        result = await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=e2e.FakeBuilder(),
            evaluator=e2e.HostileEvaluator(),
            make_executor=lambda d: e2e.ScriptedScenarioExecutor(d, {}, log),
            emit=lambda _m: None,
            repo_loader=e2e.FakeRepoLoader(),
            planner=planner,
        )
        suite = result.suite
        gate = evaluate_gate(
            suite,
            generation_problems=planner.generation_problems(),
            risks=list(planner.plan.risks),
        ) if suite else None
        observed = {
            "run_status": result.status.value,
            "planned_generated_ids": [s.id for s in planner.plan.scenarios],
            "scenarios_executed": log,
            "assembly_problems": list(suite.assembly_problems) if suite else None,
            "gate": gate.status.value if gate else None,
        }
        accepted = result.status is RunStatus.ACCEPTED
        record(
            "E1",
            "a generated scenario takes the permanent scenario's id and is dropped "
            "before execution; hostile evaluator says ACCEPT",
            observed,
            "the dropped scenario is recorded as an assembly problem and blocks",
            "FALSE ACCEPTANCE" if accepted else "EXPECTED",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ===========================================================================
# E2 — can the product produce two outcome records for one scenario id?
# ===========================================================================
class FlipFlopExecutor:
    """Fails the first time it sees a scenario, passes the second."""

    SEEN: dict[str, int] = {}

    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = artifact_dir
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        n = FlipFlopExecutor.SEEN.get(scenario.name, 0) + 1
        FlipFlopExecutor.SEEN[scenario.name] = n
        passing = n > 1
        return ScenarioResult(
            scenario_name=scenario.name,
            assertions=[AssertionResult(kind="expect_state", target="x", passed=passing,
                                        detail="" if passing else "got 2, want 1")],
        )


async def e2_dup() -> None:
    root = Path(tempfile.mkdtemp(prefix="cgate-E2-"))
    try:
        # Step 1: can build_suite be made to admit two entries with one id?
        suite = build_suite(
            permanent=[("perm", Scenario(name="perm")), ("perm", Scenario(name="perm2"))]
        )
        record(
            "E2a",
            "build_suite offered the same id twice — does the second get in?",
            {"entries": [e.scenario_id for e in suite.entries],
             "assembly_conflicts": suite.assembly_conflicts},
            "the second is refused and recorded as an assembly conflict",
            "EXPECTED" if len(suite.entries) == 1 and suite.assembly_conflicts
            else "REACHABLE DUPLICATE",
        )

        # Step 2: force a duplicate past build_suite by appending directly to
        # `entries` (bypassing `add`). Can the executor then emit two outcomes?
        forced = ScenarioSuite()
        forced.entries.append(SuiteEntry(scenario_id="perm", scenario=Scenario(name="perm"),
                                         origin=Origin.PERMANENT, required=True))
        forced.entries.append(SuiteEntry(scenario_id="perm", scenario=Scenario(name="perm"),
                                         origin=Origin.PERMANENT, required=True))
        FlipFlopExecutor.SEEN.clear()
        ex = SuiteExecutor(make_executor=FlipFlopExecutor,
                           artifact_root=root / "it", run_id="RUN-A", iteration=1)
        result = await ex.run(forced)
        verdict = evaluate_gate(result)
        observed = {
            "outcomes": [(o.scenario_id, o.outcome.value, o.evidence_verified)
                         for o in result.outcomes],
            "expected_required_ids": list(result.expected_required_ids),
            "gate": verdict.status.value,
            "blocking_failures": len(result.blocking_failures()),
        }
        # Reachable only by bypassing ScenarioSuite.add, which no product code
        # path does — but if it IS reachable, the gate would say VERIFIED.
        masked = verdict.status.value == "VERIFIED" and any(
            o.outcome is Outcome.FAILED for o in result.outcomes
        )
        record(
            "E2b",
            "duplicate entries FORCED past ScenarioSuite.add: does a later PASS "
            "mask the earlier FAIL at the gate?",
            observed,
            "the gate must not report VERIFIED while a recorded failure exists",
            "MASKED (last-record-wins)" if masked else "EXPECTED",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def main() -> None:
    await e1()
    await e2_dup()
    out_path = Path(__file__).with_name("attack_assembly_dup.json")
    out_path.write_text(json.dumps(RESULTS, indent=2, default=str), encoding="utf-8")
    bad = [r for r in RESULTS if r["OUTCOME"] != "EXPECTED"]
    print("\n" + "=" * 72)
    print(f"{len(RESULTS)} attacks; deviations: {len(bad)}")
    for r in bad:
        print(f"  {r['OUTCOME']}: {r['id']} — {r['attack']}")
    print(f"raw: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
