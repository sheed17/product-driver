"""D-SAFETY probes: redirect following, header injection, fixture-name confinement.

HARMLESS AND LOCAL. Two loopback listeners on unusual ports; the "off-target"
role is played by a second loopback port, never an external host.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from neyma_product_driver.runner import http_request  # noqa: E402

PORT_APPROVED = 45301   # the "product"
PORT_OFFTARGET = 45302  # stands in for an off-target host

HITS: dict[str, list[str]] = {"approved": [], "offtarget": []}


def _serve(port: int, role: str) -> socketserver.TCPServer:
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            HITS[role].append(self.path)
            if role == "approved" and self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{PORT_OFFTARGET}/followed")
                self.end_headers()
                return
            body = f"role={role}".encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            self.do_GET()

        def log_message(self, *a: object) -> None:
            return

    srv = socketserver.TCPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def _scenario_redirect() -> dict:
    import tempfile

    from neyma_product_driver.config import ScenarioRunConfig
    from neyma_product_driver.scenario_plan import (
        GeneratedAction,
        GeneratedRequest,
        GeneratedScenario,
        RiskCategory,
        ScenarioProvenance,
        compile_to_scenario,
    )
    from neyma_product_driver.scenario_validation import (
        ApprovedCommands,
        ValidationContext,
        validate_scenario,
    )
    from neyma_product_driver.scenarios import Scenario, ScenarioExecutor

    app_url = f"http://127.0.0.1:{PORT_APPROVED}"
    base = Scenario(name="base", app_url=app_url)
    scen = GeneratedScenario(
        id="probe-redirect",
        title="probe",
        purpose="a relative path on the approved loopback base that 302s elsewhere",
        risk_category=RiskCategory.HAPPY_PATH,
        requirement_reference="AC-PROBE-001",
        product_principle_reference="product rubric",
        expected_observations=["role=offtarget"],
        actions=[
            GeneratedAction(
                kind="request",
                request=GeneratedRequest(path="/redirect", method="GET", expect_status=200),
            )
        ],
        provenance=ScenarioProvenance(
            task_hash="p", stage="initial", model="probe", wave=1, generating_risk="p"
        ),
    )
    ctx = ValidationContext(
        approved_commands=ApprovedCommands.from_sources(scenarios=[base]),
        grounding_tokens={"probe"},
        principle_tokens={"product rubric"},
        app_url=app_url,
    )
    reasons = validate_scenario(scen, ctx)
    if reasons:
        return {"admitted": False, "reasons": reasons}
    compiled = compile_to_scenario(scen, base=base, approved_commands=set())
    before = len(HITS["offtarget"])
    tmp = Path(tempfile.mkdtemp(prefix="dsafety-r-"))
    ex = ScenarioExecutor(ROOT, ScenarioRunConfig(), tmp)
    res = await ex.execute(compiled)
    return {
        "admitted": True,
        "requested_path": "/redirect",
        "recorded_observation_url": [o.url for o in res.http],
        "response_body": [o.body_text for o in res.http],
        "offtarget_hits_during_scenario": HITS["offtarget"][before:],
        "expect_visible_passed": [
            a.passed for a in res.assertions if a.kind == "expect_visible"
        ],
    }


async def main() -> dict:
    a = _serve(PORT_APPROVED, "approved")
    b = _serve(PORT_OFFTARGET, "offtarget")
    out: dict = {}
    try:
        # R1 — does the runner follow a 302 off the validated target?
        obs = await http_request(f"http://127.0.0.1:{PORT_APPROVED}/redirect", timeout_s=5)
        out["R1_redirect"] = {
            "requested_url": f"http://127.0.0.1:{PORT_APPROVED}/redirect",
            "recorded_observation_url": obs.url,
            "status": obs.status,
            "body": obs.body_text,
            "offtarget_listener_hits": list(HITS["offtarget"]),
            "followed_offtarget": bool(HITS["offtarget"]),
        }

        # R2 — CRLF in a header value (validation does not control-char check headers).
        try:
            obs2 = await http_request(
                f"http://127.0.0.1:{PORT_APPROVED}/hdr",
                headers={"X-Probe": "a\r\nX-Injected: 1"},
                timeout_s=5,
            )
            out["R2_header_crlf"] = {"status": obs2.status, "error": obs2.error}
        except Exception as exc:  # noqa: BLE001
            out["R2_header_crlf"] = {"raised": f"{type(exc).__name__}: {exc}"}

        # R3 — arbitrary Authorization header on a local request.
        obs3 = await http_request(
            f"http://127.0.0.1:{PORT_APPROVED}/auth",
            headers={"Authorization": "Bearer probe-not-a-real-token"},
            timeout_s=5,
        )
        out["R3_arbitrary_header"] = {"status": obs3.status, "accepted": obs3.status == 200}

        # R4 — the same escape through a fully validated GeneratedScenario:
        # a *relative* path on the approved loopback base whose response is a
        # 302 to a host the validator never saw.
        out["R4_scenario_redirect"] = await _scenario_redirect()
    finally:
        a.shutdown()
        b.shutdown()
    return out


if __name__ == "__main__":
    data = asyncio.run(main())
    Path(__file__).with_name("probe_runtime.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )
    print(json.dumps(data, indent=2))
