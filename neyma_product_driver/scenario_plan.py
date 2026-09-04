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
from typing import Any, Callable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


#: The canonical vocabulary, as the strings a payload actually carries. THE ONE
#: SOURCE. Every layer that has to *state* the taxonomy rather than merely hold a
#: :class:`RiskCategory` — the generator's structured-output schema, the system
#: instructions it is given, the brief, the rejection accounting and the tests
#: that pin all of them — derives from this tuple. None of them keeps its own
#: copy, because a second hand-maintained list is how the vocabularies drift
#: apart: Product Driver went on rejecting what it had told a model to send.
RISK_CATEGORY_VALUES: tuple[str, ...] = tuple(c.value for c in RiskCategory)


#: Why a proposed scenario is not going to run. Two kinds, and they are not the
#: same fact.
#:
#: ``REJECTED_FILTERED`` — planning worked and decided against this candidate: a
#: duplicate of coverage that already exists, a safety or quality refusal, a
#: budget. Product Driver understood the proposal and said no. Coverage is
#: narrower on purpose and nothing is wrong.
REJECTED_FILTERED = "filtered"

#: ``REJECTED_CONTRACT`` — Product Driver could not READ the proposal. The
#: generator answered, the harness's own schema or taxonomy could not interpret
#: what came back, and the candidate never became a model at all. Nobody knows
#: what risk was on offer, so nobody can say whether the coverage that did run
#: reaches it. This is a defect in the harness, or in the generation contract
#: between it and the generator — never a statement about the product, and never
#: the same fact as "the generator had nothing to add".
REJECTED_CONTRACT = "generation_contract"


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
    #: Seconds, fractional allowed. A scenario probing "timed out before the
    #: effect landed" needs a sub-second deadline; the runtime takes a float, so
    #: refusing one only stopped the model expressing what it meant.
    timeout_s: float | None = None

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
    #: Seconds, fractional allowed. See GeneratedRequest.timeout_s.
    timeout_s: float | None = None


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
    #: Seconds, fractional allowed. See GeneratedRequest.timeout_s.
    timeout_s: float | None = None
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
    #: The specific failures this scenario was generated in response to, by
    #: scenario id. An adaptive scenario that cannot name one was not actually
    #: caused by the evidence, and validation refuses it: without this edge
    #: "which failure produced this case?" is unanswerable from the plan alone,
    #: because every scenario in a wave otherwise shares one flat basis list.
    source_failures: list[str] = Field(default_factory=list)
    #: Failure-cluster ids (``C01``…) this scenario answers, when the failures
    #: it responds to were grouped as sharing one cause.
    source_clusters: list[str] = Field(default_factory=list)
    #: The identified risks this scenario was generated to close, by risk key.
    #: A coverage-gap scenario exists because a named risk had no evidence, and
    #: validation checks these against the run's own register — so a case
    #: generated "to close a gap" cannot name a gap the run never identified.
    source_risks: list[str] = Field(default_factory=list)
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
        if self.source_failures:
            lines.append("generated in response to failure(s): " + ", ".join(self.source_failures))
        if self.source_clusters:
            lines.append("answering failure cluster(s): " + ", ".join(self.source_clusters))
        return "\n".join(lines)


# --------------------------------------------------------------------------
# The scenario
# --------------------------------------------------------------------------


#: Longest execution id a generated scenario may carry. Bounded because the id
#: becomes a filesystem component; shortened rather than truncated because two
#: proposals must never collapse into one.
SCENARIO_ID_LIMIT = 64


#: The citation form, duplicated from :mod:`scenario_validation` rather than
#: imported, because that module imports this one. Kept narrow on purpose: this
#: is only ever used to read a token back out of a string the validator already
#: produced, never to decide anything.
_CITATION_TOKEN = re.compile(r"^@([0-9a-f]{8})(?=\s|$)")


