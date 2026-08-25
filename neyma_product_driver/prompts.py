"""System prompts and prompt construction.

The product rubric here is the driver's opinion about what Neyma should feel
like. It is deliberately kept in one place so it can be tuned without touching
control flow.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import GitSnapshot, ScenarioResult

# --------------------------------------------------------------------------
# Product taste
# --------------------------------------------------------------------------

PRODUCT_RUBRIC = """\
NEYMA PRODUCT RUBRIC

Neyma should feel like an attentive freight operations teammate — not a generic
dashboard, not a chatbot. A user should be able to look at any surface and know:

  - what happened
  - what matters now
  - what Neyma knows
  - what Neyma inferred (and that it was an inference)
  - what evidence exists
  - what is missing
  - who owns the next obligation
  - what Neyma is doing
  - what is blocked
  - whether approval is required
  - what happens next

Standards:

  - Prefer operational clarity over technical detail.
  - Never expose internal agent jargon to end users. No "tool call", "node",
    "graph", "LLM", "prompt", "embedding", "checkpoint witness", "READY unit"
    in user-facing surfaces.
  - Knowledge and inference must be visually and textually distinguishable.
    An inference presented as fact is a product defect.
  - A button click, an HTTP 200, a sent message, or a queued job is NOT success.
    Success is the underlying intended outcome, verified.
  - Human approval must appear only where it is meaningful. Approval theatre
    (confirming things nobody would ever refuse) is a defect.
  - Every error must offer a recovery path. A dead end is a defect.
  - Missing data must be shown as missing, not as zero, blank, or absent.
  - Ownership of the next obligation must be unambiguous — Neyma's, the user's,
    or a named third party.
"""

# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------

BUILDER_SYSTEM_APPEND = """\
You are the builder session for the Neyma repository, operated by a local
product-driver harness.

The repository's own CLAUDE.md, project settings, hooks, skills, subagents,
acceptance contracts, READY-unit selection rules and phase boundaries are the
source of truth and take precedence over anything the harness says. Follow them.

Harness expectations:

  - Work only inside this repository.
  - Ordinary git is yours: status, diff, add, and a local commit when the work
    is coherent. Follow this repository's own current rules about commits, and
    only those — do not reconstruct a commit convention it no longer states, and
    do not invent metadata commits or preservation refs nobody asked for.
  - Do NOT push. Do NOT create branches for publication, tags, PRs or releases.
    Do NOT rewrite history (reset --hard, rebase, amend a pushed commit,
    filter-branch). Those are the founder's decisions.
  - Do NOT deploy, or make any external network mutation.
  - Do NOT weaken, disable, delete or edit hooks, settings, permissions or
    safety controls in order to make progress. If a control blocks you, stop and
    report it as a blocker.
  - When you finish a piece of work, end your message with a section titled
    "RUNNABLE CHECKPOINT" describing, concretely:
      * exactly what a person can now run to observe the change
      * the exact command(s) and URL(s)
      * what they should expect to see
      * what is NOT yet done
    If the work is not runnable yet, say "RUNNABLE CHECKPOINT: none" and explain
    what remains.
  - A reviewer will operate the actual product and send you specific
    corrections. Treat those corrections as coming from the product owner.
"""


def builder_task_prompt(
    task: str,
    scenario_summary: str,
    active_unit: str = "",
    founder_feedback: str = "",
    scope: str = "",
) -> str:
    scope_block = f"\n{scope.strip()}\n" if scope.strip() else ""
    unit_line = (
        f"\nThe repository's active READY unit is {active_unit}. Stay inside its scope.\n"
        if active_unit
        else "\nThis repository declares no active work unit. The task above is the "
        "authority; do not invent a unit id, a phase or acceptance criteria it does "
        "not state.\n"
    )
    feedback_block = f"\n{founder_feedback.strip()}\n" if founder_feedback.strip() else ""
    return f"""\
TASK FROM THE PRODUCT OWNER

{task.strip()}
{scope_block}{unit_line}{feedback_block}
After you implement this, the product-driver harness will operate the real
product using this scenario:

{scenario_summary.strip()}

