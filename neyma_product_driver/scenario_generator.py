"""Where scenario *ideas* come from, and how they are parsed fail-closed.

The generator answers one question:

    Given what was requested, what changed, what the product promises, and what
    could realistically fail, what are the highest-value situations we should
    exercise before trusting this implementation?

It deliberately does **not** answer "why did this fail" — that is the
investigator's question, and the two stay separate. Investigation findings flow
*into* generation as evidence (a proven race suggests situations to exercise),
never the other way round.

The seam is :class:`ScenarioReasoner`. In a real run it is a fresh, read-only
Claude session returning structured output; in the test suite it is a scripted
stand-in, so the whole planning loop is deterministic and consumes no usage.

Parsing is fail-closed at every level. A payload that is not a dict yields no
scenarios. A scenario entry that does not satisfy the Pydantic model is dropped
with a recorded reason rather than coerced into something executable. Nothing
here decides whether a scenario is *safe* — that is
:mod:`~neyma_product_driver.scenario_validation`, which runs afterwards on the
models this module produces.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from .scenario_plan import (
    GeneratedScenario,
    GenerationBasis,
    IdentifiedRisk,
    Priority,
    RiskCategory,
    ScenarioProvenance,
    task_digest,
)

# --------------------------------------------------------------------------
# The brief
# --------------------------------------------------------------------------


class GenerationBrief:
    """Everything the generator is shown, and nothing that presumes an answer.

    Assembled by the planner from sources that already exist in the driver: the
    task, repository authority, founder context, the builder's diff, the
    permanent suite, and whatever this run has already observed.
    """

    def __init__(
        self,
        *,
        stage: str,
        wave: int,
        basis: GenerationBasis,
        max_scenarios: int,
        available_commands: list[str],
        available_services: list[str],
        app_url: str,
        browser_enabled: bool,
        existing_coverage: list[str] | None = None,
        failure_clusters: list[str] | None = None,
        product_principles: list[str] | None = None,
    ) -> None:
        self.stage = stage
        self.wave = wave
        self.basis = basis
        self.max_scenarios = max_scenarios
        self.available_commands = available_commands
        self.available_services = available_services
        self.app_url = app_url
        self.browser_enabled = browser_enabled
        self.existing_coverage = existing_coverage or []
        self.failure_clusters = failure_clusters or []
        self.product_principles = product_principles or []

    def render(self) -> str:
        parts: list[str] = [
            f"GENERATION STAGE: {self.stage} (wave {self.wave})",
            f"You may propose AT MOST {self.max_scenarios} scenarios in this wave.",
            "",
            "TASK GIVEN TO THE BUILDER:",
            self.basis.task.strip() or "(none)",
            "",
            f"ACTIVE READY UNIT: {self.basis.active_unit_id or '(unresolved)'}",
        ]
        if self.basis.acceptance_criteria:
            parts += ["ACCEPTANCE CRITERIA (the only requirements you may cite):"]
            parts += [f"  - {c}" for c in self.basis.acceptance_criteria]
        if self.product_principles:
            parts += ["", "PRODUCT PRINCIPLE IDS (the only principles you may cite):"]
            parts += [f"  - {p}" for p in self.product_principles]
        if self.basis.diff_files:
            parts += [
                "",
                "WHAT THE BUILDER ACTUALLY CHANGED:",
                *(f"  - {f}" for f in self.basis.diff_files[:60]),
            ]
            if self.basis.diff_stat:
                parts += ["", self.basis.diff_stat[:4000]]
        if self.existing_coverage:
            parts += [
                "",
                "SITUATIONS ALREADY COVERED (do not repeat these):",
                *(f"  - {c}" for c in self.existing_coverage[:60]),
            ]
        if self.basis.prior_failures:
            parts += [
                "",
                "WHAT FAILED EARLIER IN THIS RUN:",
                *(f"  - {f}" for f in self.basis.prior_failures[:40]),
            ]
        if self.failure_clusters:
            parts += [
                "",
                "FAILURE CLUSTERS (several failures that appear to share one cause):",
                *(f"  - {c}" for c in self.failure_clusters),
            ]
        if self.basis.investigation_findings:
            parts += [
                "",
                "INVESTIGATOR FINDINGS (why something failed — use these to choose "
                "situations, not to re-diagnose):",
                *(f"  - {f}" for f in self.basis.investigation_findings[:20]),
            ]
        if self.basis.evaluator_requests:
            parts += [
                "",
                "THE PRODUCT EVALUATOR ASKED FOR FURTHER VERIFICATION OF:",
                *(f"  - {r}" for r in self.basis.evaluator_requests[:20]),
            ]

        parts += [
            "",
            "=== WHAT YOU MAY ACTUALLY DO ===",
            "",
            "You do NOT write shell. You choose which already-approved command runs, "
            "when, and what must be observable afterwards.",
            "",
            "APPROVED COMMANDS (a `command` or `state_check` may use exactly one of these, "
            "optionally with extra arguments; anything else is rejected):",
        ]
        parts += [f"  - {c}" for c in self.available_commands[:60]] or ["  (none)"]
        parts += [
            "",
            f"SERVICES you may reference / restart / stop / start: "
            f"{', '.join(self.available_services) or '(none)'}",
            f"LOCAL APP URL for relative request paths: {self.app_url or '(none)'}",
            f"BROWSER available: {'yes' if self.browser_enabled else 'no'}",
            "",
            "Return the JSON object now.",
        ]
        return "\n".join(parts)


# --------------------------------------------------------------------------
# The seam
# --------------------------------------------------------------------------


class ScenarioReasoner(Protocol):
    """Proposes situations worth exercising. The source of judgement.

    Implementations return a raw payload; they never decide what runs. Parsing,
    validation and compilation all happen afterwards and are all deterministic.
    """

    def propose(self, brief: GenerationBrief) -> dict[str, Any] | None: ...


PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "description": {"type": "string"},
                    "risk_category": {"type": "string"},
                    "severity": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                    "basis": {
                        "type": "string",
                        "description": "The acceptance criterion, diff file or prior "
                        "failure that makes this risk real.",
                    },
                },
                "required": ["description", "risk_category"],
            },
        },
        "scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "purpose": {"type": "string"},
                    "risk_category": {"type": "string"},
                    "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                    "rationale": {"type": "string"},
                    "requirement_reference": {"type": "string"},
                    "product_principle_reference": {"type": "string"},
                    "mode": {"type": "string", "enum": ["backend", "browser"]},
                    "setup": {"type": "array", "items": {"type": "string"}},
                    "service_refs": {"type": "array", "items": {"type": "string"}},
                    "actions": {"type": "array", "items": {"type": "object"}},
                    "persisted_state_checks": {"type": "array", "items": {"type": "object"}},
                    "expected_observations": {"type": "array", "items": {"type": "string"}},
                    "forbidden_observations": {"type": "array", "items": {"type": "string"}},
                    "cleanup": {"type": "array", "items": {"type": "string"}},
                    "isolation_note": {"type": "string"},
                    "isolation_key": {"type": "string"},
                    "generated_from": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "generating_risk": {"type": "string"},
                },
                "required": [
                    "id",
                    "title",
                    "purpose",
                    "risk_category",
                    "priority",
                    "requirement_reference",
                    "product_principle_reference",
                    "actions",
                ],
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "unresolved_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["risks", "scenarios"],
    "additionalProperties": True,
}


GENERATOR_SYSTEM = """\
You design verification scenarios for a freight-operations product. You decide
WHAT SITUATIONS SHOULD BE EXERCISED before an implementation is trusted. You do
not diagnose failures, you do not judge the product, and you do not write code.

