"""Completion-claim auditor.

The builder saying something is done is a *claim*, not a fact. This module
independently checks every completion claim against the Neyma repository: the
registry, the status surfaces, the actual receipt files, git state, and the test
output produced by this run.

The distinctions it enforces:

    code exists                  != behaviour proven
    tests exist                  != tests passed
    tests passed                 != acceptance contract passed
    targeted suite passed        != canonical suite passed
    implementer review           != independent review
    independent review           != final adjudication
    finalizer claimed            != finalizer ran
    clean-clone PASS claimed     != clean-clone gate ran
    weighted criteria PENDING    => phase is NOT COMPLETE
    a status document            cannot prove itself
    confident prose              cannot substitute for missing evidence
    dirty-tree skip              != ordinary approved skip
    checkpoint commit            != final content commit
    content commit               != required status-metadata commit
    later phase READY            requires its blockers actually closed

**What it requires follows the target repository.** The distinctions above are
about *honesty*, and honesty checks are unconditional: a cited file that does
not exist, a percentage the criteria do not support, a status surface that
contradicts the registry, a full-suite claim resting on targeted tests. The
checks that demand a *ceremony artifact* — a canonical-suite receipt, a
finalizer receipt, a clean-clone receipt — are asked only when the repository
still states the rule that makes that artifact mandatory, read fresh on every
audit via :func:`~neyma_product_driver.protocol_sources.discover_protocol`. A
repository that deletes its finalizer protocol stops being asked for a finalizer
receipt, without a change here. The auditor never invents a requirement the
repository has stopped stating; it only refuses to believe a claim the
repository refutes.

A repository that declares no unit registry at all is audited too: the
registry-derived checks simply have nothing to say, and the honesty checks still
run against the report.

Nothing here writes to the Neyma repository.
"""

from __future__ import annotations

import json
import re
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .context import ActiveUnit, RepositoryContextLoader
from .models import redact, utcnow
from .task_scope import ScopedCompletion, TaskResult, TaskScope, scoped_completion

# --------------------------------------------------------------------------
# Repository locations
# --------------------------------------------------------------------------

REGISTRY_REL = "docs/implementation/IMPLEMENTATION-REGISTRY.yaml"
CURRENT_REL = "docs/implementation/CURRENT.md"
BUILD_STATUS_REL = "docs/implementation/BUILD-STATUS.yaml"
SUITE_RESULT_REL = "docs/implementation/SUITE-RESULT.json"
GATE_RESULT_REL = "docs/implementation/GATE-RESULT.json"
APPROVED_SKIPS_REL = "docs/implementation/APPROVED-SKIPS.yaml"

# Narrative surfaces that must not claim completion ahead of the registry.
NARRATIVE_SURFACES = ("CURRENT.md", "CLAUDE.md", "README.md", "AGENTS.md")

# Criteria that, by construction, cannot be awarded by the implementing session.
INDEPENDENT_CRITERIA = ("independent_review", "final_adjudication")

# A criterion contributes weight only when it actually passed.
PASSING_RESULTS = ("PASS", "PASSED", "COMPLETE")


class ClaimType(str, Enum):
    PHASE_COMPLETE = "PHASE_COMPLETE"
    CRITERION_PASS = "CRITERION_PASS"
    IMPLEMENTATION_COMPLETE = "IMPLEMENTATION_COMPLETE"
    TESTS_PASSED = "TESTS_PASSED"
    FULL_SUITE_PASSED = "FULL_SUITE_PASSED"
    FINALIZER_RAN = "FINALIZER_RAN"
    CLEAN_CLONE_PASSED = "CLEAN_CLONE_PASSED"
    INDEPENDENT_REVIEW = "INDEPENDENT_REVIEW"
    ADJUDICATION = "ADJUDICATION"
    RISK_CONTAINED = "RISK_CONTAINED"
    PROGRESS_PERCENT = "PROGRESS_PERCENT"
    READY_ADVANCE = "READY_ADVANCE"
    COMMIT_STRUCTURE = "COMMIT_STRUCTURE"
    #: A claim that work beyond this run's scope is now open — the next phase
    #: unblocked, a later unit cleared to begin.
    NEXT_PHASE_UNBLOCKED = "NEXT_PHASE_UNBLOCKED"
    #: A claim that something is live, enabled, or in production. The most
    #: consequential claim a build session can make, and the one a build session
    #: is least able to establish.
    LIVE_ENABLEMENT = "LIVE_ENABLEMENT"


class AuditDecision(str, Enum):
    VERIFIED = "VERIFIED"
    CONTRADICTED = "CONTRADICTED"
    UNPROVEN = "UNPROVEN"
    REQUIRES_INDEPENDENT_REVIEW = "REQUIRES_INDEPENDENT_REVIEW"


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class CompletionClaim(BaseModel):
    """One completion assertion extracted from a builder report."""

    model_config = ConfigDict(extra="ignore")

    claim_type: ClaimType
    claimed_status: str = ""
    claimed_value: str = ""
    claimed_evidence: list[str] = Field(default_factory=list)
    source: str = "builder report"

    def label(self) -> str:
        v = f" {self.claimed_value}" if self.claimed_value else ""
        return f"{self.claim_type.value}{v}: {self.claimed_status}".strip()


class Contradiction(BaseModel):
    """A claim that the repository actively refutes."""

    what: str
    claimed: str
    observed: str
    authority: str = ""
    severity: str = "blocker"

    def render(self) -> str:
        auth = f"  [authority: {self.authority}]" if self.authority else ""
        return f"{self.what}\n    claimed:  {self.claimed}\n    observed: {self.observed}{auth}"


class CriterionState(BaseModel):
    criterion: str
    weight: float = 0.0
    result: str = "PENDING"

    @property
    def passed(self) -> bool:
        return str(self.result).strip().upper() in PASSING_RESULTS

    @property
    def is_independent(self) -> bool:
        return any(k in self.criterion.lower() for k in INDEPENDENT_CRITERIA)


class WeightedProgress(BaseModel):
    """Progress computed from actual criterion states, never from prose."""

    unit_id: str = ""
    total_weight: float = 0.0
    earned_weight: float = 0.0
    percent: float = 0.0
    passed: list[str] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    independent_pending: list[str] = Field(default_factory=list)
    self_awardable_ceiling_percent: float = 100.0

    @property
    def is_complete(self) -> bool:
        return self.total_weight > 0 and self.earned_weight >= self.total_weight

    @property
    def only_independent_remains(self) -> bool:
        """Everything a single session can prove is proven; review remains."""
        return (
            self.total_weight > 0
            and bool(self.independent_pending)
            and set(self.pending) == set(self.independent_pending)
        )


class ReceiptCheck(BaseModel):
    """Whether a receipt file exists and matches the state it claims to prove."""

    name: str
    path: str
    exists: bool = False
    passed: bool = False
    matches_head: bool = False
    receipt_commit: str = ""
    receipt_tree: str = ""
    detail: str = ""


class ObservedState(BaseModel):
    """What the repository actually says, independent of any claim."""

    head_commit: str = ""
    head_tree: str = ""
    branch: str = ""
    dirty_file_count: int = 0
    untracked_count: int = 0
    active_unit_id: str = ""
    active_unit_status: str = ""
    progress: WeightedProgress = Field(default_factory=WeightedProgress)
    receipts: list[ReceiptCheck] = Field(default_factory=list)
    implementation_present: bool = False
    implementation_detail: str = ""
    build_status_percent: float | None = None
    open_risks: list[str] = Field(default_factory=list)
    files_consulted: list[str] = Field(default_factory=list)

    def receipt(self, name: str) -> ReceiptCheck | None:
        for r in self.receipts:
            if r.name == name:
                return r
        return None


