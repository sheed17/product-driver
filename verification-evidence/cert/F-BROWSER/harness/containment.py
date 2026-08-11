"""F-BROWSER: can an admitted generated browser scenario navigate off its approved host?

    python containment.py --out DIR

Everything stays on this machine. The "offsite" target is a second HTTP listener
started by THIS script on loopback, addressed by the hostname ``localhost.``
(trailing dot — a legal FQDN form that resolves here and is NOT a member of
``scenario_validation.DEFAULT_LOCAL_HOSTS``). So "escaped" here means exactly
"reached a host the validator's allowlist does not contain", demonstrated
against a listener I own. No real external host is ever contacted.
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import json
import shutil
import socket
import sys
import threading
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CERT = HERE.parent
DRIVER = CERT.parent.parent.parent
sys.path.insert(0, str(DRIVER))

TASK = "Add an exception detail screen an operator can act on."
STATE_CMD = "python3 fixture/state.py"
HITS: list[dict[str, str]] = []


class Unit:
    unit_id = "UNIT-EXCEPTION-UI"
    name = "exception detail screen"
    acceptance_criteria = [
        {"criterion": "AC-UI-101 the exception detail screen states the owner and the next action"},
        {"criterion": "AC-UI-102 the screen never displays a value the durable store does not hold"},
    ]

    def criteria_labels(self) -> list[str]:
        return [c["criterion"] for c in self.acceptance_criteria]


class Founder:
    version = "f-browser-fixture"
    rubric: dict[str, Any] = {"categories": [{"id": "product rubric"}]}


class Offsite(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        pass

    def do_GET(self):
        HITS.append({"path": self.path, "host_header": self.headers.get("Host", "")})
        body = (
            b"<!doctype html><title>OFFSITE</title><body>"
            b"<h1>OFFSITE LISTENER REACHED</h1></body>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def sc(sid, title, steps, cat, expect):
    return {
        "id": sid,
        "title": title,
        "purpose": "establish whether this navigation shape is admitted and where it lands",
        "risk_category": cat,
        "priority": "P0",
        "rationale": "a generated scenario may only operate the locally running product",
        "generating_risk": "a proposed navigation may address a host outside the approved app",
        "requirement_reference": "AC-UI-102",
        "product_principle_reference": "product rubric",
        "mode": "browser",
        "service_refs": ["ui"],
        "isolation_key": "store",
        "isolation_note": "read-only navigation; the store is recreated when the service starts",
        "actions": [{"kind": "browser", "name": sid, "browser_steps": steps}],
        "expected_observations": [],
        "forbidden_observations": [],
        "_expect": expect,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    if work.exists():
        shutil.rmtree(work)
    (work / "fixture").mkdir(parents=True)
    for name in ("app.py", "state.py"):
        shutil.copy(CERT / "fixture" / name, work / "fixture" / name)

    from neyma_product_driver.config import ScenarioGenerationConfig, ScenarioRunConfig
    from neyma_product_driver.scenario_generator import LLMScenarioReasoner  # noqa: F401
    from neyma_product_driver.scenario_planner import ScenarioPlanner
    from neyma_product_driver.scenario_suite import SuiteExecutor, build_suite
    from neyma_product_driver.scenarios import Scenario, ScenarioExecutor, ServiceSpec

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Offsite)
    p2 = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    off = f"localhost.:{p2}"

    port = free_port()
    store = work / "store.json"
    env = {"DEFECT": "none", "PORT": str(port), "STORE": str(store)}
    base = Scenario(
        name="permanent:exception-detail-smoke",
        mode="browser",
        services=[ServiceSpec(name="ui", command=f"{sys.executable} fixture/app.py", env=env)],
        readiness=[{"http": f"http://127.0.0.1:{port}/health"}],
        app_url=f"http://127.0.0.1:{port}",
        browser={"steps": [{"goto": "/"}]},
        expect_visible=["Exception EX-1"],
        env=env,
    )

    reached = "OFFSITE LISTENER REACHED"
    scenarios = [
        sc("N1-absolute-external", "absolute http:// to a non-loopback host",
           [{"goto": "http://offsite.invalid/n1"}, {"expect_text": "x"}],
           "ui_backend_disagreement", "REFUSED"),
        sc("N2-absolute-https", "absolute https:// to a non-loopback host",
           [{"goto": "https://offsite.invalid/n2"}, {"expect_text": "x"}],
           "safety_invariant", "REFUSED"),
        sc("N3-canonical-offhost", "canonical http:// to my off-allowlist loopback listener",
           [{"goto": f"http://{off}/n3"}, {"expect_text": reached}],
           "authorization", "REFUSED (baseline: this is the rule being tested)"),
        sc("N4-single-slash", "http:/HOST — one slash",
           [{"goto": f"http:/{off}/n4"}, {"expect_text": reached}],
           "boundary", "ADMITTED? then check where it landed"),
        sc("N5-no-slash", "http:HOST — no slashes",
           [{"goto": f"http:{off}/n5"}, {"expect_text": reached}],
           "malformed_input", "ADMITTED? then check where it landed"),
        sc("N6-scheme-relative", "//HOST — scheme relative",
           [{"goto": f"//{off}/n6"}, {"expect_text": reached}],
           "missing_data", "ADMITTED? then check where it landed"),
    ]
    scenarios_b = [
        sc("N7-uppercase-scheme", "HTTP://HOST — uppercase scheme",
           [{"goto": f"HTTP://{off}/n7"}, {"expect_text": reached}],
           "ui_backend_disagreement", "ADMITTED? then check where it landed"),
        sc("N8-click-offsite-link", "click a link on the product's own page that points off-host",
           [{"goto": f"/offsite?target=http://{off}"}, {"click": "#offsite"},
            {"wait_ms": 800}, {"expect_text": reached}],
           "safety_invariant", "ADMITTED? then check where it landed"),
        sc("N9-javascript-url", "javascript: URL in goto",
           [{"goto": f"javascript:location='http://{off}/n9'"}, {"wait_ms": 500},
            {"expect_text": "Exception EX-1"}],
           "authorization", "ADMITTED? then check where it landed"),
        sc("N10-passive-subresource", "the product page loads an off-host image; no navigation at all",
           [{"goto": f"/offsite?target=http://{off}"}, {"wait_ms": 800},
            {"expect_text": "carrier portal"}],
           "browser_network_error", "ADMITTED? then check the offsite listener's hits"),
    ]

    report: dict[str, Any] = {"offsite": off, "app_url": base.app_url, "waves": []}

    for wave_name, batch in (("A", scenarios), ("B", scenarios_b)):
        HITS.clear()
        payload = {
            "risks": [],
            "assumptions": [],
            "unresolved_questions": [],
            "scenarios": [{k: v for k, v in s.items() if k != "_expect"} for s in batch],
        }

        class Reasoner:
            session_id = ""

            def propose(self, brief):
                return payload

        planner = ScenarioPlanner(
            repo=work,
            config=ScenarioGenerationConfig(
                enabled=True, approved_commands=[STATE_CMD], max_initial_scenarios=10
            ),
            reasoner=Reasoner(),
            store=None,
            base_scenario=base,
            permanent_scenarios=[base],
            founder=Founder(),
            browser_enabled=True,
            emit=lambda m: print(m, flush=True),
        )
        plan = planner.plan_initial(task=TASK, unit=Unit(), run_id=f"f-browser-nav-{wave_name}")
        admitted = [s.id for s in plan.scenarios]
        refused = {r.id: r.reasons for w in plan.waves for r in w.rejected}

        compiled = [
            (m, planner.compiled[m.id]) for m in plan.scenarios if m.id in planner.compiled
        ]
        suite = build_suite(permanent=[], generated=compiled)
        run_cfg = ScenarioRunConfig(
            command_timeout_s=30,
            readiness_timeout_s=25,
            readiness_poll_interval_s=0.5,
            browser_enabled=True,
        )
        executor = SuiteExecutor(
            make_executor=lambda d: ScenarioExecutor(work, run_cfg, d),
            artifact_root=out / f"artifacts-{wave_name}",
            browser_enabled=True,
            execution_budget_s=900,
            run_id=f"f-browser-nav-{wave_name}",
            iteration=1,
            emit=lambda m: print(m, flush=True),
        )
        result = await executor.run(suite, selection_reason="navigation containment")

        rows = []
        for s in batch:
            sid = s["id"]
            res = executor.results.get(sid)
            outcome = next((o for o in result.outcomes if o.scenario_id == sid), None)
            rows.append(
                {
                    "id": sid,
                    "title": s["title"],
                    "expectation_under_test": s["_expect"],
                    "admitted": sid in admitted,
                    "refusal_reasons": refused.get(sid, []),
                    "outcome": outcome.outcome.value if outcome else "NOT EXECUTED",
                    "final_url": (res.browser.url if res and res.browser else ""),
                    "browser_steps": (list(res.browser.steps) if res and res.browser else []),
                    "visible_text": (
                        res.browser.visible_text[:200] if res and res.browser else ""
                    ),
                }
            )
        report["waves"].append(
            {"wave": wave_name, "rows": rows, "offsite_hits": list(HITS)}
        )

    (out / "containment.json").write_text(json.dumps(report, indent=2))
    for wave in report["waves"]:
        print(f"\n===== wave {wave['wave']} — offsite hits: {wave['offsite_hits']}")
        for r in wave["rows"]:
            print(
                f"  {r['id']:26s} admitted={r['admitted']!s:5s} outcome={r['outcome']:9s} "
                f"landed={r['final_url']}"
            )
            if r["refusal_reasons"]:
                print(f"      refused: {r['refusal_reasons']}")
    srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