You are given the task, the repository's active work unit and its acceptance
criteria, the founder's product principles, what the builder actually changed,
what is already covered, and what has already failed. Propose the highest-value
situations that could realistically go wrong.

GROUND EVERY SCENARIO. `requirement_reference` must name the active unit, one of
the acceptance criteria you were shown, or an AC-<AREA>-<nnn> id.
`product_principle_reference` must name one of the product principle ids you were
shown. You may NOT invent a product requirement: if the repository and founder
context do not promise something, there is nothing to verify. When you believe a
requirement is missing, say so in `unresolved_questions` instead of inventing it.

BE SELECTIVE. Do not emit one scenario per category. Choose by applicability and
risk: what did the diff actually touch, what does this feature promise, what
would a careful engineer be worried about at 3am. A handful of load-bearing
situations beats twenty shallow ones.

Categories available: happy_path, boundary, missing_data, malformed_input,
conflicting_evidence, idempotency, repeated_request, concurrency, authorization,
approval_required, cross_tenant, partial_failure, service_unavailable,
dependency_failure, timeout_before_effect, timeout_after_effect,
ambiguous_external_effect, retry_safety, persistence_failure, stale_state,
ui_backend_disagreement, restart_recovery, crash_mid_workflow,
browser_network_error, unexpected_state_transition, safety_invariant, regression.