class CommandBinding(BaseModel):
    """Which human-approved command one of this scenario's commands came from.

    Recorded when a ``@token`` citation is expanded, and read back only when a
    resume finds the expanded text is no longer approved. See
    :meth:`GeneratedScenario.bind_citations` for why the token alone is not
    enough, and :func:`rebind_to_approved` for what is done with it.
    """

    model_config = ConfigDict(extra="forbid")

    #: A path from :meth:`GeneratedScenario.command_slots`.
    field: str
    #: The token that was cited: a digest of the approved command's body AS IT
    #: WAS. Still checked first on resume, because an unchanged body is the
    #: common case and the exact one.
    token: str = ""
    #: The human-authored NAME the approved command was written under. This is
    #: the identity that survives a legitimate repair of the command's body.
    source_name: str = ""
    #: The model's own argument tail, which was never part of the approval and
    #: is judged on rebinding exactly as it was judged on approval.
    tail: str = ""


class GeneratedScenario(BaseModel):
    """One situation the driver decided is worth exercising."""

    model_config = ConfigDict(extra="forbid")

    #: The execution identity: filesystem-safe, bounded, and unique to this
    #: proposal. Derived from whatever the model wrote; see :meth:`_identity`.
    id: str
    #: What the model actually wrote, kept verbatim whenever ``id`` had to be
    #: shortened. Without it the shortening would be lossy in the one direction
    #: that matters — a reader could no longer tell which proposal an evidence
    #: directory belongs to.
    proposed_id: str = ""
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
    #: Which approved command each cited command field came from. Written by
    #: :meth:`bind_citations`; read by :func:`rebind_to_approved` on a resume.
    #: Absent on a plan written before this existed, which is exactly the state
    #: :func:`rebind_to_approved` reports as unreconstructable rather than
    #: guessing at.
    command_bindings: list[CommandBinding] = Field(default_factory=list)
    #: Set when a resume re-materialized this scenario against a repaired
    #: approved command. Its prior evidence was produced by the superseded
    #: measurement and may not satisfy the gate, so the run must execute it
    #: again. One line per rebound field.
    rebound_on_resume: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @model_validator(mode="before")
    @classmethod
    def _identity(cls, data: Any) -> Any:
        """Derive a bounded, unique execution id, and remember what was proposed.

        The id is used as a dict key, a filesystem component, an evidence
        reference, a rerun selector and a gate row. Truncating it to a fixed
        width made all five agree that two proposals were one: the second
        scenario's evidence overwrote the first's, the suite held one entry, and
        the gate reported every required scenario verified while a required
        scenario it had never heard of went unrun.

        So the id is *shortened*, not truncated — a readable prefix plus a
        digest of the full sanitised id (see
        :func:`~neyma_product_driver.evidence.shorten_preserving_identity`).
        Deterministic, so resume and aggregation still agree across processes;
        distinct, so two proposals never merge; and ``proposed_id`` keeps the
        original so the shortening stays auditable.
        """
        if not isinstance(data, dict):
            return data
        raw = data.get("id")
        if not isinstance(raw, str):
            return data
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")
        if not cleaned:
            raise ValueError("scenario id is empty after sanitisation")
        from .evidence import shorten_preserving_identity

        identity = shorten_preserving_identity(cleaned, SCENARIO_ID_LIMIT)
        data = dict(data)
        data["id"] = identity
        if not data.get("proposed_id") and identity != raw:
            data["proposed_id"] = raw
        return data

    # -- derived properties ------------------------------------------------

    @property
    def blocks_acceptance(self) -> bool:
        return self.priority.blocks_acceptance

    def mutates_local_state(self) -> bool:
        return bool(self.setup) or any(a.mutates_local_state() for a in self.actions)

    def addresses_local_app(self) -> bool:
        """Does this scenario need the product running to mean anything?

        True when it issues an HTTP request or drives a browser. Such a scenario
        is only meaningful with the local service up, so the compiler starts what
        serves it rather than leaving it to fail against a closed port.
        """
        for action in self.actions:
            if action.kind in {"request", "parallel_requests", "browser"}:
                return True
        return self.mode == "browser"

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

    def command_slots(self) -> "list[tuple[str, str, Callable[[str], None]]]":
        """Every place a command string can live, as ``(path, value, set)``.

        One place that knows the layout, so a caller that has to touch all of
        them — the planner resolving approved-command citations, the resume path
        rebinding a stale one — cannot miss a field. ``path`` is stable across
        processes and is what a persisted :class:`CommandBinding` refers to.
        """
        slots: list[tuple[str, str, Callable[[str], None]]] = []

        def at_list(target: list[str], index: int) -> "Callable[[str], None]":
            def setter(value: str) -> None:
                target[index] = value

            return setter

        def at_attr(obj: Any, name: str) -> "Callable[[str], None]":
            def setter(value: str) -> None:
                setattr(obj, name, value)

            return setter

        for index, command in enumerate(self.setup):
            slots.append((f"setup[{index}]", command, at_list(self.setup, index)))
        for index, command in enumerate(self.cleanup):
            slots.append((f"cleanup[{index}]", command, at_list(self.cleanup, index)))
        for index, action in enumerate(self.actions):
            if action.kind == "command" and action.command:
                slots.append(
                    (f"actions[{index}].command", action.command, at_attr(action, "command"))
                )
            if action.kind == "state_check" and action.state_check is not None:
                slots.append(
                    (
                        f"actions[{index}].state_check.command",
                        action.state_check.command,
                        at_attr(action.state_check, "command"),
                    )
                )
        for index, check in enumerate(self.persisted_state_checks):
            slots.append(
                (
                    f"persisted_state_checks[{index}].command",
                    check.command,
                    at_attr(check, "command"),
                )
            )
        return slots

    def rewrite_commands(self, resolve: "Callable[[str], str]") -> list[tuple[str, str]]:
        """Rewrite every command string in place through ``resolve``.

        Returns the ``(before, after)`` pairs it actually changed, so the run can
        record what it resolved rather than silently substituting.
        """
        changed: list[tuple[str, str]] = []
        for _path, before, assign in self.command_slots():
            after = resolve(before)
            if after != before:
                changed.append((before, after))
                assign(after)
        return changed

    def bind_citations(self, approved: Any) -> list[tuple[str, str]]:
        """Expand every ``@token`` citation AND record what it was a citation of.

        Expansion alone is lossy in the one direction that matters. Once
        ``@49eae6f0`` has become the 4,500 characters of oracle it named, the
        only identity the command has left is its own bytes — and a token is a
        digest of exactly those bytes. So when a human legitimately *repairs*
        the approved command (run 20260903-065810 corrected seven oracles in
        ``p6_m11_policy.yaml``), a resume across the repair can no longer tell
        that this scenario was built on the command that was repaired, as
        opposed to carrying a string nobody ever approved. Both look identical:
        "not in the approved set".

        The binding is what tells them apart. It is recorded here, at the moment
        the information still exists, and read back by
        :func:`rebind_to_approved`. It carries no authority of its own: it names
        an approved command, it does not approve one.
        """
        recorded: list[tuple[str, str]] = []
        for path, before, assign in self.command_slots():
            after = approved.expand(before)
            if after == before:
                continue
            assign(after)
            recorded.append((before, after))
            match = _CITATION_TOKEN.match((before or "").strip())
            if match is None:  # pragma: no cover - expand only rewrites citations
                continue
            self.command_bindings = [b for b in self.command_bindings if b.field != path]
            self.command_bindings.append(
                CommandBinding(
                    field=path,
                    token=match.group(1),
                    source_name=approved.name_for(approved.by_token.get(match.group(1), "")),
                    tail=(before or "").strip()[match.end() :],
                )
            )
        return recorded

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

    @property
    def key(self) -> str:
        """A stable identity that does not depend on what the model called it.

        ``id`` is whatever the generator wrote, and generators number each
        wave's list from R1 — so a three-wave run holds three different risks
        all called "R1". Anything keyed on ``id`` silently merges them, and a
        scenario citing "R1" names one of three things. This is derived from the
        risk itself, so it is unique to the risk and identical across resumes.
        """
        digest = hashlib.sha256(self.description.strip().encode("utf-8")).hexdigest()
        return f"{self.risk_category.value}:{digest[:10]}"

    def label(self) -> str:
        """How this risk is named in a brief and cited by a scenario."""
        return f"{self.id or self.key} ({self.key})"


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
    #: What the waves actually produced, before this plan narrowed it. Carried
    #: here so ``total_scenarios`` can never be read on its own: "0 generated
    #: case(s)" is true of a run nobody proposed anything to and of a run whose
    #: every proposal the harness failed to parse, and only one of those is a
    #: reason to relax.
    proposed: int = 0
    accepted: int = 0
    filtered: int = 0
    invalid: int = 0

    def render(self) -> str:
        cats = ", ".join(sorted(self.by_risk_category)) or "none"
        lines = [
            f"{self.total_scenarios} generated case(s)",
            f"generation: {self.proposed} proposed, {self.accepted} accepted for "
            f"execution, {self.filtered} filtered or deduplicated, {self.invalid} "
            "invalid (Product Driver could not read them)",
        ]
        if self.invalid:
            lines.append(
                f"WARNING: {self.invalid} proposed scenario(s) were discarded because this "
                "harness could not interpret its own generated schema or taxonomy. That is "
                "a generation-contract error, NOT an absence of proposed coverage, and it "
                "does not mean those situations are safe."
            )
        lines.append(f"risk categories exercised: {cats}")
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
    #: ``REJECTED_FILTERED`` or ``REJECTED_CONTRACT``. Defaulted to *filtered*
    #: deliberately: a plan written before this field existed, and every refusal
    #: path that is working as intended, must read as an ordinary decision. Only
    #: a stage that could not interpret the proposal sets the other value, and it
    #: sets it explicitly.
    kind: str = REJECTED_FILTERED

    @property
    def is_contract_failure(self) -> bool:
        return self.kind == REJECTED_CONTRACT


