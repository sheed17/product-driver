"""Prove the generated BROWSER-scenario path end to end in a disposable fixture.

  python run_browser_e2e.py --driver <path> --work <path> --out <path> [--real]

The chain under test, with nothing stubbed between the links:

    generation (real LLMScenarioReasoner with browser available)
      → deterministic validation      (validate_plan / safety_reasons)
      → compile                       (compile_to_scenario)
      → Playwright execution          (ScenarioExecutor, real chromium)
      → per-case evidence capture     (write_case_evidence / verify_case_evidence)
      → result aggregation            (SuiteResult)
      → authoritative acceptance gate (evaluate_gate)

Loopback only. No credentials. No external hosts. No Neyma environment. The
fixture app is started by the driver as an ordinary service and torn down with
the run.

Without `--real` a scripted proposal stands in for the model, so the executable
half of the chain can be exercised without consuming usage. The scripted
proposal is written in the same JSON shape the model returns and goes through
exactly the same validation and compilation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
from pathlib import Path
from typing import Any

TASK = (
    "Add an exception detail screen an operator can act on: it must state the "
    "status, why the exception was raised, who owns it, and the next action."
)
STATE_CMD = "python3 fixture/state.py"


class Unit:
    unit_id = "UNIT-EXCEPTION-UI"
    name = "exception detail screen"
    acceptance_criteria = [
        {"criterion": "AC-UI-001 the exception detail screen names the next action and who owns it"},
        {"criterion": "AC-UI-002 the screen never shows a status the durable store does not hold"},
    ]

    def criteria_labels(self) -> list[str]:
        return [c["criterion"] for c in self.acceptance_criteria]


class Founder:
    version = "g-fixture"
    rubric: dict[str, Any] = {"categories": [{"id": "product rubric"}]}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


#: A browser proposal in the exact JSON shape the model returns. Used only when
#: `--real` is absent; it is validated and compiled by the same code either way.
SCRIPTED: dict[str, Any] = {
    "risks": [
        {
            "id": "R1",
            "description": "the screen may not tell an operator who owns the next action",
            "risk_category": "ui_backend_disagreement",
            "severity": "P0",
            "basis": "AC-UI-001",
        }
    ],
    "scenarios": [
        {
            "id": "gen-browser-next-action",
            "title": "the exception screen names the next action and its owner",
            "purpose": "an operator must be able to act without asking anyone",
            "risk_category": "ui_backend_disagreement",
            "priority": "P0",
            "rationale": "the acceptance criterion is about what a person can see",
            "generating_risk": "the screen may omit the owner or the next action",
            "requirement_reference": "AC-UI-001",
            "product_principle_reference": "product rubric",
            "mode": "browser",
            "service_refs": ["ui"],
            "isolation_key": "store",
            "actions": [
                {
                    "kind": "browser",
                    "browser_steps": [
                        {"goto": "/", "screenshot": "detail"},
                        {"expect_text": "next action"},
                        {"expect_text": "owner"},
                    ],
                }
            ],
            "expected_observations": ["status: open"],
            "forbidden_observations": ["Traceback", "[object Object]"],
        },
        {
            "id": "gen-browser-screen-matches-store",
            "title": "the screen never shows a status the durable store does not hold",
            "purpose": "a rendered status is not evidence; the store must agree",
            "risk_category": "stale_state",
            "priority": "P0",
            "rationale": "the screen is a read model and read models drift",
            "generating_risk": "the page may keep serving a status the store has moved past",
            "requirement_reference": "AC-UI-002",
            "product_principle_reference": "product rubric",
            "mode": "browser",
            "service_refs": ["ui"],
            "isolation_key": "store",
            "isolation_note": (
                "runs last against its own exception record; the store is recreated "
                "when the service starts, so nothing after it observes the resolution"
            ),
            "actions": [
                {
                    "kind": "browser",
                    "browser_steps": [
                        {"goto": "/"},
                        {"click": "#resolve"},
                        {"wait_ms": 300},
                        {"goto": "/"},
                        {"screenshot": "after-resolve"},
                        {"expect_text": "status: resolved"},
                    ],
                }
            ],
            "persisted_state_checks": [
                {
                    "name": "the store agrees the exception is resolved",
                    "command": f"{STATE_CMD} EX-1",
                    "contains": ["status=resolved"],
                }
            ],
        },
    ],
    "assumptions": ["the exception detail screen is served at /"],
    "unresolved_questions": [],
}


def base_scenario(port: int, store: Path, defect: str):
    from neyma_product_driver.scenarios import Scenario, ServiceSpec

    env = {"DEFECT": defect, "PORT": str(port), "STORE": str(store)}
    return Scenario(
        name="permanent:exception-detail-smoke",
        mode="browser",
        description="Permanent regression scenario for the exception detail screen.",
        services=[
            ServiceSpec(name="ui", command=f"{sys.executable} fixture/app.py", env=env)
        ],
        readiness=[{"http": f"http://127.0.0.1:{port}/health"}],
        app_url=f"http://127.0.0.1:{port}",
        browser={"steps": [{"goto": "/"}], "initial_screenshot": True},
        expect_visible=["Exception EX-1"],
        forbidden=["Traceback", "[object Object]"],
        env=env,
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--defect", default="none")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(args.driver).resolve()))
    work = Path(args.work).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    from neyma_product_driver.config import ScenarioGenerationConfig, ScenarioRunConfig
    from neyma_product_driver.scenario_gate import evaluate_gate
    from neyma_product_driver.scenario_generator import LLMScenarioReasoner
    from neyma_product_driver.scenario_planner import ScenarioPlanner
    from neyma_product_driver.scenario_suite import SuiteExecutor, build_suite
    from neyma_product_driver.scenarios import ScenarioExecutor

    port = free_port()
    store = out / "store.json"
    if store.exists():
        store.unlink()
    base = base_scenario(port, store, args.defect)

    report: dict[str, Any] = {"defect": args.defect, "real_model": bool(args.real)}

    class Reasoner:
        def __init__(self) -> None:
            self.session_id = ""
            self._llm = (
                LLMScenarioReasoner(work, model="opus", timeout_s=900)
                if args.real
                else None
            )

        def propose(self, brief):
            (out / "brief-wave1.txt").write_text(brief.render())
            report["brief_states_browser_available"] = "BROWSER available: yes" in brief.render()
            if self._llm is None:
                return SCRIPTED
            payload = self._llm.propose(brief)
            self.session_id = self._llm.session_id or ""
            (out / "raw-payload.json").write_text(
                json.dumps(payload, indent=2) if payload is not None else "null"
            )
            return payload

    planner = ScenarioPlanner(
        repo=work,
        config=ScenarioGenerationConfig(
            enabled=True,
            approved_commands=[STATE_CMD],
            max_initial_scenarios=6,
        ),
        reasoner=Reasoner(),
        store=None,
        base_scenario=base,
        permanent_scenarios=[base],
        founder=Founder(),
        # `browser_enabled` must be True here or the brief tells the generator
        # the browser does not exist and it stops proposing browser coverage.
        browser_enabled=True,
        emit=lambda m: print(m, flush=True),
    )

    # -- 1. generation + 2. deterministic validation ------------------------
    print("=== GENERATE ===", flush=True)
    plan = planner.plan_initial(task=TASK, unit=Unit(), run_id="g-browser")
    (out / "plan.json").write_text(json.dumps(plan.model_dump(mode="json"), indent=2))
    (out / "plan.txt").write_text(plan.render())

    browser_models = [s for s in plan.scenarios if s.mode == "browser"]
    report["generation"] = {
        "proposed": sum(w.proposed for w in plan.waves),
        "accepted": len(plan.scenarios),
        "accepted_browser": len(browser_models),
        "refused": [
            {"id": r.id, "reasons": r.reasons} for w in plan.waves for r in w.rejected
        ],
    }
    if not browser_models:
        report["RESULT"] = "NO BROWSER SCENARIO SURVIVED VALIDATION"
        (out / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return 1

    # -- 3. compile ---------------------------------------------------------
    compiled = [(m, planner.compiled[m.id]) for m in plan.scenarios if m.id in planner.compiled]
    report["compile"] = {
        "compiled": len(compiled),
        "browser_mode_preserved": [
            m.id for m, c in compiled if getattr(c, "mode", "") == "browser"
        ],
    }
    (out / "compiled.json").write_text(
        json.dumps({m.id: c.model_dump(mode="json") for m, c in compiled}, indent=2, default=str)
    )

    # -- 4. Playwright execution + 5. evidence ------------------------------
    print("=== EXECUTE (real chromium) ===", flush=True)
    suite = build_suite(permanent=[(base.name, base)], generated=compiled)
    run_cfg = ScenarioRunConfig(
        command_timeout_s=30,
        readiness_timeout_s=25,
        readiness_poll_interval_s=0.5,
        # Without this the executor refuses every browser step and the "proof"
        # would be of the refusal path, not of the browser path.
        browser_enabled=True,
    )
    executor = SuiteExecutor(
        make_executor=lambda d: ScenarioExecutor(work, run_cfg, d),
        artifact_root=out / "artifacts",
        browser_enabled=True,
        execution_budget_s=600,
        run_id="g-browser",
        iteration=1,
        emit=lambda m: print(m, flush=True),
    )
    result = await executor.run(suite, selection_reason="browser e2e proof")
    (out / "suite-result.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2)
    )
    print(result.summary_block(), flush=True)

    evidence: list[dict[str, Any]] = []
    for outcome in result.outcomes:
        directory = Path(outcome.evidence_path) if outcome.evidence_path else None
        files = sorted(p.name for p in directory.rglob("*") if p.is_file()) if directory else []
        evidence.append(
            {
                "scenario_id": outcome.scenario_id,
                "outcome": outcome.outcome.value,
                "evidence_verified": outcome.evidence_verified,
                "evidence_problem": outcome.evidence_problem,
                "files": files,
                "screenshots": [f for f in files if f.endswith(".png")],
            }
        )
    report["execution"] = {
        "outcomes": [
            {"id": o.scenario_id, "outcome": o.outcome.value, "mode": "browser"}
            for o in result.outcomes
        ],
        "skipped": [o.scenario_id for o in result.outcomes if o.outcome.value == "SKIPPED"],
        "evidence": evidence,
        "screenshots_captured": sum(len(e["screenshots"]) for e in evidence),
    }

    # -- 6. aggregation + 7. gate ------------------------------------------
    verdict = evaluate_gate(result, risks=plan.risks)
    report["aggregation"] = {
        "total": result.total,
        "passed": result.passed,
        "failed": result.failed,
        "blocked": result.blocked,
        "skipped": result.skipped,
        "coverage_by_risk_category": result.coverage_by_risk_category(),
    }
    report["gate"] = {
        "status": verdict.status.value,
        "blocks_acceptance": verdict.blocks_acceptance,
        "required_total": verdict.required_total,
        "required_passed": verdict.required_passed,
        "unverified": [c.brief() for c in verdict.unverified],
        "uncovered_risks": [r.brief() for r in verdict.uncovered_risks],
        "summary": verdict.summary_block(),
    }

    # A chromium that never opened proves nothing, so the criterion is that a
    # generated browser scenario actually drove the browser: it produced a
    # browser observation with visible page text, and its evidence resolves.
    browser_ids = {m.id for m in browser_models}
    drove_browser: list[str] = []
    for scenario_id, scenario_result in executor.results.items():
        if scenario_id not in browser_ids:
            continue
        observation = getattr(scenario_result, "browser", None)
        if observation is not None and getattr(observation, "visible_text", ""):
            drove_browser.append(scenario_id)
    report["browser_actually_driven"] = drove_browser
    report["execution"]["visible_text_sample"] = {
        scenario_id: (
            getattr(executor.results[scenario_id].browser, "visible_text", "")[:400]
        )
        for scenario_id in drove_browser
    }

    evidence_ok = all(
        e["evidence_verified"] for e in evidence if e["scenario_id"] in browser_ids
    )
    report["RESULT"] = (
        "BROWSER PATH PROVEN END TO END"
        if drove_browser and evidence_ok and report["execution"]["screenshots_captured"]
        else "BROWSER PATH INCOMPLETE"
    )
    (out / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
