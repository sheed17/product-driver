"""The judgment behind the investigator, in a real run.

The investigator's loop is domain-blind; the *ideas* — which hypotheses could
explain the evidence, which probe would tell them apart — come from a strong
model reasoning over the current brief. This module wraps a fresh Claude session
as a :class:`Reasoner`, and a second, independent one as a challenger.

Both are read-only sessions. They emit structured hypotheses and probes; they do
not decide the verdict and they cannot act on the repository. Execution stays
with :class:`~neyma_product_driver.probe_runner.ProbeRunner`, which refuses
anything consequential regardless of what a model proposes.

These classes are exercised in real runs, not in the test suite (the suite never
consumes Claude usage and drives the loop with a scripted reasoner instead).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .investigation_memory import (
    Challenge,
    Hypothesis,
    HypothesisStatus,
    InterpretationRule,
    Probe,
)

_HYPOTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "statement": {"type": "string"},
                    "category": {"type": "string"},
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "predicted_observations": {"type": "array", "items": {"type": "string"}},
                    "why_plausible": {"type": "string"},
                    "what_would_refute_it": {"type": "string"},
                },
                "required": ["id", "statement", "predicted_observations"],
            },
        }
    },
    "required": ["hypotheses"],
    "additionalProperties": True,
}

_PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "probe": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "question": {"type": "string"},
                "rationale": {"type": "string"},
                "kind": {"type": "string"},
                "command_or_action": {"type": "string"},
                "inputs": {"type": "object"},
                "expected_results": {"type": "string"},
                "interpretation_rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string"},
                            "signal": {"type": "string"},
                            "value": {"type": "string"},
                        },
                        "required": ["pattern", "signal"],
                    },
                },
                "risk": {"type": "string"},
                "read_only": {"type": "boolean"},
                "targets_hypotheses": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id", "question", "kind", "command_or_action"],
        }
    },
    "required": ["probe"],
    "additionalProperties": True,
}

_REASONER_SYSTEM = """\
You are a senior diagnostic engineer investigating a failure in a local
development workflow. You reason from evidence, not from prose. A builder's or
reviewer's claim is a claim, never a fact, until a probe confirms it.

You produce two kinds of output on request:

HYPOTHESES: 2–5 competing explanations for the evidence. Not many weak ones —
a few load-bearing ones. Each must state, as `predicted_observations`, the
machine-comparable signals it implies, written as `key=value`, `key!=value`, or
`key~=regex`. These are how the harness will refute or support you: pick signals
a probe can actually measure. Include what would REFUTE each hypothesis, not only
what would confirm it.

PROBES: the single smallest, fastest, read-only action that would most separate
the live hypotheses. Prefer running one failing test directly, inspecting one
exact command, classifying commits, checking one receipt, querying one row.
Never propose rewriting history, pushing, editing status, or any write. The
probe's `interpretation_rules` turn its raw output into the signals your
hypotheses predicted, via `pattern` (a regex) → `signal` (name) → `value`
(literal or `$1` for a capture group).

Available probe kinds: COMMAND (read-only shell), GREP, FILE_STAT,
CLASSIFY_COMMITS, CHECK_RECEIPT, PROTOCOL_RESOLVE, SQL_READONLY (SELECT only),
HTTP_GET (local only), PREDICATE (a named in-memory check).

