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
import textwrap
import threading
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from .scenario_plan import (
    RISK_CATEGORY_VALUES,
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


#: How many approved commands the brief lists. A bound, not a boundary: what may
#: RUN is decided by :mod:`~neyma_product_driver.scenario_validation`, and this
#: number changes nothing there. It exists only because a brief has to end.
#:
#: It was 60, and the approved set reached 62 the moment a per-case verification
#: probe was enumerated — so the tail of one unit's operating vocabulary stopped
#: reaching the generator, with no record that it had. Raised well clear of any
#: realistic set, and a truncation now says so in the brief itself.
MAX_RENDERED_COMMANDS = 250


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
        uncovered_risks: list[str] | None = None,
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
        #: Risks this run has already identified that nothing intends to
        #: exercise. Shown so a wave can be aimed at them: a generator that is
        #: told only what is already covered proposes whatever looks interesting
        #: next, and the gaps it named an hour ago stay exactly where they were.
        self.uncovered_risks = uncovered_risks or []

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
        if self.uncovered_risks:
            parts += [
                "",
                "RISKS THIS RUN ALREADY IDENTIFIED THAT NOTHING YET EXERCISES. These are "
                "the gaps that will block acceptance. Closing them is this wave's first "
                "job — before proposing anything new:",
                *(f"  - {r}" for r in self.uncovered_risks[:40]),
                "",
                "For each scenario you propose to close one of these, set `source_risks` "
                "to the risk key(s) shown in parentheses above. A scenario that cannot "
                "name the identified risk it closes is refused at this stage, for the "
                "same reason a case responding to a failure must name the failure.",
                "",
                "If a listed risk cannot be exercised with the approved commands "
                "available to you, do NOT invent a command and do not propose a scenario "
                "that only looks like coverage. Say so in `unresolved_questions` instead: "
                "an honestly unclosable gap blocks acceptance, which is the correct "
                "outcome, and a scenario that pretends to close it is worse than the gap.",
            ]
        if self.basis.prior_failures:
            parts += [
                "",
                "WHAT FAILED EARLIER IN THIS RUN — this is the evidence you are "
                "responding to. Each entry gives the scenario id, the risk it was "
                "exercising, what it expected, every assertion that failed, and the "
                "output the product actually produced:",
                "",
                *self.basis.prior_failures[:40],
                "",
                "Generate situations that probe the risk surface these failures just "
                "revealed — the neighbouring cases that are now plausible *because of "
                "what was observed*, not a fresh general-purpose batch. For every "
                "scenario you return, set `source_failures` to the scenario id(s) above "
                "that caused it (or `source_clusters` to the cluster id), and set "
                "`generating_risk` to the risk that failure just revealed. A scenario "
                "that cannot name the failure it answers, or the risk it probes, will "
                "be refused.",
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
        shown = self.available_commands[:MAX_RENDERED_COMMANDS]
        parts += [f"  - {c}" for c in shown] or ["  (none)"]
        withheld = len(self.available_commands) - len(shown)
        if withheld:
            # SAID, not swallowed. This list is the generator's entire operating
            # vocabulary, and truncating it silently does not narrow what may
            # run — validation is unchanged — it narrows what can be PROPOSED,
            # invisibly. The previous bound was 60 and the approved set had
            # grown past it, so the tail of a per-case probe vocabulary was
            # being withheld from the generator with nothing recorded anywhere.
            parts.append(
                f"  ... and {withheld} further approved command(s) NOT LISTED HERE. They are "
                "approved and executable; this brief simply could not fit them. If the "
                "situation you want needs a command you cannot see, say so in `purpose` "
                "rather than inventing one."
            )
        parts += [
            "",
            "`setup` and `cleanup` are ALSO command lists, held to the same rule. They "
            "are executed, not read. A precondition written as prose — \"ensure the api "
            "service is running\", \"two tenants exist\" — is not a command, and it causes "
            "the entire scenario to be refused. Express preconditions by choosing an "
            "approved command, or leave setup and cleanup empty and state the assumption "
            "in `purpose`.",
            "",
            "`expected_observations` and `forbidden_observations` are matched as LITERAL "
            "substrings against everything the run observed — command output, response "
            "bodies, visible browser text. They are not descriptions and nothing "
            "interprets them. Write the exact text the product emits:",
            "",
            "    good : \"payments=1\"          (the probe prints exactly this)",
            "    bad  : \"the ledger shows one payment\"   (never appears; fails against a",
            "                                              correct product)",
            "",
            "If you cannot name the exact text a correct product would emit, assert the "
            "property with a `persisted_state_check` and leave the observation lists empty. "
            "A scenario that fails because its expectation was phrased as prose has told "
            "you nothing about the product.",
            "",
            "Quoting is understood: an argument may contain >, <, |, ( or ) inside quotes, "
            "so a SQL oracle such as",
            "    sqlite3 data/app.sqlite3 \"SELECT key FROM grants GROUP BY key HAVING count(*) > 1\"",
            "is accepted. What is refused is composition *outside* quotes — a second "
            "command after ;, &&, a pipe, a redirect, or $( ).",
        ]
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


def run_coroutine_blocking(coro: Any, *, timeout_s: float) -> Any:
    """Run ``coro`` to completion and return its value, from any calling context.

    The coroutine is driven by a fresh event loop on a dedicated thread, so this
    is safe whether or not the caller already has a loop running. That is the
    whole point: the sync ``ScenarioReasoner`` protocol is called from inside
    ``run_control_loop``, where starting a loop in the calling thread raises.

    Exceptions raised inside the coroutine are re-raised to the caller. A hung
    call is abandoned rather than waited on forever — the thread is a daemon, so
    it cannot keep the interpreter alive.
    """
    outcome: dict[str, Any] = {}

    def runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            outcome["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # re-raised on the calling thread below
            outcome["error"] = exc
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            asyncio.set_event_loop(None)
            loop.close()

    thread = threading.Thread(target=runner, name="scenario-reasoner", daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)
    if thread.is_alive():
        raise TimeoutError(
            f"the scenario generator did not answer within {timeout_s:.0f}s"
        )
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


class GenerationSessionError(RuntimeError):
    """The generator session ended without answering, and this is why.

    Raised rather than returned so the planner records a *failed* wave — which
    the acceptance gate refuses to accept on — instead of an empty one, which
    reads as "there was nothing to add".
    """


def _describe_session_error(subtype: str, max_turns: int) -> str:
    """Turn an SDK result subtype into something an operator can act on."""
    if subtype == "error_max_turns":
        return (
            f"the generator used all {max_turns} of its turns exploring the repository "
            "and never produced a scenario plan. Raise scenario_generation.generator_max_turns "
            "if this task genuinely needs more exploration; nothing was generated for it."
        )
    if subtype == "error_during_execution":
        return "the generator session failed during execution and produced no plan"
    return f"the generator session ended in error ({subtype or 'no subtype reported'})"


def _risk_category_schema(description: str) -> dict[str, Any]:
    """The ``risk_category`` field, constrained to the canonical vocabulary.

    An ``enum`` rather than a bare ``string``, and derived from
    :data:`~neyma_product_driver.scenario_plan.RISK_CATEGORY_VALUES` rather than
    typed out again. This field used to be ``{"type": "string"}``: the taxonomy
    lived only in the system prose, so the structured-output contract permitted
    anything, and the model duly answered with nine task-shaped labels of its own
    invention ("cross-tenant-leak", "double-confirmation-race"). Every one was
    then discarded by the parser below, and the run reported zero generated
    scenarios. Prose asks; a schema constrains, and this is the layer that has
    to constrain.

    A fresh copy per call, because a schema dict is mutable and one shared object
    reachable from two places in ``PLAN_SCHEMA`` is a footgun for nothing.
    """
    return {
        "type": "string",
        "enum": list(RISK_CATEGORY_VALUES),
        "description": description,
    }


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
                    "risk_category": _risk_category_schema(
                        "The situation family this risk belongs to. It must be one "
                        "of the listed values exactly; there is no free-text option, "
                        "and a risk that fits none of them belongs in "
                        "`unresolved_questions` instead."
                    ),
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
                    "risk_category": _risk_category_schema(
                        "The situation family this scenario exercises. It must be "
                        "one of the listed values exactly. These are families, not "
                        "descriptions: a scenario about one tenant reading another's "
                        "rows is `cross_tenant`, and a scenario about two "
                        "confirmations racing is `concurrency`. Do not invent a "
                        "label for the specific defect — say that in "
                        "`generating_risk`, which is free text and exists for it."
                    ),
                    "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                    "rationale": {
                        "type": "string",
                        "description": "Why this situation is worth exercising for "
                        "THIS task, in one sentence. Recorded as provenance and "
                        "required: a scenario nobody can explain later is a scenario "
                        "nobody can act on.",
                    },
                    "requirement_reference": {"type": "string"},
                    "product_principle_reference": {"type": "string"},
                    "mode": {"type": "string", "enum": ["backend", "browser"]},
                    "setup": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Commands run before the scenario, from the APPROVED "
                        "COMMANDS list only. These are executed, not read: a precondition "
                        "written as English prose ('ensure the service is running') is not a "
                        "command and causes the whole scenario to be refused. Leave empty "
                        "unless an approved command genuinely needs to run first.",
                    },
                    "service_refs": {"type": "array", "items": {"type": "string"}},
                    "actions": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "The ordered operations to perform. Every entry "
                        "needs a `kind`, and the shape for each kind is given in the "
                        "system instructions. An entry whose kind is not one of those, "
                        "or whose payload is missing, is discarded.",
                    },
                    "persisted_state_checks": {
                        "type": "array",
                        "description": "Read-only probes of PERSISTED state, run after "
                        "the actions. This is the oracle for any claim that an effect "
                        "durably happened, and the EFFECT_FAMILY rule in the system "
                        "instructions requires at least one. Each entry is an object; "
                        "`command` is REQUIRED and must be one of the approved commands "
                        "(extra arguments are fine). There is no `description` or "
                        "`expect` field, and an entry carrying one is discarded whole.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "A label for the probe, for evidence.",
                                },
                                "command": {
                                    "type": "string",
                                    "description": "REQUIRED. An approved command that "
                                    "reads persisted state, e.g. "
                                    "'sqlite3 data/app.sqlite3 \"SELECT count(*) FROM "
                                    "payments WHERE invoice=1\"'.",
                                },
                                "contains": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "LITERAL substrings that must appear "
                                    "in the probe's output.",
                                },
                                "not_contains": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "LITERAL substrings that must not "
                                    "appear in the probe's output.",
                                },
                                "timeout_s": {"type": "integer"},
                            },
                            "required": ["command"],
                        },
                    },
                    "expected_observations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "LITERAL substrings that must appear somewhere in what "
                        "the run observed: command stdout/stderr, HTTP response bodies, or "
                        "visible browser text. These are matched with an exact substring "
                        "test, not interpreted. 'payments=1' is a valid expectation because "
                        "the probe prints exactly that; 'the ledger shows one payment' is "
                        "not, and will fail against a perfectly correct product. If you "
                        "cannot name the exact text, assert it with a persisted_state_check "
                        "instead and leave this empty.",
                    },
                    "forbidden_observations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "LITERAL substrings that must NOT appear anywhere in "
                        "what the run observed. Same exact-substring rule as "
                        "expected_observations.",
                    },
                    "cleanup": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Commands run after the scenario, from the APPROVED "
                        "COMMANDS list only. Same rule as setup: executable commands, never "
                        "prose.",
                    },
                    "isolation_note": {"type": "string"},
                    "isolation_key": {"type": "string"},
                    "generated_from": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "generating_risk": {
                        "type": "string",
                        "description": "The specific risk this scenario exists to "
                        "probe, named in your own words ('a duplicate approval could "
                        "double-pay'). Required. In an adaptive wave this is the risk "
                        "the observed failure revealed.",
                    },
                    "source_failures": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Required for adaptive waves: the scenario id(s) from "
                        "WHAT FAILED EARLIER IN THIS RUN that caused this scenario to be "
                        "generated. This is the audit trail linking a case to its evidence.",
                    },
                    "source_clusters": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Alternative to source_failures: the failure cluster "
                        "id(s) (C01, C02 …) this scenario answers.",
                    },
                    "source_risks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Required for coverage-gap waves: the risk key(s) "
                        "from RISKS THIS RUN ALREADY IDENTIFIED THAT NOTHING YET EXERCISES "
                        "that this scenario closes. Copy the key exactly as shown; a key "
                        "this run never identified is refused.",
                    },
                },
                "required": [
                    "id",
                    "title",
                    "purpose",
                    "risk_category",
                    "priority",
                    "rationale",
                    "generating_risk",
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


#: The canonical vocabulary as the generator is shown it: wrapped prose, built
#: from the enum. It was typed out by hand here, next to a structured schema that
#: did not constrain the field at all — two statements of one taxonomy, neither
#: derived from it. Rendered now, so a category added to
#: :class:`~neyma_product_driver.scenario_plan.RiskCategory` reaches the model in
#: the same commit, and one removed stops being offered in the same commit.
RENDERED_CATEGORIES = "\n".join(
    textwrap.wrap(", ".join(RISK_CATEGORY_VALUES) + ".", width=72)
)


_GENERATOR_SYSTEM_TEMPLATE = """\
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

EXERCISE THE PRODUCT YOURSELF. A scenario puts the product into a situation and
observes what it does. Running somebody else's existing test suite is not that:
"run pytest and check it says passed" tells you the suite is green, which was
already known, and tells you nothing about the situation you were asked to
think about. If an approved command happens to be a test runner, that does not
make invoking it a scenario. Drive the product — issue the requests, repeat the
call, race two calls, restart the service, then read the persisted state and say
what it must show.

`risk_category` is a CLOSED vocabulary. It must be exactly one of the values
below — the structured schema you are answering into permits nothing else, and a
value outside it is not a scenario the harness proposes differently, it is a
scenario the harness cannot read at all. Do not coin a label for the specific
defect you have in mind: `generating_risk` is free text and exists for exactly
that. Pick the family; describe the defect there.

Categories available: {CATEGORIES}

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
                     directory; reference it later as {{fixture:invoice.json}}.
                     A fixture is DATA the product reads, and its name must end
                     in .json .jsonl .ndjson .csv .tsv .txt .yaml .yml or .xml.
                     Anything an interpreter would execute, import or collect —
                     .py, .sh, .js, conftest.py, a config file — is refused: the
                     fixture's path is substituted into an approved command, and
                     an approved command may be an interpreter, so such a file
                     would be code you authored running with the driver's own
                     authority. Write the situation as actions, not as a program.
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
    idempotency, repeated requests, retries, timeouts-after-effect, ambiguous
    outcomes, partial failure, persistence, stale state, restart, crash or an
    unexpected state transition MUST inspect persisted state via a `state_check`
    action or a `persisted_state_checks` entry. A local write is not evidence of
    an external effect either. A passing test suite is not such an oracle: it
    reports on tests, not on the state your actions just produced. Concretely:

        "persisted_state_checks": [
          {"name": "exactly one payment",
           "command": "sqlite3 data/app.sqlite3 \\"SELECT count(*) FROM payments\\"",
           "contains": ["1"], "not_contains": ["2"]}
        ]

    `command` is required and must come from the approved list. Do not write a
    `description` or `expect` field here — those do not exist, and an entry
    carrying one is discarded, taking the whole scenario's only oracle with it.
  - A scenario that mutates local state must declare `cleanup` commands or an
    `isolation_note` explaining why it cannot contaminate the next scenario.
  - Do not duplicate a situation already covered. Adding a different label to the
    same operations and expectations is a duplicate.
  - Set `isolation_key` to whatever shared resource the scenario contends for
    (a database name, a port, a workspace) so the harness knows what may not run
    concurrently with it.

Return only the requested JSON object.
"""


GENERATOR_SYSTEM = _GENERATOR_SYSTEM_TEMPLATE.replace("{CATEGORIES}", RENDERED_CATEGORIES)


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
            # Recorded with the vocabulary it was measured against, so the run's
            # own artifacts answer "invalid according to what?" without anyone
            # having to go and read this module.
            malformed.append(
                (
                    raw,
                    [
                        f"unknown risk_category {raw.get('risk_category')!r}: it is not "
                        "one of "
                        + ", ".join(RISK_CATEGORY_VALUES)
                    ],
                )
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
                update={
                    "generating_risk": str(raw.get("generating_risk") or ""),
                    # Which observed failure this case answers. Validation
                    # checks these against what the run actually saw, so a
                    # scenario cannot claim a cause that never happened.
                    "source_failures": _strings(
                        raw.get("source_failures") or raw.get("caused_by_failures")
                    ),
                    "source_clusters": _strings(
                        raw.get("source_clusters") or raw.get("caused_by_clusters")
                    ),
                    # Which identified risk this case closes. Checked against
                    # the run's own register, so a coverage-gap case cannot
                    # claim to close a gap the run never named.
                    "source_risks": _strings(
                        raw.get("source_risks") or raw.get("caused_by_risks")
                    ),
                }
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


def unknown_category(raw: dict[str, Any]) -> str:
    """The declared ``risk_category``, when it is outside the canonical vocabulary.

    Returns ``""`` when the value is canonical. Exactly the check
    :func:`parse_scenarios` already makes, re-asked of the raw proposal so a
    caller can classify a rejection from the payload itself rather than by
    reading its prose. Deterministic and total: no fuzzy matching, no mapping of
    a novel string onto a canonical one. The only normalisation is the
    surrounding-whitespace-and-case fold :func:`_category` has always applied,
    which is a format variation, not a change of meaning.
    """
    if not isinstance(raw, dict):
        return ""
    if _category(raw.get("risk_category")) is not None:
        return ""
    return str(raw.get("risk_category") or "").strip() or "(none declared)"


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

    #: Turns the generator may spend before it must answer. It reads the
    #: repository to ground its proposals, and against a real product that is
    #: tens of Read/Grep calls before it writes anything at all. The previous
    #: value of 16 was measured cutting a session off mid-exploration: the
    #: session ended `error_max_turns` having emitted no structured output, and
    #: the wave was recorded as having produced nothing. A budget that stops the
    #: work before it starts is not a budget, it is a silent failure. The
    #: wall-clock timeout still bounds the session.
    DEFAULT_MAX_TURNS = 40

    def __init__(
        self,
        repo: Path,
        model: str = "opus",
        timeout_s: int = 600,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> None:
        self.repo = Path(repo)
        self.model = model
        self.timeout_s = timeout_s
        self.max_turns = max(1, int(max_turns))
        self.session_id: str | None = None

    def propose(self, brief: GenerationBrief) -> dict[str, Any] | None:
        """Ask the model for a wave, from a synchronous caller.

        Every real call site is inside the already-running control loop, so this
        must not start a loop in the calling thread — ``asyncio.run`` and
        ``run_until_complete`` both refuse while another loop is running, and the
        run would silently proceed with no generated coverage at all.

        Errors are deliberately *not* swallowed here. "The generator failed" and
        "the generator had nothing to add" are different facts, and a run that
        cannot tell them apart will treat a dead generator as a clean bill of
        health. The planner records the failure against the wave, and the
        acceptance gate refuses to accept a run whose verification never ran.
        """
        return run_coroutine_blocking(
            self._session(brief.render()), timeout_s=self.timeout_s + 60
        )

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
            max_turns=self.max_turns,
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
                        # Say *why*. "The generator produced nothing" is what the
                        # planner recorded for a session cut off at its turn
                        # limit, a refusal, and a transport failure alike — three
                        # different facts with three different responses, and a
                        # reader could not tell which had happened. The planner
                        # records this text against the wave and the gate refuses
                        # to accept a run that has one.
                        raise GenerationSessionError(
                            _describe_session_error(
                                getattr(message, "subtype", ""), self.max_turns
                            )
                        )
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
    "RENDERED_CATEGORIES",
    "GenerationBrief",
    "LLMScenarioReasoner",
    "PLAN_SCHEMA",
    "ScenarioReasoner",
    "parse_notes",
    "parse_risks",
    "parse_scenarios",
    "provenance_for",
    "unknown_category",
]
