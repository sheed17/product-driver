"""F-BROWSER: drive the generated-browser path end to end against my own fixture.

    python chain.py --out DIR [--defect flags] [--payload FILE | --live]
                    [--app-path /] [--no-browser] [--browser-unavailable]
                    [--no-playwright]

Nothing between the links is stubbed:

    generation (scripted payload OR live LLMScenarioReasoner, browser advertised)
      → deterministic validation      (ScenarioPlanner → validate_plan)
      → compile                       (compile_to_scenario)
      → execution                     (ScenarioExecutor, real chromium)
      → per-case evidence             (write_case_evidence / verify_case_evidence)
      → aggregation                   (SuiteResult)
      → acceptance gate               (evaluate_gate)

Loopback only. The fixture is copied into a disposable work dir and started by
the driver as a declared service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CERT = HERE.parent
DRIVER = CERT.parent.parent.parent

TASK = (
    "Add an exception detail screen an operator can act on: it must state the "
    "status, who owns it, and the next action — and it must never show a value "
    "the durable store does not hold."
)
STATE_CMD = "python3 fixture/state.py"


class Unit:
    unit_id = "UNIT-EXCEPTION-UI"
    name = "exception detail screen"
    acceptance_criteria = [
        {"criterion": "AC-UI-101 the exception detail screen states the owner and the next action"},
        {"criterion": "AC-UI-102 the screen never displays an owner or next action the durable store does not hold"},
    ]

    def criteria_labels(self) -> list[str]:
        return [c["criterion"] for c in self.acceptance_criteria]


class Founder:
    version = "f-browser-fixture"
    rubric: dict[str, Any] = {"categories": [{"id": "product rubric"}]}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def base_scenario(port: int, store: Path, defect: str, app_path: str):
    from neyma_product_driver.scenarios import Scenario, ServiceSpec

    env = {"DEFECT": defect, "PORT": str(port), "STORE": str(store)}
    return Scenario(
        name="permanent:exception-detail-smoke",
        mode="browser",
        description="Permanent regression scenario for the exception detail screen.",
        services=[ServiceSpec(name="ui", command=f"{sys.executable} fixture/app.py", env=env)],
        readiness=[{"http": f"http://127.0.0.1:{port}/health"}],
        app_url=f"http://127.0.0.1:{port}{app_path}",
        browser={"steps": [{"goto": "/"}], "initial_screenshot": True},
        expect_visible=["Exception EX-1"],
        forbidden=["[object Object]"],
        env=env,
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--defect", default="none")
    ap.add_argument("--payload", default="")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--app-path", default="")
    ap.add_argument("--no-browser", action="store_true", help="browser_enabled False everywhere")
    ap.add_argument("--suite-no-browser", action="store_true", help="planner True, suite False")
    ap.add_argument("--label", default="")
    ap.add_argument("--no-permanent", action="store_true", help="generated scenarios only")
    args = ap.parse_args()

    sys.path.insert(0, str(DRIVER))
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    if work.exists():
        shutil.rmtree(work)
    (work / "fixture").mkdir(parents=True)
    for name in ("app.py", "state.py"):
        shutil.copy(CERT / "fixture" / name, work / "fixture" / name)

    from neyma_product_driver.config import ScenarioGenerationConfig, ScenarioRunConfig
    from neyma_product_driver.scenario_gate import evaluate_gate
    from neyma_product_driver.scenario_generator import LLMScenarioReasoner
    from neyma_product_driver.scenario_planner import ScenarioPlanner
    from neyma_product_driver.scenario_suite import SuiteExecutor, build_suite
    from neyma_product_driver.scenarios import ScenarioExecutor

    port = free_port()
    store = work / "store.json"
    base = base_scenario(port, store, args.defect, args.app_path)
    planner_browser = not args.no_browser
    suite_browser = planner_browser and not args.suite_no_browser

    report: dict[str, Any] = {
        "label": args.label or out.name,
        "defect": args.defect,
        "live_model": bool(args.live),
        "planner_browser_enabled": planner_browser,
        "suite_browser_enabled": suite_browser,
        "app_url": base.app_url,
        "port": port,
    }

    scripted: dict[str, Any] | None = None
    if args.payload:
        scripted = json.loads(Path(args.payload).read_text())

    class Reasoner:
        def __init__(self) -> None:
            self.session_id = ""
            self._llm = LLMScenarioReasoner(work, model="opus", timeout_s=900) if args.live else None

        def propose(self, brief):
            text = brief.render()
            (out / "brief-wave1.txt").write_text(text)
            report["brief_browser_line"] = next(
                (ln for ln in text.splitlines() if ln.startswith("BROWSER available")), ""
            )
            if self._llm is None:
                return scripted
            payload = self._llm.propose(brief)
            self.session_id = self._llm.session_id or ""
            (out / "raw-payload.json").write_text(
                json.dumps(payload, indent=2) if payload is not None else "null"
            )
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
        browser_enabled=planner_browser,
        emit=lambda m: print(m, flush=True),
    )

    print("=== GENERATE ===", flush=True)
    plan = planner.plan_initial(task=TASK, unit=Unit(), run_id=f"f-browser-{out.name}")
    (out / "plan.json").write_text(json.dumps(plan.model_dump(mode="json"), indent=2))
    (out / "plan.txt").write_text(plan.render())

    browser_models = [s for s in plan.scenarios if s.mode == "browser"]
    report["generation"] = {
        "proposed": sum(w.proposed for w in plan.waves),
        "accepted": len(plan.scenarios),
        "accepted_ids": [s.id for s in plan.scenarios],
        "accepted_browser": [s.id for s in browser_models],
        "refused": [{"id": r.id, "reasons": r.reasons} for w in plan.waves for r in w.rejected],
        "wave_errors": [w.error for w in plan.waves if getattr(w, "error", "")],
    }

    compiled = [(m, planner.compiled[m.id]) for m in plan.scenarios if m.id in planner.compiled]
    report["compile"] = {
        "compiled": [m.id for m, _ in compiled],
        "browser_mode_preserved": [m.id for m, c in compiled if getattr(c, "mode", "") == "browser"],
        "browser_steps_compiled": {
            m.id: [
                s.browser.model_dump(mode="json")
                for s in c.steps
                if s.kind == "browser" and s.browser is not None
            ]
            for m, c in compiled
        },
    }

    print("=== EXECUTE ===", flush=True)
    suite = build_suite(
        permanent=[] if args.no_permanent else [(base.name, base)], generated=compiled
    )
    run_cfg = ScenarioRunConfig(
        command_timeout_s=30,
        readiness_timeout_s=25,
        readiness_poll_interval_s=0.5,
        browser_enabled=suite_browser,
    )
    run_id = f"f-browser-{out.name}"
    executor = SuiteExecutor(
        make_executor=lambda d: ScenarioExecutor(work, run_cfg, d),
        artifact_root=out / "artifacts",
        browser_enabled=suite_browser,
        execution_budget_s=900,
        run_id=run_id,
        iteration=1,
        emit=lambda m: print(m, flush=True),
    )
    result = await executor.run(suite, selection_reason="f-browser certification")
    (out / "suite-result.json").write_text(json.dumps(result.model_dump(mode="json"), indent=2))
    print(result.summary_block(), flush=True)

    evidence: list[dict[str, Any]] = []
    for outcome in result.outcomes:
        directory = Path(outcome.evidence_path) if outcome.evidence_path else None
        files = (
            sorted(
                {
                    "path": str(p.relative_to(directory)),
                    "bytes": p.stat().st_size,
                }.__repr__()
                for p in directory.rglob("*")
                if p.is_file()
            )
            if directory and directory.exists()
            else []
        )
        shots = (
            sorted(
                (str(p.relative_to(directory)), p.stat().st_size)
                for p in directory.rglob("*.png")
            )
            if directory and directory.exists()
            else []
        )
        traces = (
            sorted(
                (str(p.relative_to(directory)), p.stat().st_size)
                for p in directory.rglob("*.zip")
            )
            if directory and directory.exists()
            else []
        )
        res = executor.results.get(outcome.scenario_id)
        evidence.append(
            {
                "scenario_id": outcome.scenario_id,
                "outcome": outcome.outcome.value,
                "evidence_verified": outcome.evidence_verified,
                "evidence_problem": outcome.evidence_problem,
                "files": files,
                "screenshots": shots,
                "traces": traces,
                "assertions": [
                    {"kind": a.kind, "target": a.target, "passed": a.passed, "detail": a.detail}
                    for a in (res.assertions if res else [])
                ],
                "error": (res.error if res else None),
                "browser_steps": (list(res.browser.steps) if res and res.browser else []),
                "browser_visible_text": (
                    (res.browser.visible_text[:600] if res and res.browser else "")
                ),
                "console_errors": (list(res.browser.console_errors) if res and res.browser else []),
                "network_failures": (
                    list(res.browser.network_failures) if res and res.browser else []
                ),
                "skip_reason": outcome.skip_reason,
            }
        )

    verdict = evaluate_gate(result, risks=plan.risks)
    report["execution"] = {"evidence": evidence}
    report["aggregation"] = {
        "total": result.total,
        "passed": result.passed,
        "failed": result.failed,
        "blocked": result.blocked,
        "skipped": result.skipped,
    }
    report["gate"] = {
        "status": verdict.status.value,
        "blocks_acceptance": verdict.blocks_acceptance,
        "required_total": verdict.required_total,
        "required_passed": verdict.required_passed,
        "unverified": [c.brief() for c in verdict.unverified],
        "uncovered_risks": [r.brief() for r in verdict.uncovered_risks],
    }
    (out / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "execution"}, indent=2))
    for e in evidence:
        print(
            f"  {e['scenario_id']}: {e['outcome']} "
            f"shots={len(e['screenshots'])} traces={len(e['traces'])} "
            f"verified={e['evidence_verified']}"
        )
        for a in e["assertions"]:
            if not a["passed"]:
                print(f"      FAILED {a['kind']}: {a['target']} — {a['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
