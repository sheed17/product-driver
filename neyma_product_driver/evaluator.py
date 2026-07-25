"""The product evaluator — a second persistent Claude session.

The evaluator never edits the repository. It reads collected evidence and
returns a single structured :class:`EvaluatorDecision`.

Structured output is requested via the SDK's ``output_format`` json_schema
support (surfacing on ``ResultMessage.structured_output``). Because a model can
still reply in prose, the response text is parsed as a fallback, and a failure
to produce a valid decision degrades to BLOCKED rather than to a guess.
"""

from __future__ import annotations

import asyncio
import json
import re
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
from pydantic import ValidationError

from .config import EvaluatorConfig
from .models import Decision, EvaluatorDecision, redact
from .prompts import DECISION_SCHEMA, EVALUATOR_SYSTEM, decision_json_instruction


class EvaluatorSession:
    """Persistent evaluator session that returns structured decisions."""

    def __init__(
        self,
        repo: Path,
        config: EvaluatorConfig,
        resume_session_id: str | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self.repo = Path(repo)
        self.config = config
        self.resume_session_id = resume_session_id
        self.session_id: str | None = resume_session_id
        self._client: ClaudeSDKClient | None = None
        self._on_progress = on_progress or (lambda _msg: None)

    async def _can_use_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultDeny:
        # The evaluator's allowlist already restricts it to read-only tools;
        # anything reaching this callback is outside that allowlist.
        self._on_progress(f"  [EVALUATOR DENIED] {tool_name}")
        return PermissionResultDeny(
            message="The evaluator is read-only and may not use this tool.",
            interrupt=False,
        )

    def _options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            cwd=str(self.repo),
            model=self.config.model,
            permission_mode="default",
            # The evaluator judges the product, not the repo's build process; it
            # deliberately does not load Neyma's settings/hooks.
            setting_sources=[],
            system_prompt=EVALUATOR_SYSTEM,
            allowed_tools=list(self.config.allowed_tools),
            disallowed_tools=["Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch"],
            max_turns=self.config.max_turns,
            can_use_tool=self._can_use_tool,
            output_format={"type": "json_schema", "schema": DECISION_SCHEMA},
            resume=self.resume_session_id,
            fork_session=False,
        )

    async def __aenter__(self) -> "EvaluatorSession":
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

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision:
        if self._client is None:
            raise RuntimeError("EvaluatorSession is not connected; use 'async with'")

        timeout = timeout_s or self.config.turn_timeout_s
        full_prompt = f"{prompt}\n\n{decision_json_instruction()}"
        try:
            return await asyncio.wait_for(self._collect(full_prompt), timeout=timeout)
        except asyncio.TimeoutError:
            return blocked_decision(f"Evaluator did not respond within {timeout}s.")

    async def _collect(self, prompt: str) -> EvaluatorDecision:
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
                    return blocked_decision(
                        f"Evaluator session errored: {redact(message.result or message.subtype)}"
                    )
                if not chunks and message.result:
                    chunks.append(message.result)

        if structured is not None:
            decision = coerce_decision(structured)
            if decision is not None:
                return decision

        return parse_decision_text("".join(chunks))


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def blocked_decision(reason: str) -> EvaluatorDecision:
    """Safe fallback: never guess a decision."""
    return EvaluatorDecision(
        decision=Decision.BLOCKED,
        summary=reason,
        problems=[reason],
        confidence=0.0,
    )


def coerce_decision(payload: Any) -> EvaluatorDecision | None:
    """Validate a dict into an EvaluatorDecision, repairing benign mismatches."""
    if not isinstance(payload, dict):
        return None
    data = dict(payload)
    raw = str(data.get("decision", "")).strip().upper()
    if raw not in {d.value for d in Decision}:
        return None
    data["decision"] = raw
    # A model occasionally attaches a correction to a non-FIX decision; the
    # model validator drops it, but an empty FIX correction is unrecoverable.
    if raw == Decision.FIX.value and not str(data.get("correction_prompt", "")).strip():
        return None
    try:
        return EvaluatorDecision.model_validate(data)
    except ValidationError:
        return None


def _extract_json_objects(text: str) -> list[dict[str, Any]]:
    """Find candidate JSON objects in free text, outermost first."""
    found: list[dict[str, Any]] = []
    # Prefer fenced blocks, which is what models usually emit.
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S):
        try:
            obj = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            found.append(obj)

    # Then brace-balanced scan of the raw text.
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        pass
                    else:
                        if isinstance(obj, dict):
                            found.append(obj)
                    start = -1
    return found


def parse_decision_text(text: str) -> EvaluatorDecision:
    """Best-effort parse of an evaluator reply; BLOCKED if unparseable."""
    if not text or not text.strip():
        return blocked_decision("Evaluator returned an empty response.")

    for candidate in _extract_json_objects(text):
        decision = coerce_decision(candidate)
        if decision is not None:
            return decision

    return blocked_decision(
        "Evaluator did not return a valid decision object. Raw reply (truncated): "
        + redact(text.strip()[:1500])
    )
