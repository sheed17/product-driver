"""D-SAFETY probes: HTTP target resolution and browser navigation escape.

HARMLESS AND LOCAL. Non-loopback probes are only ever checked for *admission* by
the validator — nothing is dialled. The one execution probe drives Chromium at a
second loopback listener this script starts itself, to show that a `goto` string
the validator never inspected overrides the approved `app_url`.
"""

from __future__ import annotations

import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from neyma_product_driver.scenario_plan import (  # noqa: E402
    GeneratedAction,
    GeneratedBrowserStep,
    GeneratedRequest,
    GeneratedScenario,
    RiskCategory,
    ScenarioProvenance,
    compile_to_scenario,
)
from neyma_product_driver.scenario_validation import (  # noqa: E402
    DEFAULT_LOCAL_HOSTS,
    ApprovedCommands,
    ValidationContext,
    resolve_http_target,
    safety_reasons,
)
from neyma_product_driver.scenarios import Scenario, _join_url  # noqa: E402

APP_URL = "http://127.0.0.1:8001"
HOSTS = DEFAULT_LOCAL_HOSTS

# Non-routable / reserved documentation addresses only. Never dialled.
EVIL_HOST = "198.51.100.9"          # TEST-NET-3, RFC 5737
EVIL_NAME = "offtarget.invalid"     # RFC 6761 reserved TLD

URL_PROBES: list[tuple[str, str, str, str]] = [
    # (id, field, value, note)
    ("H01", "url", f"http://{EVIL_NAME}/x", "absolute off-target url"),
    ("H02", "path", f"http://{EVIL_NAME}/x", "absolute url in path"),
    ("H03", "path", f"//{EVIL_NAME}/x", "scheme-relative path"),
    ("H04", "path", f"\\\\{EVIL_NAME}\\x", "backslash host"),
    ("H05", "path", f"/\\{EVIL_NAME}/x", "slash-backslash host"),
    ("H06", "path", f"\\/{EVIL_NAME}/x", "backslash-slash host"),
    ("H07", "url", f"http://localhost@{EVIL_NAME}/x", "userinfo confusion"),
    ("H08", "url", f"http://{EVIL_NAME}@127.0.0.1:8001/x", "userinfo, real host loopback"),
    ("H09", "url", "http://2130706433/x", "decimal IP for 127.0.0.1"),
    ("H10", "url", "http://0177.0.0.1/x", "octal IP"),
    ("H11", "url", "http://0x7f000001/x", "hex IP"),
    ("H12", "url", "http://127.1/x", "short-form loopback"),
    ("H13", "url", "http://[::1]:8001/x", "IPv6 loopback"),
    ("H14", "url", "http://[::ffff:127.0.0.1]/x", "IPv4-mapped IPv6"),
    ("H15", "url", "http://0.0.0.0:8001/x", "0.0.0.0"),
    ("H16", "url", "http://localhost./x", "trailing dot"),
    ("H17", "url", "http://127.0.0.1.nip.io/x", "DNS-rebinding-shaped name"),
    ("H18", "url", "file:///etc/hosts", "file scheme"),
    ("H19", "url", f"gopher://{EVIL_NAME}/x", "gopher scheme"),
    ("H20", "url", "data:text/html,hi", "data scheme"),
    ("H21", "path", "%2f%2fevil", "percent-encoded scheme-relative"),
    ("H22", "url", f"htTp://{EVIL_NAME}/x", "mixed-case scheme"),
    ("H23", "url", f" http://{EVIL_NAME}/x", "leading space"),
    ("H24", "url", f"http:/{EVIL_NAME}/x", "single-slash absolute url"),
    ("H25", "url", f"http:{EVIL_NAME}/x", "no-slash absolute url"),
    ("H26", "url", "javascript:alert(1)", "javascript scheme"),
    ("H27", "path", "../../../etc/passwd", "path traversal"),
    ("H28", "url", "http://127.0.0.1:8001/x\r\nX-Injected: 1", "CRLF in url"),
    ("H29", "path", "x\r\nX-Injected: 1", "CRLF in path"),
    ("H30", "url", "http://LOCALHOST:8001/x", "uppercase host"),
]

