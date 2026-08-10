"""End-to-end through the production path, with the REAL scenario reasoner.

Mocks are right for deterministic unit tests and wrong as a capability proof, so
nothing about scenario generation is faked here: the plan comes from a live
``LLMScenarioReasoner`` session, the scenarios are validated and compiled by the
real deterministic pipeline, and they are executed against a real HTTP service
running as a real subprocess with a real defect in it.

What *is* controlled, and stated plainly rather than implied:

* the builder is scripted — it applies a prepared fix when it receives a
  correction, instead of asking Claude to write the code;
* the evaluator always ACCEPTs, which is deliberately hostile: it means every
  refusal to accept comes from the scenario gate rather than from the evaluator
  agreeing with it.

Run:  .venv/bin/python verification-evidence/remediation/real_path_demo.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

OUT = Path(__file__).parent / "real-path"

from neyma_product_driver.cli import run_control_loop  # noqa: E402
from neyma_product_driver.config import (  # noqa: E402
    DriverConfig,
    ScenarioGenerationConfig,
    ScenarioRunConfig,
)
from neyma_product_driver.evidence import EvidenceStore  # noqa: E402
from neyma_product_driver.models import RunState  # noqa: E402
from neyma_product_driver.scenario_gate import evaluate_gate  # noqa: E402
from neyma_product_driver.scenario_generator import LLMScenarioReasoner  # noqa: E402
from neyma_product_driver.scenario_planner import ScenarioPlanner  # noqa: E402
from neyma_product_driver.scenario_suite import verify_case_evidence  # noqa: E402
from neyma_product_driver.scenarios import ScenarioExecutor  # noqa: E402

from scenario_fixtures import FakeFounder  # noqa: E402
from test_scenario_e2e import (  # noqa: E402
    APP_SOURCE,
    PROBE_SOURCE,
    FixingBuilder,
    OptimisticEvaluator,
    StubRepoLoader,
    _free_port,
    permanent_scenario,
)

TRANSCRIPT: list[str] = []


def say(line: str = "") -> None:
    print(line, flush=True)
    TRANSCRIPT.append(line)


def build_product(root: Path) -> dict:
    """The controlled target: a real approval service with two real defects."""
    repo = root / "product"
    repo.mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("# authority\n")
    (repo / "app.py").write_text(APP_SOURCE)
    (repo / "probe.py").write_text(PROBE_SOURCE)
    (repo / "reset.py").write_text(
        "import os\n"
        'state = os.environ["APPROVAL_STATE"]\n'
        "if os.path.exists(state):\n"
        "    os.remove(state)\n"
        'print("reset")\n'
    )
    for args in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "remediation"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "initial product"],
    ):
        subprocess.run(args, cwd=repo, check=True)

    port = _free_port()
    return {
        "repo": repo,
        "fix_marker": repo / "FIXED",
        "port": port,
        "state_file": repo / "approvals.json",
        "app_url": f"http://127.0.0.1:{port}",
        "env": {"APPROVAL_STATE": str(repo / "approvals.json")},
        "serve": f"{sys.executable} app.py {port}",
        "probe": f"{sys.executable} probe.py",
        "reset": f"{sys.executable} reset.py",
    }


def describe_plan(planner: ScenarioPlanner) -> None:
    plan = planner.plan
    say(f"  scenarios generated : {len(plan.scenarios)}")
    for scenario in plan.scenarios:
        prov = scenario.provenance
        say(
            f"    [{scenario.priority.value} {scenario.risk_category.value}] {scenario.id}"
            f"  (wave {prov.wave}, {prov.stage})"
        )
        say(f"        title    : {scenario.title}")
        say(f"        because  : {prov.generating_risk or scenario.rationale or '(unstated)'}")
        if prov.source_failures:
            say(f"        caused by: {', '.join(prov.source_failures)}")
    for wave in plan.waves:
        say(
            f"  wave {wave.wave} ({wave.stage}): proposed {wave.proposed}, "
            f"accepted {len(wave.accepted_ids)}, refused {len(wave.rejected)}"
        )
        for refusal in wave.rejected[:6]:
            say(f"      refused {refusal.id or '(unnamed)'}: {refusal.reasons[0][:140]}")
        if wave.reasoner_error:
            say(f"      GENERATION ERROR: {wave.reasoner_error}")


async def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    app = build_product(OUT / "workspace")
    base = permanent_scenario(app)

    config = DriverConfig(
        neyma_repo=app["repo"],
        driver_root=OUT / "driver",
        runs_dir=OUT / "driver" / "runs",
        scenarios_dir=OUT / "driver" / "scenarios",
        task=(
            "Add supervised carrier invoice approval: an operator approves an invoice "
            "and the approval must be recorded durably, exactly once, and must survive "
            "a restart of the service."
        ),
        max_iterations=3,
        run=ScenarioRunConfig(command_timeout_s=60, readiness_timeout_s=30, http_timeout_s=10),
        scenario_generation=ScenarioGenerationConfig(enabled=True, max_waves=3),
    )
    assert config.scenarios_dir is not None
    config.scenarios_dir.mkdir(parents=True, exist_ok=True)

    store = EvidenceStore(config.runs_dir, "realpath-001")
    state = RunState(run_id=store.run_id, task=config.task, max_iterations=3)

    say("=" * 78)
    say("STEP 1 — TASK")
    say("=" * 78)
    say(config.task)
    say()
    say("The product has two real defects: approving twice records two payments,")
    say("and reads are served from memory so an approval vanishes after a restart.")
    say()

    planner = ScenarioPlanner(
        repo=app["repo"],
        config=config.scenario_generation,
        reasoner=LLMScenarioReasoner(app["repo"], model=config.scenario_generation.model),
        store=store,
        base_scenario=base,
        permanent_scenarios=[base],
        founder=FakeFounder(),
        browser_enabled=config.run.browser_enabled,
        emit=lambda m: say(f"    {m}"),
    )

    builder = FixingBuilder(app)
    evaluator = OptimisticEvaluator()

    say("=" * 78)
    say("STEPS 2-9 — REAL GENERATION, EXECUTION, ADAPTATION, CORRECTION, RERUN")
    say("=" * 78)
    result = await run_control_loop(
        config=config,
        scenario=base,
        store=store,
        state=state,
        builder=builder,
        evaluator=evaluator,
        make_executor=lambda artifact_dir: ScenarioExecutor(
            app["repo"], config.run, artifact_dir
        ),
        emit=lambda m: say(m),
        repo_loader=StubRepoLoader(),
        planner=planner,
    )

    say()
    say("=" * 78)
    say("STEP 10 — WHAT THE RUN DECIDED")
    say("=" * 78)
    say(f"  status              : {result.status.value}")
    say(f"  builder applied fix : {builder.fixed}")
    say(f"  evaluator said      : ACCEPT on every turn (deliberately hostile)")
    describe_plan(planner)

    suite = result.suite
    if suite is not None:
        say()
        say("  final suite:")
        for outcome in suite.outcomes:
            say(
                f"    {outcome.outcome.value:<8} {outcome.origin.value:<10} "
                f"{outcome.scenario_id}  evidence_verified={outcome.evidence_verified}"
            )
        verdict = evaluate_gate(suite, generation_problems=planner.generation_problems())
        say()
        for line in verdict.summary_block().splitlines():
            say(f"  {line}")

        say()
        say("  evidence references, re-verified independently of the run:")
        dangling = 0
        for outcome in suite.outcomes:
            problem = verify_case_evidence(
                outcome.evidence_path,
                scenario_id=outcome.scenario_id,
                run_id=store.run_id,
                iteration=len(state.iterations),
            )
            if outcome.evidence_path and problem and outcome.outcome.value != "SKIPPED":
                dangling += 1
                say(f"    DANGLING {outcome.scenario_id}: {problem}")
        say(f"    dangling references: {dangling}")

    summary = {
        "status": result.status.value,
        "builder_applied_fix": builder.fixed,
        "iterations": len(state.iterations),
        "generated_scenarios": [s.id for s in planner.plan.scenarios],
        "waves": [
            {
                "wave": w.wave,
                "stage": w.stage,
                "proposed": w.proposed,
                "accepted": w.accepted_ids,
                "refused": [{"id": r.id, "reasons": r.reasons} for r in w.rejected],
                "reasoner_error": w.reasoner_error,
            }
            for w in planner.plan.waves
        ],
        "adaptive_links": {
            s.id: s.provenance.source_failures
            for s in planner.plan.scenarios
            if s.provenance.stage == "adaptive"
        },
        "generation_problems": planner.generation_problems(),
        "final_outcomes": (
            [
                {
                    "scenario_id": o.scenario_id,
                    "origin": o.origin.value,
                    "outcome": o.outcome.value,
                    "evidence_verified": o.evidence_verified,
                }
                for o in suite.outcomes
            ]
            if suite is not None
            else []
        ),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    (OUT / "transcript.txt").write_text("\n".join(TRANSCRIPT))
    say()
    say(f"artifacts: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
