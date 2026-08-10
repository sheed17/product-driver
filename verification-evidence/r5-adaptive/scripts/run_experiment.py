"""Run one experiment end to end against the real buggy app.

  python r5/scripts/run_experiment.py <defect> <port> <outdir> [--real]

Wave 1: identical scripted batch, executed for real against the app.
Wave 2: adaptive expansion driven by the REAL failures + REAL clusters.
        The rendered GenerationBrief is always saved. With --real the actual
        LLMScenarioReasoner is invoked so wave-2 output is model-produced.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    TASK,
    WAVE1_PAYLOAD,
    ROOT,
    ScriptedReasoner,
    Unit,
    make_planner,
    run_suite,
)
from neyma_product_driver.scenario_generator import LLMScenarioReasoner  # noqa: E402


class CapturingReasoner:
    """Wave 1 scripted; wave 2 delegated to the real model (or captured only)."""

    def __init__(self, real: bool, out_dir: Path) -> None:
        self.real = real
        self.out_dir = out_dir
        self.calls = 0
        self.briefs: list[str] = []
        self.session_id = ""
        self._llm = LLMScenarioReasoner(ROOT, model="opus", timeout_s=900) if real else None

    def propose(self, brief):
        self.calls += 1
        rendered = brief.render()
        self.briefs.append(rendered)
        (self.out_dir / f"brief-wave{self.calls}.txt").write_text(rendered)
        if self.calls == 1:
            return WAVE1_PAYLOAD
        if self._llm is None:
            return None
        if "--threaded" in sys.argv:
            # WORKAROUND for the sync-over-async defect in
            # LLMScenarioReasoner.propose: run it in a thread that has no
            # running event loop, so the real model is actually reached. The
            # driver itself does NOT do this -- see r5/scripts/test_event_loop.py.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                payload = pool.submit(self._llm.propose, brief).result()
        else:
            payload = self._llm.propose(brief)
        self.session_id = self._llm.session_id or ""
        (self.out_dir / f"wave{self.calls}-raw-payload.json").write_text(
            json.dumps(payload, indent=2) if payload is not None else "null"
        )
        return payload


async def main() -> int:
    defect, port, outdir = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    real = "--real" in sys.argv
    outdir.mkdir(parents=True, exist_ok=True)
    store = outdir / "store.json"
    if store.exists():
        store.unlink()

    reasoner = CapturingReasoner(real, outdir)
    planner = make_planner(reasoner, defect, port, store, outdir)

    print(f"=== {defect}: WAVE 1 (generic, defect-blind) ===")
    planner.plan_initial(task=TASK, unit=Unit(), run_id=f"r5-{defect}")
    (outdir / "wave1-scenarios.json").write_text(
        json.dumps([s.model_dump(mode="json") for s in planner.plan.scenarios], indent=2)
    )

    print(f"=== {defect}: EXECUTING WAVE-1 SUITE AGAINST THE REAL APP ===")
    result = await run_suite(planner, defect, port, store, outdir / "artifacts-w1")
    (outdir / "wave1-suite-result.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2)
    )
    print(result.summary_block())

    failures = [f.brief() for f in result.failures()]
    (outdir / "wave1-failures.txt").write_text("\n".join(failures))
    (outdir / "wave1-clusters.txt").write_text(
        "\n\n".join(c.render() for c in result.clusters)
    )

    before = {s.id for s in planner.plan.scenarios}
    print(f"=== {defect}: WAVE 2 (adaptive expansion from {len(failures)} failure(s)) ===")
    planner.expand_after_failures(
        task=TASK,
        unit=Unit(),
        failures=failures,
        clusters=result.clusters,
        investigation_findings=[],
        evaluator_requests=[],
        diff_files=([] if "--no-diff" in sys.argv else ["r5/fixture/app.py"]),
    )
    new = [s for s in planner.plan.scenarios if s.id not in before]
    (outdir / "wave2-scenarios.json").write_text(
        json.dumps([s.model_dump(mode="json") for s in new], indent=2)
    )
    (outdir / "plan.json").write_text(json.dumps(planner.plan.model_dump(mode="json"), indent=2))

    print(f"wave 2 produced {len(new)} accepted scenario(s):")
    for s in new:
        print(f"  [{s.priority.value} {s.risk_category.value}] {s.id}: {s.title}")
    print(f"waves_used={planner.waves_used} budget_exhausted={planner.budget_exhausted()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