GOTO_PROBES: list[tuple[str, str, str]] = [
    ("B01", f"http://{EVIL_NAME}/x", "plain absolute"),
    ("B02", f"https://{EVIL_NAME}/x", "plain absolute https"),
    ("B03", f"http:/{EVIL_NAME}/x", "single-slash absolute"),
    ("B04", f"http:{EVIL_NAME}/x", "no-slash absolute"),
    ("B05", f"http:\\\\{EVIL_NAME}\\x", "backslash authority"),
    ("B06", f"httpx://{EVIL_NAME}/x", "unknown scheme starting 'http'"),
    ("B07", f"//{EVIL_NAME}/x", "scheme-relative"),
    ("B08", "file:///etc/hosts", "file scheme"),
    ("B09", "javascript:alert(1)", "javascript scheme"),
    ("B10", f"HTTP://{EVIL_NAME}/x", "uppercase scheme"),
    ("B11", f"http:/{EVIL_HOST}:1/x", "single-slash reserved IP"),
]


def _base() -> Scenario:
    return Scenario(name="base", app_url=APP_URL, commands=[{"run": "true"}])


def _ctx() -> ValidationContext:
    return ValidationContext(
        approved_commands=ApprovedCommands.from_sources(scenarios=[_base()]),
        app_url=APP_URL,
        local_hosts=HOSTS,
        declared_services=set(),
        browser_enabled=True,
    )


def _scenario(actions: list[GeneratedAction]) -> GeneratedScenario:
    return GeneratedScenario(
        id="probe",
        title="probe",
        risk_category=RiskCategory.HAPPY_PATH,
        requirement_reference="AC-PROBE-001",
        product_principle_reference="product rubric",
        actions=actions,
        expected_observations=["x"],
        provenance=ScenarioProvenance(
            task_hash="p", stage="initial", model="probe", wave=1, generating_risk="p"
        ),
    )


def url_rows() -> list[dict]:
    ctx = _ctx()
    rows = []
    for pid, field, value, note in URL_PROBES:
        req = GeneratedRequest(**{field: value, "method": "GET", "expect_status": 200})
        scen = _scenario([GeneratedAction(kind="request", request=req)])
        reasons = safety_reasons(scen, ctx)
        target, problem = resolve_http_target(
            app_url=APP_URL,
            url=value if field == "url" else "",
            path=value if field == "path" else "",
            local_hosts=HOSTS,
        )
        # What the executor would actually dial (scenarios._do_request).
        would_dial = (req.url or None) or _join_url(APP_URL, req.path or "/")
        rows.append({
            "id": pid, "field": field, "value": value, "note": note,
            "admitted": not reasons,
            "reasons": reasons,
            "resolve_http_target": {"target": target, "problem": problem},
            "executor_would_dial": would_dial,
            "resolver_and_executor_agree": (not problem) and (target == would_dial),
        })
    return rows


def goto_rows() -> list[dict]:
    ctx = _ctx()
    rows = []
    for pid, goto, note in GOTO_PROBES:
        scen = _scenario([
            GeneratedAction(
                kind="browser",
                browser_steps=[GeneratedBrowserStep(goto=goto, expect_text="x")],
            )
        ])
        reasons = safety_reasons(scen, ctx)
        # The exact expression scenarios.ScenarioExecutor._run_step uses.
        would_navigate = goto if goto.startswith("http") else _join_url(APP_URL, goto)
        rows.append({
            "id": pid, "goto": goto, "note": note,
            "admitted": not reasons,
            "reasons": reasons,
            "validator_inspected": goto.startswith(("http://", "https://")),
            "executor_treats_as_absolute": goto.startswith("http"),
            "executor_would_navigate_to": would_navigate,
        })
    return rows


