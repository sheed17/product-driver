"""Independent reviewer session.

When the repository requires an independent review, the session that wrote the
implementation may not supply it. This module launches a *fresh* Claude session
that:

  - never resumes or inherits the builder conversation
  - cannot change the product: no Write, Edit, MultiEdit, commit, push or deploy
  - *can* re-run the repository's own deterministic verification, under an
    explicit command boundary (see :mod:`~neyma_product_driver.reviewer_boundary`)
  - receives repository authority, the scoped task, and the exact repository
    state it is reviewing
  - returns structured findings, each with an evidence path
  - explicitly adjudicates each discrepancy the auditor raised
  - never writes status files

The one capability that is new here, and why. A reviewer with no shell can only
adjudicate the receipts the harness collected — so the harness's own honesty
becomes an unexamined premise of the review that exists to examine it. During
the P6/M3 review exactly that happened: the reviewer could not execute the M3
probe, suite or mutation battery, and half its support rested on Product
Driver's captured output. It now runs them itself and says, in
``evidence_reproduced``, whether it did.

That capability is bounded by :mod:`~neyma_product_driver.reviewer_boundary`,
which is enforced deterministically by a ``PreToolUse`` hook rather than by
instructions in a prompt — the same enforcement shape the builder uses, for the
same reason.

WHAT ``evidence_reproduced`` MEANS, AND WHAT IT USED TO MEAN
------------------------------------------------------------

It used to mean "the reviewer said so, and the boundary allowed at least one
command". That is a statement about the boundary. It is satisfied by a reviewer
that runs ``git status``, and it says nothing whatever about whether the thing
under review works.

It now means: *the reviewer itself executed deterministic verification that
exercises the product, and the observation satisfied a named expectation.* Three
independent facts, each recorded per command in ``reproduced_evidence`` — what
was requested, what actually ran and what it produced (captured by a
``PostToolUse`` hook, not read out of the reviewer's prose), and the oracle it
was measured against. Everything short of that keeps its own honest name:
structural verification, reviewer inspection, a failed expectation, a refused
command, or evidence corroborated from Product Driver's records. See
:mod:`~neyma_product_driver.reviewer_evidence`.

The driver launches this itself as a step inside the run loop. ``review`` on the
command line remains available for an ad-hoc look at a finished run; it is no
longer the way an ordinary run reaches its verdict.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Sequence

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
)
from pydantic import BaseModel, ConfigDict, Field

from .completion_auditor import CompletionAudit
from .context import ActiveUnit
from .models import redact, utcnow
from .review_cycle import BlockerKind, TreeFingerprint
from .reviewer_boundary import (
    REVIEWER_FORBIDDEN_TOOLS,
    REVIEWER_TOOLS,
    ReviewerCommandPolicy,
    classify_reviewer_tool,
)
from .reviewer_evidence import (
    EvidenceExpectation,
    EvidenceLedger,
    EvidenceObservation,
    classify_evidence,
    observation_from_tool_response,
)

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["SUPPORTED", "NOT_SUPPORTED", "INSUFFICIENT_EVIDENCE"],
        },
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "finding": {"type": "string"},
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
                    "evidence_path": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
                "required": ["finding", "severity", "evidence_path", "reasoning"],
            },
        },
        "adjudications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "discrepancy": {"type": "string"},
                    "ruling": {
                        "type": "string",
                        "enum": ["UPHELD", "OVERTURNED", "CANNOT_DETERMINE"],
                    },
                    "basis": {"type": "string"},
                },
                "required": ["discrepancy", "ruling", "basis"],
            },
        },
        "criteria_assessment": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string"},
                    "assessment": {"type": "string", "enum": ["PASS", "FAIL", "CANNOT_DETERMINE"]},
                    "basis": {"type": "string"},
                },
                "required": ["criterion", "assessment", "basis"],
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_reproduced": {
            "type": "boolean",
            "description": (
                "True only if you executed deterministic verification yourself in this "
                "session AND a named expectation you declared for it actually held. "
                "False if every fact you relied on came from records the harness "
                "collected. The harness checks this against what your commands really "
                "produced; claiming it without a satisfied expectation does not make it "
                "true, it just makes your review say something false."
            ),
        },
        "commands_run": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "purpose": {"type": "string"},
                    "what_it_showed": {"type": "string"},
                    "expectation": {
                        "type": "object",
                        "description": (
                            "The deterministic oracle for this command: what its output "
                            "and exit code must be for the claim to hold. The harness "
                            "checks it against what the command really produced."
                        ),
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": (
                                    "What this expectation is about — the invariant, the "
                                    "criterion, the probe case. Empty means you are not "
                                    "asserting anything with this command."
                                ),
                            },
                            "expect_exit_code": {
                                "type": ["integer", "null"],
                                "description": (
                                    "The exit code that must be observed. Use a non-zero "
                                    "value deliberately for a negative control. null does "
                                    "NOT mean any exit code is acceptable: it announces "
                                    "none, and a command that then exits non-zero is "
                                    "recorded as errored however well its output matched. "
                                    "Name the exit code you expect."
                                ),
                            },
                            "expect_contains": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Literal substrings the output must contain.",
                            },
                            "expect_absent": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Literal substrings the output must not contain.",
                            },
                        },
                        "required": [
                            "name",
                            "expect_exit_code",
                            "expect_contains",
                            "expect_absent",
                        ],
                    },
                },
                "required": ["command", "purpose", "what_it_showed", "expectation"],
            },
        },
        "blocked_on": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "NONE",
                        "PRODUCT_DEFECT",
                        "VERIFICATION_HARNESS",
                        "REVIEWER_CAPABILITY",
                        "REPOSITORY_AUTHORITY",
                        "EXTERNAL_ACTION",
                    ],
                },
                "detail": {"type": "string"},
                "requested_action": {"type": "string"},
            },
            "required": ["kind", "detail", "requested_action"],
        },
    },
    "required": [
        "verdict",
        "summary",
        "findings",
        "adjudications",
        "criteria_assessment",
        "confidence",
        "evidence_reproduced",
        "commands_run",
        "blocked_on",
    ],
    "additionalProperties": False,
}


_REVIEWER_SYSTEM_BASE = """\
You are an INDEPENDENT REVIEWER for the Neyma repository.