class CompletionAudit(BaseModel):
    """The auditor's verdict on a builder's completion claims."""

    model_config = ConfigDict(extra="ignore")

    decision: AuditDecision
    claim: CompletionClaim | None = None
    claims: list[CompletionClaim] = Field(default_factory=list)
    observed_state: ObservedState = Field(default_factory=ObservedState)
    missing_evidence: list[str] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    evidence_paths: list[str] = Field(default_factory=list)
    correction_prompt: str = ""
    confidence: float = 0.0
    audited_at: str = Field(default_factory=utcnow)
    headline: str = ""

    #: What this run was asked for, and the phase it sits inside. The audit is
    #: against the first; the second is reported, never moved.
    scope: TaskScope | None = None
    #: The two-level completion record: task result and parent-phase state, side
    #: by side, with no arithmetic between them.
    completion: ScopedCompletion | None = None

    @property
    def blocks_acceptance(self) -> bool:
        return self.decision is not AuditDecision.VERIFIED

    def summary_block(self) -> str:
        """The concise terminal summary."""
        st = self.observed_state
        impl = "PRESENT" if st.implementation_present else "NOT OBSERVED"
        missing = ", ".join(self.missing_evidence[:6]) if self.missing_evidence else "nothing"
        lines = []
        if self.completion is not None:
            lines += self.completion.summary_block().splitlines()
        lines += [
            f"COMPLETION CLAIM: {self.decision.value}",
            f"IMPLEMENTATION STATE: {impl}",
            f"VERIFIED PROGRESS: {st.progress.percent:.0f}%"
            + (f" of {st.active_unit_id}" if st.active_unit_id else "")
            + (
                f"  (ceiling without independent review: "
                f"{st.progress.self_awardable_ceiling_percent:.0f}%)"
                if st.progress.independent_pending
                else ""
            ),
            f"MISSING: {missing}",
            f"NEXT SAFE ACTION: {self.next_safe_action()}",
        ]
        return "\n".join(lines)

    def next_safe_action(self) -> str:
        st = self.observed_state
        scope = self.scope
        if (
            self.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW
            and scope is not None
            and scope.is_nested
        ):
            return (
                f"have a session that did not build {scope.scope_id} review it; "
                f"{scope.parent_phase_id} stays {scope.phase_state_text} either way"
            )
        if self.decision is AuditDecision.CONTRADICTED:
            return (
                "restore the status documents to the highest evidence-supported state, "
                "preserving implementation code and valid evidence"
            )
        if self.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW:
            if st.dirty_file_count or st.untracked_count:
                return (
                    "create the content commit, then begin independent verification "
                    "in a fresh session"
                )
            return "begin independent verification in a fresh session"
        if self.decision is AuditDecision.UNPROVEN:
            return "produce the missing evidence, or downgrade the claim to what evidence supports"
        return "proceed"


# --------------------------------------------------------------------------
# Claim extraction
# --------------------------------------------------------------------------

_UNIT_RE = r"(?:P-?\d+|U-[A-Z0-9-]+)"

_CLAIM_PATTERNS: list[tuple[ClaimType, re.Pattern[str]]] = [
    (
        ClaimType.PHASE_COMPLETE,
        re.compile(
            rf"\b({_UNIT_RE})\b[^.\n]{{0,60}}?\b(?:is\s+|now\s+|marked\s+|✅\s*)?COMPLETE\b",
            re.I,
        ),
    ),
    (
        ClaimType.PHASE_COMPLETE,
        re.compile(rf"\b(?:phase|unit)\s+({_UNIT_RE}|\d+)\b[^.\n]{{0,40}}\bcomplete\b", re.I),
    ),
    (
        ClaimType.IMPLEMENTATION_COMPLETE,
        re.compile(r"\b(?:implementation|kernel|feature)\s+is\s+(?:now\s+)?(?:complete|done|finished)\b", re.I),
    ),
    (
        ClaimType.FULL_SUITE_PASSED,
        re.compile(
            r"\b(?:full|whole|entire|canonical)\s+(?:test\s+)?suite\s+(?:passes|passed|is\s+green)\b", re.I
        ),
    ),
    (
        ClaimType.TESTS_PASSED,
        re.compile(r"\b(?:all\s+)?tests?\s+(?:pass|passed|are\s+passing|green)\b", re.I),
    ),
    (ClaimType.TESTS_PASSED, re.compile(r"\b(\d{2,5})\s+passed\b", re.I)),
    (
        ClaimType.FINALIZER_RAN,
        re.compile(r"\b(?:finalize_status\.py|finalizer)\b[^.\n]{0,50}\b(?:ran|run|passed|succeeded|complete)\b", re.I),
    ),
    (ClaimType.FINALIZER_RAN, re.compile(r"\bstatus\s+(?:has\s+been\s+)?finalized\b", re.I)),
    (
        ClaimType.CLEAN_CLONE_PASSED,
        re.compile(r"\bclean[- ]clone\b[^.\n]{0,50}\b(?:gate\s+)?(?:pass|passed|green|succeeded)\b", re.I),
    ),
    (
        ClaimType.INDEPENDENT_REVIEW,
        re.compile(r"\bindependent(?:ly)?\s+review(?:ed|s)?\b[^.\n]{0,40}\b(?:complete|done|passed|satisfied)\b", re.I),
    ),
    (
        ClaimType.ADJUDICATION,
        re.compile(r"\b(?:final\s+)?adjudicat(?:ed|ion)\b[^.\n]{0,40}\b(?:complete|done|passed|satisfied)\b", re.I),
    ),
    (
        ClaimType.RISK_CONTAINED,
        re.compile(r"\b(R-\d+)\b[^.\n]{0,40}\b(?:is\s+)?(?:contained|closed|mitigated|resolved)\b", re.I),
    ),
    (ClaimType.PROGRESS_PERCENT, re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s*%\s*(?:complete|done|progress)\b", re.I)),
    (
        ClaimType.READY_ADVANCE,
        re.compile(rf"\b({_UNIT_RE})\b[^.\n]{{0,40}}\bis\s+(?:now\s+)?(?:the\s+)?(?:sole\s+)?READY\b", re.I),
    ),
    (
        ClaimType.CRITERION_PASS,
        re.compile(r"\b(?:criterion|criteria)\b[^.\n]{0,60}\b(?:pass|passed|satisfied|met)\b", re.I),
    ),
    (
        ClaimType.NEXT_PHASE_UNBLOCKED,
        re.compile(
            rf"\b({_UNIT_RE})\b[^.\n]{{0,40}}\b(?:is\s+)?(?:now\s+)?"
            r"(?:unblocked|no\s+longer\s+blocked|may\s+begin|can\s+begin|cleared\s+to\s+start)\b",
            re.I,
        ),
    ),
    (
        ClaimType.NEXT_PHASE_UNBLOCKED,
        re.compile(
            r"\bthe\s+next\s+(?:phase|unit)\b[^.\n]{0,30}\b"
            r"(?:is\s+)?(?:now\s+)?(?:unblocked|may\s+begin|can\s+begin|open)\b",
            re.I,
        ),
    ),
    (
        ClaimType.LIVE_ENABLEMENT,
        re.compile(
            r"\b(?:enabled|live|in\s+production|production-ready|shipped\s+live|"
            r"turned\s+on)\b[^.\n]{0,40}\b(?:traffic|production|customers?|tenants?)\b|"
            r"\b(?:on|for)\s+live\s+traffic\b[^.\n]{0,30}\b(?:enabled|now)\b",
            re.I,
        ),
    ),
]

# Phrases that mean the builder is explicitly NOT claiming completion.
_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|none|zero|cannot|can't|isn't|is\s+not|won't|refuse|refusing|"
    r"without|pending|awaiting|remains?|still|neither|nor)\b[^.\n]{0,40}$",
    re.I,
)

_EVIDENCE_PATH_RE = re.compile(r"\b((?:docs|src|eval|scripts|configs|data)/[\w./-]+\.\w{1,6})\b")

# Words that turn a nearby "COMPLETE" into a statement of INcompleteness.
_NEGATED_COMPLETION_RE = re.compile(
    r"\b(?:not|never|cannot|can'?t|isn'?t|aren'?t|won'?t|may\s+not|must\s+not|"
    r"before|until|unless|refuse[sd]?|prohibited|forbidden|do\s+not|does\s+not|"
    r"requires?|needs?|depends?\s+on|blocked|at\s+best|"
    r"pending|awaiting|premature(?:ly)?|false|unsupported)\b",
    re.I,
)