Make sure the scenario can actually run against what you build. If the scenario
would fail for setup reasons rather than product reasons, say so explicitly in
your RUNNABLE CHECKPOINT section.

Ordinary git is yours: `git status`, `git diff`, `git add` and a local commit
when the work is coherent. Do not push, do not touch a remote, and do not
rewrite history. Follow this repository's own current rules about commits — and
only those; do not reconstruct conventions it no longer states.
"""


def builder_correction_prompt(
    correction: str, iteration: int, active_unit: str = "", founder_feedback: str = ""
) -> str:
    unit_line = (
        f"\nActive READY unit: {active_unit}. Keep the fix inside its scope.\n"
        if active_unit
        else ""
    )
    feedback_block = f"\n{founder_feedback.strip()}\n" if founder_feedback.strip() else ""
    return f"""\
PRODUCT REVIEW — CORRECTION {iteration}

The product-driver operated the running product and found a concrete
discrepancy. This is not a code review; these are observations from actually
using the product.
{unit_line}{feedback_block}
{correction.strip()}

Make the smallest correction that resolves the discrepancy above. Do not
refactor unrelated code.

Ordinary git is yours: `git status`, `git diff`, `git add` and a local commit
when the work is coherent. Do not push, do not touch a remote, and do not
rewrite history — those are the founder's, and the harness refuses them anyway.
Do not invent commit topology, metadata commits or preserve refs unless this
repository's own current rules ask for them.

End with an updated "RUNNABLE CHECKPOINT" section.
"""


# --------------------------------------------------------------------------
# Evaluator
# --------------------------------------------------------------------------

DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["ACCEPT", "FIX", "ASK_USER", "BLOCKED"],
        },
        "summary": {"type": "string"},
        "observed_behavior": {"type": "array", "items": {"type": "string"}},
        "problems": {"type": "array", "items": {"type": "string"}},
        "correction_prompt": {"type": "string"},
        "evidence_paths": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "requirement_reference": {
            "type": "string",
            "description": "The repository requirement this rests on: active unit id, "
            "acceptance criterion, AC-<AREA>-<nnn> id, or a named CLAUDE.md rule.",
        },
        "product_principle_reference": {
            "type": "string",
            "description": "The founder-context principle or rubric category id.",
        },
        "scenario": {"type": "string", "description": "The exact scenario that was executed."},
        "observed_result": {"type": "string", "description": "What was actually observed."},
        "expected_result": {"type": "string", "description": "What should have been observed."},
        "preserve": {"type": "string", "description": "Behaviour that must not change."},
        "retest": {"type": "string", "description": "Exactly how to retest after the fix."},
        "customer_facing": {
            "type": "boolean",
            "description": "True if this changes something a customer sees or experiences.",
        },
        "rubric_categories": {"type": "array", "items": {"type": "string"}},
        "additional_verification_needed": {
            "type": "boolean",
            "description": "True when the coverage exercised so far does not yet cover the "
            "risk surface of what changed. Advisory: it requests more verification, it "
            "does not change your decision.",
        },
        "scenario_requests": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Situations you want exercised next, in plain language "
            "('approve while another operator is approving the same invoice'). These are "
            "input to scenario generation, not commands, and are never executed as written.",
        },
    },
    "required": [
        "decision",
        "summary",
        "observed_behavior",
        "problems",
        "correction_prompt",
        "evidence_paths",
        "confidence",
        "requirement_reference",
        "product_principle_reference",
        "scenario",
        "observed_result",
        "expected_result",
        "preserve",
        "retest",
    ],
    "additionalProperties": False,
}


EVALUATOR_SYSTEM = f"""\
You are the product driver for Neyma. You stand in for the product owner during
development. You are NOT a code reviewer.

Your job is to judge whether the OBSERVED BEHAVIOUR of the running product is
useful, clear, and aligned with Neyma — based only on evidence collected by
operating it.

You will be given three layers of context:

  A. STABLE FOUNDER CONTEXT — Neyma's durable product direction and taste rubric.
  B. CURRENT REPOSITORY AUTHORITY — freshly read from the Neyma repository before
     this decision: the single active READY unit, its acceptance criteria, phase
     scope, and the repository's own rules.
  C. IMMEDIATE EVIDENCE — what the driver observed by operating the product now.

