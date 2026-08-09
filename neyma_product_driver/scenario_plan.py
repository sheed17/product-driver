"""Structured scenario plans, and the deterministic compiler that makes them run.

A generated scenario is never executed as the model wrote it. It arrives as a
:class:`GeneratedScenario` — a validated Pydantic model describing *intent*
("approve the same invoice twice and prove only one payment effect exists") —
and is then **compiled** into the ordinary :class:`~neyma_product_driver.scenarios.Scenario`
the existing executor already knows how to run. Three separable stages, in this
order, and nothing skips a stage:

    1. PLAN      a model proposes GeneratedScenario objects (scenario_generator)
    2. VALIDATE  deterministic rules accept or reject each one (scenario_validation)
    3. COMPILE   accepted scenarios become Scenario objects (this module)

The compiler is total and closed: every field it reads is already constrained by
the models here, and the only operations it can emit are the step kinds the
executor implements. A model cannot widen that set by writing something
inventive, because there is no field in which to write it. In particular there
is no field anywhere in this module that carries a free-form shell command into
execution — ``setup``, ``cleanup`` and command actions all carry a command
*string that validation must first match against an approved set*, and the
compiler refuses to emit a step whose command was not marked approved.

Provenance travels with every scenario, so "why did Product Driver test this
situation?" is answerable from ``runs/<id>/scenario-plan.json`` alone, without
reading a model transcript.
"""

from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import utcnow
from .scenarios import (
    BrowserSpec,
    BrowserStep,
    CommandSpec,
    RequestSpec,
    Scenario,
    ScenarioStep,
    ServiceSpec,
    StateCheckSpec,
)


class RiskCategory(str, Enum):
    """The situation families a plan may draw from.

    Deliberately a closed enum. A model that wants to exercise something outside
    this list is telling us the taxonomy is incomplete — which is a change a
    human makes here, not one a run makes silently at 3am.
    """

    HAPPY_PATH = "happy_path"
    BOUNDARY = "boundary"
    MISSING_DATA = "missing_data"
    MALFORMED_INPUT = "malformed_input"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    IDEMPOTENCY = "idempotency"
    REPEATED_REQUEST = "repeated_request"
    CONCURRENCY = "concurrency"
    AUTHORIZATION = "authorization"
    APPROVAL_REQUIRED = "approval_required"
    CROSS_TENANT = "cross_tenant"
    PARTIAL_FAILURE = "partial_failure"
    SERVICE_UNAVAILABLE = "service_unavailable"
    DEPENDENCY_FAILURE = "dependency_failure"
    TIMEOUT_BEFORE_EFFECT = "timeout_before_effect"
    TIMEOUT_AFTER_EFFECT = "timeout_after_effect"
    AMBIGUOUS_EXTERNAL_EFFECT = "ambiguous_external_effect"
    RETRY_SAFETY = "retry_safety"
    PERSISTENCE_FAILURE = "persistence_failure"
    STALE_STATE = "stale_state"
    UI_BACKEND_DISAGREEMENT = "ui_backend_disagreement"
    RESTART_RECOVERY = "restart_recovery"
    CRASH_MID_WORKFLOW = "crash_mid_workflow"
    BROWSER_NETWORK_ERROR = "browser_network_error"
    UNEXPECTED_TRANSITION = "unexpected_state_transition"
    SAFETY_INVARIANT = "safety_invariant"
    REGRESSION = "regression"


#: Categories whose whole claim is about an effect having (or not having)
#: durably happened. For these, an HTTP status is not evidence — the scenario
#: must inspect persisted state, or it is rejected. This is the mechanical form
#: of the rubric's "a 200 is not success".
EFFECT_FAMILY: frozenset[RiskCategory] = frozenset(
    {
        RiskCategory.IDEMPOTENCY,
        RiskCategory.PARTIAL_FAILURE,
        RiskCategory.TIMEOUT_AFTER_EFFECT,
        RiskCategory.AMBIGUOUS_EXTERNAL_EFFECT,
        RiskCategory.RETRY_SAFETY,
        RiskCategory.PERSISTENCE_FAILURE,
        RiskCategory.RESTART_RECOVERY,
        RiskCategory.CRASH_MID_WORKFLOW,
        RiskCategory.UNEXPECTED_TRANSITION,
    }
)