You did not write this implementation. You have no history with the session that
did. You cannot change the product: no Write, Edit, MultiEdit, no commit, no
push, no merge, no deploy, no network, no credentials. You never write a status
file.

Your job is to adjudicate, from evidence, whether the claimed work is actually
supported. The repository is the authority. Its CLAUDE.md, registry, acceptance
contracts and progress protocol outrank anything a builder or a harness says.

DISCIPLINE

  - Every finding must cite a concrete evidence path you actually read, or a
    command you actually ran in this session.
  - Distinguish what you VERIFIED from what you INFERRED. Do not blur them.
  - Code existing is not behaviour proven. Tests existing is not tests passing.
    A targeted suite passing is not the canonical suite passing. A status
    document cannot prove itself.
  - The harness's own captured output is a CLAIM about what happened, exactly
    like the builder's report. Where you can re-run the thing yourself, re-run
    it; where you cannot, say the evidence was corroborated rather than
    reproduced.
  - If the evidence for a criterion does not exist, the assessment is
    CANNOT_DETERMINE — never PASS. Absence of evidence is never evidence.
  - Do not accept a receipt whose commit or tree does not match the state it
    claims to validate.
  - You may not recommend deleting or weakening any acceptance guard, test or
    control in order to obtain a green result.

VERDICT

  SUPPORTED             the claims are supported by evidence you verified
  NOT_SUPPORTED         the evidence contradicts one or more claims
  INSUFFICIENT_EVIDENCE you cannot determine it from what exists

WHAT HAPPENS TO YOUR ANSWER, so you can aim it correctly:

  SUPPORTED             the scoped task's review requirement is satisfied and
                        the run continues toward acceptance.
  NOT_SUPPORTED with findings that cite evidence
                        goes straight back to the SAME builder session as a
                        grounded correction, and a NEW reviewer looks at the
                        result. Refuse this way only for a real correctness or
                        safety defect in the product.
  INSUFFICIENT_EVIDENCE stops the run fail-closed. It is the right answer when
                        you genuinely cannot tell — and the wrong answer when
                        you simply have not run the verification you were
                        allowed to run.

`blocked_on.kind` decides who owns a review that could not conclude. Choose it
honestly; each one goes somewhere different:

  NONE                   nothing blocked you.
  PRODUCT_DEFECT         the implementation is wrong. Goes to the builder.
  VERIFICATION_HARNESS   Product Driver cannot produce the evidence this review
                         needs. That is the DRIVER's gap to fix — it must not
                         become a change to the product.
  REVIEWER_CAPABILITY    the evidence exists but is outside your read-only
                         boundary. Say exactly which command or file you needed.
  REPOSITORY_AUTHORITY   two authoritative sources disagree, or none of them
                         answers the question. A human decides that.
  EXTERNAL_ACTION        concluding needs something outside this machine — a
                         push, a CI run, a credential, partner data. Name the
                         exact action in `requested_action`. NEVER invent or
                         assume the result of one.

REPRODUCED EVIDENCE IS NOT "I WAS ALLOWED TO RUN SOMETHING"

List every command you ran in `commands_run`. For each one, declare the
`expectation` BEFORE you rely on what you saw: a `name` for what the command is
supposed to establish, and the deterministic assertions that decide it —
`expect_exit_code`, `expect_contains`, `expect_absent`. This is the same shape
of oracle this repository's own scenarios use, and it is checked by the harness
against what your command actually produced. Your prose is not what decides it.

  - A command that exercises the product — a test suite, a probe, a mutation
    battery — with a satisfied expectation is REPRODUCED RUNTIME EVIDENCE.
  - A command that reads the repository — `git status`, `git diff`, `grep`,
    `ls` — with a satisfied expectation is STRUCTURAL verification. Real, and
    not the same thing. It never makes `evidence_reproduced` true on its own.
  - A command with no named expectation is an inspection. Say so; it establishes
    nothing by machine.
  - A negative control is a first-class oracle: if the point is that the command
    must FAIL, declare the non-zero `expect_exit_code` and it counts exactly as
    much as a passing one.
  - Declare `expect_exit_code` whenever you can. Substrings alone do not announce
    an exit code, and a command that exits non-zero with nothing having announced
    it establishes nothing — `pytest -q` prints "passed" on its way to failing.