Return only the requested JSON object.
"""

_CHALLENGER_SYSTEM = """\
You are a skeptical engineer asked to REFUTE a colleague's current diagnosis. You
were given the evidence and their hypotheses, and NO preferred answer. Your job
is to find the explanation they are anchored against, or the probe whose result
would break their leading hypothesis. Propose one alternative and one read-only
probe that would distinguish it. Return only JSON.
"""


class LLMReasoner:
    """A fresh, read-only Claude session that proposes hypotheses and probes."""

    def __init__(self, repo: Path, model: str = "opus", timeout_s: int = 300) -> None:
        self.repo = Path(repo)
        self.model = model
        self.timeout_s = timeout_s
        self._system = _REASONER_SYSTEM

    def generate_hypotheses(self, brief: Any) -> list[Hypothesis]:
        payload = self._ask(
            f"{brief.render()}\n\nPropose competing HYPOTHESES now as JSON.", _HYPOTHESIS_SCHEMA
        )
        out: list[Hypothesis] = []
        for i, h in enumerate((payload or {}).get("hypotheses", []), start=1):
            try:
                out.append(
                    Hypothesis(
                        id=str(h.get("id") or f"H{i}"),
                        statement=str(h.get("statement", "")),
                        category=str(h.get("category", "")),
                        assumptions=[str(a) for a in h.get("assumptions", [])],
                        confidence=float(h.get("confidence", 0.3) or 0.3),
                        predicted_observations=[str(p) for p in h.get("predicted_observations", [])],
                        status=HypothesisStatus.ACTIVE,
                    )
                )
            except (TypeError, ValueError):
                continue
        return out

    def design_probe(self, brief: Any) -> Probe | None:
        payload = self._ask(
            f"{brief.render()}\n\nDesign the single smallest discriminating PROBE now as JSON.",
            _PROBE_SCHEMA,
        )
        p = (payload or {}).get("probe")
        if not isinstance(p, dict):
            return None
        try:
            return Probe(
                id=str(p.get("id") or f"pr{brief.iteration}"),
                question=str(p.get("question", "")),
                rationale=str(p.get("rationale", "")),
                kind=str(p.get("kind", "COMMAND")),
                command_or_action=str(p.get("command_or_action", "")),
                inputs=p.get("inputs") if isinstance(p.get("inputs"), dict) else {},
                expected_results=str(p.get("expected_results", "")),
                interpretation_rules=[
                    InterpretationRule(
                        pattern=str(r.get("pattern", "")),
                        signal=str(r.get("signal", "")),
                        value=str(r.get("value", "")),
                    )
                    for r in p.get("interpretation_rules", [])
                    if isinstance(r, dict) and r.get("pattern") and r.get("signal")
                ],
                risk=str(p.get("risk", "low")),
                read_only=bool(p.get("read_only", True)),
                targets_hypotheses=[str(t) for t in p.get("targets_hypotheses", [])],
            )
        except (TypeError, ValueError):
            return None

    def challenge(self, brief: Any) -> Challenge | None:
        return None  # the dedicated Challenger does this; the primary reasoner does not

    # -- session -----------------------------------------------------------

    def _ask(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return asyncio.run(self._session(prompt, schema))
        except RuntimeError:
            # Already inside an event loop (rare in the CLI path): fall back.
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._session(prompt, schema))
            finally:
                loop.close()
        except Exception:
            return None

    async def _session(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKClient,
            ResultMessage,
            TextBlock,
        )

        options = ClaudeAgentOptions(
            cwd=str(self.repo),
            model=self.model,
            permission_mode="default",
            setting_sources=[],
            system_prompt=self._system,
            allowed_tools=["Read", "Grep", "Glob"],
            disallowed_tools=["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "Task"],
            max_turns=12,
            output_format={"type": "json_schema", "schema": schema},
            resume=None,
            continue_conversation=False,
            fork_session=False,
        )
        chunks: list[str] = []
        structured: Any = None
        client = ClaudeSDKClient(options=options)
        await client.connect()
        try:
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            chunks.append(block.text)
                elif isinstance(message, ResultMessage):
                    if message.structured_output is not None:
                        structured = message.structured_output
                    if not chunks and message.result:
                        chunks.append(message.result)
        finally:
            await client.disconnect()

        if isinstance(structured, dict):
            return structured
        text = "".join(chunks).strip()
        try:
            return json.loads(text)
        except ValueError:
            from .evaluator import _extract_json_objects

            for candidate in _extract_json_objects(text):
                if isinstance(candidate, dict):
                    return candidate
        return None


class Challenger(LLMReasoner):
    """An independent skeptic. Same machinery, adversarial framing, no preferred answer."""

    def __init__(self, repo: Path, model: str = "opus", timeout_s: int = 300) -> None:
        super().__init__(repo, model=model, timeout_s=timeout_s)
        self._system = _CHALLENGER_SYSTEM

    def generate_hypotheses(self, brief: Any) -> list[Hypothesis]:
        return []

    def design_probe(self, brief: Any) -> Probe | None:
        return None

    def challenge(self, brief: Any) -> Challenge | None:
        payload = self._ask(
            f"{brief.render()}\n\nRefute the leading diagnosis. Return JSON with keys "
            "`alternative`, `reasoning`, and optionally `probe` (same probe schema).",
            {
                "type": "object",
                "properties": {
                    "alternative": {"type": "string"},
                    "reasoning": {"type": "string"},
                    "challenges_hypothesis": {"type": "string"},
                    "probe": _PROBE_SCHEMA["properties"]["probe"],
                },
                "required": ["alternative", "reasoning"],
                "additionalProperties": True,
            },
        )
        if not isinstance(payload, dict):
            return None
        probe = None
        raw_probe = payload.get("probe")
        if isinstance(raw_probe, dict):
            probe = self._probe_from_dict(raw_probe, brief)
        return Challenge(
            challenges_hypothesis=str(payload.get("challenges_hypothesis", "")),
            alternative=str(payload.get("alternative", "")),
            reasoning=str(payload.get("reasoning", "")),
            proposed_probe=probe,
        )

    def _probe_from_dict(self, p: dict[str, Any], brief: Any) -> Probe | None:
        try:
            return Probe(
                id=str(p.get("id") or f"challenge-pr{brief.iteration}"),
                question=str(p.get("question", "")),
                kind=str(p.get("kind", "COMMAND")),
                command_or_action=str(p.get("command_or_action", "")),
                inputs=p.get("inputs") if isinstance(p.get("inputs"), dict) else {},
                interpretation_rules=[
                    InterpretationRule(
                        pattern=str(r.get("pattern", "")),
                        signal=str(r.get("signal", "")),
                        value=str(r.get("value", "")),
                    )
                    for r in p.get("interpretation_rules", [])
                    if isinstance(r, dict) and r.get("pattern") and r.get("signal")
                ],
                read_only=bool(p.get("read_only", True)),
                targets_hypotheses=[str(t) for t in p.get("targets_hypotheses", [])],
            )
        except (TypeError, ValueError):
            return None