#: Related categories, used to widen a rerun after a failure and to cluster
#: failures that plausibly share one cause. Membership is symmetric within a
#: family; a category absent here neighbours only itself.
RISK_FAMILIES: tuple[frozenset[RiskCategory], ...] = (
    frozenset(
        {
            RiskCategory.IDEMPOTENCY,
            RiskCategory.REPEATED_REQUEST,
            RiskCategory.CONCURRENCY,
            RiskCategory.RETRY_SAFETY,
            RiskCategory.TIMEOUT_AFTER_EFFECT,
            RiskCategory.AMBIGUOUS_EXTERNAL_EFFECT,
        }
    ),
    frozenset(
        {
            RiskCategory.PERSISTENCE_FAILURE,
            RiskCategory.RESTART_RECOVERY,
            RiskCategory.CRASH_MID_WORKFLOW,
            RiskCategory.STALE_STATE,
            RiskCategory.UI_BACKEND_DISAGREEMENT,
            RiskCategory.UNEXPECTED_TRANSITION,
        }
    ),
    frozenset(
        {
            RiskCategory.AUTHORIZATION,
            RiskCategory.CROSS_TENANT,
            RiskCategory.APPROVAL_REQUIRED,
            RiskCategory.SAFETY_INVARIANT,
        }
    ),
    frozenset(
        {
            RiskCategory.MISSING_DATA,
            RiskCategory.MALFORMED_INPUT,
            RiskCategory.BOUNDARY,
            RiskCategory.CONFLICTING_EVIDENCE,
        }
    ),
    frozenset(
        {
            RiskCategory.SERVICE_UNAVAILABLE,
            RiskCategory.DEPENDENCY_FAILURE,
            RiskCategory.PARTIAL_FAILURE,
            RiskCategory.TIMEOUT_BEFORE_EFFECT,
            RiskCategory.BROWSER_NETWORK_ERROR,
        }
    ),
)


def neighbours(category: RiskCategory) -> frozenset[RiskCategory]:
    """Categories that plausibly fail for the same underlying reason."""
    for family in RISK_FAMILIES:
        if category in family:
            return family
    return frozenset({category})


class Priority(str, Enum):
    """How much an unresolved failure here should block acceptance."""

    P0 = "P0"  # a safety or correctness invariant; never acceptable while failing
    P1 = "P1"  # high: the task's core promise
    P2 = "P2"  # meaningful, but not on its own a reason to hold the run
    P3 = "P3"  # nice to know

    @property
    def blocks_acceptance(self) -> bool:
        return self in (Priority.P0, Priority.P1)

    @property
    def rank(self) -> int:
        return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}[self.value]


ExecutionMode = Literal["backend", "browser"]

ActionKind = Literal[
    "command",
    "request",
    "parallel_requests",
    "browser",
    "state_check",
    "fixture",
    "wait",
    "restart_service",
    "stop_service",
    "start_service",
]


# --------------------------------------------------------------------------
# The operations a generated scenario may propose
# --------------------------------------------------------------------------


class GeneratedRequest(BaseModel):
    """One local HTTP call. Loopback only; enforced in scenario_validation."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    method: str = "GET"
    path: str = ""
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: Any = None
    body: str = ""
    expect_status: int | None = None
    expect_contains: list[str] = Field(default_factory=list)
    timeout_s: int | None = None

    @field_validator("method")
    @classmethod
    def _upper(cls, v: str) -> str:
        return (v or "GET").strip().upper()

    def target(self) -> str:
        return self.url or self.path


class GeneratedBrowserStep(BaseModel):
    """One supported browser interaction. Mirrors the executor's BrowserStep."""

    model_config = ConfigDict(extra="forbid")

    goto: str | None = None
    click: str | None = None
    fill: str | None = None
    value: str | None = None
    press: str | None = None
    wait_for: str | None = None
    wait_ms: int | None = None
    screenshot: str | None = None
    expect_text: str | None = None


