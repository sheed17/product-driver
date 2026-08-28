"""Builders for generated-scenario tests. No test here consumes Claude usage."""

from __future__ import annotations

from typing import Any

from neyma_product_driver.scenario_generator import ScenarioReasoner
from neyma_product_driver.scenario_plan import (
    GeneratedAction,
    GeneratedRequest,
    GeneratedScenario,
    GeneratedStateCheck,
    Priority,
    RiskCategory,
    ScenarioProvenance,
)
from neyma_product_driver.scenarios import Scenario, ServiceSpec

#: The commands the fake repository's human-written scenarios already approve.
APPROVED_SETUP = "./probe.sh seed"
APPROVED_STATE = "./probe.sh payments"
APPROVED_CLEANUP = "./probe.sh reset"


def base_scenario(app_url: str = "http://127.0.0.1:8931") -> Scenario:
    """A permanent scenario supplying the service, app_url and approved commands."""
    return Scenario(
        name="backend_generic",
        mode="backend",
        setup=[APPROVED_SETUP],
        services=[ServiceSpec(name="api", command="./serve.sh")],
        readiness=[{"tcp": "127.0.0.1:8931"}],
        app_url=app_url,
        commands=[{"name": "smoke", "run": APPROVED_STATE}],
        expect_state=[{"name": "payments", "command": APPROVED_STATE, "contains": ["ok"]}],
        teardown=[APPROVED_CLEANUP],
    )


class FakeUnit:
    """Stands in for context.ActiveUnit without needing a real registry."""

    def __init__(self, unit_id: str = "U-042") -> None:
        self.unit_id = unit_id
        self.name = "supervised carrier invoice approval"
        self.acceptance_criteria = [
            {"criterion": "an approved invoice is paid exactly once", "weight": 3, "result": "PENDING"},
            {"criterion": "approval survives a restart", "weight": 2, "result": "PENDING"},
        ]

    #: A declared unit, as the loop now asks. The undeclared case is exercised
    #: against a real repository in tests/test_operating_policy.py.
    is_declared = True
    resolution_problem = ""

    def criteria_labels(self) -> list[str]:
        return [f"{c['criterion']} (weight {c['weight']}): {c['result']}" for c in self.acceptance_criteria]


class FakeFounder:
    """Stands in for context.FounderContext: only the rubric ids matter here."""

    version = "founder-v1"
    rubric = {
        "categories": [
            {"id": "effect-truth", "description": "a 200 is not success"},
            {"id": "ownership", "description": "who owns the next obligation"},
        ],
        "never_acceptable": [{"id": "silent-data-loss", "description": "..."}],
    }


class ScriptedReasoner:
    """Returns a queued payload per wave. Deterministic; no model involved."""

    def __init__(self, payloads: list[dict[str, Any] | None]) -> None:
        self.payloads = list(payloads)
        self.briefs: list[Any] = []
        self.session_id = "scripted"

    def propose(self, brief: Any) -> dict[str, Any] | None:
        self.briefs.append(brief)
        return self.payloads.pop(0) if self.payloads else {"risks": [], "scenarios": []}


class ExplodingReasoner:
    """Raises. A generator that fails must not take the run with it."""

    session_id = "exploding"

    def propose(self, brief: Any) -> dict[str, Any] | None:
        raise RuntimeError("the model session died")


# --------------------------------------------------------------------------
# Raw payloads (what a model would return)
# --------------------------------------------------------------------------


def raw_scenario(
    scenario_id: str = "gen-approve-twice",
    *,
    risk_category: str = "idempotency",
    requirement: str = "U-042: an approved invoice is paid exactly once",
    principle: str = "effect-truth",
    actions: list[dict[str, Any]] | None = None,
    state_checks: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": scenario_id,
        "title": "approving the same invoice twice produces one payment",
        "purpose": "a duplicate approval must not produce a second payment effect",
        "risk_category": risk_category,
        "priority": "P0",
        "rationale": "the diff touched the approval transition",
        "requirement_reference": requirement,
        "product_principle_reference": principle,
        "mode": "backend",
        "service_refs": ["api"],
        "actions": actions
        if actions is not None
        else [
            {
                "kind": "request",
                "name": "first approval",
                "request": {"method": "POST", "path": "/approve", "expect_status": 200},
            },
            {
                "kind": "request",
                "name": "second approval",
                "request": {"method": "POST", "path": "/approve"},
            },
        ],
        "persisted_state_checks": state_checks
        if state_checks is not None
        else [{"name": "payments", "command": APPROVED_STATE, "contains": ["payments=1"]}],
        "expected_observations": ["payments=1"],
        "forbidden_observations": ["payments=2"],
        "cleanup": [APPROVED_CLEANUP],
        "isolation_key": "workflow-db",
        "generating_risk": "duplicate approval could double-pay",
        "confidence": 0.8,
    }
    payload.update(overrides)
    _bind_observations(payload, state_checks_given=state_checks is not None)
    return payload


