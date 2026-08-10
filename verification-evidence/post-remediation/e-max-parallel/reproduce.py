"""Reproduce r4 F-5: `max_parallel` is a dead parameter on the executor.

Run against any driver checkout:  python reproduce.py --driver <path> [--out f]

The config validator does refuse anything but 1, and that part was never the
defect. The defect is one layer down: `SuiteExecutor` accepts `max_parallel`,
coerces it, stores it, and then never reads it. A caller that does not go
through config — a test, an embedder, a verification harness — is told it has
parallel execution and gets sequential execution.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    sys.path.insert(0, str(Path(args.driver).resolve()))

    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.models import ScenarioResult
    from neyma_product_driver.scenario_plan import Priority, RiskCategory
    from neyma_product_driver.scenario_suite import (
        Origin,
        ScenarioSuite,
        SuiteEntry,
        SuiteExecutor,
    )
    from neyma_product_driver.scenarios import Scenario

    f: dict[str, object] = {}

    # -- 1. the config layer (already honest, checked so the report is complete)
    try:
        ScenarioGenerationConfig(max_parallel=4)
        f["config_refuses_above_one"] = False
    except Exception as exc:
        f["config_refuses_above_one"] = True
        f["config_refusal"] = str(exc).splitlines()[-1][:160]

    # -- 2. the executor layer -------------------------------------------------
    source = inspect.getsource(SuiteExecutor)
    body_after_init = source.split("def run(", 1)[-1]
    f["executor_reads_max_parallel_after_init"] = "max_parallel" in body_after_init

    accepted_value: object = None
    construction_error = ""
    try:
        probe = SuiteExecutor(
            make_executor=lambda _d: None,  # type: ignore[arg-type]
            artifact_root=Path("."),
            max_parallel=8,
        )
        accepted_value = getattr(probe, "max_parallel", "(absent)")
    except Exception as exc:
        construction_error = f"{type(exc).__name__}: {exc}"
    f["executor_accepts_max_parallel_8"] = not construction_error
    f["executor_stored_value"] = accepted_value
    f["executor_construction_error"] = construction_error

    # -- 3. does it actually overlap anything? ---------------------------------
    overlap = {"max_concurrent": 0}

    class Timed:
        live = 0

        def __init__(self, directory: Path) -> None:
            self.service_logs: dict[str, str] = {}

        async def execute(self, scenario: Scenario) -> ScenarioResult:
            Timed.live += 1
            overlap["max_concurrent"] = max(overlap["max_concurrent"], Timed.live)
            await asyncio.sleep(0.05)
            Timed.live -= 1
            return ScenarioResult(
                scenario_name=scenario.name, phase="p", readiness_ok=True, assertions=[]
            )

    suite = ScenarioSuite()
    for index in range(4):
        suite.add(
            SuiteEntry(
                scenario_id=f"s{index}",
                scenario=Scenario(name=f"s{index}", phase="p"),
                origin=Origin.GENERATED,
                priority=Priority.P0,
                risk_category=RiskCategory.HAPPY_PATH,
                isolation_key=f"key-{index}",  # provably non-contending
            )
        )

    if not construction_error:
        with tempfile.TemporaryDirectory() as tmp:
            executor = SuiteExecutor(
                make_executor=Timed,
                artifact_root=Path(tmp),
                run_id="maxpar",
                iteration=1,
                max_parallel=8,
            )
            started = time.monotonic()
            asyncio.run(executor.run(suite))
            elapsed = time.monotonic() - started
        f["observed_max_concurrent"] = overlap["max_concurrent"]
        f["elapsed_s"] = round(elapsed, 3)
        f["sequential_in_fact"] = overlap["max_concurrent"] == 1

    f["REPRODUCED"] = bool(
        f["executor_accepts_max_parallel_8"]
        and not f["executor_reads_max_parallel_after_init"]
    )
    f["SUMMARY"] = (
        "the executor accepts and stores a parallelism it never implements: "
        f"max_parallel={f['executor_stored_value']!r}, observed concurrency "
        f"{f.get('observed_max_concurrent')}"
        if f["REPRODUCED"]
        else "the executor no longer claims a parallelism it does not implement"
    )

    text = json.dumps(f, indent=2, default=str)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