class GeneratedStateCheck(BaseModel):
    """A read-only probe of persisted state — the oracle for an effect claim."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    command: str
    contains: list[str] = Field(default_factory=list)
    not_contains: list[str] = Field(default_factory=list)
    timeout_s: int | None = None


class GeneratedAction(BaseModel):
    """One ordered operation a generated scenario proposes.

    A flat model with a ``kind`` discriminator: whichever payload the kind names
    is read, and every other field is ignored by the compiler. Nothing here can
    express an operation the executor does not implement.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ActionKind
    name: str = ""
    #: For ``command``. Must match the approved command set or the scenario is
    #: rejected before compilation — see scenario_validation.approve_commands.
    command: str = ""
    expect_exit_code: int | None = 0
    expect_contains: list[str] = Field(default_factory=list)
    timeout_s: int | None = None
    request: GeneratedRequest | None = None
    requests: list[GeneratedRequest] = Field(default_factory=list)
    browser_steps: list[GeneratedBrowserStep] = Field(default_factory=list)
    state_check: GeneratedStateCheck | None = None
    fixture_name: str = ""
    fixture_content: str = ""
    service: str = ""
    wait_ms: int | None = None

    def mutates_local_state(self) -> bool:
        """True when this action could leave something behind."""
        if self.kind in {"command", "fixture", "restart_service", "stop_service", "start_service"}:
            return True
        if self.kind == "request" and self.request is not None:
            return self.request.method != "GET"
        if self.kind == "parallel_requests":
            return any(r.method != "GET" for r in self.requests)
        if self.kind == "browser":
            return any(s.click or s.fill or s.press for s in self.browser_steps)
        return False

    def signature(self) -> str:
        """Stable identity for duplicate detection. Ignores prose."""
        if self.kind == "command":
            return f"command:{_norm(self.command)}"
        if self.kind == "request" and self.request is not None:
            return f"request:{self.request.method}:{_norm(self.request.target())}"
        if self.kind == "parallel_requests":
            inner = ",".join(sorted(f"{r.method}:{_norm(r.target())}" for r in self.requests))
            return f"parallel:{inner}"
        if self.kind == "state_check" and self.state_check is not None:
            return f"state:{_norm(self.state_check.command)}"
        if self.kind == "browser":
            return "browser:" + ",".join(
                _norm(s.goto or s.click or s.fill or s.press or s.wait_for or s.expect_text or "")
                for s in self.browser_steps
            )
        if self.kind in {"restart_service", "stop_service", "start_service"}:
            return f"{self.kind}:{_norm(self.service)}"
        if self.kind == "fixture":
            return f"fixture:{_norm(self.fixture_name)}"
        return f"{self.kind}:{self.wait_ms}"


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


class ScenarioProvenance(BaseModel):
    """Why this scenario exists, recorded at generation time.

    Everything a future engineer needs in order to reconstruct the decision
    without the model conversation: what the driver was asked to do, what the
    repository said at the time, what changed, what had already failed, and
    which risk prompted this particular case.
    """

    model_config = ConfigDict(extra="forbid")

    generated_at: str = Field(default_factory=utcnow)
    #: Digest of the task text, so a plan can be matched to the task that caused it.
    task_hash: str = ""
    founder_context_version: str = ""
    repository_head: str = ""
    active_unit_id: str = ""
    acceptance_criteria_consulted: list[str] = Field(default_factory=list)
    files_consulted: list[str] = Field(default_factory=list)
    diff_files_consulted: list[str] = Field(default_factory=list)
    prior_failures_consulted: list[str] = Field(default_factory=list)
    #: The identified risk that caused this scenario to be generated.
    generating_risk: str = ""
    #: Which stage produced it: initial, diff_refinement, adaptive.
    stage: str = ""
    wave: int = 0
    model: str = ""
    session_id: str = ""
    #: Ephemeral generated scenarios live in the run directory and are never
    #: written into the permanent suite without an explicit founder action.
    kind: Literal["ephemeral", "permanent"] = "ephemeral"

    def render(self) -> str:
        lines = [
            f"generated at {self.generated_at} (stage={self.stage or 'n/a'}, wave {self.wave})",
            f"because of risk: {self.generating_risk or '(unstated)'}",
            f"repository HEAD {self.repository_head or '?'}, active unit "
            f"{self.active_unit_id or '?'}, founder context {self.founder_context_version or '?'}",
        ]
        if self.acceptance_criteria_consulted:
            lines.append("acceptance criteria consulted: " + "; ".join(self.acceptance_criteria_consulted[:6]))
        if self.diff_files_consulted:
            lines.append("diff files consulted: " + ", ".join(self.diff_files_consulted[:10]))
        if self.prior_failures_consulted:
            lines.append("prior failures consulted: " + "; ".join(self.prior_failures_consulted[:6]))
        return "\n".join(lines)


