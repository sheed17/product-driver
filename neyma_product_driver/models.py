"""Structured types shared across the driver.

Everything the evaluator returns, everything the runner observes, and everything
persisted to ``runs/<run-id>/`` is modelled here so that state on disk stays
readable and validated.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> str:
    """Timestamp used for every persisted record."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Decision(str, Enum):
    """The only four outcomes the evaluator may return."""

    ACCEPT = "ACCEPT"
    FIX = "FIX"
    ASK_USER = "ASK_USER"
    BLOCKED = "BLOCKED"


class EvaluatorDecision(BaseModel):
    """The evaluator's structured verdict for one iteration.

    ``correction_prompt`` is required for FIX and must be empty otherwise, so a
    stale correction can never leak into an ACCEPT.

    The grounding fields (``requirement_reference`` … ``retest``) implement the
    prompt-quality contract: a FIX must name a repository requirement or product
    principle, the scenario that was run, what was observed, what was expected,
    what must be preserved, and exactly how to retest. They are validated by
    :func:`neyma_product_driver.prompts.validate_correction_quality` rather than
    here, so a malformed reply becomes a recorded BLOCKED instead of an
    exception.
    """

    model_config = ConfigDict(extra="ignore")

    decision: Decision
    summary: str = ""
    observed_behavior: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    correction_prompt: str = ""
    evidence_paths: list[str] = Field(default_factory=list)
    confidence: float = 0.0

    # -- prompt-quality contract ------------------------------------------
    requirement_reference: str = ""
    product_principle_reference: str = ""
    scenario: str = ""
    observed_result: str = ""
    expected_result: str = ""
    preserve: str = ""
    retest: str = ""

    # -- escalation metadata ----------------------------------------------
    customer_facing: bool = False
    rubric_categories: list[str] = Field(default_factory=list)

    # -- adaptive verification --------------------------------------------
    #: The evaluator's judgement that the coverage exercised so far does not yet
    #: cover the risk surface. Advisory: it requests further verification, it
    #: never changes the four terminal outcomes above, and an ACCEPT carrying it
    #: is still an ACCEPT unless a required scenario actually failed.
    additional_verification_needed: bool = False
    #: Plain-language situations the evaluator wants exercised next. These are
    #: an input to scenario generation, never executable instructions.
    scenario_requests: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @field_validator(
        "observed_behavior",
        "problems",
        "evidence_paths",
        "rubric_categories",
        "scenario_requests",
        mode="before",
    )
    @classmethod
    def _coerce_list(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return v

    @field_validator(
        "correction_prompt",
        "summary",
        "requirement_reference",
        "product_principle_reference",
        "scenario",
        "observed_result",
        "expected_result",
        "preserve",
        "retest",
        mode="before",
    )
    @classmethod
    def _coerce_str(cls, v: Any) -> str:
        return "" if v is None else str(v)

    def model_post_init(self, _ctx: Any) -> None:
        if self.decision is Decision.FIX and not self.correction_prompt.strip():
            raise ValueError("decision=FIX requires a non-empty correction_prompt")
        if self.decision is not Decision.FIX and self.correction_prompt.strip():
            # A correction prompt outside FIX is meaningless; drop it rather than
            # risk sending it to the builder.
            object.__setattr__(self, "correction_prompt", "")


class CommandResult(BaseModel):
    """Outcome of one shell command executed by the runner."""

    command: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    timed_out: bool = False
    cwd: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def brief(self, limit: int = 2000) -> str:
        status = "TIMEOUT" if self.timed_out else f"exit={self.exit_code}"
        out = (self.stdout or "").strip()[-limit:]
        err = (self.stderr or "").strip()[-limit:]
        parts = [f"$ {self.command}  [{status}, {self.duration_s:.1f}s]"]
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        return "\n".join(parts)


class HttpObservation(BaseModel):
    """One API request/response pair captured during a backend scenario."""

    name: str = ""
    method: str = "GET"
    url: str = ""
    status: int | None = None
    error: str | None = None
    body_text: str = ""
    json_body: Any = None
    duration_s: float = 0.0


class BrowserTextExpectation(BaseModel):
    """One ``expect_text`` step and whether the page satisfied it.

    Recorded structurally rather than only narrated into ``steps``. A narration
    line saying ``NOT FOUND`` reads like a failure and is not one: nothing scores
    it, so a browser scenario whose only oracle was ``expect_text`` could not
    fail whatever the page said.
    """

    text: str
    present: bool
    #: Which step in the browser sequence asked for it, for attribution.
    step: int = 0
    label: str = ""


class BrowserObservation(BaseModel):
    """What Playwright actually saw on the page."""

    url: str = ""
    title: str = ""
    visible_text: str = ""
    screenshots: list[str] = Field(default_factory=list)
    console_errors: list[str] = Field(default_factory=list)
    network_failures: list[str] = Field(default_factory=list)
    trace_path: str | None = None
    steps: list[str] = Field(default_factory=list)
    #: Every ``expect_text`` checked while driving the page, in order.
    text_expectations: list[BrowserTextExpectation] = Field(default_factory=list)
    #: Steps that raised — a click on a selector that is not there, a navigation
    #: that timed out. Recorded structurally for the same reason as
    #: ``text_expectations``: a step that did not happen is not a step that
    #: passed, and narration alone is not scored.
    step_failures: list[str] = Field(default_factory=list)


class AssertionResult(BaseModel):
    """A single scenario expectation and whether the observation satisfied it."""

    kind: Literal["expect_visible", "expect_state", "forbidden"]
    target: str
    passed: bool
    detail: str = ""


class ScenarioResult(BaseModel):
    """Everything one scenario execution observed. Feeds the evaluator."""

    scenario_name: str
    mode: Literal["backend", "browser"] = "backend"
    started_at: str = Field(default_factory=utcnow)
    setup: list[CommandResult] = Field(default_factory=list)
    services_started: list[str] = Field(default_factory=list)
    readiness_ok: bool = True
    readiness_detail: str = ""
    commands: list[CommandResult] = Field(default_factory=list)
    http: list[HttpObservation] = Field(default_factory=list)
    browser: BrowserObservation | None = None
    assertions: list[AssertionResult] = Field(default_factory=list)
    teardown: list[CommandResult] = Field(default_factory=list)
    error: str | None = None

    # -- ordered-step scenarios -------------------------------------------
    #: The ordered operations actually performed, in order. Empty for a
    #: phase-form scenario, whose ordering is fixed and therefore implicit.
    steps_performed: list[str] = Field(default_factory=list)
    #: Absolute paths of temporary fixtures materialized during the run.
    fixtures_written: list[str] = Field(default_factory=list)
    #: Set when this result came from a generated scenario, so evidence can be
    #: traced back to the plan entry that asked for it.
    scenario_id: str = ""
    origin: Literal["permanent", "generated"] = "permanent"

    @property
    def passed(self) -> bool:
        return (
            self.error is None
            and self.readiness_ok
            and all(a.passed for a in self.assertions)
        )

    def failed_assertions(self) -> list[AssertionResult]:
        return [a for a in self.assertions if not a.passed]


class GitSnapshot(BaseModel):
    """Read-only view of the Neyma working tree at one moment."""

    branch: str = ""
    status_porcelain: str = ""
    diff_stat: str = ""
    head_commit: str = ""
    dirty_file_count: int = 0


class IterationRecord(BaseModel):
    """One full pass of the control loop, persisted verbatim."""

    iteration: int
    timestamp: str = Field(default_factory=utcnow)
    builder_session_id: str | None = None
    evaluator_session_id: str | None = None
    builder_summary: str = ""
    git: GitSnapshot | None = None
    scenario: ScenarioResult | None = None
    decision: EvaluatorDecision | None = None
    raw_decision: EvaluatorDecision | None = None  # pre-validation, when rejected
    correction_prompt_sent: str = ""
    paused_permission_requests: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Populated by the context layer; typed loosely to avoid a circular import.
    context_provenance: dict[str, Any] | None = None
    rejected_reasons: list[str] = Field(default_factory=list)
    #: Aggregate ScenarioSuite outcome for this iteration, when the run used
    #: one. Typed loosely, like the other cross-layer records below, so the
    #: suite layer can evolve without a circular import.
    suite: dict[str, Any] | None = None
    completion_audit: dict[str, Any] | None = None
    protocol_resolution: dict[str, Any] | None = None
    investigation: dict[str, Any] | None = None
    independent_review: dict[str, Any] | None = None


class RunStatus(str, Enum):
    RUNNING = "RUNNING"
    ACCEPTED = "ACCEPTED"
    NEEDS_USER = "NEEDS_USER"
    BLOCKED = "BLOCKED"
    MAX_ITERATIONS = "MAX_ITERATIONS"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    # Implementation stands, acceptance does not. Neither failure nor completion.
    NEEDS_INDEPENDENT_REVIEW = "NEEDS_INDEPENDENT_REVIEW"
    # Repository governance blocks finalization, and clearing it needs a human
    # decision about history or authority. Also neither failure nor completion.
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"


class RunState(BaseModel):
    """Persisted run state; enables ``status``, ``stop`` and resume-after-restart."""

    run_id: str
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    status: RunStatus = RunStatus.RUNNING
    neyma_repo: str = ""
    scenario_name: str = ""
    task: str = ""
    builder_session_id: str | None = None
    evaluator_session_id: str | None = None
    iteration: int = 0
    max_iterations: int = 5
    iterations: list[IterationRecord] = Field(default_factory=list)
    final_decision: EvaluatorDecision | None = None
    stop_requested: bool = False
    pid: int | None = None

    def touch(self) -> None:
        self.updated_at = utcnow()


# --- Redaction -------------------------------------------------------------
#
# Applied to every string before it is written to disk, printed, or handed to
# the evaluator. Deliberately blunt: a false positive costs a masked token, a
# false negative persists a credential.

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "[REDACTED:anthropic-key]"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[REDACTED:api-key]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"), "[REDACTED:github-token]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED:github-token]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "[REDACTED:slack-token]"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"), "[REDACTED:jwt]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws-key]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED:private-key]"),
    (re.compile(r"(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s:@/]+:[^\s@]+@"), r"\1://[REDACTED:credentials]@"),
    # KEY=value / "key": "value" forms for anything that smells like a secret.
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE[_-]?KEY)[A-Z0-9_]*)"
            r"(\s*[=:]\s*)(\"?)([^\s\"',;]{4,})(\3)"
        ),
        r"\1\2\3[REDACTED]\5",
    ),
]

_SAFE_ENV_MARKER = "[REDACTED:environment-dump]"


def redact(text: str | None) -> str:
    """Mask credential-shaped substrings. Never raises."""
    if not text:
        return ""
    out = str(text)
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def redact_obj(obj: Any) -> Any:
    """Recursively redact strings inside dicts/lists, masking secret-ish keys."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        result: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and re.search(
                r"(?i)(api[_-]?key|secret|token|password|passwd|credential|private[_-]?key|authorization)",
                k,
            ):
                result[k] = "[REDACTED]"
            else:
                result[k] = redact_obj(v)
        return result
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj


def looks_like_env_dump(text: str) -> bool:
    """Heuristic guard against persisting a full ``env`` output."""
    if not text:
        return False
    lines = [ln for ln in text.splitlines() if re.match(r"^[A-Z][A-Z0-9_]{2,}=", ln)]
    return len(lines) >= 10