SET `evidence_reproduced` TO TRUE ONLY IF at least one command you ran in this
session exercised the product AND its named expectation held. If you ran
nothing, `evidence_reproduced` is false and `commands_run` is empty — say so
rather than implying you measured something you read. An expectation that failed
is not a reason to hide it: record it, and let it inform your verdict.

YOU DO NOT WRITE STATUS. Your output is findings and adjudications only. Marking
the registry, CURRENT.md or BUILD-STATUS is not yours to do unless the
repository explicitly authorizes an adjudication session to do so — and even
then, not in this session.

Reply with a single JSON object matching the required schema and nothing else.
"""

_REVIEWER_READ_ONLY_APPEND = """\

TOOLS AVAILABLE TO YOU: Read, Grep and Glob. You cannot execute commands in this
run, so every fact you rely on comes from files you read. Where a claim rests on
a command's output that you could not reproduce, mark it corroborated rather than
verified, and set `evidence_reproduced` to false.
"""

_REVIEWER_EXECUTION_APPEND = """\

TOOLS AVAILABLE TO YOU: Read, Grep, Glob — and Bash, under a strict boundary.

USE IT. You were given execution precisely so this review does not rest on the
harness's own captured output. Re-run the deterministic verification that bears
on the question you were called for: the repository's tests, its probes, its
mutation batteries; read the diff with `git diff`; check the tree with
`git status` and `git rev-parse`. A review that could have measured something and
chose to read about it instead is a weaker review.

The boundary is enforced deterministically, not by your restraint. A command
outside it is refused before it runs and the refusal is recorded in the run
evidence. You may not, by any route — a wrapper script, a substitution, an
interpreter payload, a pipe:

  * write, create, edit, move or delete any file
  * commit, push, merge, rebase, reset, checkout, stash or otherwise touch git
    state
  * deploy, publish, or reach any network
  * send mail, Slack or SMS, or cause any external-world effect
  * read a secret, credential, key or token
  * install software of any kind
  * compose commands with `;`, `|`, `&`, `>`, `<`, `$(...)` or backticks

A refused command is not a defect in the product and must never be reported as
one. If the verification you need is genuinely outside this boundary, say so in
`blocked_on` with `kind: REVIEWER_CAPABILITY` and name the exact command.