# --------------------------------------------------------------------------
# The scenario
# --------------------------------------------------------------------------


class GeneratedScenario(BaseModel):
    """One situation the driver decided is worth exercising."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    purpose: str = ""
    risk_category: RiskCategory
    priority: Priority = Priority.P2
    rationale: str = ""

    #: Grounding. Both must name something the repository or founder context
    #: actually says; validation refuses a scenario that invents a requirement.
    requirement_reference: str = ""
    product_principle_reference: str = ""

    mode: ExecutionMode = "backend"
    #: Approved commands run before the product is exercised.
    setup: list[str] = Field(default_factory=list)
    #: Names of services declared by the base scenario. A generated scenario may
    #: never introduce a service command of its own.
    service_refs: list[str] = Field(default_factory=list)
    actions: list[GeneratedAction] = Field(default_factory=list)
    persisted_state_checks: list[GeneratedStateCheck] = Field(default_factory=list)
    expected_observations: list[str] = Field(default_factory=list)
    forbidden_observations: list[str] = Field(default_factory=list)
    cleanup: list[str] = Field(default_factory=list)
    #: How this scenario avoids contaminating the next one, when it mutates
    #: state that no cleanup command can undo.
    isolation_note: str = ""
    #: Scenarios sharing a key contend for the same resource (database, port,
    #: workspace) and are never run concurrently with each other.
    isolation_key: str = "default"

    generated_from: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    provenance: ScenarioProvenance = Field(default_factory=ScenarioProvenance)

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @field_validator("id")
    @classmethod
    def _safe_id(cls, v: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(v)).strip("-")
        if not cleaned:
            raise ValueError("scenario id is empty after sanitisation")
        return cleaned[:64]

    # -- derived properties ------------------------------------------------

    @property
    def blocks_acceptance(self) -> bool:
        return self.priority.blocks_acceptance

    def mutates_local_state(self) -> bool:
        return bool(self.setup) or any(a.mutates_local_state() for a in self.actions)

    def has_observable_outcome(self) -> bool:
        """True when *something* about this scenario could actually be judged."""
        if self.expected_observations or self.forbidden_observations:
            return True
        if self.persisted_state_checks:
            return True
        for action in self.actions:
            if action.expect_contains or action.expect_exit_code is not None and action.kind == "command":
                return True
            if action.kind == "request" and action.request is not None:
                if action.request.expect_status is not None or action.request.expect_contains:
                    return True
            if action.kind == "parallel_requests":
                if any(r.expect_status is not None or r.expect_contains for r in action.requests):
                    return True
            if action.kind == "state_check" and action.state_check is not None:
                if action.state_check.contains or action.state_check.not_contains:
                    return True
            if action.kind == "browser":
                if any(s.expect_text for s in action.browser_steps):
                    return True
        return False

    def inspects_persisted_state(self) -> bool:
        return bool(self.persisted_state_checks) or any(
            a.kind == "state_check" for a in self.actions
        )

    def command_strings(self) -> list[str]:
        """Every command string this scenario would run, from any field."""
        out = list(self.setup) + list(self.cleanup)
        for action in self.actions:
            if action.kind == "command" and action.command:
                out.append(action.command)
            if action.kind == "state_check" and action.state_check is not None:
                out.append(action.state_check.command)
        out.extend(c.command for c in self.persisted_state_checks)
        return [c for c in out if c.strip()]

    def signature(self) -> str:
        """Coverage identity: what this scenario exercises, not how it is worded.

        Deliberately excludes the risk category. The same operations with the
        same expectations exercise the same situation whichever risk lens they
        were proposed under, and relabelling must not buy a second slot in the
        budget.
        """
        return coverage_signature(
            actions=[a.signature() for a in self.actions],
            expected=self.expected_observations,
            forbidden=self.forbidden_observations,
            state_commands=[c.command for c in self.persisted_state_checks]
            + [
                a.state_check.command
                for a in self.actions
                if a.kind == "state_check" and a.state_check is not None
            ],
        )

    def summary(self) -> str:
        return (
            f"[{self.priority.value} {self.risk_category.value}] {self.id}: {self.title}"
        )


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------


class IdentifiedRisk(BaseModel):
    """One thing that could realistically fail, and why we believe that."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    description: str
    risk_category: RiskCategory
    severity: Priority = Priority.P2
    basis: str = ""
    covered_by: list[str] = Field(default_factory=list)

    @property
    def covered(self) -> bool:
        return bool(self.covered_by)


