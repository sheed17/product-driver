"""ADJ-F F-2: same generated browser scenario under two missing-capability states.

  mode=nopkg      the playwright PYTHON PACKAGE is absent
                  (a meta_path blocker makes `import playwright.async_api`
                  raise ModuleNotFoundError, exactly as a real absence does)
  mode=nochromium the package is present, the chromium BINARY is not
                  (PLAYWRIGHT_BROWSERS_PATH points at an empty directory)

Everything else is identical: same fixture, same scenario, same gate.
"""
from __future__ import annotations

import asyncio
import importlib.abc
import json
import os
import sys
from pathlib import Path

MODE = sys.argv[1]
OUT = Path(sys.argv[2])

if MODE == "nochromium":
    empty = OUT / "empty-browsers"
    empty.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(empty.resolve())

if MODE == "nopkg":
    class _Blocker(importlib.abc.MetaPathFinder):
        def find_module(self, fullname, path=None):  # legacy, unused
            return None

        def find_spec(self, fullname, path=None, target=None):
            if fullname == "playwright" or fullname.startswith("playwright."):
                raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
            return None

    for mod in [m for m in sys.modules if m == "playwright" or m.startswith("playwright.")]:
        del sys.modules[mod]
    sys.meta_path.insert(0, _Blocker())

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from neyma_product_driver.config import ScenarioRunConfig  # noqa: E402
from neyma_product_driver.scenario_gate import evaluate_gate  # noqa: E402
from neyma_product_driver.scenario_plan import (  # noqa: E402
    GeneratedAction,
    GeneratedBrowserStep,
    GeneratedScenario,
    Priority,
    RiskCategory,
    compile_to_scenario,
)
from neyma_product_driver.scenario_suite import (  # noqa: E402
    Origin,
    ScenarioSuite,
    SuiteEntry,
    SuiteExecutor,
)
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor  # noqa: E402

import fixture  # noqa: E402


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    srv, base_url = fixture.start()
    try:
        base = Scenario(name="adjf-base", mode="browser", app_url=base_url + "/")
        generated = GeneratedScenario(
            id=f"ADJF-{MODE}",
            title=f"adjudicator {MODE}",
            risk_category=RiskCategory.UI_BACKEND_DISAGREEMENT,
            priority=Priority.P0,
            requirement_reference="ADJ-F local fixture",
            product_principle_reference="ADJ-F local fixture",
            mode="browser",
            actions=[
                GeneratedAction(
                    kind="browser",
                    name="look at the screen",
                    browser_steps=[
                        GeneratedBrowserStep(screenshot="landed"),
                        GeneratedBrowserStep(expect_text="adjudicator clean page"),
                    ],
                )
            ],
        )
        compiled = compile_to_scenario(generated, base=base, approved_commands=set())
        suite = ScenarioSuite(
            entries=[
                SuiteEntry(
                    scenario_id=generated.id,
                    scenario=compiled,
                    origin=Origin.GENERATED,
                    required=True,
                    priority=generated.priority,
                    risk_category=generated.risk_category,
                    generated=generated,
                )
            ]
        )
        cfg = ScenarioRunConfig(browser_enabled=True, headless=True, capture_trace=False)
        repo = Path(__file__).resolve().parents[3]
        runner = SuiteExecutor(
            make_executor=lambda d: ScenarioExecutor(repo, cfg, d),
            artifact_root=OUT,
            browser_enabled=True,
            run_id="ADJF",
            iteration=1,
            emit=print,
        )
        suite_result = await runner.run(suite)
        verdict = evaluate_gate(suite_result, risks=[])
        (OUT / "suite-result.json").write_text(
            json.dumps(suite_result.model_dump(mode="json"), indent=2)
        )
        (OUT / "gate.json").write_text(json.dumps(verdict.model_dump(mode="json"), indent=2))

        o = suite_result.outcomes[0]
        sr = runner.results[generated.id]
        print("=" * 70)
        print(f"MODE            {MODE}")
        print(f"outcome         {o.outcome.value}")
        print(f"assertions      total={o.assertions_total} failed={o.assertions_failed}")
        print(f"evidence_verified {o.evidence_verified}")
        print(f"result.error    {sr.error!r}")
        print(f"screenshots     {len(sr.browser.screenshots) if sr.browser else 'n/a'}")
        print(f"browser.steps   {sr.browser.steps if sr.browser else 'n/a'}")
        print(f"GATE            {verdict.status.value}  "
              f"required {verdict.required_passed}/{verdict.required_total}  "
              f"blocks_acceptance={verdict.blocks_acceptance}")
        print("=" * 70)
    finally:
        srv.shutdown()


asyncio.run(main())