{vocabulary}
"""


def reviewer_system_prompt(policy: ReviewerCommandPolicy | None) -> str:
    """The reviewer's system prompt, matching the capability it actually has.

    Rendered from the policy rather than written twice, so a reviewer is never
    told it may run commands it cannot, or left unaware of ones it may.
    """
    if policy is None:
        return _REVIEWER_SYSTEM_BASE + _REVIEWER_READ_ONLY_APPEND
    return _REVIEWER_SYSTEM_BASE + _REVIEWER_EXECUTION_APPEND.format(
        vocabulary=policy.vocabulary_block()
    )


#: Kept as a module constant because tests and docs refer to it by name. It is
#: the read-only form; :func:`reviewer_system_prompt` is what a session uses.
REVIEWER_SYSTEM = _REVIEWER_SYSTEM_BASE + _REVIEWER_READ_ONLY_APPEND


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    finding: str
    severity: str = "major"
    evidence_path: str = ""
    reasoning: str = ""


class ReviewCommandExpectation(BaseModel):
    """The oracle a reviewer declares for one command it runs.

    The reviewer chooses it; the harness decides whether it held. That
    asymmetry is the point: a model naming what must be observed is judgement,
    which is what a reviewer is for, and a model asserting that it *was*
    observed is a claim, which is what this whole layer exists not to accept.

    Same three assertion shapes a scenario uses — see
    :class:`~neyma_product_driver.reviewer_evidence.EvidenceExpectation`.
    """

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    expect_exit_code: int | None = None
    expect_contains: list[str] = Field(default_factory=list)
    expect_absent: list[str] = Field(default_factory=list)


class ReviewCommand(BaseModel):
    """One command the reviewer says it ran, what it expected, and what it showed.

    The reviewer's own account. The *authoritative* record of what it was
    permitted to run is the boundary's, recorded separately as
    ``executed_commands``; where the two disagree the boundary's is the fact.
    ``what_it_showed`` is prose and decides nothing. ``expectation`` is the part
    a machine can check, and the only part that can make evidence reproduced.
    """

    model_config = ConfigDict(extra="ignore")

    command: str = ""
    purpose: str = ""
    what_it_showed: str = ""
    expectation: ReviewCommandExpectation = Field(default_factory=ReviewCommandExpectation)


class ReviewBlocker(BaseModel):
    """What stopped the review from concluding, and therefore who owns it."""

    model_config = ConfigDict(extra="ignore")

    kind: str = BlockerKind.NONE.value
    detail: str = ""
    requested_action: str = ""

    @property
    def blocking(self) -> bool:
        return BlockerKind.parse(self.kind) is not BlockerKind.NONE


class Adjudication(BaseModel):
    model_config = ConfigDict(extra="ignore")

    discrepancy: str
    ruling: str = "CANNOT_DETERMINE"
    basis: str = ""


class CriterionAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    criterion: str
    assessment: str = "CANNOT_DETERMINE"
    basis: str = ""


class IndependentReview(BaseModel):
    """The reviewer's structured output. Advisory: it never writes status."""

    model_config = ConfigDict(extra="ignore")

    verdict: str = "INSUFFICIENT_EVIDENCE"
    summary: str = ""
    findings: list[ReviewFinding] = Field(default_factory=list)
    adjudications: list[Adjudication] = Field(default_factory=list)
    criteria_assessment: list[CriterionAssessment] = Field(default_factory=list)
    confidence: float = 0.0
    reviewer_session_id: str | None = None
    reviewed_at: str = Field(default_factory=utcnow)
    inherited_builder_context: bool = False  # always False; asserted by tests

    # -- what the reviewer says about its own evidence ---------------------

    #: The reviewer's claim that it executed verification itself. Corrected
    #: downward by the session: a reviewer cannot claim to have reproduced
    #: evidence it never ran, and it cannot claim to have reproduced evidence
    #: whose named expectation did not hold. Never corrected upward — a reviewer
    #: that says it measured nothing is believed.
    evidence_reproduced: bool = False
    #: What the reviewer originally said, kept after the correction above so the
    #: artifact records the disagreement rather than hiding it.
    claimed_evidence_reproduced: bool = False
    #: The reviewer's own account of what it ran.
    commands_run: list[ReviewCommand] = Field(default_factory=list)
    #: What stopped it concluding, and therefore who owns the result.
    blocked_on: ReviewBlocker = Field(default_factory=ReviewBlocker)

    # -- what the harness recorded, which is the authoritative half --------

    #: Commands the reviewer asked to run, with the boundary's decision on each.
    #: Written by the session from its own hook, not by the model.
    executed_commands: list[dict[str, Any]] = Field(default_factory=list)
    #: One record per command, joining the boundary's decision, the harness's
    #: own observation of what the command produced, and the named expectation
    #: it was supposed to satisfy. Written by the session, never by the model.
    #: See :mod:`~neyma_product_driver.reviewer_evidence`.
    reproduced_evidence: list[dict[str, Any]] = Field(default_factory=list)
    #: The exact repository state this review is evidence about.
    reviewed_fingerprint: dict[str, Any] = Field(default_factory=dict)
    #: The scoped task this review was asked to adjudicate.
    review_scope_id: str = ""
    #: The builder session this review must not be. Recorded so independence is
    #: checkable from the artifact alone.
    builder_session_id: str = ""

    @property
    def blockers(self) -> list[ReviewFinding]:
        return [f for f in self.findings if f.severity == "blocker"]

    @property
    def commands_allowed(self) -> list[dict[str, Any]]:
        return [c for c in self.executed_commands if c.get("allowed")]

    @property
    def commands_refused(self) -> list[dict[str, Any]]:
        return [c for c in self.executed_commands if not c.get("allowed")]

    @property
    def evidence(self) -> EvidenceLedger:
        """The per-command evidence record, rebuilt from what the session wrote."""
        return EvidenceLedger.from_dicts(self.reproduced_evidence)

    @property
    def reproduced_runtime_evidence(self) -> bool:
        """Whether the reviewer really established anything by running the product.

        Three things have to hold, and each of them rules out a different way of
        being wrong:

        * the reviewer said it reproduced evidence — never corrected upward;
        * the boundary recorded a command it actually let through — a refused
          command measured nothing;
        * and at least one of those commands **exercised the product** and its
          **named expectation was satisfied by what the harness observed**.

        The third is the one this property used to be missing. Without it,
        launching ``git status`` successfully was indistinguishable from running
        the probe and watching the invariant hold, and the founder summary said
        the same sentence about both.
        """
        return bool(
            self.evidence_reproduced
            and self.commands_allowed
            and self.evidence.reproduced_runtime_evidence
        )

    @property
    def structurally_verified(self) -> bool:
        """Whether the reviewer at least checked something deterministic itself."""
        return bool(self.evidence.structurally_verified)

    def evidence_basis(self) -> str:
        """One line naming exactly what this review's evidence rests on."""
        ledger = self.evidence
        if self.reproduced_runtime_evidence:
            return ledger.basis()
        if self.claimed_evidence_reproduced and not self.evidence_reproduced:
            return (
                "claimed reproduced, but "
                + ledger.basis()
                + " — the claim is not carried"
            )
        if ledger.records:
            return ledger.basis()
        return "corroborated from Product Driver's captured records; not reviewer-reproduced"

    def evidence_lines(self, limit: int = 12) -> list[str]:
        """Per-command evidence, in the detail a reader needs to disbelieve it."""
        return [record.brief() for record in list(self.evidence)[:limit]]

    def fingerprint(self) -> TreeFingerprint:
        return TreeFingerprint.from_dict(self.reviewed_fingerprint)


def _declared_oracle_block(declared: Any, limit: int = 12) -> str:
    """The oracles this repository already wrote down, for the reviewer to reuse.

    A reviewer that reaches for the repository's own declared expectation is
    verifying against a human's statement of what the command must show, rather
    than against one it invented a moment ago. Where such an oracle exists it is
    the one the harness applies, whatever the reviewer declares, so this block
    exists so the reviewer is not surprised by that.
    """
    commands = list(getattr(declared, "commands", ()) or ())
    if not commands:
        return ""
    lines = [
        "--- EXPECTATIONS THIS REPOSITORY ALREADY DECLARES ---",
        "A human wrote each of these commands into a scenario file together with what "
        "it must show. Run one of these and its declared expectation is the oracle the "
        "harness applies — you do not have to invent one, and yours will not override it:",
    ]
    for command in commands[:limit]:
        expectation = declared.for_command(command)
        if expectation is None:
            continue
        lines.append(f"  {command}")
        lines.append(f"      must show: {expectation.describe()}")
    if len(commands) > limit:
        lines.append(f"  ... and {len(commands) - limit} more")
    return "\n".join(lines)