class GenerationBasis(BaseModel):
    """Exactly what the generator was shown. The plan's own provenance."""

    model_config = ConfigDict(extra="forbid")

    task: str = ""
    task_hash: str = ""
    active_unit_id: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    founder_context_version: str = ""
    repository_head: str = ""
    diff_files: list[str] = Field(default_factory=list)
    diff_stat: str = ""
    existing_scenarios: list[str] = Field(default_factory=list)
    prior_failures: list[str] = Field(default_factory=list)
    investigation_findings: list[str] = Field(default_factory=list)
    evaluator_requests: list[str] = Field(default_factory=list)


class CoverageSummary(BaseModel):
    """What the plan claims to cover — stated as coverage, never as proof."""

    model_config = ConfigDict(extra="forbid")

    total_scenarios: int = 0
    by_risk_category: dict[str, int] = Field(default_factory=dict)
    by_priority: dict[str, int] = Field(default_factory=dict)
    uncovered_risks: list[str] = Field(default_factory=list)

    def render(self) -> str:
        cats = ", ".join(sorted(self.by_risk_category)) or "none"
        lines = [
            f"{self.total_scenarios} generated case(s)",
            f"risk categories exercised: {cats}",
        ]
        if self.uncovered_risks:
            lines.append("identified but NOT exercised: " + "; ".join(self.uncovered_risks))
        return "\n".join(lines)


