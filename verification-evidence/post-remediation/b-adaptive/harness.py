"""Shared harness for the adaptive-divergence experiment.

Adapted from `verification-evidence/r5-adaptive/scripts/harness.py`. The design
is r5's and is kept deliberately: wave 1 is an IDENTICAL scripted batch in every
run, so it knows nothing about which defect is seeded. Only the seeded defect
differs, and whatever wave 2 does differently is therefore attributable to the
observed evidence and nothing else.

Two changes from r5's version, both mechanical:

  * the driver checkout and the working directory are parameters, so the
    experiment can be pointed at a specific commit and can run outside the
    repository tree;
  * fixture paths are relative to the working directory rather than to a `r5/`
    directory that had to exist at the repository root.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

TASK = "Implement an approval endpoint with persistent state."
STATE_CMD = "python3 fixture/state.py"


def load_driver(driver_root: str | Path) -> None:
    sys.path.insert(0, str(Path(driver_root).resolve()))


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
    version = "b-fixture"
    rubric: dict[str, Any] = {"categories": [{"id": "product rubric"}]}


def base_scenario(defect: str, port: int, store: Path):
    from neyma_product_driver.scenarios import Scenario, ServiceSpec

    env = {
        "DEFECT": defect,
        "PORT": str(port),
        "STORE": str(store),
        "STALL_S": "8",
    }
    return Scenario(
        name="permanent:approval-smoke",
        description="Permanent regression scenario for the approval endpoint.",
        services=[
            ServiceSpec(
                name="api",
                command=f"{sys.executable} fixture/app.py",
                env=env,
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
            },
        ],
        expect_state=[
            {"command": f"{STATE_CMD} SMOKE", "contains": ["status=approved"]}
        ],
        env=env,
    )


# --------------------------------------------------------------------------
# Wave 1 — the same generic batch in every experiment
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
            "rationale": "the endpoint's core promise must hold before anything else",
            "requirement_reference": "AC-APPROVAL-001",
            "product_principle_reference": "product rubric",
            "service_refs": ["api"],
            "isolation_key": "store",
            "isolation_note": "uses its own invoice id, so it cannot contaminate others",
            "generating_risk": "an approval may not be accepted at all",
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
            "rationale": "the acceptance criterion demands durability, not a response code",
            "requirement_reference": "AC-APPROVAL-001",
            "product_principle_reference": "product rubric",
            "service_refs": ["api"],
            "isolation_key": "store",
            "isolation_note": "uses its own invoice id",
            "generating_risk": "the approval may never reach the durable store",
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
            "rationale": "a retried approval is the ordinary path to a duplicate payment",
            "requirement_reference": "AC-APPROVAL-001",
            "product_principle_reference": "product rubric",
            "service_refs": ["api"],
            "isolation_key": "store",
            "isolation_note": "uses its own invoice id",
            "generating_risk": "a repeated approval could pay twice",
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


def make_planner(reasoner, defect: str, port: int, store: Path, work: Path):
    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.scenario_planner import ScenarioPlanner

    base = base_scenario(defect, port, store)
    cfg = ScenarioGenerationConfig(enabled=True, approved_commands=[STATE_CMD])
    return ScenarioPlanner(
        repo=work,
        config=cfg,
        reasoner=reasoner,
        store=None,
        base_scenario=base,
        permanent_scenarios=[base],
        founder=Founder(),
        emit=lambda m: print(m, flush=True),
    )


async def run_suite(planner, defect: str, port: int, store: Path, work: Path, artifact_root: Path):
    from neyma_product_driver.config import ScenarioRunConfig
    from neyma_product_driver.scenario_suite import SuiteExecutor, build_suite
    from neyma_product_driver.scenarios import ScenarioExecutor

    base = base_scenario(defect, port, store)
    suite = build_suite(
        permanent=[(base.name, base)],
        generated=[
            (m, planner.compiled[m.id])
            for m in planner.plan.scenarios
            if m.id in planner.compiled
        ],
    )
    run_cfg = ScenarioRunConfig(
        command_timeout_s=30, readiness_timeout_s=25, readiness_poll_interval_s=0.5
    )
    executor = SuiteExecutor(
        make_executor=lambda d: ScenarioExecutor(work, run_cfg, d),
        artifact_root=artifact_root,
        browser_enabled=False,
        execution_budget_s=300,
        run_id=f"b-{defect}",
        iteration=1,
        emit=lambda m: print(m, flush=True),
    )
    result = await executor.run(suite, selection_reason="adaptive divergence wave 1")
    # The suite and the per-scenario results are what `build_failure_evidence`
    # needs; returning them keeps the experiment on the driver's own path rather
    # than reconstructing the brief by hand.
    return result, suite, executor.results