WHAT AN ACTION MAY BE. `actions` is an ordered list; each entry has a `kind`:

  command           {"kind":"command","name":...,"command":"<an approved command>",
                     "expect_exit_code":0,"expect_contains":[...]}
  request           {"kind":"request","request":{"method":"POST","path":"/api/x",
                     "json_body":{...},"expect_status":200,"expect_contains":[...],
                     "timeout_s":5}}
  parallel_requests {"kind":"parallel_requests","requests":[{...},{...}]}  — issued
                     simultaneously; this is how you exercise a race
  state_check       {"kind":"state_check","state_check":{"command":"<approved>",
                     "contains":[...],"not_contains":[...]}}
  browser           {"kind":"browser","browser_steps":[{"goto":"/"},{"click":"..."},
                     {"expect_text":"..."}]}
  fixture           {"kind":"fixture","fixture_name":"invoice.json",
                     "fixture_content":"..."}  — written into the run's evidence
                     directory; reference it later as {{fixture:invoice.json}}
  wait              {"kind":"wait","wait_ms":500}
  restart_service / stop_service / start_service  {"kind":"restart_service",
                     "service":"<a service you listed in service_refs>"}

HARD CONSTRAINTS — a scenario violating any of these is discarded by the harness:

  - A `command` or `state_check` may use ONLY a command from the approved list you
    were given (extra arguments are fine; shell operators are not). You cannot
    author new shell, and there is no way around this.
  - Requests must address the local product (loopback) only. No external effect.
  - `restart_service`/`stop_service`/`start_service` may only name a service you
    listed in `service_refs`, and `service_refs` may only name services that
    already exist.
  - Never reference secrets, credentials, credential environment variables, or
    repository authority files (CLAUDE.md, docs/implementation/, .claude/).
  - Every scenario needs an observable outcome. If nothing it does could pass or
    fail, it is worthless.
  - An HTTP 200 is NOT evidence that an effect happened. Any scenario about
    idempotency, retries, timeouts-after-effect, ambiguous outcomes, partial
    failure, persistence, restart or crash MUST inspect persisted state via a
    `state_check` or `persisted_state_checks`. A local write is not evidence of
    an external effect either.
  - A scenario that mutates local state must declare `cleanup` commands or an
    `isolation_note` explaining why it cannot contaminate the next scenario.
  - Do not duplicate a situation already covered. Adding a different label to the
    same operations and expectations is a duplicate.
  - Set `isolation_key` to whatever shared resource the scenario contends for
    (a database name, a port, a workspace) so the harness knows what may not run
    concurrently with it.