class RejectedScenario(BaseModel):
    """A proposal that did not survive validation, kept as evidence."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    title: str = ""
    reasons: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class WaveRecord(BaseModel):
    """One generation wave: what was asked, proposed, accepted and refused."""

    model_config = ConfigDict(extra="forbid")

    wave: int
    stage: str
    requested_at: str = Field(default_factory=utcnow)
    basis: GenerationBasis = Field(default_factory=GenerationBasis)
    proposed: int = 0
    accepted_ids: list[str] = Field(default_factory=list)
    rejected: list[RejectedScenario] = Field(default_factory=list)
    budget_notes: list[str] = Field(default_factory=list)
    reasoner_error: str = ""


class GeneratedScenarioPlan(BaseModel):
    """Everything the planner decided for one run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = ""
    task: str = ""
    active_unit_id: str = ""
    generation_basis: GenerationBasis = Field(default_factory=GenerationBasis)
    risks: list[IdentifiedRisk] = Field(default_factory=list)
    scenarios: list[GeneratedScenario] = Field(default_factory=list)
    coverage_summary: CoverageSummary = Field(default_factory=CoverageSummary)
    assumptions: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    waves: list[WaveRecord] = Field(default_factory=list)

    def by_id(self, scenario_id: str) -> GeneratedScenario | None:
        return next((s for s in self.scenarios if s.id == scenario_id), None)

    def signatures(self) -> set[str]:
        return {s.signature() for s in self.scenarios}

    def count_for(self, category: RiskCategory) -> int:
        return sum(1 for s in self.scenarios if s.risk_category is category)

    def recompute_coverage(self) -> None:
        by_cat: dict[str, int] = {}
        by_pri: dict[str, int] = {}
        for scenario in self.scenarios:
            by_cat[scenario.risk_category.value] = by_cat.get(scenario.risk_category.value, 0) + 1
            by_pri[scenario.priority.value] = by_pri.get(scenario.priority.value, 0) + 1
        covered = {s.risk_category for s in self.scenarios}
        self.coverage_summary = CoverageSummary(
            total_scenarios=len(self.scenarios),
            by_risk_category=by_cat,
            by_priority=by_pri,
            uncovered_risks=[
                r.description for r in self.risks if r.risk_category not in covered
            ],
        )

    def render(self) -> str:
        lines = [
            f"GENERATED SCENARIO PLAN — run {self.run_id or '(unsaved)'}",
            f"task: {self.task.strip()[:200] or '(none)'}",
            f"active unit: {self.active_unit_id or '(unresolved)'}",
            "",
            self.coverage_summary.render(),
        ]
        if self.risks:
            lines += ["", "IDENTIFIED RISKS:"]
            lines += [
                f"  [{r.severity.value} {r.risk_category.value}] {r.description}"
                + (f"  (covered by {', '.join(r.covered_by)})" if r.covered_by else "  (NOT covered)")
                for r in self.risks
            ]
        if self.scenarios:
            lines += ["", "SCENARIOS:"]
            for s in self.scenarios:
                lines.append(f"  {s.summary()}")
                if s.purpose:
                    lines.append(f"      purpose: {s.purpose}")
                lines.append(f"      grounded in: {s.requirement_reference or '(none)'}")
                lines.append(f"      because: {s.provenance.generating_risk or s.rationale or '(unstated)'}")
        if self.assumptions:
            lines += ["", "ASSUMPTIONS:"] + [f"  - {a}" for a in self.assumptions]
        if self.unresolved_questions:
            lines += ["", "UNRESOLVED QUESTIONS:"] + [f"  - {q}" for q in self.unresolved_questions]
        rejected = [r for w in self.waves for r in w.rejected]
        if rejected:
            lines += ["", "REJECTED PROPOSALS:"]
            lines += [f"  {r.id or '(unnamed)'}: {'; '.join(r.reasons)}" for r in rejected]
        lines += [
            "",
            "This describes verified coverage, not exhaustive correctness. Situations "
            "nobody thought of are not covered by anything above.",
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Compilation
# --------------------------------------------------------------------------


class CompilationError(ValueError):
    """A validated scenario could still not be compiled. Always fails closed."""


def compile_to_scenario(
    generated: GeneratedScenario,
    *,
    base: Scenario | None = None,
    approved_commands: set[str] | None = None,
    name_prefix: str = "generated",
) -> Scenario:
    """Turn a validated :class:`GeneratedScenario` into an executable Scenario.

    ``base`` supplies the things a generated scenario is not allowed to invent:
    the service commands, the readiness checks, the app URL and the environment.
    ``approved_commands`` is the exact set validation approved; the compiler
    re-checks every command string against it and refuses rather than trusting
    that validation ran. Belt and braces on purpose — this is the last point
    before a string becomes a subprocess.
    """
    approved = approved_commands if approved_commands is not None else set()

    def _check(command: str, where: str) -> str:
        if command not in approved:
            raise CompilationError(
                f"refusing to compile {generated.id}: the {where} command was not in the "
                f"approved command set ({command!r}). Generated scenarios may only run "
                "commands a human already approved."
            )
        return command

    services: list[ServiceSpec] = []
    readiness: list[dict[str, Any]] = []
    app_url = ""
    env: dict[str, str] = {}
    if base is not None:
        wanted = set(generated.service_refs)
        declared = {s.name: s for s in base.services}
        missing = sorted(wanted - set(declared))
        if missing:
            raise CompilationError(
                f"refusing to compile {generated.id}: it references service(s) "
                f"{', '.join(missing)} that the base scenario does not declare"
            )
        services = [declared[name] for name in base_order(base, wanted)]
        readiness = list(base.readiness) if services else []
        app_url = base.app_url
        env = dict(base.env)
    elif generated.service_refs:
        raise CompilationError(
            f"refusing to compile {generated.id}: it references services but no base "
            "scenario declares any"
        )

    steps: list[ScenarioStep] = [
        _compile_action(action, generated, _check) for action in generated.actions
    ]
    steps += [
        ScenarioStep(
            kind="state_check",
            name=check.name or "persisted state",
            state_check=StateCheckSpec(
                name=check.name,
                command=_check(check.command, "persisted-state"),
                contains=list(check.contains),
                not_contains=list(check.not_contains),
                timeout_s=check.timeout_s,
            ),
        )
        for check in generated.persisted_state_checks
    ]

    return Scenario(
        name=f"{name_prefix}:{generated.id}",
        phase=base.phase if base is not None else "",
        description=_describe(generated),
        mode=generated.mode,
        setup=[_check(c, "setup") for c in generated.setup],
        services=services,
        readiness=readiness,
        app_url=app_url,
        steps=steps,
        expect_visible=list(generated.expected_observations),
        forbidden=list(generated.forbidden_observations),
        teardown=[_check(c, "cleanup") for c in generated.cleanup],
        env=env,
    )


def base_order(base: Scenario, wanted: set[str]) -> list[str]:
    """Preserve the base scenario's own service ordering — startup order matters."""
    return [s.name for s in base.services if s.name in wanted]


def _describe(generated: GeneratedScenario) -> str:
    parts = [
        generated.title,
        "",
        f"risk: {generated.risk_category.value} (priority {generated.priority.value})",
        f"purpose: {generated.purpose}" if generated.purpose else "",
        f"requirement: {generated.requirement_reference}",
        f"product principle: {generated.product_principle_reference}",
        f"rationale: {generated.rationale}" if generated.rationale else "",
        "",
        generated.provenance.render(),
    ]
    return "\n".join(p for p in parts if p != "" or True).strip()


def _compile_action(
    action: GeneratedAction,
    generated: GeneratedScenario,
    check: Any,
) -> ScenarioStep:
    """One action → one executor step. Total over the ActionKind literal."""
    if action.kind == "command":
        return ScenarioStep(
            kind="command",
            name=action.name,
            command=CommandSpec(
                name=action.name,
                run=check(action.command, "action"),
                expect_exit_code=action.expect_exit_code,
                expect_contains=list(action.expect_contains),
                timeout_s=action.timeout_s,
            ),
        )

    if action.kind == "state_check":
        if action.state_check is None:
            raise CompilationError(f"{generated.id}: state_check action has no probe")
        return ScenarioStep(
            kind="state_check",
            name=action.name or action.state_check.name,
            state_check=StateCheckSpec(
                name=action.state_check.name or action.name,
                command=check(action.state_check.command, "state-check"),
                contains=list(action.state_check.contains),
                not_contains=list(action.state_check.not_contains),
                timeout_s=action.state_check.timeout_s,
            ),
        )

    if action.kind == "request":
        if action.request is None:
            raise CompilationError(f"{generated.id}: request action has no request")
        return ScenarioStep(
            kind="request", name=action.name, request=_compile_request(action.request)
        )

    if action.kind == "parallel_requests":
        if not action.requests:
            raise CompilationError(f"{generated.id}: parallel_requests action has no requests")
        return ScenarioStep(
            kind="parallel_requests",
            name=action.name,
            requests=[_compile_request(r) for r in action.requests],
        )

    if action.kind == "browser":
        return ScenarioStep(
            kind="browser",
            name=action.name,
            browser=BrowserSpec(
                steps=[
                    BrowserStep(**s.model_dump(exclude_none=True)) for s in action.browser_steps
                ],
                initial_screenshot=True,
                final_screenshot=True,
            ),
        )

    if action.kind == "fixture":
        return ScenarioStep(
            kind="fixture",
            name=action.name or action.fixture_name,
            fixture_name=action.fixture_name,
            fixture_content=action.fixture_content,
        )

    if action.kind == "wait":
        return ScenarioStep(kind="wait", name=action.name, wait_ms=action.wait_ms or 0)

    # restart_service / stop_service / start_service
    return ScenarioStep(kind=action.kind, name=action.name, service=action.service)


def _compile_request(request: GeneratedRequest) -> RequestSpec:
    return RequestSpec(
        name=request.name,
        method=request.method,
        path=request.path or None,
        url=request.url or None,
        headers=dict(request.headers),
        json=request.json_body,
        body=request.body or None,
        expect_status=request.expect_status,
        expect_contains=list(request.expect_contains),
        timeout_s=request.timeout_s,
    )


def coverage_signature(
    *,
    actions: Sequence[str],
    expected: Sequence[str],
    forbidden: Sequence[str],
    state_commands: Sequence[str],
) -> str:
    """The one definition of "these two scenarios cover the same thing".

    Shared by generated scenarios and by the handwritten ones they are checked
    against, so a generated proposal that merely restates a permanent scenario
    is caught by the same rule that catches a generated duplicate.
    """
    parts = [
        "|".join(actions),
        "|".join(sorted(_norm(e) for e in expected)),
        "|".join(sorted(_norm(e) for e in forbidden)),
        "|".join(sorted(_norm(c) for c in state_commands)),
    ]
    return hashlib.sha256("::".join(parts).encode()).hexdigest()[:20]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def task_digest(task: str) -> str:
    return hashlib.sha256((task or "").strip().encode()).hexdigest()[:16]
