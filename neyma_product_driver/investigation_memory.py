"""Investigation records and their persistence.

The investigator does not live inside one long model conversation. Its entire
state — what it has observed, what contradicts what, which hypotheses are alive,
which probes it ran, and how the diagnosis moved — is written to disk after every
step and reloaded before the next one. A crash, a restart, or a fresh session
picks up exactly where it left off.

The distinctions these records exist to keep straight:

    a fact              != an inference drawn from it
    an inference        != a hypothesis that would explain it
    a plausible story   != a supported one
    a builder's prose   != primary evidence
    "sounds right"      != "survived a probe designed to refute it"

Nothing here writes to the Neyma repository. Everything lands under
``runs/<run-id>/investigation/``.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import redact_obj, utcnow


# --------------------------------------------------------------------------
# Epistemic taxonomy
# --------------------------------------------------------------------------


class EpistemicStatus(str, Enum):
    """What kind of claim something is. Never blurred.

    A hypothesis is not promoted to FACT because it is plausible; it becomes
    supported only when a probe designed to refute it fails to.
    """

    FACT = "FACT"
    INFERENCE = "INFERENCE"
    HYPOTHESIS = "HYPOTHESIS"
    DISPROVEN = "DISPROVEN"
    UNRESOLVED = "UNRESOLVED"
    DECISION_REQUIRED = "DECISION_REQUIRED"


class Reliability(str, Enum):
    """How much a single observation can be trusted on its own.

    ``REPORTED`` is prose — a builder or reviewer saying something happened.
    It can raise suspicion; it can never, by itself, confirm a hypothesis. Only
    ``DIRECT`` primary evidence can do that.
    """

    DIRECT = "DIRECT"  # primary evidence: a command ran, a file exists, a test failed
    DERIVED = "DERIVED"  # an inference the driver computed from direct evidence
    REPORTED = "REPORTED"  # a claim in prose; trust nothing until a probe confirms it

    @property
    def weight(self) -> float:
        return {Reliability.DIRECT: 1.0, Reliability.DERIVED: 0.6, Reliability.REPORTED: 0.25}[self]


class ObservationKind(str, Enum):
    TEST_RESULT = "TEST_RESULT"
    LOG = "LOG"
    GIT_STATE = "GIT_STATE"
    FILE_CONTENT = "FILE_CONTENT"
    COMMAND_RESULT = "COMMAND_RESULT"
    BUILDER_CLAIM = "BUILDER_CLAIM"
    REVIEWER_CLAIM = "REVIEWER_CLAIM"
    ENVIRONMENT = "ENVIRONMENT"
    PRODUCT_BEHAVIOR = "PRODUCT_BEHAVIOR"
    OTHER = "OTHER"


class HypothesisStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPPORTED = "SUPPORTED"
    WEAKENED = "WEAKENED"
    DISPROVEN = "DISPROVEN"
    UNRESOLVED = "UNRESOLVED"

    @property
    def rank(self) -> int:
        return {
            HypothesisStatus.SUPPORTED: 4,
            HypothesisStatus.ACTIVE: 3,
            HypothesisStatus.WEAKENED: 2,
            HypothesisStatus.UNRESOLVED: 1,
            HypothesisStatus.DISPROVEN: 0,
        }[self]

    @property
    def is_alive(self) -> bool:
        return self in (HypothesisStatus.ACTIVE, HypothesisStatus.SUPPORTED, HypothesisStatus.WEAKENED)


# --------------------------------------------------------------------------
# Core records
# --------------------------------------------------------------------------

# A signal is one machine-comparable fact a probe or observation surfaces —
# ``finalizer_exit=1``, ``content_commit_count=2``, ``socket_tests=pass``. The
# engine reasons over signals; hypotheses predict them. Free-text prose is never
# the thing compared, because prose is the cheapest thing to get wrong.
CLAIM_SIGNAL = "claim"


class Observation(BaseModel):
    """One thing the investigator saw. A fact, not a story about the fact."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    source: str = ""
    content: str = ""
    evidence_path: str = ""
    timestamp: str = Field(default_factory=utcnow)
    reliability: Reliability = Reliability.DIRECT
    kind: ObservationKind = ObservationKind.OTHER
    # Machine-comparable facts extracted from this observation.
    signals: dict[str, str] = Field(default_factory=dict)
    iteration: int = 0

    @property
    def epistemic(self) -> EpistemicStatus:
        return EpistemicStatus.FACT if self.reliability is Reliability.DIRECT else EpistemicStatus.INFERENCE

    def brief(self) -> str:
        tag = {
            Reliability.DIRECT: "fact",
            Reliability.DERIVED: "inference",
            Reliability.REPORTED: "reported",
        }[self.reliability]
        sig = f"  [{', '.join(f'{k}={v}' for k, v in self.signals.items())}]" if self.signals else ""
        return f"({tag}) {self.content}{sig}"


