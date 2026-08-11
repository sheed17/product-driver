"""ADJ-F reachability probe for F-1: an ordinary RESTART_RECOVERY-shaped
generated browser scenario.

  browser action 1  -> page loads, expect_text FOUND (a real, scored pass)
  stop_service      -> the product goes away
  browser action 2  -> the session-level goto(app_url) is refused

Nothing here is contrived: the driver declares the service itself, readiness
passes honestly, and "reopen the page after the service was restarted" is a
shape the generator is explicitly encouraged to produce.
"""
from __future__ import annotations

import asyncio
import json
import socket
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
    Origin,
    ScenarioSuite,
    SuiteEntry,
    SuiteExecutor,
)
from neyma_product_driver.scenarios import Scenario, ServiceSpec, ScenarioExecutor  # noqa: E402

OUT = Path(sys.argv[1])
REPO = Path(__file__).resolve().parents[3]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    serve = Path(__file__).resolve().parent / "serve.py"

    base = Scenario(
        name="adjf-restart-base",
        mode="browser",
        app_url=url,
        services=[
            ServiceSpec(name="site", command=f"{REPO}/.venv/bin/python {serve} {port}")
        ],
        readiness=[{"http": url + "/", "expect_status": 200, "timeout_s": 5}],
    )
    generated = GeneratedScenario(
        id="ADJF-restart",
        title="the operator screen after the service was bounced",
        risk_category=RiskCategory.RESTART_RECOVERY,
        priority=Priority.P0,
        requirement_reference="ADJ-F local fixture",
        product_principle_reference="ADJ-F local fixture",
        mode="browser",
        service_refs=["site"],
        expected_observations=(
            ["adjudicator clean page"] if len(sys.argv) > 2 and sys.argv[2] == "expect" else []
        ),
        actions=[
            GeneratedAction(
                kind="browser",
                name="before the bounce",
                browser_steps=[GeneratedBrowserStep(expect_text="adjudicator clean page")],
            ),
            GeneratedAction(kind="stop_service", name="bounce the site", service="site"),
            GeneratedAction(
                kind="browser",
                name="after the bounce",
                browser_steps=[GeneratedBrowserStep(expect_text="adjudicator clean page")],
            ),
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
    cfg = ScenarioRunConfig(
        browser_enabled=True, headless=True, capture_trace=False, readiness_timeout_s=20
    )
    runner = SuiteExecutor(
        make_executor=lambda d: ScenarioExecutor(REPO, cfg, d),
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
    print(f"outcome         {o.outcome.value}")
    print(f"assertions      total={o.assertions_total} failed={o.assertions_failed}")
    print(f"evidence_verified {o.evidence_verified}")
    print(f"readiness       ok={sr.readiness_ok} {sr.readiness_detail!r}")
    print(f"result.error    {sr.error!r}")
    print("steps_performed", sr.steps_performed)
    print("browser.steps:")
    for s in (sr.browser.steps if sr.browser else []):
        print("   ", s[:160].replace("\n", " | "))
    print(f"step_failures   {sr.browser.step_failures if sr.browser else 'n/a'}")
    print(f"visible_text    {(sr.browser.visible_text if sr.browser else '')[:120]!r}")
    print("assertion rows:")
    for a in sr.assertions:
        print(f"   [{'PASS' if a.passed else 'FAIL'}] {a.kind} :: {a.target[:120]}")
    print(f"GATE            {verdict.status.value}  "
          f"required {verdict.required_passed}/{verdict.required_total}  "
          f"blocks_acceptance={verdict.blocks_acceptance}")
    print("=" * 70)


asyncio.run(main())
