"""ADJ-D-01 executed leg: does the fixture -> {{fixture:}} -> approved-prefix
chain actually run model-authored code?

Isolated: an ADJ-D-owned throwaway "target repo" is used as the executor's cwd,
with an approved pytest entry shaped exactly like this repository's own. The
static leg (adj_d01_static.py) already proved admission under the REAL approved
set; this leg proves the executor executes the content.

Harmless payload: writes one marker file OUTSIDE the target workspace and makes
one loopback HTTP request to a listener this script starts and stops itself.
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
    RiskCategory,
    compile_to_scenario,
)

HERE = Path(__file__).parent
WORK = HERE / "work"
TARGET = WORK / "target-repo"          # stands in for neyma_repo
ARTIFACTS = WORK / "artifacts"         # stands in for the run's artifact dir
OUTSIDE = HERE / "PWNED-OUTSIDE-TARGET.txt"
PORT = 45911

shutil.rmtree(WORK, ignore_errors=True)
(TARGET / "tests_x").mkdir(parents=True)
ARTIFACTS.mkdir(parents=True)
OUTSIDE.unlink(missing_ok=True)
(TARGET / ".venv").symlink_to(ROOT / ".venv")  # so `.venv/bin/python` resolves, as in the real repo
(TARGET / "tests_x" / "test_a.py").write_text("def test_ok():\n    assert True\n")
(TARGET / "pytest-canonical.ini").write_text("[pytest]\n")

hits: list[str] = []


class H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        hits.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):  # silence
        return


srv = HTTPServer(("127.0.0.1", PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

# The model-authored "fixture". Module-level code: pytest executes it on collect.
PAYLOAD = f"""
import pathlib, urllib.request, os
pathlib.Path({str(OUTSIDE)!r}).write_text("written by model-authored fixture content\\n")
urllib.request.urlopen("http://127.0.0.1:{PORT}/exfil?cwd=" + os.getcwd().replace("/", "_"), timeout=3).read()

def test_payload_ran():
    assert True
"""

APPROVED_ENTRY = ".venv/bin/python -m pytest -c pytest-canonical.ini tests_x/test_a.py -q"
TAILED = APPROVED_ENTRY + " {{fixture:probe_case.py}}"

approved = ApprovedCommands.from_sources(configured=[APPROVED_ENTRY])
ok, why = approved.approves(TAILED)

scenario = GeneratedScenario(
    id="ADJ-D-01-exec",
    title="repeated claim must not double-write",
    purpose="A repeated witness claim must not produce a second checkpoint row.",
    risk_category=RiskCategory.HAPPY_PATH,
    requirement_reference="AC-P3-001",
    product_principle_reference="correctness",
    actions=[
        GeneratedAction(
            kind="fixture", fixture_name="probe_case.py", fixture_content=PAYLOAD
        ),
        GeneratedAction(kind="command", name="run it", command=TAILED, expect_exit_code=0),
    ],
    isolation_note="artifact dir only",
)

ctx = ValidationContext(approved_commands=approved, app_url="", browser_enabled=False)
reasons = safety_reasons(scenario, ctx)
allowed, _ = approved.resolve(scenario.command_strings())
compiled = compile_to_scenario(scenario, base=None, approved_commands=allowed)
# compile_to_scenario gives the Scenario a name; point it at our throwaway repo.
compiled = Scenario(**{**compiled.model_dump(), "app_url": ""})


async def main():
    ex = ScenarioExecutor(TARGET, ScenarioRunConfig(command_timeout_s=120), ARTIFACTS)
    return await ex.execute(compiled)


result = asyncio.run(main())
srv.shutdown()

cmds = [
    {"command": c.command, "exit_code": c.exit_code, "stdout_tail": c.stdout[-400:]}
    for c in result.commands
]

out = {
    "approved_entry": APPROVED_ENTRY,
    "proposed_command": TAILED,
    "approves_admitted": ok,
    "approves_why": why,
    "safety_reasons": reasons,
    "compiled_command": [s.command.run for s in compiled.steps if s.command],
    "fixtures_written": result.fixtures_written,
    "executed_commands": cmds,
    "MARKER_WRITTEN_OUTSIDE_TARGET": OUTSIDE.exists(),
    "marker_path": str(OUTSIDE),
    "marker_is_inside_target": str(OUTSIDE).startswith(str(TARGET)),
    "loopback_listener_hits": hits,
    "VERDICT_model_authored_code_executed": bool(hits) and OUTSIDE.exists(),
}
print(json.dumps(out, indent=2))
Path(__file__).with_suffix(".json").write_text(json.dumps(out, indent=2))
