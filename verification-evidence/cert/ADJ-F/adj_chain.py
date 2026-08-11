"""ADJ-F independent reproduction: generated browser scenario -> compile ->
suite runner (real chromium) -> evidence write/verify -> acceptance gate.

Nothing is stubbed between the links. The only network target is a loopback
HTTP server this process starts and stops itself.

Usage:  adj_chain.py <case> <outdir>
  case = f1        initial goto hangs (app_url=/hang)
  case = f1ctl      control: same scenario, app_url=/  (must PASS honestly)
  case = f3        two browser actions; forbidden string rendered by the first
  case = f3ctl      control: one browser action; same forbidden string
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

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
    ScenarioSuite,
    SuiteExecutor,
    SuiteEntry,
    Origin,
)
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor  # noqa: E402

import fixture  # noqa: E402


def make_generated(case: str, app_path: str) -> GeneratedScenario:
    if case in ("f1", "f1ctl"):
        actions = [
            GeneratedAction(
                kind="browser",
                name="look at the screen",
                browser_steps=[
                    GeneratedBrowserStep(screenshot="landed"),
                    GeneratedBrowserStep(expect_text="adjudicator clean page"),
                    GeneratedBrowserStep(expect_text="owner: unassigned"),
                ],
            )
        ]
        expected: list[str] = []
        forbidden: list[str] = []
    else:  # f3 / f3ctl
        first = GeneratedAction(
            kind="browser",
            name="first look at the failing screen",
            browser_steps=[
                GeneratedBrowserStep(goto="/boom"),
                GeneratedBrowserStep(screenshot="initial"),
            ],
        )
        second = GeneratedAction(
            kind="browser",
            name="second look at an unrelated screen",
            browser_steps=[
                GeneratedBrowserStep(goto="/quiet"),
                GeneratedBrowserStep(screenshot="after"),
            ],
        )
        if case == "f3":
            actions = [first, second]
        elif case == "f3seq":
            # ONE browser action / ONE session, two navigations — the exact
            # shape of the shipped browser_generic.yaml template.
            actions = [
                GeneratedAction(
                    kind="browser",
                    name="walk the surface the way an operator would",
                    browser_steps=[
                        GeneratedBrowserStep(goto="/boom"),
                        GeneratedBrowserStep(screenshot="first-screen"),
                        GeneratedBrowserStep(goto="/quiet"),
                        GeneratedBrowserStep(screenshot="second-screen"),
                    ],
                )
            ]
        else:
            actions = [first]
        expected = []
        forbidden = ["Traceback (most recent call last):"]

    return GeneratedScenario(
        id=f"ADJF-{case}",
        title=f"adjudicator {case}",
        risk_category=RiskCategory.UI_BACKEND_DISAGREEMENT,
        priority=Priority.P0,
        requirement_reference="ADJ-F local fixture",
        product_principle_reference="ADJ-F local fixture",
        mode="browser",
        actions=actions,
        expected_observations=expected,
        forbidden_observations=forbidden,
    )


async def main() -> None:
    case, outdir = sys.argv[1], Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)
    srv, base_url = fixture.start()
    try:
        app_path = "/hang" if case == "f1" else "/"
        base = Scenario(
            name="adjf-base",
            mode="browser",
            app_url=base_url + app_path,
        )
        generated = make_generated(case, app_path)
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
        cfg = ScenarioRunConfig(
            browser_enabled=True,
            headless=True,
            capture_trace=False,
            readiness_timeout_s=5,
        )
        repo = Path(__file__).resolve().parents[3]
        runner = SuiteExecutor(
            make_executor=lambda d: ScenarioExecutor(repo, cfg, d),
            artifact_root=outdir,
            browser_enabled=True,
            run_id="ADJF",
            iteration=1,
            emit=lambda m: print(m),
        )
        suite_result = await runner.run(suite)
        verdict = evaluate_gate(suite_result, risks=[])

        (outdir / "suite-result.json").write_text(
            json.dumps(suite_result.model_dump(mode="json"), indent=2)
        )
        (outdir / "gate.json").write_text(json.dumps(verdict.model_dump(mode="json"), indent=2))

        o = suite_result.outcomes[0]
        sr = runner.results[generated.id]
        print("=" * 70)
        print(f"CASE            {case}   app_url={base.app_url}")
        print(f"outcome         {o.outcome.value}")
        print(f"assertions      total={o.assertions_total} failed={o.assertions_failed}")
        print(f"evidence_verified {o.evidence_verified}")
        print(f"result.error    {sr.error!r}")
        print(f"readiness_ok    {sr.readiness_ok}")
        print(f"screenshots     {len(sr.browser.screenshots) if sr.browser else 'n/a'}")
        print(f"browser.steps   {sr.browser.steps if sr.browser else 'n/a'}")
        print(f"step_failures   {sr.browser.step_failures if sr.browser else 'n/a'}")
        vt = (sr.browser.visible_text if sr.browser else "") or ""
        print(f"visible_text    {vt[:200]!r}")
        print("assertion rows:")
        for a in sr.assertions:
            print(f"   [{'PASS' if a.passed else 'FAIL'}] {a.kind} :: {a.target}")
        print(f"GATE            {verdict.status.value}  "
              f"required {verdict.required_passed}/{verdict.required_total}  "
              f"blocks_acceptance={verdict.blocks_acceptance}")
        print("=" * 70)
    finally:
        srv.shutdown()


asyncio.run(main())
