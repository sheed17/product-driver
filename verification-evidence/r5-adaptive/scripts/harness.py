"""Shared harness: drive ScenarioPlanner + SuiteExecutor against the buggy app.

Wave 1 is IDENTICAL across all three experiments -- a generic, task-derived
batch that knows nothing about which defect is seeded. Only the seeded defect
differs. Whatever wave 2 does differently is therefore attributable to the
evidence and nothing else.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from neyma_product_driver.config import ScenarioGenerationConfig, ScenarioRunConfig  # noqa: E402
from neyma_product_driver.scenario_planner import ScenarioPlanner  # noqa: E402
from neyma_product_driver.scenario_suite import (  # noqa: E402
    SuiteExecutor,
    build_suite,
)
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor, ServiceSpec  # noqa: E402

PY = sys.executable
TASK = "Implement an approval endpoint with persistent state."
STATE_CMD = "python3 r5/fixture/state.py"


class Unit:
    unit_id = "UNIT-APPROVAL"
    name = "invoice approval endpoint"

    acceptance_criteria = [
        {"criterion": "AC-APPROVAL-001 approving an invoice records the approval durably"},
        {"criterion": "AC-APPROVAL-002 approval state survives a re-read of persisted state"},
    ]

    def criteria_labels(self):
        return [c["criterion"] for c in self.acceptance_criteria]


class Founder:
    version = "r5-fixture"
    rubric: dict[str, Any] = {"categories": [{"id": "product rubric"}]}


def base_scenario(defect: str, port: int, store: Path) -> Scenario:
    return Scenario(
        name="permanent:approval-smoke",
        description="Permanent regression scenario for the approval endpoint.",
        services=[
            ServiceSpec(
                name="api",
                command=f"{PY} r5/fixture/app.py",
                env={"DEFECT": defect, "PORT": str(port), "STORE": str(store), "STALL_S": "8"},
            )
        ],
        readiness=[{"http": f"http://127.0.0.1:{port}/health"}, {"file": str(store)}],
        app_url=f"http://127.0.0.1:{port}",
        requests=[
            {
                "name": "identify the app under test",
                "method": "GET",
                "path": "/health",
                "expect_status": 200,
                "expect_contains": ["defect"],
                "timeout_s": 4,
            },
            {
                "name": "approve smoke invoice",
                "method": "POST",
                "path": "/api/invoices/SMOKE/approve",
                "json": {"actor": "alice"},
                "expect_status": 200,
                "timeout_s": 4,
            }
        ],
        expect_state=[{"command": f"{STATE_CMD} SMOKE", "contains": ["status=approved"]}],
        env={"DEFECT": defect, "PORT": str(port), "STORE": str(store), "STALL_S": "8"},
    )


# --------------------------------------------------------------------------
# Wave 1 -- the same generic batch in every experiment
# --------------------------------------------------------------------------

WAVE1_PAYLOAD: dict[str, Any] = {
    "risks": [
        {
            "id": "R1",
            "description": "Approval may not be recorded durably.",
            "risk_category": "persistence_failure",
            "severity": "P1",
            "basis": "AC-APPROVAL-001",
        },
        {
            "id": "R2",
            "description": "Approving twice may apply the effect twice.",
            "risk_category": "idempotency",
            "severity": "P0",
            "basis": "AC-APPROVAL-001",
        },
    ],
    "scenarios": [
        {
            "id": "gen-happy-approve",
            "title": "Approving a pending invoice succeeds",
            "purpose": "The core promise of the endpoint: an approval is accepted.",
            "risk_category": "happy_path",
            "priority": "P1",
            "requirement_reference": "AC-APPROVAL-001",
            "product_principle_reference": "product rubric",
            "service_refs": ["api"],
            "isolation_key": "store",
            "isolation_note": "uses its own invoice id, so it cannot contaminate others",
            "generating_risk": "R1",
            "actions": [
                {
                    "kind": "request",
                    "name": "approve",
                    "request": {
                        "method": "POST",
                        "path": "/api/invoices/W1A/approve",
                        "json_body": {"actor": "alice"},
                        "expect_status": 200,
                        "timeout_s": 4,
                    },
                }
            ],
            "expected_observations": ["approved"],
        },
        {
            "id": "gen-persist-approve",
            "title": "An approval is written to durable storage",
            "purpose": "A 200 is not evidence; the durable store must agree.",
            "risk_category": "persistence_failure",
            "priority": "P0",
            "requirement_reference": "AC-APPROVAL-001",
            "product_principle_reference": "product rubric",
            "service_refs": ["api"],
            "isolation_key": "store",
            "isolation_note": "uses its own invoice id",
            "generating_risk": "R1",
            "actions": [
                {
                    "kind": "request",
                    "name": "approve",
                    "request": {
                        "method": "POST",
                        "path": "/api/invoices/W1B/approve",
                        "json_body": {"actor": "alice"},
                        "expect_status": 200,
                        "timeout_s": 4,
                    },
                }
            ],
            "persisted_state_checks": [
                {
                    "name": "durable store shows approved",
                    "command": f"{STATE_CMD} W1B",
                    "contains": ["status=approved"],
                }
            ],
        },
        {
            "id": "gen-idempotent-approve",
            "title": "Approving the same invoice twice pays once",
            "purpose": "A repeated approval must not duplicate the payment effect.",
            "risk_category": "idempotency",
            "priority": "P0",
            "requirement_reference": "AC-APPROVAL-001",
            "product_principle_reference": "product rubric",
            "service_refs": ["api"],
            "isolation_key": "store",
            "isolation_note": "uses its own invoice id",
            "generating_risk": "R2",
            "actions": [
                {
                    "kind": "request",
                    "name": "approve once",
                    "request": {
                        "method": "POST",
                        "path": "/api/invoices/W1C/approve",
                        "json_body": {"actor": "alice"},
                        "expect_status": 200,
                        "timeout_s": 4,
                    },
                },
                {
                    "kind": "request",
                    "name": "approve again",
                    "request": {
                        "method": "POST",
                        "path": "/api/invoices/W1C/approve",
                        "json_body": {"actor": "alice"},
                        "expect_status": 200,
                        "timeout_s": 4,
                    },
                },
            ],
            "persisted_state_checks": [
                {
                    "name": "exactly one payment",
                    "command": f"{STATE_CMD} W1C",
                    "contains": ["payments=1"],
                    "not_contains": ["payments=2"],
                }
            ],
        },
    ],
    "assumptions": ["The approval endpoint is POST /api/invoices/{id}/approve."],
    "unresolved_questions": [],
}


class ScriptedReasoner:
    """Deterministic stand-in. Returns a queued payload per wave."""

    def __init__(self, payloads: list[dict[str, Any] | None]) -> None:
        self.payloads = list(payloads)
        self.briefs: list[Any] = []
        self.session_id = "scripted"

    def propose(self, brief):
        self.briefs.append(brief)
        if not self.payloads:
            return None
        return self.payloads.pop(0)


def make_planner(reasoner, defect: str, port: int, store: Path, out_dir: Path,
                 config: ScenarioGenerationConfig | None = None) -> ScenarioPlanner:
    base = base_scenario(defect, port, store)
    cfg = config or ScenarioGenerationConfig(
        enabled=True,
        approved_commands=[STATE_CMD],
    )
    return ScenarioPlanner(
        repo=ROOT,
        config=cfg,
        reasoner=reasoner,
        store=None,
        base_scenario=base,
        permanent_scenarios=[base],
        founder=Founder(),
        emit=lambda m: print(m),
    )


async def run_suite(planner: ScenarioPlanner, defect: str, port: int, store: Path,
                    artifact_root: Path, only=None):
    base = base_scenario(defect, port, store)
    suite = build_suite(
        permanent=[(base.name, base)],
        generated=[
            (m, planner.compiled[m.id]) for m in planner.plan.scenarios if m.id in planner.compiled
        ],
    )
    run_cfg = ScenarioRunConfig(
        command_timeout_s=30, readiness_timeout_s=25, readiness_poll_interval_s=0.5
    )
    executor = SuiteExecutor(
        make_executor=lambda d: ScenarioExecutor(ROOT, run_cfg, d),
        artifact_root=artifact_root,
        browser_enabled=False,
        execution_budget_s=300,
        max_parallel=1,
        emit=lambda m: print(m),
    )
    ids = only if only is not None else [e.scenario_id for e in suite.entries]
    return await executor.run(suite, only=ids, selection_reason="r5 audit pass")