def _bind_observations(payload: dict[str, Any], *, state_checks_given: bool) -> None:
    """Keep a fixture's asserted literals attributed to the operation that prints them.

    Validation refuses an ``expected_observations`` entry that no operation in
    the same scenario declares — that is the rule the M7 ``S3`` shape violated.
    Most tests here override ``expected_observations`` only to give a candidate a
    distinct coverage signature, so this keeps the default state check declaring
    whatever those tests asked for, exactly as a compliant generator would. A
    test that supplies its own checks is left alone: those tests are about the
    binding itself.
    """
    if state_checks_given:
        return
    literals = [text for text in payload.get("expected_observations") or [] if str(text).strip()]
    checks = payload.get("persisted_state_checks")
    if not literals or not checks:
        return
    first = checks[0]
    if isinstance(first, dict):
        first["contains"] = list(literals)
    else:
        first.contains = list(literals)


def raw_payload(*scenarios: dict[str, Any], risks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "risks": risks
        if risks is not None
        else [
            {
                "id": "R1",
                "description": "approval may not be idempotent",
                "risk_category": "idempotency",
                "severity": "P0",
                "basis": "the diff touched approval state",
            }
        ],
        "scenarios": list(scenarios),
        "assumptions": ["the approval endpoint is POST /approve"],
        "unresolved_questions": [],
    }


# --------------------------------------------------------------------------
# Model objects (skipping the parse step)
# --------------------------------------------------------------------------


def make_scenario(
    scenario_id: str = "gen-1",
    *,
    risk_category: RiskCategory = RiskCategory.IDEMPOTENCY,
    priority: Priority = Priority.P0,
    actions: list[GeneratedAction] | None = None,
    state_checks: list[GeneratedStateCheck] | None = None,
    **overrides: Any,
) -> GeneratedScenario:
    data: dict[str, Any] = {
        "id": scenario_id,
        "title": "approving twice pays once",
        "purpose": "a duplicate approval must not produce a second payment effect",
        "risk_category": risk_category,
        "priority": priority,
        "requirement_reference": "U-042: an approved invoice is paid exactly once",
        "product_principle_reference": "effect-truth",
        "service_refs": ["api"],
        "actions": actions
        if actions is not None
        else [
            GeneratedAction(
                kind="request",
                request=GeneratedRequest(method="POST", path="/approve", expect_status=200),
            )
        ],
        "persisted_state_checks": state_checks
        if state_checks is not None
        else [GeneratedStateCheck(command=APPROVED_STATE, contains=["payments=1"])],
        "expected_observations": ["payments=1"],
        "cleanup": [APPROVED_CLEANUP],
        "isolation_key": "workflow-db",
        # A realistic stamp, matching what ``provenance_for`` produces on the
        # real path. Generated scenarios never carry an empty one, and
        # validation now refuses those, so a fixture without it would be
        # testing a shape the planner cannot produce.
        "provenance": ScenarioProvenance(
            generating_risk="duplicate approval could double-pay",
            task_hash="task-digest-fixture",
            repository_head="0" * 40,
            active_unit_id="U-042",
            stage="initial",
            wave=1,
            model="opus",
            session_id="fixture-session",
        ),
    }
    data.update(overrides)
    _bind_observations(data, state_checks_given=state_checks is not None)
    return GeneratedScenario.model_validate(data)


def recorded_contract_probe(recording: dict[str, str]) -> Any:
    """A contract probe answering from output recorded off the real program.

    A planner built over a ``tmp_path`` has no program to interrogate, so the
    quality boundary's contest cannot be settled by running anything — and an
    unsettled contest is a refusal, by design. Tests that construct a planner
    over a fake repository and expect a *correct* scenario to survive supply one
    of these instead.

    An unrecorded invocation comes back UNDETERMINED rather than empty: "I did
    not ask" and "it printed nothing" are different answers, and only one of them
    is a reason to refuse an oracle on the product's behalf.
    """
    from neyma_product_driver.scenario_validation import ContractProbeResult

    table = dict(recording)

    def probe(command: str) -> Any:
        if command not in table:
            return ContractProbeResult(False, detail="no recording for this invocation")
        return ContractProbeResult(True, output=table[command])

    return probe


def validation_context(**overrides: Any) -> Any:
    from neyma_product_driver.scenario_validation import (
        ApprovedCommands,
        ValidationContext,
        established_observations_from,
        grounding_tokens_from,
        principle_tokens_from,
    )

    base = base_scenario()
    defaults: dict[str, Any] = {
        "approved_commands": ApprovedCommands.from_sources(scenarios=[base]),
        "established_observations": established_observations_from([base]),
        "grounding_tokens": grounding_tokens_from(FakeUnit()),
        "principle_tokens": principle_tokens_from(FakeFounder()),
        "declared_services": {"api"},
        "app_url": base.app_url,
    }
    defaults.update(overrides)
    return ValidationContext(**defaults)


__all__ = [
    "APPROVED_CLEANUP",
    "APPROVED_SETUP",
    "APPROVED_STATE",
    "ExplodingReasoner",
    "FakeFounder",
    "FakeUnit",
    "ScenarioReasoner",
    "ScriptedReasoner",
    "base_scenario",
    "make_scenario",
    "raw_payload",
    "raw_scenario",
    "recorded_contract_probe",
    "validation_context",
]