class WaveRecord(BaseModel):
    """One generation wave: what was asked, proposed, accepted and refused.

    The four counts below are kept apart on purpose, because collapsing them is
    exactly how a run reports zero generated scenarios when nine were proposed
    and the harness could not read any of them (Neyma recorded that shape as
    ``P6-D46`` after the P6/M6 re-verification run). "Nothing was proposed",
    "everything proposed was a duplicate", and "everything proposed was
    unreadable" are three different facts with three different responses, and a
    single number cannot carry them.
    """

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
    #: Approved-command citations this wave resolved, as
    #: ``"<scenario id>: @<token> -> <the human's command>"``. Recorded because
    #: a citation means the string that RAN is not the string the generator
    #: returned, and a reader of the plan must be able to see that substitution
    #: rather than infer it.
    resolved_citations: list[str] = Field(default_factory=list)

    @property
    def accepted_count(self) -> int:
        return len(self.accepted_ids)

    @property
    def contract_rejections(self) -> list[RejectedScenario]:
        """Candidates Product Driver could not interpret. A harness defect."""
        return [r for r in self.rejected if r.is_contract_failure]

    @property
    def filtered_rejections(self) -> list[RejectedScenario]:
        """Candidates Product Driver understood and decided against. Intended."""
        return [r for r in self.rejected if not r.is_contract_failure]

    def accounting(self) -> str:
        """Proposed / accepted / filtered / invalid, in one line, always all four."""
        return (
            f"{self.proposed} proposed, {self.accepted_count} accepted for execution, "
            f"{len(self.filtered_rejections)} filtered or deduplicated, "
            f"{len(self.contract_rejections)} invalid (Product Driver could not read them)"
        )


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
    #: Failures and clusters this run observed, carried in the plan so a resumed
    #: run can still check that an adaptive scenario names a cause that really
    #: happened. Without these, resume would have to trust the citation.
    observed_failure_ids: list[str] = Field(default_factory=list)
    observed_cluster_ids: list[str] = Field(default_factory=list)
    #: Scenario ids the run has already executed, and promotion candidates, so a
    #: resumed run continues from what happened rather than from nothing.
    executed_scenario_ids: list[str] = Field(default_factory=list)
    #: Risk category -> the permanent claims that declare they verify it, as
    #: ``"<scenario name>: <claim>"``. Read from the permanent scenario files'
    #: reviewed ``verifies:`` blocks and recorded here so the plan's own
    #: coverage accounting matches the gate's, and so a wave is never asked to
    #: generate a case duplicating coverage that already exists.
    #:
    #: A *planned* claim, never a result: whether the claim held is decided by
    #: execution and by :mod:`~neyma_product_driver.scenario_gate`.
    permanent_coverage: dict[str, list[str]] = Field(default_factory=dict)

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
        covered = {s.risk_category.value for s in self.scenarios} | set(
            self.permanent_coverage
        )
        self.coverage_summary = CoverageSummary(
            total_scenarios=len(self.scenarios),
            by_risk_category=by_cat,
            by_priority=by_pri,
            uncovered_risks=[
                r.description for r in self.risks if r.risk_category.value not in covered
            ],
            # Summed across every wave, so the plan's own summary carries the
            # four counts rather than the one that survived.
            proposed=sum(w.proposed for w in self.waves),
            accepted=sum(w.accepted_count for w in self.waves),
            filtered=sum(len(w.filtered_rejections) for w in self.waves),
            invalid=sum(len(w.contract_rejections) for w in self.waves),
        )

    def planned_gaps(self) -> list["IdentifiedRisk"]:
        """Acceptance-blocking risks nothing in this plan intends to exercise.

        The generator's input, not the gate's output: this asks what the plan
        *intends*, so a wave can be aimed at what is still missing instead of
        wandering. What was actually verified is decided from execution records
        by :func:`~neyma_product_driver.scenario_gate.risk_coverage`.
        """
        covered = {s.risk_category.value for s in self.scenarios} | set(
            self.permanent_coverage
        )
        return [
            r
            for r in self.risks
            if r.severity.blocks_acceptance and r.risk_category.value not in covered
        ]

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
            # The kind is printed, not inferred from the prose. "Product Driver
            # decided against this" and "Product Driver could not read this" are
            # the two facts a reader of this file most needs kept apart.
            lines += [
                f"  [{'INVALID — harness could not read it' if r.is_contract_failure else 'filtered'}] "
                f"{r.id or '(unnamed)'}: {'; '.join(r.reasons)}"
                for r in rejected
            ]
        lines += [
            "",
            "This describes verified coverage, not exhaustive correctness. Situations "
            "nobody thought of are not covered by anything above.",
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Re-materialization across a repaired harness
# --------------------------------------------------------------------------


class Rebinding(BaseModel):
    """One command field re-materialized against the current approved set."""

    model_config = ConfigDict(extra="forbid")

    field: str
    source_name: str
    before: str
    after: str

    def brief(self) -> str:
        return (
            f"{self.field} was re-materialized from the approved command "
            f"{self.source_name!r}, whose body the harness has since repaired"
        )


def rebind_to_approved(
    scenario: GeneratedScenario, approved: Any
) -> tuple[list[Rebinding], list[str]]:
    """Re-materialize a scenario's stale commands against the CURRENT approved set.

    Returns ``(rebindings, unreconstructable)``. A field appears in exactly one
    of them, and a field whose command is still approved as written appears in
    neither.

    THE PROBLEM THIS SOLVES. A run generates a scenario from an approved
    command, executes it, and then discovers a defect in the *measuring
    instrument* — the oracle itself is wrong. The founder repairs the oracle in
    the permanent scenario file and resumes the same run. The generated
    scenario's persisted command is now a string no human approves: its body is
    the superseded oracle. Three answers are available and two of them are
    wrong. Executing it anyway grandfathers a command current policy refuses,
    and measures with an instrument already proven defective. Deleting it
    destroys a verification obligation the run had committed to, takes its risk
    linkage and provenance with it, and leaves the run permanently unable to
    say what it had decided to verify — which is what run 20260903-065810 did to
    four cases. The third answer is this one: keep the obligation, discard the
    stale executable, and re-materialize the measurement from the repaired
    instrument.

    WHAT MAKES IT SAFE. Nothing here approves anything. The replacement text is
    read out of the current approved set and nowhere else, so what runs is a
    string a human wrote *now*; the stale body is discarded unexecuted; the
    model's argument tail is carried across unchanged and is judged by the same
    ``approves`` check it always was; and the result is handed back to
    :func:`compile_to_scenario` against the current approved set, which refuses
    it independently if any of that is wrong. A binding is a *name*, not a
    permission: a scenario edited on disk to cite a name it was not generated
    from gains nothing, because the name can only ever resolve to a command
    already approved. And a field with no binding, or one whose binding names
    something the current set does not have, is reported as unreconstructable
    rather than repaired — a secure inability to reconstruct must stay blocked.
    """
    rebindings: list[Rebinding] = []
    unreconstructable: list[str] = []
    bindings = {b.field: b for b in scenario.command_bindings}

    for path, before, assign in scenario.command_slots():
        ok, _why = approved.approves(before)
        if ok:
            continue
        binding = bindings.get(path)
        if binding is None:
            unreconstructable.append(
                f"{path} runs a command the current approved set refuses, and this run "
                "did not record which approved command it was built from, so there is "
                "nothing to re-materialize it against"
            )
            continue
        current = approved.by_name.get(binding.source_name, "")
        if not current:
            unreconstructable.append(
                f"{path} was built from the approved command {binding.source_name!r}, "
                "which the current approved set no longer offers under that name, so the "
                "measurement it made cannot be re-established"
            )
            continue
        after = f"{current}{binding.tail}"
        if after == before:
            unreconstructable.append(
                f"{path} was built from the approved command {binding.source_name!r} and "
                "already carries its current text, so what the current set refuses is not "
                "a stale body: it is this command"
            )
            continue
        ok, why = approved.approves(after)
        if not ok:
            unreconstructable.append(
                f"{path} was built from the approved command {binding.source_name!r}, and "
                f"re-materializing it is still refused: {why}"
            )
            continue
        assign(after)
        rebindings.append(
            Rebinding(
                field=path, source_name=binding.source_name, before=before, after=after
            )
        )
    return rebindings, unreconstructable


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
        if not wanted and generated.addresses_local_app():
            # `service_refs` says which services this scenario may *operate on*
            # — restart, stop, start — which is how the generator is told to read
            # it. It was also, silently, the list of services that get started at
            # all, so a scenario that merely issued requests against `app_url`
            # and named no service ran against nothing: no service, a vacuously
            # satisfied readiness check, and then Connection refused on every
            # request. Those look exactly like product failures and are not.
            #
            # A scenario that addresses the local product gets whatever serves
            # it. It still may not operate on a service it did not declare, and
            # it still cannot introduce a service of its own.
            wanted = set(declared)
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
        # `verifies` is deliberately absent, and its absence is the rule. A
        # ``verifies:`` claim is a human's reviewed statement that a scenario
        # verifies a named risk; letting a generated scenario carry one would
        # let a model declare its own coverage and have the acceptance gate
        # believe it. There is no field on GeneratedScenario that could produce
        # one, and there must not be.
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