class Contradiction(BaseModel):
    """Two statements that cannot both be true. The engine's raw material."""

    model_config = ConfigDict(extra="ignore")

    statement_a: str
    statement_b: str
    evidence_a: str = ""
    evidence_b: str = ""
    significance: str = ""
    signal_key: str = ""

    def render(self) -> str:
        return (
            f"{self.statement_a}\n  vs {self.statement_b}"
            + (f"\n  significance: {self.significance}" if self.significance else "")
        )


class Prediction(BaseModel):
    """What a hypothesis says a probe would find if it were true.

    Written as ``key<op>value`` where op is ``=`` (equal), ``!=`` (unequal) or
    ``~=`` (regex). This is the forward model the engine tests against reality,
    and the reason a hypothesis can be *refuted* rather than merely doubted.
    """

    model_config = ConfigDict(extra="ignore")

    expression: str

    def evaluate(self, signals: dict[str, str]) -> str | None:
        """Return 'support', 'contradict', or None (probe said nothing to it)."""
        return match_prediction(self.expression, signals)


class Hypothesis(BaseModel):
    """A candidate explanation. Alive until refuted, never true until supported."""

    model_config = ConfigDict(extra="ignore")

    id: str
    statement: str
    category: str = ""
    supporting_evidence: list[str] = Field(default_factory=list)
    opposing_evidence: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = 0.3
    status: HypothesisStatus = HypothesisStatus.ACTIVE
    predicted_observations: list[str] = Field(default_factory=list)
    next_probe: str = ""
    # True once a DIRECT (measured) observation has supported this hypothesis.
    # Prose support never sets it, so an elimination win can require real
    # evidence for the survivor rather than a mere absence of contradiction.
    has_direct_support: bool = False

    @property
    def epistemic(self) -> EpistemicStatus:
        return {
            HypothesisStatus.SUPPORTED: EpistemicStatus.INFERENCE,
            HypothesisStatus.DISPROVEN: EpistemicStatus.DISPROVEN,
            HypothesisStatus.UNRESOLVED: EpistemicStatus.UNRESOLVED,
        }.get(self.status, EpistemicStatus.HYPOTHESIS)

    def predictions(self) -> list[Prediction]:
        return [Prediction(expression=e) for e in self.predicted_observations]

    def clamp(self) -> None:
        object.__setattr__(self, "confidence", max(0.0, min(1.0, self.confidence)))


class InterpretationRule(BaseModel):
    """How to turn one probe's raw output into a comparable signal.

    A rule extracts a ``signal`` from output matching ``pattern`` (using a capture
    group, or a literal ``value``). The engine then matches that signal against
    every live hypothesis's predictions — the probe never has to know which
    hypotheses exist.
    """

    model_config = ConfigDict(extra="ignore")

    pattern: str
    signal: str
    value: str = ""  # literal value, or "$1" for the first capture group
    note: str = ""


class Probe(BaseModel):
    """The smallest action that would move the diagnosis. Read-only by default."""

    model_config = ConfigDict(extra="ignore")

    id: str
    question: str
    rationale: str = ""
    # kind + command_or_action drive execution; see probe_runner.ProbeRunner.
    kind: str = "COMMAND"
    command_or_action: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_results: str = ""
    interpretation_rules: list[InterpretationRule] = Field(default_factory=list)
    risk: str = "low"
    estimated_cost: str = "cheap"
    timeout: int = 60
    read_only: bool = True
    targets_hypotheses: list[str] = Field(default_factory=list)

    def fingerprint(self) -> str:
        """Identity for repeat detection: same action, same inputs."""
        payload = json.dumps(
            {"kind": self.kind, "action": self.command_or_action, "inputs": self.inputs},
            sort_keys=True,
            default=str,
        )
        return payload


class ProbeResult(BaseModel):
    """What running one probe produced."""

    model_config = ConfigDict(extra="ignore")

    probe_id: str
    ran: bool = False
    signals: dict[str, str] = Field(default_factory=dict)
    output: str = ""
    exit_code: int | None = None
    duration_s: float = 0.0
    error: str = ""
    refused: bool = False
    refusal_reason: str = ""
    evidence_path: str = ""

    def result_fingerprint(self) -> str:
        return json.dumps(
            {"signals": self.signals, "exit": self.exit_code, "refused": self.refused},
            sort_keys=True,
        )


class HypothesisTransition(BaseModel):
    """One recorded change of belief, so the timeline shows *how* it moved."""

    model_config = ConfigDict(extra="ignore")

    iteration: int
    hypothesis_id: str
    from_status: HypothesisStatus
    to_status: HypothesisStatus
    from_confidence: float
    to_confidence: float
    reason: str = ""
    timestamp: str = Field(default_factory=utcnow)


