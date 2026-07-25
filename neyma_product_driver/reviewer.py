"""Independent reviewer session.

When the repository requires an independent review, the session that wrote the
implementation may not supply it. This module launches a *fresh* Claude session
that:

  - never resumes or inherits the builder conversation
  - is read-only: no Write, Edit, or Bash
  - receives repository authority and the actual collected evidence
  - returns structured findings, each with an evidence path
  - explicitly adjudicates each discrepancy the auditor raised
  - never writes status files

Launching it is a deliberate act. The driver pauses and reports; a human runs
``python -m neyma_product_driver review --run <id>`` to authorize the transition
from implementer to independent reviewer.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
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
    },
    "required": ["verdict", "summary", "findings", "adjudications", "criteria_assessment", "confidence"],
    "additionalProperties": False,
}


REVIEWER_SYSTEM = """\
You are an INDEPENDENT REVIEWER for the Neyma repository.

You did not write this implementation. You have no history with the session that
did. You are read-only: you may Read, Grep and Glob, and nothing else. You may
not edit any file, run any command, or write any status.

Your job is to adjudicate, from evidence, whether the claimed work is actually
supported. The repository is the authority. Its CLAUDE.md, registry, acceptance
contracts and progress protocol outrank anything a builder or a harness says.

DISCIPLINE

  - Every finding must cite a concrete evidence path you actually read.
  - Distinguish what you VERIFIED from what you INFERRED. Do not blur them.
  - Code existing is not behaviour proven. Tests existing is not tests passing.
    A targeted suite passing is not the canonical suite passing. A status
    document cannot prove itself.
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

YOU DO NOT WRITE STATUS. Your output is findings and adjudications only. Marking
the registry, CURRENT.md or BUILD-STATUS is not yours to do unless the
repository explicitly authorizes an adjudication session to do so — and even
then, not in this read-only session.

Reply with a single JSON object matching the required schema and nothing else.
"""


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    finding: str
    severity: str = "major"
    evidence_path: str = ""
    reasoning: str = ""


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

    @property
    def blockers(self) -> list[ReviewFinding]:
        return [f for f in self.findings if f.severity == "blocker"]


def review_prompt(
    *,
    unit: ActiveUnit,
    audit: CompletionAudit,
    builder_report: str,
    evidence_dir: str,
    repository_context: str = "",
) -> str:
    """Build the reviewer's prompt from authority and evidence, not conversation."""
    st = audit.observed_state
    parts: list[str] = [
        "=== INDEPENDENT REVIEW REQUEST ===",
        "",
        "You are reviewing work you did not perform. Adjudicate from evidence.",
        "",
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
        "",
        "Read the repository yourself to confirm or refute each point. Assess every",
        "criterion listed above. Return the JSON review object now.",
    ]
    return "\n".join(parts)


class IndependentReviewerSession:
    """A fresh, read-only Claude session. Never resumes the builder."""

    def __init__(
        self,
        repo: Path,
        model: str = "opus",
        max_turns: int | None = 40,
        timeout_s: int = 1200,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self.repo = Path(repo)
        self.model = model
        self.max_turns = max_turns
        self.timeout_s = timeout_s
        self.session_id: str | None = None
        self._client: ClaudeSDKClient | None = None
        self._on_progress = on_progress or (lambda _m: None)

    async def _can_use_tool(
        self, tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResultDeny:
        self._on_progress(f"  [REVIEWER DENIED] {tool_name}")
        return PermissionResultDeny(
            message="The independent reviewer is read-only and may not use this tool.",
            interrupt=False,
        )

    def _options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            cwd=str(self.repo),
            model=self.model,
            permission_mode="default",
            # Repository authority is supplied in the prompt; the reviewer must
            # not inherit the builder's project hooks or subagent lenses.
            setting_sources=[],
            system_prompt=REVIEWER_SYSTEM,
            allowed_tools=["Read", "Grep", "Glob"],
            disallowed_tools=[
                "Write", "Edit", "NotebookEdit", "MultiEdit", "Bash",
                "WebFetch", "WebSearch", "Task",
            ],
            max_turns=self.max_turns,
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
            return await asyncio.wait_for(self._collect(prompt), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            return IndependentReview(
                verdict="INSUFFICIENT_EVIDENCE",
                summary=f"The independent reviewer did not respond within {self.timeout_s}s.",
                reviewer_session_id=self.session_id,
            )

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
