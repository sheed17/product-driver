"""Do the wave-2 scenarios hold against a CORRECT product?

A follow-up scenario is only useful if it distinguishes the defect from
correctness. This replays each run's ACCEPTED wave-2 population against the
fixture with `DEFECT=none` — the correct, authorized-only, idempotent, durable,
ledger-writing implementation. Every wave-2 scenario should PASS there. One that
fails is a false alarm: it would report a defect against a product that has none.

Nothing under `neyma_product_driver/` is modified; the driver's own
`compile_to_scenario` and `SuiteExecutor` do the work.

  python soundness.py <driver-root> <work-dir> <run-dir> <port>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import harness  # noqa: E402


async def main() -> int:
    driver, work_s, run_s, port_s = sys.argv[1:5]
    seeded = sys.argv[5] if len(sys.argv) > 5 else "none"
    harness.load_driver(driver)

    from neyma_product_driver.config import ScenarioGenerationConfig, ScenarioRunConfig
    from neyma_product_driver.scenario_plan import GeneratedScenarioPlan, compile_to_scenario
    from neyma_product_driver.scenario_suite import SuiteExecutor, build_suite
    from neyma_product_driver.scenarios import ScenarioExecutor
    from neyma_product_driver.scenario_validation import ApprovedCommands

    work = Path(work_s).resolve()
    run_dir = Path(run_s).resolve()
    port = int(port_s)
    out = run_dir / ("soundness" if (len(sys.argv) <= 5 or sys.argv[5] == "none") else f"soundness-{sys.argv[5]}")
    out.mkdir(exist_ok=True)
    store = out / "store.json"
    if store.exists():
        store.unlink()

    plan = GeneratedScenarioPlan.model_validate(json.loads((run_dir / "plan.json").read_text()))
    wave2_ids = {s.id for s in plan.scenarios if s.provenance.wave == 2}
    base = harness.base_scenario(seeded, port, store)
    cfg = ScenarioGenerationConfig(enabled=True, approved_commands=[harness.STATE_CMD])
    approved = ApprovedCommands(list(cfg.approved_commands))

    generated = []
    failed_compile = []
    for scenario in plan.scenarios:
        if scenario.id not in wave2_ids:
            continue
        try:
            ok, _reasons = approved.resolve(scenario.command_strings())
            generated.append(
                (scenario, compile_to_scenario(scenario, base=base, approved_commands=set(ok)))
            )
        except Exception as exc:
            failed_compile.append(f"{scenario.id}: {type(exc).__name__}: {exc}")

    if not generated:
        (out / "result.json").write_text(
            json.dumps({"run": run_dir.name, "scenarios": 0, "failed_compile": failed_compile}, indent=2)
        )
        print(f"{run_dir.name}: nothing to replay")
        return 0

    suite = build_suite(permanent=[], generated=generated)
    run_cfg = ScenarioRunConfig(
        command_timeout_s=30, readiness_timeout_s=25, readiness_poll_interval_s=0.5
    )
    executor = SuiteExecutor(
        make_executor=lambda d: ScenarioExecutor(work, run_cfg, d),
        artifact_root=out / "artifacts",
        browser_enabled=False,
        execution_budget_s=300,
        run_id=f"sound-{run_dir.name}",
        iteration=1,
        emit=lambda m: None,
    )
    result = await executor.run(suite, selection_reason=f"wave-2 soundness replay against DEFECT={seeded}")
    failures = result.failures()
    payload = {
        "run": run_dir.name,
        "scenarios": len(generated),
        "failed_compile": failed_compile,
        "false_alarms": [
            {"scenario_id": f.scenario_id, "brief": f.brief()} for f in failures
        ],
        "summary": result.summary_block(),
    }
    (out / "result.json").write_text(json.dumps(payload, indent=2))
    print(f"{run_dir.name}: {len(generated)} wave-2 scenario(s), {len(failures)} failing against DEFECT={seeded}")
    for f in failures:
        print(f"    {f.scenario_id}: {f.brief()[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
