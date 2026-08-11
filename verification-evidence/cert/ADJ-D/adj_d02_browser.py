"""ADJ-D-02 executed leg: does `http:/host/x` (one slash) escape the approved app_url?

Two loopback listeners started and stopped by this script:
  45921 = the approved app_url the validator saw
  45922 = a host:port the validator never inspected
Nothing external is dialled.
"""
import asyncio
import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from neyma_product_driver.config import ScenarioRunConfig  # noqa: E402
from neyma_product_driver.scenarios import ScenarioExecutor, Scenario  # noqa: E402
from neyma_product_driver.scenario_validation import (  # noqa: E402
    ApprovedCommands,
    ValidationContext,
    safety_reasons,
)
from neyma_product_driver.scenario_plan import (  # noqa: E402
    GeneratedScenario,
    GeneratedAction,
    GeneratedBrowserStep,
    RiskCategory,
    compile_to_scenario,
)

HERE = Path(__file__).parent
WORK = HERE / "work-browser"
shutil.rmtree(WORK, ignore_errors=True)
(WORK / "artifacts").mkdir(parents=True)

APPROVED_PORT, OFFTARGET_PORT = 45921, 45922
APP_URL = f"http://127.0.0.1:{APPROVED_PORT}"
hits = {APPROVED_PORT: [], OFFTARGET_PORT: []}


def serve(port):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits[port].append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<html><body>role={'approved' if port == APPROVED_PORT else 'OFFTARGET'}</body></html>".encode())

        def log_message(self, *a):
            return

    s = HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


servers = [serve(APPROVED_PORT), serve(OFFTARGET_PORT)]

GOTO = f"http:/127.0.0.1:{OFFTARGET_PORT}/escaped-the-approved-target"

approved = ApprovedCommands.from_sources(configured=["/bin/true"])
ctx = ValidationContext(
    approved_commands=approved,
    app_url=APP_URL,
    local_hosts=frozenset({"127.0.0.1", "localhost", "::1"}),
    browser_enabled=True,
)

scenario = GeneratedScenario(
    id="ADJ-D-02-exec",
    title="operator console renders the load list",
    purpose="An operator opening the console must see the load list without errors.",
    risk_category=RiskCategory.HAPPY_PATH,
    requirement_reference="AC-P10-001",
    product_principle_reference="clarity",
    mode="browser",
    actions=[
        GeneratedAction(
            kind="browser",
            name="open the console",
            browser_steps=[GeneratedBrowserStep(goto=GOTO), GeneratedBrowserStep(expect_text="role")],
        )
    ],
    isolation_note="read-only navigation",
)

reasons = safety_reasons(scenario, ctx)
base = Scenario(name="browser_base", mode="browser", app_url=APP_URL)
compiled = compile_to_scenario(scenario, base=base, approved_commands=set())


async def main():
    ex = ScenarioExecutor(
        WORK,
        ScenarioRunConfig(browser_enabled=True, headless=True, capture_trace=False),
        WORK / "artifacts",
    )
    return await ex.execute(compiled)


result = asyncio.run(main())
for s in servers:
    s.shutdown()

obs = result.browser
out = {
    "approved_app_url": APP_URL,
    "proposed_goto": GOTO,
    "validator_inspected_goto": GOTO.startswith(("http://", "https://")),
    "executor_treats_as_absolute": GOTO.startswith("http"),
    "safety_reasons": reasons,
    "browser_steps_recorded": list(obs.steps) if obs else None,
    "final_url": obs.url if obs else None,
    "visible_text": (obs.visible_text or "").strip() if obs else None,
    "hits_on_approved_port": hits[APPROVED_PORT],
    "hits_on_unvalidated_port": hits[OFFTARGET_PORT],
    "VERDICT_escaped_approved_target": bool(hits[OFFTARGET_PORT]),
}
print(json.dumps(out, indent=2))
Path(__file__).with_suffix(".json").write_text(json.dumps(out, indent=2))