def review_prompt(
    *,
    unit: ActiveUnit,
    audit: CompletionAudit,
    builder_report: str,
    evidence_dir: str,
    repository_context: str = "",
    task: str = "",
    risk: Any = None,
    changed_files: Sequence[str] = (),
    suite_summary: str = "",
    scope: Any = None,
    requirement: Any = None,
    fingerprint: TreeFingerprint | None = None,
    policy: ReviewerCommandPolicy | None = None,
    diff_stat: str = "",
    prior_review: Any = None,
) -> str:
    """Build the reviewer's prompt from authority and evidence, not conversation.

    ``task``, ``risk``, ``changed_files`` and ``suite_summary`` make the review
    *focused*: a reviewer told which high-consequence surface this change touched
    and what the scenario suite already demonstrated spends its one pass on the
    question that earned it, instead of re-deriving the situation from the
    repository. Omitting them yields the original whole-state review.

    ``requirement`` says why the review is owed and on whose authority;
    ``fingerprint`` names the exact state under review, so a reviewer can check
    for itself that it is looking at what it was asked to look at; ``policy``
    supplies the commands it may run. ``prior_review`` is the refusal a builder
    has just corrected — supplied so a *fresh* reviewer knows what was alleged
    without ever having been in the conversation where it was alleged.
    """
    st = audit.observed_state
    parts: list[str] = [
        "=== INDEPENDENT REVIEW REQUEST ===",
        "",
        "You are reviewing work you did not perform. Adjudicate from evidence.",
        "",]
    if requirement is not None and getattr(requirement, "required", False):
        parts += [
            "--- WHY THIS REVIEW IS REQUIRED ---",
            *(f"  - {reason}" for reason in getattr(requirement, "reasons", []) or []),
            "",
        ]
    if fingerprint is not None:
        parts += [
            "--- THE EXACT STATE YOU ARE REVIEWING ---",
            f"  {fingerprint.describe()}",
            "",
            "This review is evidence about THAT state and no other. If the implementation "
            "changes afterwards, this review stops applying to it and a new one is taken. "
            "Check the state yourself before you rely on it.",
            "",
        ]
    if task:
        parts += ["--- WHAT THE PRODUCT OWNER ASKED FOR ---", task.strip()[:4000], ""]
    if scope is not None and getattr(scope, "render", None) is not None:
        parts += ["--- THE UNIT THIS REVIEW IS ABOUT ---", scope.render(), ""]
    if audit.scope is not None and audit.scope.is_nested:
        parts += [
            "--- THE SCOPE OF THIS REVIEW ---",
            audit.scope.render(),
            "",
            f"Review {audit.scope.scope_id} against what {audit.scope.scope_id} owes. "
            f"Do not withhold support because {audit.scope.parent_phase_id} is unfinished — "
            "it is supposed to be. A finding about work this task was told not to do is "
            "not a blocker on this task.",
            "",
        ]
    if risk is not None and getattr(risk, "surfaces", None):
        parts += [
            "--- WHY THIS REVIEW WAS TRIGGERED (focus here first) ---",
            f"This change was classified {getattr(getattr(risk, 'level', None), 'value', '?')} "
            "because it touches:",
            *(f"  - {surface}" for surface in risk.surfaces),
            "",
            "Those surfaces are where a defect reaches a customer, a payment, an",
            "authorization boundary or a tenant wall. Spend this review on whether the",
            "change is correct THERE. Ordinary code quality is not what you were called for.",
            "",
        ]
    if changed_files:
        parts += [
            "--- FILES THIS RUN CHANGED ---",
            *(f"  {name}" for name in list(changed_files)[:60]),
            "",
        ]
    if diff_stat:
        parts += ["--- DIFF STAT (read the diff yourself) ---", diff_stat[:4000], ""]
    if prior_review is not None:
        parts += [
            "--- WHAT AN EARLIER REVIEWER ALLEGED, AND THE BUILDER THEN CORRECTED ---",
            "You are a different session. You were not present for any of this and you "
            "are not bound by it. It is here so the same ground is not lost, not so that "
            "it is re-asserted: check each point against the code as it stands now.",
            f"  earlier verdict: {getattr(prior_review, 'verdict', '?')}",
            *(
                f"  - [{getattr(f, 'severity', 'major')}] {getattr(f, 'finding', '')} "
                f"({getattr(f, 'evidence_path', '')})"
                for f in list(getattr(prior_review, "findings", []) or [])[:12]
            ),
            "",
        ]
    if suite_summary:
        parts += [
            "--- WHAT THE SCENARIO SUITE ALREADY DEMONSTRATED ---",
            suite_summary[:4000],
            "",
            "Treat this as measurement you may verify, not as a conclusion you must accept.",
            "",
        ]
    parts += [
        "--- REPOSITORY AUTHORITY ---",
        repository_context or unit.render(),
        "",
        "--- OBSERVED REPOSITORY STATE (collected by the harness, verify it yourself) ---",
        f"branch: {st.branch}   HEAD: {st.head_commit[:12]}   tree: {st.head_tree[:12]}",
        f"working tree: {st.dirty_file_count} modified, {st.untracked_count} untracked",
        f"registry status of {unit.unit_id}: {st.active_unit_status}",
        f"verified weighted progress: {st.progress.percent:.0f}% "
        f"(earned {st.progress.earned_weight:g} of {st.progress.total_weight:g})",
        f"criteria not passing: {', '.join(st.progress.pending) or 'none'}",
        f"criteria requiring an independent session: {', '.join(st.progress.independent_pending) or 'none'}",
        "",
        "receipts:",
    ]
    for r in st.receipts:
        parts.append(
            f"  {r.name}: exists={r.exists} passed={r.passed} matches_current_tree={r.matches_head}"
            f"  ({r.detail})"
        )

    parts += [
        "",
        "--- THE IMPLEMENTING SESSION'S REPORT (a claim, not evidence) ---",
        (builder_report or "(none)")[:8000],
        "",
        "--- DISCREPANCIES THE AUDITOR RAISED (adjudicate each one explicitly) ---",
    ]
    if audit.contradictions:
        for i, c in enumerate(audit.contradictions, 1):
            parts.append(f"  {i}. {c.render()}")
    else:
        parts.append("  (none raised)")

    if audit.missing_evidence:
        parts += ["", "--- EVIDENCE THE AUDITOR COULD NOT FIND ---"]
        parts += [f"  - {m}" for m in audit.missing_evidence]

    parts += [
        "",
        f"--- RUN EVIDENCE DIRECTORY ---\n{evidence_dir}",
    ]

    if policy is not None:
        parts += [
            "",
            "--- VERIFICATION YOU MAY RUN YOURSELF ---",
            policy.vocabulary_block(),
            "",
            "Everything above this line is a CLAIM about what happened, including the "
            "harness's own suite summary and receipts. Re-run what bears on the question "
            "you were called for before you rely on it. Then set `evidence_reproduced` "
            "and fill in `commands_run` from what you actually executed.",
            "",
            "For each command, declare its `expectation` — a name for what it establishes, "
            "and the deterministic assertions that decide it. The harness compares that "
            "expectation to the command's real exit code and output. A command with no "
            "named expectation is recorded as an inspection and establishes nothing; a "
            "command that reads the repository rather than running the product is recorded "
            "as structural verification, not reproduced runtime evidence.",
        ]
        declared_block = _declared_oracle_block(getattr(policy, "declared", None))
        if declared_block:
            parts += ["", declared_block]
    else:
        parts += [
            "",
            "--- YOU CANNOT EXECUTE ANYTHING IN THIS REVIEW ---",
            "Command execution is not enabled for this reviewer, so every fact above is a "
            "record you can read but not reproduce. Say so: `evidence_reproduced` is "
            "false, and a criterion whose only support is an unreproducible record is "
            "CANNOT_DETERMINE rather than PASS.",
        ]

    parts += [
        "",
        "Read the repository yourself to confirm or refute each point. Assess every",
        "criterion listed above. Return the JSON review object now.",
    ]
    return "\n".join(parts)