Return only the requested JSON object.
"""


# --------------------------------------------------------------------------
# Parsing — fail-closed
# --------------------------------------------------------------------------


def parse_risks(payload: dict[str, Any] | None) -> list[IdentifiedRisk]:
    """Parse the risk list, silently dropping entries that do not validate."""
    out: list[IdentifiedRisk] = []
    for index, raw in enumerate(_items(payload, "risks"), start=1):
        category = _category(raw.get("risk_category"))
        if category is None:
            continue
        description = str(raw.get("description") or "").strip()
        if not description:
            continue
        out.append(
            IdentifiedRisk(
                id=str(raw.get("id") or f"R{index}"),
                description=description,
                risk_category=category,
                severity=_priority(raw.get("severity")),
                basis=str(raw.get("basis") or ""),
            )
        )
    return out


def parse_scenarios(
    payload: dict[str, Any] | None,
    *,
    provenance: ScenarioProvenance,
) -> tuple[list[GeneratedScenario], list[tuple[dict[str, Any], list[str]]]]:
    """Parse scenario proposals into models.

    Returns ``(parsed, [(raw, reasons), ...])``. A proposal that cannot be
    modelled is *not* repaired into something runnable — it is dropped with the
    validation error recorded, because a half-understood scenario executed
    against a real product is worse than one scenario fewer.
    """
    parsed: list[GeneratedScenario] = []
    malformed: list[tuple[dict[str, Any], list[str]]] = []

    for index, raw in enumerate(_items(payload, "scenarios"), start=1):
        category = _category(raw.get("risk_category"))
        if category is None:
            malformed.append(
                (raw, [f"unknown risk_category {raw.get('risk_category')!r}"])
            )
            continue

        data = {
            "id": str(raw.get("id") or f"gen-{provenance.wave:02d}-{index:02d}"),
            "title": str(raw.get("title") or "").strip(),
            "purpose": str(raw.get("purpose") or "").strip(),
            "risk_category": category,
            "priority": _priority(raw.get("priority")),
            "rationale": str(raw.get("rationale") or "").strip(),
            "requirement_reference": str(raw.get("requirement_reference") or "").strip(),
            "product_principle_reference": str(
                raw.get("product_principle_reference") or ""
            ).strip(),
            "mode": "browser" if str(raw.get("mode") or "") == "browser" else "backend",
            "setup": _strings(raw.get("setup")),
            "service_refs": _strings(raw.get("service_refs")),
            "actions": _dicts(raw.get("actions")),
            "persisted_state_checks": _dicts(raw.get("persisted_state_checks")),
            "expected_observations": _strings(raw.get("expected_observations")),
            "forbidden_observations": _strings(raw.get("forbidden_observations")),
            "cleanup": _strings(raw.get("cleanup")),
            "isolation_note": str(raw.get("isolation_note") or "").strip(),
            "isolation_key": str(raw.get("isolation_key") or "default").strip() or "default",
            "generated_from": _strings(raw.get("generated_from")),
            "confidence": _float(raw.get("confidence"), 0.5),
            "provenance": provenance.model_copy(
                update={"generating_risk": str(raw.get("generating_risk") or "")}
            ),
        }
        if not data["title"]:
            malformed.append((raw, ["scenario has no title"]))
            continue
        try:
            parsed.append(GeneratedScenario.model_validate(data))
        except ValidationError as exc:
            malformed.append((raw, [_render_error(e) for e in exc.errors()]))
    return parsed, malformed


def parse_notes(payload: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """``(assumptions, unresolved_questions)`` — both always lists."""
    if not isinstance(payload, dict):
        return [], []
    return _strings(payload.get("assumptions")), _strings(payload.get("unresolved_questions"))


def _render_error(error: dict[str, Any]) -> str:
    location = ".".join(str(part) for part in error.get("loc", ())) or "(root)"
    return f"{location}: {error.get('msg', 'invalid')}"


def _items(payload: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get(key)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
    return []


def _dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, dict)]


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _category(value: Any) -> RiskCategory | None:
    try:
        return RiskCategory(str(value).strip().lower())
    except ValueError:
        return None


def _priority(value: Any) -> Priority:
    try:
        return Priority(str(value).strip().upper())
    except ValueError:
        return Priority.P2


# --------------------------------------------------------------------------
# The real reasoner
# --------------------------------------------------------------------------


class LLMScenarioReasoner:
    """A fresh, read-only Claude session that proposes situations to exercise.

    Read-only by construction: Read/Grep/Glob only, no Bash, no writes, no
    repository settings loaded. It can look at the code to understand what
    changed; it cannot run anything, and nothing it returns runs without passing
    validation first.

    Exercised in real runs, not in the test suite — the suite drives the planner
    with a scripted reasoner so that no test consumes Claude usage.
    """

    def __init__(self, repo: Path, model: str = "opus", timeout_s: int = 600) -> None:
        self.repo = Path(repo)
        self.model = model
        self.timeout_s = timeout_s
        self.session_id: str | None = None

    def propose(self, brief: GenerationBrief) -> dict[str, Any] | None:
        try:
            return asyncio.run(self._session(brief.render()))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._session(brief.render()))
            finally:
                loop.close()
        except Exception:
            # A generator that errors must not take the run with it: the planner
            # records the wave as producing nothing and the run proceeds with
            # whatever coverage it already has.
            return None

    async def _session(self, prompt: str) -> dict[str, Any] | None:
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
            system_prompt=GENERATOR_SYSTEM,
            allowed_tools=["Read", "Grep", "Glob"],
            disallowed_tools=["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "Task"],
            max_turns=16,
            output_format={"type": "json_schema", "schema": PLAN_SCHEMA},
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
                        return None
                    if not chunks and message.result:
                        chunks.append(message.result)
        finally:
            await client.disconnect()

        if isinstance(structured, dict):
            return structured
        text = "".join(chunks).strip()
        try:
            payload = json.loads(text)
        except ValueError:
            from .evaluator import _extract_json_objects

            for candidate in _extract_json_objects(text):
                if isinstance(candidate, dict) and "scenarios" in candidate:
                    return candidate
            return None
        return payload if isinstance(payload, dict) else None


def provenance_for(
    basis: GenerationBasis,
    *,
    stage: str,
    wave: int,
    model: str = "",
    session_id: str = "",
) -> ScenarioProvenance:
    """Build the provenance stamp every scenario in a wave shares."""
    return ScenarioProvenance(
        task_hash=basis.task_hash or task_digest(basis.task),
        founder_context_version=basis.founder_context_version,
        repository_head=basis.repository_head,
        active_unit_id=basis.active_unit_id,
        acceptance_criteria_consulted=list(basis.acceptance_criteria),
        files_consulted=list(basis.existing_scenarios),
        diff_files_consulted=list(basis.diff_files),
        prior_failures_consulted=list(basis.prior_failures),
        stage=stage,
        wave=wave,
        model=model,
        session_id=session_id,
        kind="ephemeral",
    )


__all__ = [
    "GENERATOR_SYSTEM",
    "GenerationBrief",
    "LLMScenarioReasoner",
    "PLAN_SCHEMA",
    "ScenarioReasoner",
    "parse_notes",
    "parse_risks",
    "parse_scenarios",
    "provenance_for",
]