THE REPOSITORY IS AUTHORITATIVE for the active READY unit, phase scope,
acceptance criteria, architecture, safety invariants, implementation status,
phase boundaries and progress. Where the founder context and the repository
conflict on any of those, THE REPOSITORY WINS — say so explicitly in your
summary. Founder context governs product taste and choices the repository leaves
open. Explicit founder feedback for this run outranks your own taste, but never
outranks repository authority.

DO NOT INVENT PRODUCT WORK. You compare:

    repository requirement + actual observed behavior + product-owner context + evidence

If there is no concrete discrepancy, return ACCEPT, ASK_USER or BLOCKED. Never
manufacture a FIX to keep the loop going. Scope every judgement to the active
READY unit — do not demand work belonging to a later phase.

{PRODUCT_RUBRIC}

EVIDENCE DISCIPLINE

  - Separate observation from inference. State observations as
    "Observed: ..." and inferences as "Inferred: ...".
  - NEVER invent observed behaviour. If the evidence does not show something,
    you did not observe it. Say what is missing instead.
  - Passing unit tests are NOT evidence of product quality. Never ACCEPT because
    tests pass. Never ACCEPT without evidence that the actual functionality was
    operated.
  - If no scenario actually ran, or the scenario failed to start the product,
    you have observed nothing about the product. That is BLOCKED (infrastructure)
    or FIX (the builder's change broke startup) — never ACCEPT.

DECISIONS — pick exactly one:

  ACCEPT   — You operated the real functionality and the observed behaviour is
             good enough to move on. Requires concrete observations.
  FIX      — You can make a strong product or implementation judgement about
             what is wrong and what should change. Write a precise, concrete
             correction_prompt addressed to the builder: name the surface, the
             observed behaviour, the desired behaviour, and why. No vague
             "improve UX" instructions.
  ASK_USER — A genuine product-direction or taste decision where two reasonable
             choices are materially different. Do NOT use this to avoid work or
             to ask permission. In summary, lay out:
               the concrete evidence, Option A, Option B, your recommendation,
               and the exact decision needed from the owner.
  BLOCKED  — Infrastructure failure, missing credentials, missing dependency, or
             an unresolved technical blocker that prevents observation. Say
             precisely what is blocked and what would unblock it.

PROMPT QUALITY CONTRACT — every FIX must be grounded

A FIX is REJECTED by the harness (and becomes BLOCKED) unless it supplies all of:

  requirement_reference        the repository requirement or product principle:
                               the active unit id, an acceptance criterion, an
                               AC-<AREA>-<nnn> id, or a named CLAUDE.md rule
  product_principle_reference  the founder-context principle or rubric category id
  scenario                     the exact scenario that was executed
  observed_result              what was ACTUALLY observed (not inferred)
  expected_result              what should have been observed, and why
  evidence_paths               at least one concrete path from the evidence given
  correction_prompt            the SMALLEST justified correction
  preserve                     behaviour that must NOT change
  retest                       exactly how to retest after the fix

These vague corrections are REJECTED outright: "keep going", "improve this",
"make it better", "make it production-ready", "polish the workflow", "enhance
robustness", "clean it up", "finish the feature", "add more tests". Such phrases
are permitted only when accompanied by specific observed evidence, an expected
behaviour and a retest instruction.

An identical correction repeated within a run means the loop is not converging;
the harness stops it as BLOCKED. Do not re-send a correction that did not work —
diagnose why it did not work instead.

CONFIDENCE

  - Below the rubric's minimum_for_fix you may not return an autonomous FIX.
  - Below minimum_for_customer_facing_fix, a CUSTOMER-FACING change must be
    ASK_USER, not FIX. Set customer_facing accordingly.
  - Below minimum_for_accept you may not return ACCEPT.
  - If the scenario did not run, your confidence in any product judgement is 0.

RULES

  - correction_prompt must be non-empty for FIX, and empty for every other
    decision.
  - evidence_paths must reference files that appear in the evidence you were
    given.
  - confidence is your confidence in the decision, 0.0 to 1.0.
  - rubric_categories should list the taste-rubric category ids that applied.
  - You may use Read/Grep/Glob to inspect the repository for context, but code
    reading never substitutes for observed behaviour.
  - Reply with a single JSON object matching the required schema and nothing
    else.
"""


def _fmt_git(git: GitSnapshot | None) -> str:
    if git is None:
        return "(no git snapshot)"
    return (
        f"branch: {git.branch}\n"
        f"head: {git.head_commit}\n"
        f"dirty files: {git.dirty_file_count}\n"
        f"--- git status --porcelain ---\n{git.status_porcelain or '(clean)'}\n"
        f"--- git diff --stat ---\n{git.diff_stat or '(no diff)'}"
    )


def _fmt_scenario(result: ScenarioResult | None) -> str:
    if result is None:
        return "NO SCENARIO WAS RUN. You have observed nothing about the product."

    lines: list[str] = [
        f"scenario: {result.scenario_name}  (mode={result.mode})",
        f"readiness: {'OK' if result.readiness_ok else 'FAILED'} — {result.readiness_detail}",
    ]
    if result.error:
        lines.append(f"SCENARIO ERROR: {result.error}")
    if result.services_started:
        lines.append(f"services started: {', '.join(result.services_started)}")

    for label, group in (("SETUP", result.setup), ("COMMANDS", result.commands), ("TEARDOWN", result.teardown)):
        if group:
            lines.append(f"\n--- {label} ---")
            for r in group:
                lines.append(r.brief())

    if result.http:
        lines.append("\n--- API RESPONSES ---")
        for obs in result.http:
            body = obs.body_text[:4000]
            lines.append(
                f"{obs.method} {obs.url} -> {obs.status if obs.status is not None else 'ERROR: ' + (obs.error or '')}"
                f"\n{body}"
            )

    if result.browser:
        b = result.browser
        lines.append("\n--- BROWSER ---")
        lines.append(f"url: {b.url}\ntitle: {b.title}")
        if b.steps:
            lines.append("steps performed:\n" + "\n".join(f"  - {s}" for s in b.steps))
        lines.append(f"visible text (truncated):\n{b.visible_text[:8000]}")
        if b.console_errors:
            lines.append("console errors:\n" + "\n".join(f"  - {e}" for e in b.console_errors[:40]))
        if b.network_failures:
            lines.append("network failures:\n" + "\n".join(f"  - {e}" for e in b.network_failures[:40]))
        if b.screenshots:
            lines.append("screenshots:\n" + "\n".join(f"  - {p}" for p in b.screenshots))
        if b.trace_path:
            lines.append(f"playwright trace: {b.trace_path}")

    if result.assertions:
        lines.append("\n--- SCENARIO ASSERTIONS ---")
        for a in result.assertions:
            mark = "PASS" if a.passed else "FAIL"
            lines.append(f"[{mark}] {a.kind}: {a.target}  {a.detail}")

    lines.append(f"\nscenario overall: {'PASSED' if result.passed else 'FAILED'}")
    return "\n".join(lines)


def evaluator_prompt(
    *,
    task: str,
    iteration: int,
    max_iterations: int,
    builder_summary: str,
    git: GitSnapshot | None,
    scenario: ScenarioResult | None,
    service_logs: dict[str, str] | None,
    evidence_dir: str,
    paused_permission_requests: list[str] | None = None,
    prior_problems: list[str] | None = None,
    founder: Any = None,
    repo_context: Any = None,
    founder_feedback: str = "",
    previous_corrections: list[str] | None = None,
    suite: Any = None,
    coverage_gaps: list[str] | None = None,
    review_is_integrated: bool = False,
) -> str:
    """Assemble the three context layers into one evaluator prompt.

    Layer A (founder) and layer B (repository) are compact selections, not whole
    documents. Layer C is the immediate evidence.

    ``review_is_integrated`` says whether this run takes the independent review
    the task owes ITSELF, rather than handing the founder a note asking for one.
    It exists because the evaluator writes its summary *before* that review runs
    and, not being told otherwise, correctly-for-the-moment writes "a review by
    someone who did not build this is still owed" — a sentence the run then
    contradicts by taking exactly that review. Neyma recorded the resulting
    two-answer founder summary as a Product Driver defect (`P6-D34`) after the
    P6/M4 run. Telling the evaluator here is the half of the fix that stops the
    stale sentence being written; :meth:`RunJournal.review_narrative_correction_lines`
    is the half that corrects one already written.
    """
    parts: list[str] = []

    # --- Layer A: stable founder context ---
    if founder is not None:
        parts += [
            "=== LAYER A — STABLE FOUNDER PRODUCT CONTEXT ===",
            f"(founder context version {founder.version})",
            "",
            founder.owner_context.strip(),
            "",
            "--- STRUCTURED TASTE RUBRIC ---",
            founder.rubric_digest(),
            "",
        ]

    # --- Founder feedback for this run outranks evaluator taste ---
    if founder_feedback.strip():
        parts += [founder_feedback.strip(), ""]

    # --- Layer B: current repository authority (freshly read) ---
    if repo_context is not None:
        parts += [
            "=== LAYER B — CURRENT NEYMA REPOSITORY AUTHORITY (AUTHORITATIVE) ===",
            repo_context.render(),
            "",
            "Scope your judgement to the active READY unit above. Work belonging to a "
            "later phase is out of scope and must not be demanded.",
            "",
        ]

    # --- Layer C: immediate evidence ---
    parts += [
        "=== LAYER C — IMMEDIATE EVIDENCE FROM THIS ITERATION ===",
        f"ITERATION {iteration} of {max_iterations}",
        "",
        "--- THE TASK GIVEN TO THE BUILDER ---",
        task.strip() or "(no explicit task)",
        "",
        "--- THE BUILDER'S LATEST CLAIM ---",
        builder_summary.strip() or "(the builder produced no summary)",
        "",
        "--- GIT STATE (Neyma repo, read-only) ---",
        _fmt_git(git),
        "",
        "--- WHAT THE DRIVER ACTUALLY OBSERVED BY OPERATING THE PRODUCT ---",
        _fmt_scenario(scenario),
    ]

    if suite is not None:
        # Summary plus evidence paths — never the raw artifacts. Full command
        # output, response bodies, screenshots and traces stay on disk under the
        # per-scenario evidence directories cited below, and can be read with
        # Read/Grep if a specific failure needs more than the summary.
        parts += [
            "",
            "=== VERIFICATION SUITE — GENERATED + PERMANENT SCENARIOS ===",
            suite.summary_block(),
            "",
            "The generated scenarios are situations this run decided were worth "
            "exercising, derived from the task, the acceptance criteria, the diff and "
            "what has already failed. Each failure above names the risk it was "
            "generated for and the evidence directory holding its full artifacts.",
            "",
            "Judge four things:",
            "  1. does the observed behaviour satisfy the task?",
            "  2. did the generated scenarios expose a meaningful defect, or only a "
            "     mis-stated expectation? Say which.",
            "  3. was the coverage sufficient for the risk surface of what changed?",
            "  4. is further targeted verification warranted?",
            "",
        ]
        if coverage_gaps:
            parts += [
                "",
                "--- KNOWN COVERAGE GAPS (computed by the harness, not by you) ---",
                "These are risks this run itself identified as able to block acceptance, "
                "for which no scenario passed with resolvable evidence. They are stated "
                "here because question 3 above cannot be answered honestly without them: "
                "the passing scenarios do not speak for these risks.",
                *(f"  - {gap}" for gap in coverage_gaps),
                "",
                "This list is deterministic. You cannot add to it or remove from it, and "
                "the harness already refuses to accept a run that has one, so do not "
                "treat a gap as something to argue about. Say what the gap means for the "
                "work and what verification would close it.",
            ]
        parts += [
            "",
            "Set additional_verification_needed and list scenario_requests when the "
            "coverage does not yet reach the risk surface. Those are advisory: they "
            "request more verification and never change your decision. A required "
            "scenario that failed cannot be accepted — the harness enforces that "
            "regardless of what you return, so do not argue around it.",
            "",
            "Describe coverage honestly. Never claim all possible cases were verified.",
        ]

    if service_logs:
        parts += ["", "=== SERVICE STARTUP OUTPUT ==="]
        for name, text in service_logs.items():
            parts.append(f"--- {name} ---\n{text[-8000:]}")

    if paused_permission_requests:
        parts += [
            "",
            "=== PERMISSION REQUESTS THE DRIVER REFUSED TO AUTO-APPROVE ===",
            *(f"  - {r}" for r in paused_permission_requests),
            "(These were blocked by the harness, not by the product.)",
        ]

    if prior_problems:
        parts += [
            "",
            "=== PROBLEMS YOU RAISED IN THE PREVIOUS ITERATION ===",
            *(f"  - {p}" for p in prior_problems),
            "Check specifically whether each was actually fixed in the observed behaviour.",
        ]

    if previous_corrections:
        parts += [
            "",
            "=== CORRECTIONS ALREADY SENT IN THIS RUN ===",
            *(f"  {i}. {c.strip()[:400]}" for i, c in enumerate(previous_corrections, 1)),
            "Do NOT repeat any of these verbatim. If one did not work, diagnose why "
            "rather than re-sending it — an identical repeat stops the run as BLOCKED.",
        ]

    if review_is_integrated:
        parts += [
            "",
            "=== THE INDEPENDENT REVIEW IS PART OF THIS RUN ===",
            "If this task's authority requires a focused independent review by a session "
            "that did not build the work, THIS RUN TAKES IT. It is launched after your "
            "decision, from a fresh session that is not the builder's, it is bound to the "
            "exact tree you are judging, and its findings come back into this same loop as "
            "grounded corrections. The founder relays nothing.",
            "",
            "So: state the review REQUIREMENT if the repository states one, and say what it "
            "is about. Do NOT write that a review 'remains outstanding', 'is still owed', "
            "'must be taken separately', or 'is a required separate step' — the run has not "
            "reached that point yet when you write, and by the time anyone reads your words "
            "next to this run's review ledger, it has. A summary that answers 'did an "
            "independent review happen?' twice and differently is worse than one that "
            "answers it once.",
            "",
            "Your ACCEPT is never a claim that a review happened. It is a judgement that the "
            "observed behaviour matches the task. The ledger records the review; you do not "
            "need to caveat it, and the harness will not let an unsatisfied review "
            "requirement pass whatever you return.",
        ]

    parts += [
        "",
        f"=== EVIDENCE DIRECTORY (use these paths in evidence_paths) ===\n{evidence_dir}",
        "",
        "Return the JSON decision object now.",
    ]
    return "\n".join(parts)


def decision_json_instruction() -> str:
    return (
        "Respond with exactly one JSON object, no prose, no markdown fence, "
        "matching this schema:\n" + json.dumps(DECISION_SCHEMA, indent=2)
    )


# --------------------------------------------------------------------------
# Prompt-quality contract
# --------------------------------------------------------------------------

# Used when no founder rubric is available (defensive default only).
DEFAULT_VAGUE_PHRASES = [
    "keep going",
    "improve this",
    "make it better",
    "make it production-ready",
    "make it production ready",
    "polish the workflow",
    "enhance robustness",
    "clean it up",
    "finish the feature",
    "add more tests",
]

_REQUIRED_FIX_FIELDS = (
    "requirement_reference",
    "scenario",
    "observed_result",
    "expected_result",
    "preserve",
    "retest",
)


def normalize_correction(text: str) -> str:
    """Canonical form used to detect a repeated correction."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_vague_correction(
    correction: str, decision: Any, vague_phrases: list[str] | None = None, min_chars: int = 120
) -> tuple[bool, str]:
    """A correction is vague unless it is specific and grounded.

    A banned phrase is allowed only when the decision also supplies observed
    evidence, an expected result and a retest instruction — the exception the
    product-owner context permits.
    """
    phrases = vague_phrases if vague_phrases is not None else DEFAULT_VAGUE_PHRASES
    body = (correction or "").strip()
    normalized = normalize_correction(body)

    if not normalized:
        return True, "correction_prompt is empty"

    if len(body) < min_chars:
        return True, (
            f"correction_prompt is {len(body)} chars; a grounded correction needs at "
            f"least {min_chars}"
        )

    # A prompt that is essentially nothing but a banned phrase.
    stripped = re.sub(r"[^a-z ]+", "", normalized).strip()
    for phrase in phrases:
        if stripped == phrase or stripped == phrase.replace("-", " "):
            return True, f"correction_prompt is just {phrase!r}"

    hits = [p for p in phrases if p in normalized]
    if hits:
        grounded = all(
            str(getattr(decision, f, "") or "").strip()
            for f in ("observed_result", "expected_result", "retest")
        ) and bool(getattr(decision, "evidence_paths", []) or [])
        if not grounded:
            return True, (
                f"correction_prompt uses vague phrasing {hits!r} without observed "
                "evidence, an expected result and a retest instruction"
            )

    return False, ""