#: Claim families whose meaning inverts when the sentence around them is a
#: denial. A count ("46 passed") does not invert, so counts are left out and
#: judged on their own evidence.
_NEGATION_SENSITIVE: frozenset[ClaimType] = frozenset(
    {
        ClaimType.PHASE_COMPLETE,
        ClaimType.IMPLEMENTATION_COMPLETE,
        ClaimType.FULL_SUITE_PASSED,
        ClaimType.FINALIZER_RAN,
        ClaimType.CLEAN_CLONE_PASSED,
        ClaimType.INDEPENDENT_REVIEW,
        ClaimType.ADJUDICATION,
        ClaimType.RISK_CONTAINED,
        ClaimType.READY_ADVANCE,
        ClaimType.CRITERION_PASS,
        ClaimType.NEXT_PHASE_UNBLOCKED,
        ClaimType.LIVE_ENABLEMENT,
    }
)

_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
_QUOTED_SPAN_RE = re.compile(r"\"[^\"\n]{0,400}\"|\u201c[^\u201d\n]{0,400}\u201d")


def _mask_non_assertions(text: str) -> str:
    """Blank out the parts of a report that quote rather than assert.

    A report is not only its claims. It also contains the commands it ran, the
    audit text it is answering, and the strings it searched for — and every one
    of those can contain the exact words a claim is made of. Reading
    ``grep -n "P6.*COMPLETE"`` as "the builder says P6 is COMPLETE" turns a
    session that is being scrupulous into a session that is caught lying, and
    then sends it a correction it cannot act on. That is not a hypothetical: it
    is what happened, for eight consecutive iterations, in run 20260820-204803.

    Fenced code and quoted spans are replaced with spaces so every offset the
    caller computes still lines up with the original text. Inline backticks are
    only stripped, not blanked: "`P6` is COMPLETE" is a real claim and must stay
    one.
    """
    masked = _FENCE_RE.sub(lambda m: " " * len(m.group(0)), text)
    masked = _QUOTED_SPAN_RE.sub(lambda m: " " * len(m.group(0)), masked)
    return masked.replace("`", " ")


def extract_claims(text: str, source: str = "builder report") -> list[CompletionClaim]:
    """Pull completion claims out of a builder report.

    Deliberately conservative about negation: "P3 is NOT complete" is not a
    completion claim, and an honest report that refuses self-adjudication should
    produce no phase-completion claim at all. Equally conservative about
    *mention*: a phrase inside a shell command, a quoted citation of the audit,
    or a string the builder was searching for is not the builder asserting it.
    """
    if not text or not text.strip():
        return []

    claims: list[CompletionClaim] = []
    seen: set[tuple[str, str]] = set()
    evidence = _EVIDENCE_PATH_RE.findall(text)
    scannable = _mask_non_assertions(text)

    for claim_type, pattern in _CLAIM_PATTERNS:
        for match in pattern.finditer(scannable):
            line_start = scannable.rfind("\n", 0, match.start()) + 1
            prefix = scannable[line_start : match.start()]
            if _NEGATION_RE.search(prefix):
                continue
            # Negation inside the matched span, e.g. "P3 is not COMPLETE".
            if re.search(r"\b(?:not|never|isn't|is\s+not)\b", match.group(0), re.I):
                continue
            # Negation anywhere in the surrounding SENTENCE: "No status document
            # claims P6 is complete", "P6 may not be recorded COMPLETE". The
            # window stops at the sentence boundary in both directions — a
            # report that honestly says what is unfinished in one paragraph must
            # not have the next paragraph's claim silently discarded because the
            # word "not" appeared eighty characters earlier.
            before, after = _sentence_window(scannable, match.start(), match.end())
            if claim_type in _NEGATION_SENSITIVE and any(
                _NEGATED_COMPLETION_RE.search(chunk) for chunk in (match.group(0), before, after)
            ):
                continue

            value = ""
            if match.groups():
                value = str(match.group(1) or "")
            key = (claim_type.value, value.upper())
            if key in seen:
                continue
            seen.add(key)

            claims.append(
                CompletionClaim(
                    claim_type=claim_type,
                    claimed_status="COMPLETE" if claim_type is ClaimType.PHASE_COMPLETE else "CLAIMED",
                    claimed_value=value,
                    claimed_evidence=sorted(set(evidence))[:20],
                    source=source,
                )
            )
    return claims


def primary_claim(claims: list[CompletionClaim]) -> CompletionClaim | None:
    """The most consequential claim, for the audit's headline."""
    order = [
        ClaimType.PHASE_COMPLETE,
        ClaimType.ADJUDICATION,
        ClaimType.INDEPENDENT_REVIEW,
        ClaimType.CLEAN_CLONE_PASSED,
        ClaimType.FINALIZER_RAN,
        ClaimType.FULL_SUITE_PASSED,
        ClaimType.RISK_CONTAINED,
        ClaimType.READY_ADVANCE,
        ClaimType.PROGRESS_PERCENT,
        ClaimType.IMPLEMENTATION_COMPLETE,
        ClaimType.TESTS_PASSED,
        ClaimType.CRITERION_PASS,
    ]
    for wanted in order:
        for c in claims:
            if c.claim_type is wanted:
                return c
    return claims[0] if claims else None


# --------------------------------------------------------------------------
# The auditor
# --------------------------------------------------------------------------