# ---------------------------------------------------------------------------
# Execution probe: does Chromium resolve `http:/host` to that host?
# ---------------------------------------------------------------------------

HITS_A: list[str] = []
HITS_B: list[str] = []


def _server(hits: list[str], port: int) -> socketserver.TCPServer:
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hits.append(self.path)
            body = b"<html><body>served by port %d</body></html>" % port
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a: object) -> None:
            return

    srv = socketserver.TCPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def browser_execution_probe() -> dict:
    """app_url is port A; the generated `goto` is `http:/127.0.0.1:B/escaped`.

    Both are loopback, so nothing leaves this machine — but B is a host:port the
    validator never inspected, and reaching it proves the approved base URL was
    overridden by an unvalidated string.
    """
    import asyncio

    from neyma_product_driver.config import ScenarioRunConfig
    from neyma_product_driver.scenarios import ScenarioExecutor

    port_a, port_b = 45211, 45212
    srv_a = _server(HITS_A, port_a)
    srv_b = _server(HITS_B, port_b)
    app_url = f"http://127.0.0.1:{port_a}"
    goto = f"http:/127.0.0.1:{port_b}/escaped-the-approved-target"

    base = Scenario(name="base", app_url=app_url, commands=[{"run": "true"}])
    ctx = ValidationContext(
        approved_commands=ApprovedCommands.from_sources(scenarios=[base]),
        app_url=app_url,
        local_hosts=HOSTS,
        browser_enabled=True,
    )
    scen = _scenario([
        GeneratedAction(
            kind="browser",
            browser_steps=[GeneratedBrowserStep(goto=goto, expect_text="served by port")],
        )
    ])
    reasons = safety_reasons(scen, ctx)
    out: dict = {"goto": goto, "app_url": app_url, "admitted": not reasons, "reasons": reasons}
    if reasons:
        srv_a.shutdown(); srv_b.shutdown()
        return out

    compiled = compile_to_scenario(scen, base=base, approved_commands=set())
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="dsafety-b-"))
    try:
        ex = ScenarioExecutor(
            ROOT,
            ScenarioRunConfig(headless=True, capture_trace=False, browser_enabled=True),
            tmp,
        )
        res = asyncio.run(ex.execute(compiled))
        out["scenario_error"] = res.error
        out["assertions"] = [
            {"target": a.target, "passed": a.passed, "detail": a.detail} for a in res.assertions
        ]
        out["browser_steps"] = list(res.browser.steps) if res.browser else []
        out["final_url"] = res.browser.url if res.browser else ""
    finally:
        srv_a.shutdown()
        srv_b.shutdown()
    out["hits_on_approved_app_url_port"] = list(HITS_A)
    out["hits_on_unvalidated_goto_port"] = list(HITS_B)
    return out


if __name__ == "__main__":
    data = {
        "http_targets": url_rows(),
        "browser_goto": goto_rows(),
        "browser_execution": browser_execution_probe(),
    }
    Path(__file__).with_name("probe_http_browser.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )
    print("=== HTTP TARGETS ===")
    for r in data["http_targets"]:
        print(f"{r['id']:4} {'ADMITTED' if r['admitted'] else 'refused '} "
              f"{r['field']:5} {r['note']:38} dial={r['executor_would_dial']!r}")
    print("\n=== BROWSER goto ===")
    for r in data["browser_goto"]:
        print(f"{r['id']:4} {'ADMITTED' if r['admitted'] else 'refused '} "
              f"inspected={str(r['validator_inspected']):5} "
              f"abs={str(r['executor_treats_as_absolute']):5} "
              f"{r['note']:32} -> {r['executor_would_navigate_to']!r}")
    print("\n=== BROWSER EXECUTION ===")
    print(json.dumps(data["browser_execution"], indent=2))