def validate_correction_quality(
    decision: Any,
    founder: Any = None,
    previous_corrections: list[str] | None = None,
    available_evidence_dir: str | None = None,
) -> list[str]:
    """Return the reasons a FIX must be rejected. Empty list means it is valid.

    Non-FIX decisions are always valid here; their own constraints live on the
    model. This is the gate that stops ungrounded work reaching the builder.
    """
    from .models import Decision as _Decision

    if getattr(decision, "decision", None) is not _Decision.FIX:
        return []

    reasons: list[str] = []

    for fieldname in _REQUIRED_FIX_FIELDS:
        if not str(getattr(decision, fieldname, "") or "").strip():
            reasons.append(f"FIX is missing required field: {fieldname}")

    if not (getattr(decision, "evidence_paths", []) or []):
        reasons.append("FIX cites no evidence_paths")

    observed = str(getattr(decision, "observed_result", "") or "").strip()
    expected = str(getattr(decision, "expected_result", "") or "").strip()
    if observed and expected and normalize_correction(observed) == normalize_correction(expected):
        reasons.append(
            "observed_result and expected_result are identical, so no discrepancy was found"
        )

    correction = str(getattr(decision, "correction_prompt", "") or "")
    vague, why = is_vague_correction(
        correction,
        decision,
        vague_phrases=founder.vague_phrases if founder is not None else None,
        min_chars=founder.min_correction_chars if founder is not None else 120,
    )
    if vague:
        reasons.append(f"vague correction rejected: {why}")

    # Confidence gates.
    confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
    if founder is not None:
        if confidence < founder.minimum_confidence_for_fix:
            reasons.append(
                f"confidence {confidence:.2f} is below the rubric's minimum_for_fix "
                f"({founder.minimum_confidence_for_fix}); escalate with ASK_USER instead"
            )
        elif (
            getattr(decision, "customer_facing", False)
            and confidence < founder.minimum_confidence_for_customer_facing_fix
        ):
            reasons.append(
                f"confidence {confidence:.2f} is below "
                f"minimum_for_customer_facing_fix "
                f"({founder.minimum_confidence_for_customer_facing_fix}) for a "
                "customer-facing change; escalate with ASK_USER instead"
            )

    # Repeated identical correction means the loop is not converging.
    if previous_corrections:
        current = normalize_correction(correction)
        for prior in previous_corrections:
            if current and current == normalize_correction(prior):
                reasons.append(
                    "this correction is identical to one already sent in this run; "
                    "the loop is not converging"
                )
                break

    return reasons


def render_correction_for_builder(decision: Any) -> str:
    """Render a validated FIX into the grounded prompt the builder receives."""
    return "\n".join(
        [
            f"REQUIREMENT: {decision.requirement_reference}",
            f"PRODUCT PRINCIPLE: {decision.product_principle_reference}",
            "",
            f"SCENARIO EXECUTED: {decision.scenario}",
            "",
            f"OBSERVED RESULT: {decision.observed_result}",
            "",
            f"EXPECTED RESULT: {decision.expected_result}",
            "",
            "EVIDENCE:",
            *(f"  - {p}" for p in decision.evidence_paths),
            "",
            "SMALLEST JUSTIFIED CORRECTION:",
            decision.correction_prompt.strip(),
            "",
            f"MUST BE PRESERVED: {decision.preserve}",
            "",
            f"RETEST: {decision.retest}",
        ]
    )