class CompletionAuditor:
    """Independently verifies builder completion claims against the repository."""

    def __init__(self, repo: Path, declared_rules: frozenset[str] | None = None) -> None:
        self.repo = Path(repo)
        self._loader = RepositoryContextLoader(self.repo)
        #: Rule families the target repository currently states. ``None`` means
        #: "read them from the repository on each audit", which is what every
        #: ordinary caller wants: the requirement set then follows the repository
        #: automatically. Tests pass an explicit set to describe a repository
        #: without building one.
        self._declared_rules = declared_rules

    # -- what the repository still requires ---------------------------------

    def declared_rules(self) -> frozenset[str]:
        """Rule families the repository states right now, read fresh."""
        if self._declared_rules is not None:
            return self._declared_rules
        from .policy import declared_rule_kinds
        from .protocol_sources import discover_protocol

        try:
            return declared_rule_kinds(discover_protocol(self.repo))
        except Exception:  # discovery must never break an audit
            return frozenset()

    def _requires(self, rule_kind: str) -> bool:
        """True when the repository still states the rule behind an artifact."""
        return rule_kind in self.declared_rules()

    # -- git ---------------------------------------------------------------

    def _git(self, *args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(self.repo), capture_output=True, text=True, timeout=30
            )
            return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def _read_json(self, rel: str) -> dict[str, Any] | None:
        path = self.repo / rel
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _read_yaml(self, rel: str) -> Any:
        path = self.repo / rel
        if not path.exists():
            return None
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None

    def _read_text(self, rel: str) -> str:
        path = self.repo / rel
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    # -- weighted acceptance ----------------------------------------------

    def weighted_progress(self, unit: ActiveUnit) -> WeightedProgress:
        """Compute progress from actual criterion states, per the protocol.

        Only a passing criterion contributes weight. The builder's own reported
        percentage is never used.
        """
        states = [
            CriterionState(
                criterion=str(c.get("criterion", "")),
                weight=float(c.get("weight", 0) or 0),
                result=str(c.get("result", "PENDING")),
            )
            for c in unit.acceptance_criteria
        ]
        total = sum(s.weight for s in states)
        earned = sum(s.weight for s in states if s.passed)
        independent_weight = sum(s.weight for s in states if s.is_independent)

        return WeightedProgress(
            unit_id=unit.unit_id,
            total_weight=total,
            earned_weight=earned,
            percent=(earned / total * 100.0) if total else 0.0,
            passed=[s.criterion for s in states if s.passed],
            pending=[s.criterion for s in states if not s.passed],
            independent_pending=[s.criterion for s in states if not s.passed and s.is_independent],
            self_awardable_ceiling_percent=(
                ((total - independent_weight) / total * 100.0) if total else 100.0
            ),
        )

    # -- receipts ----------------------------------------------------------

    def _validated_states(self) -> list[str]:
        """Commits/trees a receipt may legitimately name.

        Under the two-commit convention a receipt records the *content* commit,
        and a status-metadata commit lands directly on top of it. So HEAD or
        HEAD's parent are both acceptable; anything older is stale.
        """
        states: list[str] = []
        for rev in ("HEAD", "HEAD^"):
            for suffix in ("", "^{tree}"):
                value = self._git("rev-parse", f"{rev}{suffix}")
                if value:
                    states.append(value)
        return states

    def check_receipts(self, head_commit: str, head_tree: str) -> list[ReceiptCheck]:
        """Verify each receipt exists, passed, and matches the validated tree."""
        checks: list[ReceiptCheck] = []
        valid = self._validated_states()

        def fresh(commit: str, tree: str) -> bool:
            return any(_matches(commit, v) or _matches(tree, v) for v in valid)

        suite = self._read_json(SUITE_RESULT_REL)
        sc = ReceiptCheck(name="suite", path=SUITE_RESULT_REL, exists=suite is not None)
        if suite is not None:
            sc.receipt_commit = str(suite.get("commit", ""))
            sc.receipt_tree = str(suite.get("tree", ""))
            sc.passed = int(suite.get("exit_status", 1)) == 0 and int(suite.get("failed", 1)) == 0
            sc.matches_head = fresh(sc.receipt_commit, sc.receipt_tree)
            sc.detail = (
                f"exit_status={suite.get('exit_status')}, passed={suite.get('passed')}, "
                f"failed={suite.get('failed')}, skipped={suite.get('skipped')}, "
                f"commit={sc.receipt_commit[:12]}"
            )
        else:
            sc.detail = "receipt file absent"
        checks.append(sc)

        gate = self._read_json(GATE_RESULT_REL)
        gc = ReceiptCheck(name="clean_clone", path=GATE_RESULT_REL, exists=gate is not None)
        if gate is not None:
            gc.receipt_commit = str(gate.get("commit", ""))
            gc.receipt_tree = str(gate.get("tree", ""))
            gc.passed = bool(gate.get("passed", False))
            gc.matches_head = fresh(gc.receipt_commit, gc.receipt_tree)
            gc.detail = f"passed={gc.passed}, gate={gate.get('gate')}, commit={gc.receipt_commit[:12]}"
        else:
            gc.detail = "receipt file absent"
        checks.append(gc)

        # The finalizer's receipt is the status block it writes into BUILD-STATUS.
        build = self._read_yaml(BUILD_STATUS_REL)
        derived = (build or {}).get("derived", {}) if isinstance(build, dict) else {}
        snapshot = (build or {}).get("snapshot", {}) if isinstance(build, dict) else {}
        fc = ReceiptCheck(name="finalizer", path=BUILD_STATUS_REL, exists=build is not None)
        if build is not None:
            fc.receipt_commit = str(derived.get("content_commit", ""))
            fc.receipt_tree = str(derived.get("content_tree", ""))
            fc.matches_head = fresh(fc.receipt_commit, fc.receipt_tree)
            reported = str(snapshot.get("finalizer_result", ""))
            fc.passed = bool(reported) and not re.search(
                r"NOT\s+EXECUTED|NOT\s+RUN|FAILED|NOT\s+REFRESHED", reported, re.I
            )
            if not (fc.receipt_commit or fc.receipt_tree):
                fc.detail = (
                    f"finalizer_result={reported[:70]!r} but no content_commit/content_tree "
                    "is recorded, so it validates no particular state"
                )
            else:
                fc.detail = f"finalizer_result={reported[:90]!r}"
        else:
            fc.detail = "BUILD-STATUS.yaml absent"
        checks.append(fc)

        return checks

    # -- independent review ------------------------------------------------

    def independent_review_artifacts(self, unit_id: str = "") -> tuple[list[str], list[str]]:
        """Return (candidate review files, reasons each is not independent).

        Scoped to the active unit when one is given, so a completed earlier
        phase's implementer record is not reported against the current claim.
        """
        candidates: list[str] = []
        reasons: list[str] = []
        impl_dir = self.repo / "docs" / "implementation"
        if not impl_dir.is_dir():
            return candidates, reasons

        # "P3" -> matches phase-3-*.md / p3-*.md; empty unit_id matches all.
        digits = re.sub(r"\D", "", unit_id or "")
        scope = re.compile(
            rf"(?:phase-?{digits}|p-?{digits})\b" if digits else r".", re.I
        )

        for path in sorted(impl_dir.glob("*review*.md")):
            if not scope.search(path.name):
                continue
            rel = str(path.relative_to(self.repo))
            candidates.append(rel)
            text = path.read_text(encoding="utf-8", errors="replace")[:20000].lower()
            name = path.name.lower()
            if "implementation-review" in name or "implementer" in name:
                reasons.append(
                    f"{rel} is the implementer's own record (filename), not an independent review"
                )
            elif re.search(r"not an adjudication|implementer'?s record|written by the implementing session", text):
                reasons.append(f"{rel} states it is not an independent adjudication")
            elif self._git("ls-files", "--error-unmatch", rel) == "":
                reasons.append(
                    f"{rel} is untracked — it was produced in the implementing session's working tree"
                )
        return candidates, reasons

    # -- observation -------------------------------------------------------

    def observe(self, unit: ActiveUnit) -> ObservedState:
        """Read the repository's actual state. No claims involved."""
        head_commit = self._git("rev-parse", "HEAD")
        head_tree = self._git("rev-parse", "HEAD^{tree}")
        status_lines = [ln for ln in self._git("status", "--porcelain").splitlines() if ln.strip()]

        build = self._read_yaml(BUILD_STATUS_REL)
        derived = (build or {}).get("derived", {}) if isinstance(build, dict) else {}
        snapshot = (build or {}).get("snapshot", {}) if isinstance(build, dict) else {}

        risks = snapshot.get("open_program_risks") or []
        if isinstance(risks, str):
            risks = [risks]

        progress = self.weighted_progress(unit)
        impl_present, impl_detail = self._implementation_present(unit)

        return ObservedState(
            head_commit=head_commit,
            head_tree=head_tree,
            branch=self._git("branch", "--show-current"),
            dirty_file_count=len([ln for ln in status_lines if not ln.startswith("??")]),
            untracked_count=len([ln for ln in status_lines if ln.startswith("??")]),
            active_unit_id=unit.unit_id,
            active_unit_status=unit.status,
            progress=progress,
            receipts=self.check_receipts(head_commit, head_tree),
            implementation_present=impl_present,
            implementation_detail=impl_detail,
            build_status_percent=_as_float(derived.get("current_phase_percent")),
            open_risks=[str(r)[:200] for r in risks],
            files_consulted=[
                str(self.repo / rel)
                for rel in (REGISTRY_REL, CURRENT_REL, BUILD_STATUS_REL, SUITE_RESULT_REL, GATE_RESULT_REL)
                if (self.repo / rel).exists()
            ],
        )

    def _implementation_present(self, unit: ActiveUnit) -> tuple[bool, str]:
        """Is there code on disk for the active unit? Existence only, never proof."""
        changed = [
            ln[3:].strip()
            for ln in self._git("status", "--porcelain").splitlines()
            if ln.strip()
        ]
        code = [f for f in changed if f.endswith(".py") and not f.startswith("docs/")]
        if code:
            return True, f"{len(code)} changed/untracked source file(s), e.g. {', '.join(code[:3])}"

        recent = self._git("show", "--stat", "--oneline", "HEAD")
        py_lines = [ln for ln in recent.splitlines() if ".py" in ln]
        if py_lines:
            return True, f"HEAD touches {len(py_lines)} source file(s)"
        return False, "no source changes observed in the working tree or at HEAD"

    # -- status consistency ------------------------------------------------

    def status_contradictions(self, unit: ActiveUnit, state: ObservedState) -> list[Contradiction]:
        """Compare every live status surface against the registry."""
        found: list[Contradiction] = []
        registry_status = unit.status

        # A narrative surface declaring completion the registry does not record.
        if registry_status != "COMPLETE":
            uid = re.escape(unit.unit_id)
            claim_re = re.compile(
                rf"(?:\b{uid}\b|implementation\s+phase\s+{re.escape(unit.unit_id.lstrip('P'))})"
                rf"[^\n]{{0,60}}?(?:✅\s*)?\bCOMPLETE\b",
                re.I,
            )
            for surface in NARRATIVE_SURFACES:
                text = self._read_text(surface) or self._read_text(f"docs/implementation/{surface}")
                if not text:
                    continue
                for m in claim_re.finditer(text):
                    # A markdown blockquote, or text inside quotation marks, is a
                    # citation — typically of a claim the document is refuting —
                    # not an assertion by the document itself.
                    line_start = text.rfind("\n", 0, m.start()) + 1
                    raw_line = text[line_start : text.find("\n", m.end()) % (len(text) + 1)]
                    if raw_line.lstrip().startswith(">"):
                        continue
                    if _is_quoted(text, m.start(), m.end()):
                        continue

                    line = text[max(0, m.start() - 40) : m.end() + 40].replace("\n", " ")
                    # "may not be recorded COMPLETE", "is NOT COMPLETE", "before
                    # P3 is COMPLETE" are statements of incompleteness, not claims.
                    # Negation may sit before the match ("may not be recorded
                    # COMPLETE") or after it ("Requires P3 COMPLETE, and P3 is
                    # not."), so inspect the whole surrounding sentence.
                    before = text[max(0, m.start() - 90) : m.start()].replace("\n", " ")
                    after = text[m.end() : m.end() + 70].split("\n")[0]
                    if any(
                        _NEGATED_COMPLETION_RE.search(chunk)
                        for chunk in (m.group(0), before, after)
                    ):
                        continue
                    found.append(
                        Contradiction(
                            what=f"{surface} declares {unit.unit_id} COMPLETE",
                            claimed=f"...{line.strip()[:160]}...",
                            observed=f"registry records {unit.unit_id} as {registry_status}",
                            authority=REGISTRY_REL,
                        )
                    )
                    break

        # A reported percentage the criteria do not support.
        if state.build_status_percent is not None:
            if state.build_status_percent > state.progress.percent + 0.01:
                found.append(
                    Contradiction(
                        what="BUILD-STATUS reports more progress than the criteria support",
                        claimed=f"current_phase_percent = {state.build_status_percent:.1f}%",
                        observed=(
                            f"weighted criteria yield {state.progress.percent:.1f}% "
                            f"({len(state.progress.pending)} of "
                            f"{len(state.progress.pending) + len(state.progress.passed)} still pending)"
                        ),
                        authority=REGISTRY_REL,
                    )
                )

        # A later unit READY while its dependencies are not COMPLETE.
        found += self._dependency_contradictions()

        # A required limitation removed from the architecture text.
        found += self._erased_limitations()

        return found

    def _dependency_contradictions(self) -> list[Contradiction]:
        data = self._read_yaml(REGISTRY_REL)
        if not isinstance(data, dict):
            return []
        raw = data.get("units")
        units = list(raw.values()) if isinstance(raw, dict) else list(raw or [])
        by_id = {str(u.get("unit_id")): u for u in units if isinstance(u, dict)}

        out: list[Contradiction] = []
        for u in units:
            if not isinstance(u, dict) or u.get("status") != "READY":
                continue
            deps = u.get("dependencies") or []
            deps = [deps] if isinstance(deps, str) else deps
            for dep in deps:
                dep_unit = by_id.get(str(dep))
                if dep_unit is not None and dep_unit.get("status") != "COMPLETE":
                    out.append(
                        Contradiction(
                            what=f"{u.get('unit_id')} is READY while its dependency {dep} is not COMPLETE",
                            claimed=f"{u.get('unit_id')} status = READY",
                            observed=f"{dep} status = {dep_unit.get('status')}",
                            authority=REGISTRY_REL,
                        )
                    )
        return out

    def _erased_limitations(self) -> list[Contradiction]:
        """Detect a limitation deleted from architecture/status prose."""
        diff = self._git("diff", "--unified=0", "--", "ARCHITECTURE.md", "PRODUCT.md", "CLAUDE.md")
        if not diff:
            return []
        markers = re.compile(
            r"\bNOT\s+CONTAINED\b|\bOPEN\b\s*[-—]\s*NOT|\bR-\d+\b[^\n]{0,40}\bOPEN\b|"
            r"\bNEEDS\s+VALIDATION\b|\bships?\s+dark\b|\bmay\s+NEVER\b|\bmust\s+not\b",
            re.I,
        )
        removed = [
            ln[1:].strip()
            for ln in diff.splitlines()
            if ln.startswith("-") and not ln.startswith("---") and markers.search(ln)
        ]
        if not removed:
            return []
        return [
            Contradiction(
                what="a required limitation was removed from a canonical document",
                claimed="the limitation no longer appears in the working tree",
                observed="removed line(s): " + "; ".join(r[:110] for r in removed[:3]),
                authority="git diff",
            )
        ]

    # -- the audit ---------------------------------------------------------

    def unit_status(self, unit_id: str) -> str:
        """The registry's recorded status for any unit, not only the active one.

        A report that says "P0-P5 are COMPLETE" is making a claim about five
        units the run is not building. Checking it against the *active* unit's
        status turns a true statement into a contradiction; checking it against
        its own unit's status is the only reading that can be right.
        """
        if not unit_id:
            return ""
        data = self._read_yaml(REGISTRY_REL)
        if not isinstance(data, dict):
            return ""
        raw = data.get("units")
        units = list(raw.values()) if isinstance(raw, dict) else list(raw or [])
        wanted = unit_id.replace("-", "").upper()
        for u in units:
            if not isinstance(u, dict):
                continue
            if str(u.get("unit_id", "")).replace("-", "").upper() == wanted:
                return str(u.get("status") or "")
        return ""

    def audit(
        self,
        builder_report: str,
        unit: ActiveUnit | None = None,
        run_commands: list[Any] | None = None,
        evidence_dir: str | None = None,
        scope: TaskScope | None = None,
    ) -> CompletionAudit:
        """Audit a builder report against the repository. Never raises.

        ``scope`` says what this run was asked for. Without one, the audit is
        against the whole active phase, which is the strict reading and the
        historical behaviour: a caller that does not say what it asked for gets
        held to everything.
        """
        if unit is None:
            # Optional, not fail-closed: a repository that declares no unit
            # registry still deserves to have its builder's claims checked. The
            # registry-derived checks below simply find nothing to compare
            # against, while every honesty check still runs.
            unit = self._loader.resolve_active_unit_optional()

        if scope is None:
            scope = TaskScope(
                scope_id=unit.unit_id,
                parent_phase_id=unit.unit_id,
                parent_phase_state=unit.status,
                parent_phase_execution_state=getattr(unit, "execution_state", ""),
                claims_phase_completion=True,
                derivation=["no task scope supplied; audited against the whole active phase"],
            )

        claims = extract_claims(builder_report)
        state = self.observe(unit)
        contradictions: list[Contradiction] = []
        missing: list[str] = []

        # Whether the *phase* is what this run claims to have finished. Only
        # then does the phase's own acceptance evidence become this run's bar.
        # Everything below that reads "phase completion" asks this, not "did the
        # word COMPLETE appear next to a phase id somewhere".
        phase_scope = scope.requires_phase_acceptance

        # 1. Status surfaces disagreeing with the registry.
        contradictions += self.status_contradictions(unit, state)

        # 2. Cited evidence that does not exist. Checked for every path the
        #    report cites, whether or not a claim pattern also fired: a citation
        #    to a non-existent file is never acceptable.
        for cited in _dedupe(_EVIDENCE_PATH_RE.findall(builder_report or "")):
            if not (self.repo / cited).exists():
                contradictions.append(
                    Contradiction(
                        what="cited evidence file does not exist",
                        claimed=f"{cited} (cited in the builder report)",
                        observed=f"no such file under {self.repo}",
                        authority="filesystem",
                    )
                )

        claim_types = {c.claim_type for c in claims}

        # 2b. A completion claim about a unit is a claim about THAT unit. Split
        #     them: one set is about the phase this run sits in, the rest are
        #     about units the run is not building, and each is checked against
        #     its own registry status. "P0-P5 are COMPLETE" is a true sentence
        #     in a repository where they are, and reading it as a claim about
        #     the active phase manufactures a contradiction out of an accurate
        #     report.
        parent = scope.parent_phase_id or unit.unit_id
        phase_claims = [
            c
            for c in claims
            if c.claim_type is ClaimType.PHASE_COMPLETE and _same_unit(c.claimed_value, parent)
        ]
        other_unit_claims = [
            c
            for c in claims
            if c.claim_type is ClaimType.PHASE_COMPLETE and not _same_unit(c.claimed_value, parent)
        ]
        phase_completion_claimed = bool(phase_claims)

        for claim in other_unit_claims:
            other_id = claim.claimed_value
            recorded = self.unit_status(other_id)
            if recorded and recorded.upper() != "COMPLETE":
                contradictions.append(
                    Contradiction(
                        what=f"{other_id} claimed COMPLETE while the registry says otherwise",
                        claimed=f"{other_id} is COMPLETE",
                        observed=f"registry records {other_id} as {recorded}",
                        authority=REGISTRY_REL,
                    )
                )

        # 3. Phase completion against weighted criteria.
        #
        # When the ONLY outstanding criteria are the ones a single session
        # structurally cannot award itself, an over-eager COMPLETE is a premature
        # label on genuinely finished implementation work — not a fabrication.
        # That routes to REQUIRES_INDEPENDENT_REVIEW, so it is recorded as
        # missing evidence rather than as a contradiction.
        awaiting_review_only = state.progress.only_independent_remains

        if phase_completion_claimed:
            # Unconditional, whatever this run was asked for: saying the phase is
            # COMPLETE when the registry says it is not is a false statement, and
            # a narrow task is not a licence to make one.
            if unit.status != "COMPLETE" and not awaiting_review_only:
                contradictions.append(
                    Contradiction(
                        what="phase completion claimed while the registry says otherwise",
                        claimed=f"{unit.unit_id} is COMPLETE",
                        observed=f"registry records {unit.unit_id} as {unit.status}",
                        authority=REGISTRY_REL,
                    )
                )
            if state.progress.pending and not awaiting_review_only:
                contradictions.append(
                    Contradiction(
                        what="phase completion claimed with weighted criteria still pending",
                        claimed=f"{unit.unit_id} is COMPLETE",
                        observed=(
                            f"{len(state.progress.pending)} criteria are not PASS "
                            f"({', '.join(state.progress.pending[:6])}); verified progress "
                            f"{state.progress.percent:.0f}%"
                        ),
                        authority=REGISTRY_REL,
                    )
                )
            elif awaiting_review_only:
                missing.append(
                    f"{unit.unit_id} cannot be recorded COMPLETE: "
                    f"{', '.join(state.progress.independent_pending)} still pending"
                )

        # 4. Full-suite claims must rest on a canonical-suite receipt — when the
        #    repository still says a canonical-suite gate exists. Where it does
        #    not, demanding the receipt would be this driver enforcing a gate its
        #    target has retired. The claim-versus-receipt checks below stay
        #    unconditional: a receipt that exists and says FAIL refutes a claim
        #    whatever the protocol says, and a full-suite claim resting on
        #    targeted tests is a false statement, not a missing ceremony.
        wants_canonical_suite = self._requires("CANONICAL_SUITE_GATE") and phase_scope
        if ClaimType.FULL_SUITE_PASSED in claim_types or phase_completion_claimed:
            suite = state.receipt("suite")
            targeted = _targeted_only(run_commands or [])
            if suite is None or not suite.exists:
                # Claimed outright, or implied by a completion claim while the
                # repository still states a canonical-suite gate. A claim made
                # with nothing behind it is unproven whatever the protocol says;
                # only the *implied* requirement follows the repository.
                if ClaimType.FULL_SUITE_PASSED in claim_types or wants_canonical_suite:
                    missing.append("canonical suite receipt (SUITE-RESULT.json)")
            elif not suite.passed:
                contradictions.append(
                    Contradiction(
                        what="canonical suite is claimed to pass but the receipt says otherwise",
                        claimed="the full suite passed",
                        observed=suite.detail,
                        authority=SUITE_RESULT_REL,
                    )
                )
            elif not suite.matches_head:
                contradictions.append(
                    Contradiction(
                        what="the canonical suite receipt does not match the current tree",
                        claimed="the full suite passed for this work",
                        observed=(
                            f"receipt is for commit {suite.receipt_commit[:12]} / tree "
                            f"{suite.receipt_tree[:12]}, current HEAD is "
                            f"{state.head_commit[:12]} / tree {state.head_tree[:12]}"
                        ),
                        authority="git",
                    )
                )
            if targeted and ClaimType.FULL_SUITE_PASSED in claim_types:
                contradictions.append(
                    Contradiction(
                        what="a full-suite claim rests only on targeted tests",
                        claimed="the full/canonical suite passed",
                        observed=f"this run executed only targeted tests: {targeted}",
                        authority="this run's command output",
                    )
                )

        # 5. Skips must be approved skips, not a dirty-tree finalization failure.
        contradictions += self._skip_contradictions(state)

        # 6. Finalizer / clean-clone claims need real, matching receipts.
        #
        #    A claim about one of these is checked whenever it is MADE — saying
        #    the finalizer ran when it did not is a false statement regardless of
        #    protocol. What follows the repository is whether a *completion*
        #    claim implies these artifacts must exist: that only holds while the
        #    repository still states the rule that makes them mandatory.
        for claim_type, receipt_name, human, rule_kind in (
            (ClaimType.FINALIZER_RAN, "finalizer", "finalizer", "FINALIZER_OWNERSHIP"),
            (ClaimType.CLEAN_CLONE_PASSED, "clean_clone", "clean-clone gate", "CLEAN_CLONE_GATE"),
        ):
            claimed = claim_type in claim_types
            implied = phase_completion_claimed and phase_scope and self._requires(rule_kind)
            if not claimed and not implied:
                continue
            receipt = state.receipt(receipt_name)
            if receipt is None or not receipt.exists:
                if claim_type in claim_types:
                    contradictions.append(
                        Contradiction(
                            what=f"{human} PASS claimed with no receipt",
                            claimed=f"the {human} ran and passed",
                            observed=f"no receipt at {receipt.path if receipt else 'the expected path'}",
                            authority="filesystem",
                        )
                    )
                else:
                    missing.append(f"{human} receipt")
            elif not receipt.passed:
                if claim_type in claim_types:
                    contradictions.append(
                        Contradiction(
                            what=f"{human} PASS claimed but the receipt does not say it passed",
                            claimed=f"the {human} passed",
                            observed=receipt.detail,
                            authority=receipt.path,
                        )
                    )
                else:
                    missing.append(f"{human} has not run for this work ({receipt.detail[:80]})")
            elif not receipt.matches_head:
                contradictions.append(
                    Contradiction(
                        what=f"the {human} receipt is for a different tree",
                        claimed=f"the {human} validates the current work",
                        observed=(
                            f"receipt commit {receipt.receipt_commit[:12]}, "
                            f"current HEAD {state.head_commit[:12]}"
                        ),
                        authority="git",
                    )
                )

        # 7. Independent review and adjudication.
        candidates, not_independent = self.independent_review_artifacts(unit.unit_id)
        if ClaimType.INDEPENDENT_REVIEW in claim_types or ClaimType.ADJUDICATION in claim_types:
            if not_independent:
                for reason in not_independent:
                    contradictions.append(
                        Contradiction(
                            what="independent review claimed but the artifact is not independent",
                            claimed="an independent review exists",
                            observed=reason,
                            authority=REGISTRY_REL,
                        )
                    )
            elif not candidates:
                contradictions.append(
                    Contradiction(
                        what="independent review claimed but no review artifact exists",
                        claimed="an independent review exists",
                        observed="no review document found under docs/implementation/",
                        authority="filesystem",
                    )
                )
            for name in state.progress.independent_pending:
                contradictions.append(
                    Contradiction(
                        what=f"{name} claimed but the registry still records it PENDING",
                        claimed=f"{name} is satisfied",
                        observed=f"registry criterion {name} is not PASS",
                        authority=REGISTRY_REL,
                    )
                )

        # 8. Risk containment claims.
        for claim in claims:
            if claim.claim_type is not ClaimType.RISK_CONTAINED:
                continue
            risk = claim.claimed_value.upper()
            still_open = [r for r in state.open_risks if risk and risk in r.upper()]
            if still_open:
                contradictions.append(
                    Contradiction(
                        what=f"{risk} claimed contained while the repository records it open",
                        claimed=f"{risk} is contained",
                        observed=still_open[0][:200],
                        authority=BUILD_STATUS_REL,
                    )
                )

        # 9. Progress percentages the criteria do not support.
        for claim in claims:
            if claim.claim_type is not ClaimType.PROGRESS_PERCENT:
                continue
            value = _as_float(claim.claimed_value)
            if value is not None and value > state.progress.percent + 0.01:
                contradictions.append(
                    Contradiction(
                        what="reported progress exceeds what the criteria support",
                        claimed=f"{value:.0f}% complete",
                        observed=f"weighted criteria yield {state.progress.percent:.0f}%",
                        authority=REGISTRY_REL,
                    )
                )

        # 10. Commit structure: uncommitted work cannot be a validated final tree.
        if phase_completion_claimed and (state.dirty_file_count or state.untracked_count):
            contradictions.append(
                Contradiction(
                    what="completion claimed on an uncommitted tree",
                    claimed=f"{unit.unit_id} is COMPLETE",
                    observed=(
                        f"{state.dirty_file_count} modified and {state.untracked_count} untracked "
                        "file(s); final-tree validation cannot have run on this tree"
                    ),
                    authority="git status",
                )
            )

        # Missing evidence for a completion claim, independent of contradictions.
        if phase_completion_claimed and phase_scope:
            for name in state.progress.independent_pending:
                missing.append(f"{name} (requires a session other than the implementing one)")

        # 11. Scope overreach. A run asked for one unit inside a phase may not
        #     report the phase forward, score its criteria, open the next phase,
        #     or turn anything on. These are checked as claims about REACH,
        #     separately from whether each claim happens to be true: a build
        #     session is not the authority that opens the next phase even when
        #     the next phase is genuinely ready.
        contradictions += self._scope_overreach(claims, scope, state)

        review_outstanding = self._task_review_outstanding(scope)

        decision, headline, confidence = self._decide(
            claims,
            state,
            contradictions,
            missing,
            scope=scope,
            phase_completion_claimed=phase_completion_claimed,
            review_outstanding=review_outstanding,
        )

        audit = CompletionAudit(
            decision=decision,
            claim=primary_claim(claims),
            claims=claims,
            observed_state=state,
            missing_evidence=_dedupe(missing),
            contradictions=contradictions,
            evidence_paths=_dedupe(state.files_consulted + ([evidence_dir] if evidence_dir else [])),
            confidence=confidence,
            headline=headline,
            scope=scope,
        )
        audit.completion = scoped_completion(
            scope,
            _task_result_for(decision),
            evidence=list(audit.evidence_paths),
            outstanding=(
                _dedupe([c.what for c in contradictions] + audit.missing_evidence + review_outstanding)
            ),
            # The only route to a phase acceptance: the task claimed the phase,
            # the registry records it COMPLETE, and the audit found nothing
            # against it. A task-scope run cannot reach this line with True.
            phase_accepted=(
                decision is AuditDecision.VERIFIED
                and scope.requires_phase_acceptance
                and unit.status.upper() == "COMPLETE"
            ),
        )
        audit.correction_prompt = self.build_correction(audit, unit)
        return audit

    # -- scope ------------------------------------------------------------

    def _scope_overreach(
        self, claims: list[CompletionClaim], scope: TaskScope, state: ObservedState
    ) -> list[Contradiction]:
        """Claims that reach past what this run was asked to do."""
        if not scope.is_nested:
            return []
        found: list[Contradiction] = []
        types = {c.claim_type for c in claims}
        reach = (
            (
                ClaimType.NEXT_PHASE_UNBLOCKED,
                "a later phase declared unblocked by the session that built one unit",
                "the next phase is unblocked",
            ),
            (
                ClaimType.CRITERION_PASS,
                "a phase acceptance criterion claimed by a task-scope run",
                "an acceptance criterion is satisfied",
            ),
            (
                ClaimType.ADJUDICATION,
                "phase adjudication claimed by a task-scope run",
                "adjudication is complete",
            ),
            (
                ClaimType.LIVE_ENABLEMENT,
                "live or production enablement claimed by a task-scope run",
                "the capability is enabled",
            ),
        )
        for claim_type, what, claimed in reach:
            if claim_type not in types:
                continue
            found.append(
                Contradiction(
                    what=what,
                    claimed=claimed,
                    observed=(
                        f"this run was asked for {scope.scope_id} only; "
                        f"{scope.parent_phase_id} is recorded "
                        f"{scope.phase_state_text} and accepting "
                        f"{scope.scope_id} does not move it"
                    ),
                    authority=REGISTRY_REL,
                )
            )
        return found

    def _task_review_outstanding(self, scope: TaskScope) -> list[str]:
        """What review the repository still owes this scoped task, if any.

        Asked of the repository, not of this driver's habits: a repository that
        states an independent-review rule gets one, and a repository that states
        none is not given one it never asked for. The rule survives every
        retirement mechanism by construction — a review requirement protects
        something rather than produces an artifact — so this is the one demand a
        target repository cannot delete by simplifying its process.
        """
        if not scope.is_nested or not self._requires("INDEPENDENT_REVIEW"):
            return []
        candidates, not_independent = self.independent_review_artifacts(scope.parent_phase_id)
        usable = [c for c in candidates if _mentions_scope(c, scope)]
        if usable and not not_independent:
            return []
        return [
            f"an independent review of {scope.scope_id} by a session that did not build it"
        ]

    def _skip_contradictions(self, state: ObservedState) -> list[Contradiction]:
        """A dirty-tree finalization failure must not read as an ordinary skip."""
        suite = self._read_json(SUITE_RESULT_REL)
        if not suite:
            return []
        skipped_nodes = suite.get("skipped_nodes") or []
        if not skipped_nodes:
            return []

        approved_raw = self._read_yaml(APPROVED_SKIPS_REL)
        approved: set[str] = set()
        if isinstance(approved_raw, dict):
            for value in approved_raw.values():
                if isinstance(value, list):
                    approved.update(str(v).split(" ")[0] for v in value)
        elif isinstance(approved_raw, list):
            approved.update(str(v).split(" ")[0] for v in approved_raw)

        out: list[Contradiction] = []
        unapproved = [
            str(n).split(" ")[0]
            for n in skipped_nodes
            if str(n).split(" ")[0] not in approved
        ]
        if unapproved and approved:
            out.append(
                Contradiction(
                    what="the suite receipt contains skips that are not approved skips",
                    claimed="the suite passed with only expected skips",
                    observed=f"unapproved skip(s): {', '.join(unapproved[:4])}",
                    authority=APPROVED_SKIPS_REL,
                )
            )

        # An environmental skip (a permission or bind failure) is a failed run.
        env_skips = [
            str(n)
            for n in skipped_nodes
            if re.search(r"permission|operation not permitted|bind|socket|dirty", str(n), re.I)
        ]
        if env_skips:
            out.append(
                Contradiction(
                    what="an environmental failure is recorded as an ordinary skip",
                    claimed="the suite passed with an ordinary skip",
                    observed=f"skip reason indicates an environment failure: {env_skips[0][:140]}",
                    authority=SUITE_RESULT_REL,
                )
            )
        return out

    def _decide(
        self,
        claims: list[CompletionClaim],
        state: ObservedState,
        contradictions: list[Contradiction],
        missing: list[str],
        *,
        scope: TaskScope | None = None,
        phase_completion_claimed: bool = False,
        review_outstanding: list[str] | None = None,
    ) -> tuple[AuditDecision, str, float]:
        if contradictions:
            return (
                AuditDecision.CONTRADICTED,
                "The repository contradicts the builder's completion claims.",
                0.9,
            )

        # A run asked for one unit inside a phase is judged on that unit. The
        # phase's weighted criteria describe twelve other units and a phase
        # acceptance nobody has claimed; measuring this run against them is
        # measuring it against work it was told not to do. What still has to
        # hold: nothing missing, nothing contradicted, and the review the
        # repository requires actually done.
        if scope is not None and scope.is_nested:
            if missing:
                return (
                    AuditDecision.UNPROVEN,
                    f"{scope.scope_id} cannot be confirmed: required evidence is missing.",
                    0.7,
                )
            if review_outstanding:
                return (
                    AuditDecision.REQUIRES_INDEPENDENT_REVIEW,
                    f"{scope.scope_id} IMPLEMENTED — AWAITING INDEPENDENT REVIEW "
                    f"({scope.parent_phase_id} stays {scope.phase_state_text})",
                    0.85,
                )
            return (
                AuditDecision.VERIFIED,
                f"{scope.scope_id} is supported by the repository; "
                f"{scope.parent_phase_id} remains {scope.phase_state_text}.",
                0.85,
            )

        completion_claimed = any(
            c.claim_type
            in (ClaimType.PHASE_COMPLETE, ClaimType.ADJUDICATION, ClaimType.INDEPENDENT_REVIEW)
            for c in claims
        )

        if state.progress.only_independent_remains:
            return (
                AuditDecision.REQUIRES_INDEPENDENT_REVIEW,
                "IMPLEMENTED — AWAITING INDEPENDENT REVIEW",
                0.85,
            )

        if missing:
            return (
                AuditDecision.UNPROVEN,
                "Completion cannot be confirmed: required evidence is missing.",
                0.7,
            )

        # Pending independent criteria only route here when completion was
        # actually claimed. A report that claims nothing has nothing to verify.
        if completion_claimed and state.progress.independent_pending:
            return (
                AuditDecision.REQUIRES_INDEPENDENT_REVIEW,
                "IMPLEMENTED — AWAITING INDEPENDENT REVIEW",
                0.8,
            )

        if completion_claimed and not state.progress.is_complete:
            return (
                AuditDecision.UNPROVEN,
                "Completion claimed but the weighted criteria do not support it.",
                0.75,
            )

        if not claims:
            return (
                AuditDecision.VERIFIED,
                "No completion claim was made; nothing to contradict.",
                0.8,
            )

        return (AuditDecision.VERIFIED, "Completion claims are supported by the repository.", 0.85)

    # -- honest rollback ---------------------------------------------------

    def build_correction(self, audit: CompletionAudit, unit: ActiveUnit) -> str:
        """Instruct the builder to restore the highest evidence-supported state."""
        if audit.decision is AuditDecision.VERIFIED:
            return ""

        st = audit.observed_state
        scope = audit.scope
        lines: list[str] = [
            "COMPLETION-CLAIM AUDIT — the repository does not support the claims made.",
            "",
        ]
        if scope is not None and scope.is_nested:
            lines += [
                f"This run was asked for: {scope.scope_id}",
                f"Parent phase: {scope.parent_phase_id} — recorded "
                f"{scope.phase_state_text}"
                + (
                    f" / {scope.parent_phase_execution_state}"
                    if scope.parent_phase_execution_state
                    else ""
                ),
                "",
                f"{scope.parent_phase_id}'s phase acceptance is NOT this run's bar and is NOT "
                f"what is being asked of you. Do not produce phase-level evidence, do not "
                f"score a {scope.parent_phase_id} criterion, and do not edit a status surface "
                f"to move {scope.parent_phase_id}. What follows is about {scope.scope_id}.",
            ]
        else:
            lines += [
                f"Active unit: {unit.unit_id} (registry status: {unit.status})",
                f"Verified progress from weighted criteria: {st.progress.percent:.0f}% "
                f"(the builder's reported figure was not used)",
            ]
            if st.progress.independent_pending:
                lines.append(
                    "Criteria that cannot be self-awarded: "
                    + ", ".join(st.progress.independent_pending)
                )

        if audit.contradictions:
            lines += ["", "CONTRADICTIONS FOUND:"]
            lines += [f"  {i}. {c.render()}" for i, c in enumerate(audit.contradictions, 1)]

        if audit.missing_evidence:
            lines += ["", "MISSING EVIDENCE:"]
            lines += [f"  - {m}" for m in audit.missing_evidence]

        if audit.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW and (
            scope is not None and scope.is_nested
        ):
            lines += [
                "",
                "REQUIRED ACTION — record the scoped state, then stop:",
                f"  1. Record {scope.scope_id} as IMPLEMENTED and AWAITING REVIEW. Its",
                "     implementation may stand; its acceptance does not, and the review must",
                "     come from a session that did not write it.",
                f"  2. Leave {scope.parent_phase_id} exactly where the registry has it. This",
                f"     task does not complete {scope.parent_phase_id}, does not score one of",
                "     its criteria, and does not unblock anything after it.",
                "  3. State plainly what remains and who must do it.",
            ]
        elif audit.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW:
            lines += [
                "",
                "REQUIRED ACTION — honest status, then stop:",
                "  1. Record the state as IMPLEMENTED, NOT COMPLETE. The implementation may",
                "     stand; the acceptance does not.",
                "  2. Do NOT mark the independent-review or final-adjudication criteria. They",
                "     require a session other than the one that wrote the implementation.",
                "     Self-adjudication is a defect with a passing status.",
                "  3. State plainly what remains and who must do it.",
            ]
        else:
            lines += [
                "",
                "REQUIRED ACTION — honest rollback:",
                "  1. Restore every status document to the HIGHEST EVIDENCE-SUPPORTED state.",
                "     Where a claim has no evidence, correct the claim — do not manufacture",
                "     the evidence.",
                "  2. PRESERVE all implementation code and all valid existing evidence. This is",
                "     a correction of claims, not a revert of work.",
                "  3. Do NOT delete, weaken or disable any acceptance guard, test or control in",
                "     order to obtain a green result. A green test asserting a forbidden",
                "     behaviour is a defect with a passing status.",
                "  4. Do NOT use git stash / restore / clean / checkout -- / reset --hard.",
                "     Untracked implementation work would be destroyed irrecoverably.",
                "  5. Leave criteria requiring an independent session PENDING.",
            ]

        lines += [
            "",
            f"NEXT SAFE ACTION: {audit.next_safe_action()}",
            "",
            "Then report what is now true, with the evidence path for each claim. If you",
            "cannot evidence a claim, say so rather than restating it.",
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


_SENTENCE_BREAK = re.compile(r"[.!?;]\s|\n")


def _sentence_window(text: str, start: int, end: int) -> tuple[str, str]:
    """The text before and after a span, cut at the nearest sentence boundary."""
    before = text[max(0, start - 220) : start]
    parts = _SENTENCE_BREAK.split(before)
    before = parts[-1] if parts else before

    after = text[end : end + 140]
    match = _SENTENCE_BREAK.search(after)
    after = after[: match.start()] if match else after
    return before.replace("\n", " "), after.replace("\n", " ")


def _same_unit(a: str, b: str) -> bool:
    """Two unit ids naming the same unit. P-6 and P6 are the same phase."""
    norm = lambda v: re.sub(r"[^A-Z0-9]", "", (v or "").upper())  # noqa: E731
    return bool(norm(a)) and norm(a) == norm(b)


def _mentions_scope(rel_path: str, scope: TaskScope) -> bool:
    """Whether a review artifact names the unit this run built.

    A phase-wide review document is not a review of this unit, and a review of
    the previous unit is not one either. Both are matched by the same filename
    glob, so the unit's own identifiers have to appear in the name.
    """
    name = rel_path.rsplit("/", 1)[-1].lower()
    tokens = [
        t.lower()
        for t in (scope.scope_id.replace("/", "-"), scope.repository_unit_id)
        if t
    ]
    nested = scope.scope_id.split("/")[-1].lower() if "/" in scope.scope_id else ""
    if nested:
        tokens.append(nested)
    return any(re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", name) for t in tokens)


def _task_result_for(decision: AuditDecision) -> TaskResult:
    return {
        AuditDecision.VERIFIED: TaskResult.VERIFIED,
        AuditDecision.CONTRADICTED: TaskResult.CONTRADICTED,
        AuditDecision.UNPROVEN: TaskResult.UNPROVEN,
        AuditDecision.REQUIRES_INDEPENDENT_REVIEW: TaskResult.AWAITING_INDEPENDENT_REVIEW,
    }[decision]


def _is_quoted(text: str, start: int, end: int) -> bool:
    """True when the span sits inside a pair of double quotes on its own line."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end == -1 else line_end
    before = text[line_start:start]
    after = text[end:line_end]
    # An odd number of quotes before and at least one after means we are inside
    # a quoted span.
    return before.count('"') % 2 == 1 and '"' in after


def _matches(a: str, b: str) -> bool:
    """Git ids match if either is a prefix of the other (short vs full sha)."""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return False
    n = min(len(a), len(b))
    return n >= 7 and a[:n] == b[:n]


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


_CANONICAL_SUITE_RE = re.compile(
    r"pytest[^\n]*\beval/\s*(?:-|$)|run_canonical_suite\.py|finalize_status\.py", re.I
)


def _targeted_only(run_commands: list[Any]) -> str:
    """Return a description if this run executed only targeted tests."""
    pytest_cmds = [
        str(getattr(c, "command", c))
        for c in run_commands
        if "pytest" in str(getattr(c, "command", c)).lower()
    ]
    if not pytest_cmds:
        return ""
    if any(_CANONICAL_SUITE_RE.search(cmd) for cmd in pytest_cmds):
        return ""
    return "; ".join(redact(c)[:110] for c in pytest_cmds[:3])