class Challenge(BaseModel):
    """A fresh perspective's attempt to refute the current diagnosis."""

    model_config = ConfigDict(extra="ignore")

    challenges_hypothesis: str = ""
    alternative: str = ""
    reasoning: str = ""
    proposed_probe: Probe | None = None
    changed_the_diagnosis: bool = False


class InvestigationStatus(str, Enum):
    ROOT_CAUSE_FOUND = "ROOT_CAUSE_FOUND"
    PARTIAL_DIAGNOSIS = "PARTIAL_DIAGNOSIS"
    NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"
    ASK_USER = "ASK_USER"
    BLOCKED = "BLOCKED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class AskFounder(BaseModel):
    """A consequential action the investigator will not take on its own."""

    model_config = ConfigDict(extra="ignore")

    what: str
    why: str
    exact_command: str = ""
    expected_benefit: str = ""
    risk: str = ""
    rollback: str = ""

    def render(self) -> str:
        return "\n".join(
            [
                "ASK FOUNDER",
                f"  what:     {self.what}",
                f"  why:      {self.why}",
                f"  command:  {self.exact_command}",
                f"  benefit:  {self.expected_benefit}",
                f"  risk:     {self.risk}",
                f"  rollback: {self.rollback}",
            ]
        )


class InvestigationResult(BaseModel):
    """The investigator's verdict. A diagnosis with its epistemic status attached."""

    model_config = ConfigDict(extra="ignore")

    status: InvestigationStatus
    root_cause: str = ""
    contributing_causes: list[str] = Field(default_factory=list)
    rejected_hypotheses: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    recommended_action: str = ""
    confidence: float = 0.0
    next_verification: str = ""
    ask_founder: AskFounder | None = None
    iterations_used: int = 0

    @property
    def found_root_cause(self) -> bool:
        return self.status is InvestigationStatus.ROOT_CAUSE_FOUND

    def summary_block(self) -> str:
        lines = [
            f"INVESTIGATION RESULT: {self.status.value.replace('_', ' ')}",
        ]
        if self.root_cause:
            lines += ["", "Root cause:", f"  {self.root_cause}"]
        if self.contributing_causes:
            lines += ["", "Contributing:"]
            lines += [f"  - {c}" for c in self.contributing_causes]
        lines += ["", f"Confidence: {self.confidence:.2f}"]
        if self.rejected_hypotheses:
            lines += ["", "Rejected (do not repeat):"]
            lines += [f"  - {r}" for r in self.rejected_hypotheses]
        if self.evidence:
            lines += ["", "Evidence:"]
            lines += [f"  - {e}" for e in self.evidence[:8]]
        if self.recommended_action:
            lines += ["", "Recommended next action:", f"  {self.recommended_action}"]
        if self.unresolved_questions:
            lines += ["", "Unresolved:"]
            lines += [f"  - {q}" for q in self.unresolved_questions]
        if self.ask_founder is not None:
            lines += ["", self.ask_founder.render()]
        return "\n".join(lines)


class InvestigationState(BaseModel):
    """The whole live investigation, persisted verbatim after each step."""

    model_config = ConfigDict(extra="ignore")

    run_id: str = ""
    issue: str = ""
    trigger: str = ""
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    iteration: int = 0
    max_iterations: int = 8
    hard_cap: int = 16
    repo_fingerprint: str = ""
    repo_changed: bool = False

    observations: list[Observation] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    probes: list[Probe] = Field(default_factory=list)
    probe_results: list[ProbeResult] = Field(default_factory=list)
    transitions: list[HypothesisTransition] = Field(default_factory=list)
    challenges: list[Challenge] = Field(default_factory=list)
    diagnosis_changes: int = 0
    result: InvestigationResult | None = None

    def touch(self) -> None:
        self.updated_at = utcnow()

    def hypothesis(self, hid: str) -> Hypothesis | None:
        for h in self.hypotheses:
            if h.id == hid:
                return h
        return None

    def alive_hypotheses(self) -> list[Hypothesis]:
        return [h for h in self.hypotheses if h.status.is_alive]

    def ranked(self) -> list[Hypothesis]:
        return sorted(
            self.hypotheses, key=lambda h: (h.status.rank, h.confidence), reverse=True
        )

    def leading(self) -> Hypothesis | None:
        alive = [h for h in self.ranked() if h.status.is_alive]
        return alive[0] if alive else None


# --------------------------------------------------------------------------
# Prediction matching — the one piece of domain-blind reasoning
# --------------------------------------------------------------------------

_PRED_RE = re.compile(r"^\s*([\w.\-/]+)\s*(!=|~=|=)\s*(.*?)\s*$")