class IndependentReviewerSession:
    """A fresh Claude session that can verify but cannot change anything.

    Never resumes, continues or forks the builder conversation — a review whose
    session inherited the implementer's context is not independent, and that is
    a property of how the session is constructed rather than a promise made in a
    prompt.

    Execution, when ``command_policy`` is supplied, is enforced by a
    ``PreToolUse`` hook. That is not a stylistic choice: a whole-tool
    ``allowed_tools`` entry *shadows* ``can_use_tool``, so adding ``Bash`` to
    the allow list while relying on the permission callback would have silently
    handed the reviewer an unrestricted shell. The hook fires whatever
    pre-approved the tool, which is the same reason the builder uses one.
    """

    def __init__(
        self,
        repo: Path,
        model: str = "opus",
        max_turns: int | None = 40,
        timeout_s: int = 1200,
        on_progress: Callable[[str], None] | None = None,
        command_policy: ReviewerCommandPolicy | None = None,
        fingerprint: TreeFingerprint | None = None,
        scope_id: str = "",
        builder_session_id: str = "",
    ) -> None:
        self.repo = Path(repo)
        self.model = model
        self.max_turns = max_turns
        self.timeout_s = timeout_s
        self.session_id: str | None = None
        self._client: ClaudeSDKClient | None = None
        self._on_progress = on_progress or (lambda _m: None)
        #: What this reviewer may execute. ``None`` means it may execute nothing.
        self.command_policy = command_policy
        #: The exact state under review, bound into the returned review.
        self.fingerprint = fingerprint
        self.scope_id = scope_id
        #: The session this reviewer must not be. Recorded, never resumed.
        self.builder_session_id = builder_session_id

    # -- the boundary -----------------------------------------------------

    @property
    def executions(self) -> list[Any]:
        return list(getattr(self.command_policy, "executions", []) or [])

    async def _can_use_tool(
        self, tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResultDeny:
        """The catch-all. Reached only for tools ``allowed_tools`` does not name.

        Everything the reviewer is actually permitted to use is pre-approved and
        then policed by the hook below, so arriving here at all means a tool
        outside the reviewer's set was requested.
        """
        verdict = classify_reviewer_tool(tool_name, tool_input, self.command_policy)
        self._on_progress(f"  [REVIEWER DENIED] {tool_name}: {verdict.reason}")
        return PermissionResultDeny(
            message=(
                f"The independent reviewer may not use this tool: {verdict.reason}"
            ),
            interrupt=False,
        )

    async def _pre_tool_use_hook(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        """Deterministic enforcement, above whatever pre-approved the tool.

        Fires for pre-approved tools too, which is the whole point: ``Bash`` is
        in ``allowed_tools`` when execution is enabled, so this is the only layer
        that decides which commands actually run.
        """
        tool_name = str(input_data.get("tool_name", ""))
        tool_input = input_data.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}

        verdict = classify_reviewer_tool(tool_name, tool_input, self.command_policy)

        if tool_name == "Bash" and self.command_policy is not None:
            command = str(tool_input.get("command", ""))
            self.command_policy.record(
                command,
                verdict,
                tool_use_id=str(
                    tool_use_id or input_data.get("tool_use_id", "") or ""
                ),
            )
            if verdict.allowed:
                self._on_progress(f"  [REVIEWER RAN] {command[:140]}")
            else:
                self._on_progress(f"  [REVIEWER REFUSED] {command[:120]} — {verdict.reason}")

        if verdict.allowed:
            return {}

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Refused by the independent-reviewer boundary: {verdict.reason}. "
                    "A reviewer verifies; it never changes the product, reaches the "
                    "network, or reads a secret. This refusal is recorded in the run "
                    "evidence and is not a defect in the product."
                ),
            }
        }

    async def _post_tool_use_hook(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        """Record what a permitted command actually produced.

        This is the half the boundary structurally cannot supply. ``PreToolUse``
        knows what was *asked for* and whether it was allowed; only ``PostToolUse``
        knows the exit code and the output, and without those,
        ``evidence_reproduced`` can only ever mean "a command was launched".

        It observes and returns ``{}``. It never denies, never rewrites and never
        influences the session — an observation hook that could change the run
        would be a second enforcement layer nobody asked for.
        """
        return self._observe(input_data, tool_use_id)

    async def _post_tool_use_failure_hook(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        """The same, for a tool that did not complete at all.

        A timeout, an interrupt or a launch failure is not a command that ran and
        exited non-zero — a negative control legitimately does the latter — so it
        is recorded as its own thing and can never satisfy an expectation.
        """
        return self._observe(
            input_data,
            tool_use_id,
            failed=True,
            error=str(input_data.get("error", "") or ""),
        )

    def _observe(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        *,
        failed: bool = False,
        error: str = "",
    ) -> dict[str, Any]:
        if self.command_policy is None:
            return {}
        if str(input_data.get("tool_name", "")) != "Bash":
            return {}
        tool_input = input_data.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}
        observation = observation_from_tool_response(
            tool_input,
            input_data.get("tool_response"),
            execution_failed=failed,
            error=error,
        )
        self.command_policy.observe(
            tool_use_id=str(tool_use_id or input_data.get("tool_use_id", "") or ""),
            command=str(tool_input.get("command", "") or ""),
            command_executed=observation.command_executed,
            exit_code=observation.exit_code,
            exit_code_inferred=observation.exit_code_inferred,
            output=redact(observation.output),
            execution_failed=observation.execution_failed,
            error_detail=redact(observation.error_detail),
            response_seen=observation.response_seen,
        )
        return {}

    def _options(self) -> ClaudeAgentOptions:
        executes = self.command_policy is not None
        forbidden = list(REVIEWER_FORBIDDEN_TOOLS)
        if not executes:
            forbidden.append("Bash")
        return ClaudeAgentOptions(
            cwd=str(self.repo),
            model=self.model,
            permission_mode="default",
            # Repository authority is supplied in the prompt; the reviewer must
            # not inherit the builder's project hooks or subagent lenses.
            setting_sources=[],
            system_prompt=reviewer_system_prompt(self.command_policy),
            allowed_tools=[*REVIEWER_TOOLS, *(["Bash"] if executes else [])],
            disallowed_tools=forbidden,
            max_turns=self.max_turns,
            # The hook is the enforcement layer: an allowed_tools entry shadows
            # can_use_tool, so Bash would otherwise be unrestricted. The callback
            # stays as the catch-all for everything not in the allow list.
            hooks={
                "PreToolUse": [HookMatcher(hooks=[self._pre_tool_use_hook])],
                # Observation, not enforcement. PreToolUse cannot see an exit
                # code, and an exit code is half of what "the reviewer verified
                # this" means.
                "PostToolUse": [HookMatcher(hooks=[self._post_tool_use_hook])],
                "PostToolUseFailure": [
                    HookMatcher(hooks=[self._post_tool_use_failure_hook])
                ],
            },
            can_use_tool=self._can_use_tool,
            output_format={"type": "json_schema", "schema": REVIEW_SCHEMA},
            # No resume, no continue, no fork: a genuinely fresh session.
            resume=None,
            continue_conversation=False,
            fork_session=False,
        )

    async def __aenter__(self) -> "IndependentReviewerSession":
        self._client = ClaudeSDKClient(options=self._options())
        await self._client.connect()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    async def review(self, prompt: str) -> IndependentReview:
        if self._client is None:
            raise RuntimeError("IndependentReviewerSession is not connected; use 'async with'")
        try:
            review = await asyncio.wait_for(self._collect(prompt), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            review = IndependentReview(
                verdict="INSUFFICIENT_EVIDENCE",
                summary=f"The independent reviewer did not respond within {self.timeout_s}s.",
                reviewer_session_id=self.session_id,
            )
        return self.bind(review)

    def bind(self, review: IndependentReview) -> IndependentReview:
        """Attach what the harness knows to what the reviewer said.

        The reviewer's own account of what it ran is a claim. The boundary's
        record is the fact, so it is written here rather than trusted from the
        reply, and ``evidence_reproduced`` is corrected downward when the two
        disagree. Nothing is ever corrected *upward*: a reviewer that says it ran
        nothing is believed.

        The correction now has two limbs rather than one. The old limb — a
        reviewer cannot have reproduced evidence with no command the boundary
        allowed — was necessary and never sufficient: it proved a command was
        *launched*, which is a fact about the boundary. The second limb is that
        at least one launched command must have exercised the product and had a
        named expectation satisfied by what the harness itself observed.
        """
        review.reviewer_session_id = review.reviewer_session_id or self.session_id
        review.executed_commands = [e.to_dict() for e in self.executions]
        review.builder_session_id = self.builder_session_id
        review.review_scope_id = self.scope_id or review.review_scope_id
        if self.fingerprint is not None:
            review.reviewed_fingerprint = self.fingerprint.to_dict()

        review.claimed_evidence_reproduced = bool(review.evidence_reproduced)
        review.reproduced_evidence = [e.to_dict() for e in self.evidence_for(review)]
        if not (review.commands_allowed and review.evidence.reproduced_runtime_evidence):
            review.evidence_reproduced = False
        return review

    def evidence_for(self, review: IndependentReview) -> list[Any]:
        """Join every boundary decision to its observation and its oracle.

        Three inputs, in decreasing order of how much they are trusted:

        1. the boundary's own record of the request and the decision;
        2. the harness's own observation of the exit code and output;
        3. the expectation — the repository's, where a human declared one for
           this command in a scenario file, and otherwise the reviewer's own.

        (3) is the only input a model touches, and it is a statement of what
        *must* be observed, checked against (2). The reviewer choosing the
        expectation is the reviewer exercising judgement; the reviewer asserting
        the expectation held is what this refuses to accept.
        """
        declared = getattr(self.command_policy, "declared", None)
        by_command: dict[str, Any] = {}
        for entry in review.commands_run:
            command = str(getattr(entry, "command", "") or "").strip()
            if command and command not in by_command:
                by_command[command] = entry.expectation

        records: list[Any] = []
        for execution in self.executions:
            expectation: EvidenceExpectation | None = None
            if declared is not None:
                expectation = declared.for_command(execution.command)
            if expectation is None:
                claimed = by_command.get(execution.command.strip())
                if claimed is not None:
                    expectation = EvidenceExpectation.from_any(
                        claimed, source="reviewer-declared"
                    )
            observation = (
                EvidenceObservation(
                    command_executed=execution.command_executed,
                    exit_code=execution.exit_code,
                    exit_code_inferred=execution.exit_code_inferred,
                    output=execution.output,
                    execution_failed=execution.execution_failed,
                    error_detail=execution.error_detail,
                    response_seen=execution.response_seen,
                )
                if execution.observed
                else None
            )
            records.append(
                classify_evidence(
                    command_requested=execution.command,
                    allowed=execution.allowed,
                    basis=execution.basis,
                    refusal_reason=execution.reason,
                    observation=observation,
                    expectation=expectation,
                )
            )
        return records

    async def _collect(self, prompt: str) -> IndependentReview:
        assert self._client is not None
        chunks: list[str] = []
        structured: Any = None

        await self._client.query(prompt)
        async for message in self._client.receive_response():
            if isinstance(message, SystemMessage):
                sid = (message.data or {}).get("session_id")
                if sid:
                    self.session_id = sid
            elif isinstance(message, AssistantMessage):
                if message.session_id:
                    self.session_id = message.session_id
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
            elif isinstance(message, ResultMessage):
                if message.session_id:
                    self.session_id = message.session_id
                if message.structured_output is not None:
                    structured = message.structured_output
                if message.is_error:
                    return IndependentReview(
                        verdict="INSUFFICIENT_EVIDENCE",
                        summary=f"Reviewer session errored: {redact(message.result or message.subtype)}",
                        reviewer_session_id=self.session_id,
                    )
                if not chunks and message.result:
                    chunks.append(message.result)

        review = parse_review(structured if structured is not None else "".join(chunks))
        review.reviewer_session_id = self.session_id
        return review


def parse_review(payload: Any) -> IndependentReview:
    """Validate a reviewer reply; degrade to INSUFFICIENT_EVIDENCE, never to PASS."""
    if isinstance(payload, dict):
        try:
            return IndependentReview.model_validate(payload)
        except Exception:
            pass

    text = payload if isinstance(payload, str) else ""
    if text.strip():
        from .evaluator import _extract_json_objects

        for candidate in _extract_json_objects(text):
            try:
                return IndependentReview.model_validate(candidate)
            except Exception:
                continue

    return IndependentReview(
        verdict="INSUFFICIENT_EVIDENCE",
        summary="The reviewer did not return a valid review object.",
    )


def review_to_json(review: IndependentReview) -> str:
    return json.dumps(review.model_dump(mode="json"), indent=2)