def match_prediction(expression: str, signals: dict[str, str]) -> str | None:
    """Evaluate ``key<op>value`` against observed signals.

    Returns ``"support"`` when reality matches the prediction, ``"contradict"``
    when reality speaks to the same signal but disagrees, and ``None`` when no
    probe has spoken to that signal yet. Plain text with no operator is treated
    as a substring expectation over a special ``_text`` signal, if present.
    """
    m = _PRED_RE.match(expression or "")
    if not m:
        return None
    key, op, expected = m.group(1), m.group(2), m.group(3)
    if key not in signals:
        return None
    actual = str(signals[key])
    expected = str(expected)

    if op == "=":
        return "support" if _eq(actual, expected) else "contradict"
    if op == "!=":
        return "contradict" if _eq(actual, expected) else "support"
    if op == "~=":
        try:
            return "support" if re.search(expected, actual, re.I) else "contradict"
        except re.error:
            return "support" if expected.lower() in actual.lower() else "contradict"
    return None


def _eq(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


class InvestigationMemory:
    """Filesystem-backed investigation state under ``runs/<id>/investigation/``."""

    DIRNAME = "investigation"

    def __init__(self, run_dir: Path) -> None:
        self.dir = Path(run_dir) / self.DIRNAME
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- paths -------------------------------------------------------------

    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    def _write_json(self, name: str, data: Any) -> None:
        (self.dir / name).write_text(
            json.dumps(redact_obj(data), indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )

    # -- saving ------------------------------------------------------------

    def save(self, state: InvestigationState) -> None:
        """Persist the whole investigation, one file per record family."""
        state.touch()
        self._write_json("state.json", state.model_dump(mode="json"))
        self._write_json("observations.json", [o.model_dump(mode="json") for o in state.observations])
        self._write_json(
            "contradictions.json", [c.model_dump(mode="json") for c in state.contradictions]
        )
        self._write_json("hypotheses.json", [h.model_dump(mode="json") for h in state.hypotheses])
        self._write_json(
            "probes.json",
            {
                "probes": [p.model_dump(mode="json") for p in state.probes],
                "results": [r.model_dump(mode="json") for r in state.probe_results],
            },
        )
        if state.result is not None:
            self._write_json("result.json", state.result.model_dump(mode="json"))
        (self.dir / "timeline.md").write_text(render_timeline(state), encoding="utf-8")

    def load(self) -> InvestigationState | None:
        if not self.state_path.exists():
            return None
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return InvestigationState.model_validate(raw)
        except (OSError, ValueError):
            return None


# --------------------------------------------------------------------------
# Timeline — shows how the diagnosis moved, not just where it landed
# --------------------------------------------------------------------------


def render_timeline(state: InvestigationState) -> str:
    lines: list[str] = [
        f"# Investigation timeline — {state.run_id or '(no run)'}",
        "",
        f"Issue: {state.issue or '(open-ended)'}",
        f"Trigger: {state.trigger or '(manual)'}",
        f"Iterations: {state.iteration}/{state.max_iterations}"
        + (f" (extended, hard cap {state.hard_cap})" if state.iteration > state.max_iterations else ""),
    ]
    if state.repo_changed:
        lines.append("**The repository changed during this investigation — earlier observations may be stale.**")

    lines += ["", "## Hypotheses"]
    for h in state.ranked():
        lines.append(
            f"- **{h.id}** [{h.status.value} · {h.epistemic.value} · conf {h.confidence:.2f}] {h.statement}"
        )
        for e in h.supporting_evidence[:4]:
            lines.append(f"    + {e}")
        for e in h.opposing_evidence[:4]:
            lines.append(f"    − {e}")

    if state.contradictions:
        lines += ["", "## Contradictions"]
        for c in state.contradictions:
            lines.append(f"- {c.statement_a}  ⟂  {c.statement_b}")

    lines += ["", "## How the diagnosis moved"]
    if not state.transitions:
        lines.append("- (no belief changes recorded yet)")
    for t in state.transitions:
        arrow = f"{t.from_status.value}→{t.to_status.value}"
        lines.append(
            f"- it{t.iteration}: {t.hypothesis_id} {arrow} "
            f"(conf {t.from_confidence:.2f}→{t.to_confidence:.2f}) — {t.reason}"
        )

    if state.probe_results:
        lines += ["", "## Probes run"]
        by_id = {p.id: p for p in state.probes}
        for r in state.probe_results:
            probe = by_id.get(r.probe_id)
            q = probe.question if probe else r.probe_id
            outcome = (
                "REFUSED: " + r.refusal_reason
                if r.refused
                else ", ".join(f"{k}={v}" for k, v in r.signals.items()) or "(no signals)"
            )
            lines.append(f"- {r.probe_id}: {q} → {outcome}")

    if state.result is not None:
        lines += ["", "## Result", "```", state.result.summary_block(), "```"]

    return "\n".join(lines)
