"""What a run was actually asked to build, and what it therefore has to prove.

A driver run has two different things in view at once and they are not the same
thing:

    the TASK       the build unit the product owner asked for this run
    the PHASE      the parent implementation phase that task sits inside

Conflating them is a specific, expensive failure. A run asked to build one
nested unit of a thirteen-unit phase produces exactly the evidence that unit
owes, and is then judged against the acceptance contract of the whole phase —
which it cannot satisfy, because twelve other units have not been written yet.
The run cannot pass no matter how good the work is, and every iteration spends
its budget trying to close a gap that is not a defect.

So the scope is resolved once, from the run's own task text and the repository's
registry, and it answers what the task asks to happen (:class:`ScopeIntent`):

    build one unit inside the phase     UNIT_IMPLEMENTATION
    build the whole phase               PHASE_IMPLEMENTATION
    accept / close / adjudicate it      PHASE_ACCEPTANCE

For a unit, phase-level acceptance is not this run's bar; the task's own
authoritative requirements are, and the phase stays exactly where the
repository says it is. For a whole-phase build, the phase's criteria are the bar
the work is built TO — and scoring them is phase closure's act, not the
builder's, so a verified build is the candidate phase closure is handed, never
an accepted phase. Only a task that asks for acceptance is held to acceptance.

Three things this module deliberately does not do:

* it never reads the builder's report. Scope comes from the request and the
  repository, so a builder cannot widen or narrow what it is being held to by
  describing its work a particular way;
* it never marks anything complete. It reports the parent phase's state as the
  repository records it, and nothing here can move it;
* it never assumes a task is narrow. When no nested unit can be derived, the
  scope is the phase — the stricter reading — and the phase's full bar applies.

Nothing here writes to the target repository.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

#: A phase id, as repositories that use them tend to write them: P6, P-6, U5.
_PHASE_TOKEN = r"(?:P-?\d{1,3}|U-[A-Z0-9-]{1,12}|U\d{1,3}(?:\.\d{1,3})?)"

#: A nested unit id inside a phase: M3, CP-2, U5.1, D11, G2.
_NESTED_TOKEN = r"[A-Z]{1,4}-?\d{1,3}(?:\.\d{1,3})?"

#: The separators a task uses between a phase and the unit inside it.
_JOINER = r"\s*(?:/|–|—|-|·|:|\|)\s*"

#: Words a repository uses for "a unit inside a phase".
_NESTED_NOUN = r"(?:unit|checkpoint|milestone|machine|module|increment|sub-?unit|step|slice|part)"

#: Words a repository or a task uses for "an acceptance criterion of a phase".
#: A criterion is something the phase is MEASURED by; a unit is something the
#: phase is BUILT from. An id that the task itself introduces with one of these
#: nouns is the first kind, whatever its shape.
_CRITERION_NOUN = (
    r"(?:acceptance\s+criteri(?:on|a)|acceptance\s+condition(?:s)?|criteri(?:on|a)|"
    r"acceptance\s+test(?:s)?|acceptance\s+bar)"
)

#: Range and list joiners. "X through Y" and "X, Y and Z" enumerate a SET, and a
#: set of sibling ids is never the one nested unit a run was asked to build.
_RANGE_JOINER = r"(?:through|thru|to|until|and|,|-|–|—|\.\.\.?)"

#: Asking for the phase itself to be finished — as WORK. Each alternative is
#: guarded against being the first half of a compound: "complete-stream
#: behaviour" names a feature, and reading it as a request to complete something
#: is how a task that says "build one unit" gets heard as "finish the phase".
#:
#: The adverbial forms are here for a reason that cost a whole run. "Build P7
#: completely" is the plainest way a founder writes whole-phase intent, and
#: `complet(?:e|ing|ion)(?![-\w])` refuses it — "complete" is followed by "ly",
#: which is a word character — so the task read as claiming nothing about the
#: phase at all. An adverb is not a compound noun: "completely", "fully" and "in
#: full" say what "complete" says, and each is listed rather than inferred.
#:
#: None of these is a request for ACCEPTANCE. "Finish P7" asks for P7's work to
#: be done; whether that work is accepted is a separate act, performed by the
#: repository's phase-closure process and an adjudicator who did not build it.
#: Reading "complete" as "accept" is what made a whole-phase build unable to
#: finish until acceptance had already happened.
_PHASE_IMPLEMENTATION_VERB = (
    r"complet(?:e|ing|ion|ely)(?![-\w])|finish(?:ing|ed)?(?![-\w])|"
    r"conclud(?:e|ing)(?![-\w])|wrap\s+up|fully(?![-\w])|entirel(?:y)(?![-\w])|"
    r"in\s+full(?![-\w])|in\s+its\s+entirety"
)

#: Asking for the phase to be formally ACCEPTED — closed, adjudicated, signed
#: off. Never inferred from a mention of the acceptance criteria: "build P7
#: according to its acceptance criteria" names the bar the work is built to,
#: which is the opposite of asking this run to award it. That exclusion is the
#: negative lookahead on "acceptance".
_PHASE_ACCEPTANCE_VERB = (
    r"accept(?:ed|ing)?(?![-\w])|acceptance(?![-\w])(?!\s+(?:criteri|condition|test|bar|contract))|"
    r"adjudicat(?:e|es|ed|ing|ion)(?![-\w])|sign(?:s|ed|ing)?\s*-?\s*off|"
    r"clos(?:e|es|ed|ing|ure)(?![-\w])(?:\s+out)?|declare\s+(?:it\s+)?(?:done|accepted|complete)"
)

#: A phase id that is really a phase id, and not the stem of a nested unit's:
#: `P6-D11` mentions P6 and is not about P6.
def _phase_ref(phase_id: str) -> str:
    return rf"\b{re.escape(phase_id)}\b(?![-/\u00b7]\s*[A-Za-z0-9])"

#: Words that turn a completion phrase into a statement that it must NOT happen,
#: or has not happened. A task that says "do not mark P6 COMPLETE" is the
#: opposite of a task that claims phase completion.
_NEGATED = re.compile(
    r"\b(?:no|not|never|cannot|can'?t|isn'?t|won'?t|may\s+not|must\s+not|do\s+not|does\s+not|"
    r"without|before|until|unless|forbidden|prohibited|refuse[sd]?|premature(?:ly)?|"
    r"remains?|still|pending|awaiting|legitimately)\b",
    re.I,
)


class ScopeLevel(str, Enum):
    """How much of the program a run was asked to finish."""

    #: A nested build unit inside a phase. Its acceptance is its own.
    TASK = "TASK"
    #: The phase itself. Whether as work or as acceptance is :class:`ScopeIntent`.
    PHASE = "PHASE"


class ScopeIntent(str, Enum):
    """What the task asks to happen to its scope. Three different jobs.

    Building a phase and accepting a phase are not two strengths of one
    request. The first is the builder's, and is finished when the work the
    phase's criteria describe exists and can be pointed at. The second belongs
    to the repository's phase-closure process — external verification on the
    candidate tree, then an adjudicator outside the build lineage — and it is
    what scores the criteria. A run that treats "build the phase" as "accept
    the phase" cannot finish until acceptance has already happened, which is a
    loop with no exit; a run that treats "accept the phase" as "build it" lets
    a builder award its own phase.
    """

    #: Build one nested unit inside a phase.
    UNIT_IMPLEMENTATION = "UNIT_IMPLEMENTATION"
    #: Build the whole phase. Its criteria are the bar the work is built TO;
    #: they stay as the repository records them until phase closure scores them.
    PHASE_IMPLEMENTATION = "PHASE_IMPLEMENTATION"
    #: Formally accept, close, adjudicate or sign off the phase. The full phase
    #: bar: every required criterion satisfied, by its own authority.
    PHASE_ACCEPTANCE = "PHASE_ACCEPTANCE"
    #: No unit and no explicit phase request could be read. The strict default
    #: governs what a CLAIM is measured against; nothing is owed on its behalf.
    UNSPECIFIED = "UNSPECIFIED"


class TaskScope(BaseModel):
    """The unit of work a run was asked for, and the phase it sits inside.

    Read as one sentence: *this run was asked for ``scope_id``, which lives
    inside ``parent_phase_id``, and the repository records that phase as
    ``parent_phase_state``.*
    """

    model_config = ConfigDict(extra="ignore")

    scope_id: str = ""
    label: str = ""
    level: ScopeLevel = ScopeLevel.PHASE

    parent_phase_id: str = ""
    parent_phase_state: str = ""
    parent_phase_execution_state: str = ""

    #: True when the task itself asks for the parent phase to be completed or
    #: accepted. Only then is phase-level acceptance evidence this run's bar.
    claims_phase_completion: bool = True

    #: True only when the task text ACTUALLY asked for the phase itself — to be
    #: built or to be accepted — never when the phase bar was applied because
    #: no unit could be derived. WHICH of the two it asked for is ``intent``,
    #: and nothing that decides what a run owes may read this flag alone: it is
    #: the umbrella that "build P7 completely" and "accept P7" share, and
    #: reading it as acceptance is the conflation ``intent`` exists to prevent.
    #:
    #: The distinction from ``claims_phase_completion`` matters because that
    #: flag is deliberately the strict *default*: a task naming no unit gets
    #: held to the phase, which is right for evidence and wrong for anything
    #: that reads the flag as a statement about intent.
    phase_completion_requested: bool = False

    #: What the task asks to happen: build a unit, build the phase, or accept
    #: the phase. See :class:`ScopeIntent`.
    intent: ScopeIntent = ScopeIntent.UNSPECIFIED

    #: The id the repository gives this unit, when it names one (a checkpoint
    #: id, say). Corroboration, never a requirement: a repository is entitled to
    #: have work in flight that its registry has not yet named.
    repository_unit_id: str = ""

    #: How each field above was arrived at, for the record.
    derivation: list[str] = Field(default_factory=list)
    evidence_paths: list[str] = Field(default_factory=list)

    @property
    def is_nested(self) -> bool:
        return self.level is ScopeLevel.TASK and not self.claims_phase_completion

    @property
    def phase_state_text(self) -> str:
        """How to say where the phase is, in one word.

        A registry can carry two different facts about a phase: which unit is
        selected to work on (``status: READY``) and how far it has got
        (``execution_state: IN_PROGRESS``). The second is the one a reader means
        by "where is P6", so it wins when both are present.
        """
        return self.parent_phase_execution_state or self.parent_phase_state or "IN_PROGRESS"

    @property
    def requires_phase_acceptance(self) -> bool:
        """Whether a CLAIM this run makes about the phase is held to the phase's
        own acceptance evidence. The strict default, about claims — not a
        statement that this run is the one that accepts the phase. For that,
        ask :attr:`phase_acceptance_requested`."""
        return self.claims_phase_completion

    @property
    def phase_implementation_requested(self) -> bool:
        """The task asked for the whole phase to be BUILT, and not accepted."""
        return self.intent is ScopeIntent.PHASE_IMPLEMENTATION

    @property
    def phase_acceptance_requested(self) -> bool:
        """The task asked for the phase to be formally accepted or closed."""
        return self.intent is ScopeIntent.PHASE_ACCEPTANCE

    @property
    def may_record_phase_acceptance(self) -> bool:
        """Whether this run's record may ever say the parent phase is accepted.

        Only a run that was asked to accept the phase. A unit build cannot, and
        neither can a whole-phase build: completing the implementation is the
        thing phase closure is handed, not the thing it concludes.
        """
        return self.claims_phase_completion and self.phase_acceptance_requested

    def describe(self) -> str:
        if self.is_nested:
            return (
                f"{self.scope_id} (a unit inside {self.parent_phase_id}; "
                f"{self.parent_phase_id} stays {self.phase_state_text})"
            )
        if self.phase_implementation_requested:
            return (
                f"{self.scope_id or self.parent_phase_id} (whole-phase implementation; "
                "acceptance belongs to phase closure)"
            )
        if self.phase_acceptance_requested:
            return f"{self.scope_id or self.parent_phase_id} (formal phase acceptance)"
        return f"{self.scope_id or self.parent_phase_id} (phase scope)"

    def summary_block(self) -> str:
        lines = [
            f"TASK SCOPE: {self.scope_id or '(none derived)'}  [{self.level.value}]",
            f"PARENT PHASE: {self.parent_phase_id or '(none declared)'} "
            f"— {self.parent_phase_state or 'unknown'}"
            + (
                f" / {self.parent_phase_execution_state}"
                if self.parent_phase_execution_state
                else ""
            ),
            f"TASK INTENT: {self.intent.value}",
        ]
        if self.phase_implementation_requested:
            lines.append(
                "CLAIMS PHASE COMPLETION: no — this run builds the phase; its acceptance "
                "belongs to phase closure"
            )
        else:
            lines.append(
                "CLAIMS PHASE COMPLETION: "
                + (
                    "yes — phase acceptance is this run's bar"
                    if self.claims_phase_completion
                    else "no"
                )
            )
        return "\n".join(lines)

    def render(self) -> str:
        """The block handed to the builder and the reviewer."""
        phase = self.parent_phase_id or self.scope_id or "the phase"
        if self.phase_implementation_requested:
            return (
                f"SCOPE OF THIS RUN: {phase} — the WHOLE phase, as IMPLEMENTATION.\n"
                f"PARENT PHASE: {phase} — recorded as {self.phase_state_text}, and this run "
                "does not accept it.\n"
                f"Build everything {phase}'s required acceptance criteria describe. Those "
                "criteria are the bar the work is built TO; they are not yours to score. Leave "
                "every criterion's result exactly as the repository records it — they stay "
                "PENDING until phase closure scores them.\n"
                "This run is finished when the implementation exists and can be pointed at: in "
                "your report, account for EVERY required criterion by its id, naming the "
                "test, probe or battery in the tree that establishes it "
                "(`<criterion id>: IMPLEMENTED — <path/to/test.py::test_name>`), or saying "
                "plainly that it is NOT IMPLEMENTED yet. Criteria settled by an independent "
                "review or by external verification (CI) are phase closure's; do not claim them.\n"
                f"Completing this implementation does NOT accept {phase}, does NOT score a "
                "criterion, does NOT unblock the next phase, and enables nothing in "
                f"production. Do not describe {phase} as accepted or COMPLETE, and do not "
                "edit a status surface to say so. The completed candidate is handed to phase "
                "closure, which is where acceptance happens."
            )
        if not self.is_nested:
            return (
                f"SCOPE OF THIS RUN: {self.scope_id or self.parent_phase_id or 'the task as written'}\n"
                "This task is read as claiming completion of the parent phase, so the phase's "
                "own acceptance evidence is required before it can be accepted."
            )
        return (
            f"SCOPE OF THIS RUN: {self.scope_id}\n"
            f"PARENT PHASE: {self.parent_phase_id} — recorded as "
            f"{self.phase_state_text}, and this run does not change that.\n"
            "Accepting this task does NOT complete the parent phase, does NOT score a phase "
            "acceptance criterion, does NOT unblock any later phase, and enables nothing in "
            "production. Do not claim otherwise, and do not edit a status surface to say "
            "otherwise."
        )


class TaskResult(str, Enum):
    """What a scoped task achieved, kept separate from any phase verdict."""

    ACCEPTED = "ACCEPTED"
    VERIFIED = "VERIFIED"
    UNPROVEN = "UNPROVEN"
    CONTRADICTED = "CONTRADICTED"
    AWAITING_INDEPENDENT_REVIEW = "AWAITING_INDEPENDENT_REVIEW"


class ScopedCompletion(BaseModel):
    """The run's completion state, at both levels, in one record.

        task_scope:           P6/M3
        task_result:          VERIFIED
        parent_phase:         P6
        parent_phase_state:   IN_PROGRESS

    The second pair is copied from the repository and never computed from the
    first. That is the whole point: there is no arithmetic anywhere that turns a
    task result into a phase state.
    """

    model_config = ConfigDict(extra="ignore")

    task_scope: str = ""
    task_result: TaskResult = TaskResult.UNPROVEN
    task_evidence: list[str] = Field(default_factory=list)
    task_outstanding: list[str] = Field(default_factory=list)

    parent_phase: str = ""
    parent_phase_state: str = ""
    parent_phase_execution_state: str = ""
    #: Always false unless the task itself asked for the phase to be ACCEPTED
    #: and the phase's own acceptance evidence held. Nothing else may set it —
    #: in particular, not a whole-phase build, however complete.
    parent_phase_accepted: bool = False
    #: What accepting this task explicitly does not do. Written out because the
    #: failure this module exists to prevent was a reader inferring the opposite.
    does_not_imply: list[str] = Field(default_factory=list)
    #: What the task asked for — see :class:`ScopeIntent`.
    intent: ScopeIntent = ScopeIntent.UNSPECIFIED
    #: Where a verified task goes next, when that is somewhere other than the
    #: founder's push: a verified whole-phase implementation is handed to phase
    #: closure. Empty for every other outcome.
    handoff: str = ""

    @property
    def implementation_verified(self) -> bool:
        """A whole-phase build whose implementation is complete and evidenced —
        IMPLEMENTATION VERIFIED / READY FOR PHASE CLOSURE, and nothing more."""
        return (
            self.intent is ScopeIntent.PHASE_IMPLEMENTATION
            and self.task_result is TaskResult.VERIFIED
            and not self.task_outstanding
        )

    def summary_block(self) -> str:
        lines = [
            f"TASK SCOPE: {self.task_scope or '(none derived)'}",
            f"TASK RESULT: {self.task_result.value}"
            + (
                " — IMPLEMENTATION VERIFIED / READY FOR PHASE CLOSURE"
                if self.implementation_verified
                else ""
            ),
            f"PARENT PHASE: {self.parent_phase or '(none declared)'}",
            f"PARENT PHASE STATE: {self.parent_phase_state or 'unknown'}"
            + (
                f" / {self.parent_phase_execution_state}"
                if self.parent_phase_execution_state
                else ""
            ),
        ]
        if self.intent is ScopeIntent.PHASE_IMPLEMENTATION:
            lines.append(
                f"PARENT PHASE ACCEPTED: no — {self.parent_phase or 'the phase'} is accepted "
                "only by phase closure"
            )
        if self.handoff:
            lines.append(f"HANDOFF: {self.handoff}")
        return "\n".join(lines)


#: What a scoped task acceptance never means. Stated once, carried everywhere,
#: so no reader has to infer it and no later code has to re-derive it.
def standard_exclusions(phase_id: str) -> list[str]:
    phase = phase_id or "the parent phase"
    return [
        f"{phase} is COMPLETE",
        f"any {phase} acceptance criterion is scored",
        f"the units {phase} still owes are built",
        "the next phase is unblocked",
        "phase acceptance has occurred",
        "anything is enabled in production or on live traffic",
    ]


#: What a verified whole-phase IMPLEMENTATION never means. The implementation is
#: the candidate phase closure is handed; everything on this list is what phase
#: closure, and only phase closure, can establish.
def implementation_exclusions(phase_id: str) -> list[str]:
    phase = phase_id or "the phase"
    return [
        f"{phase} is accepted or COMPLETE",
        f"any {phase} acceptance criterion is scored",
        f"{phase}'s independent-review or external-verification criteria are satisfied",
        "phase acceptance has occurred",
        "the next phase is unblocked",
        "anything is enabled in production or on live traffic",
    ]


#: Where a verified whole-phase implementation goes next. One sentence, so the
#: audit, the founder summary and the journal say the same thing.
def phase_closure_handoff(phase_id: str) -> str:
    phase = phase_id or "the phase"
    return (
        f"hand the {phase} candidate to phase closure (`phase close`): external "
        "verification on the candidate tree, then an independent adjudication, which "
        f"is what scores {phase}'s criteria"
    )


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def _clean(task: str) -> str:
    """The task text with fenced code stripped: examples are not requests."""
    return re.sub(r"```.*?```", " ", task or "", flags=re.S)


#: Where one sentence ends and the next begins. A denial belongs to its own
#: sentence: "Finish P6. Do not push it." is a request to finish P6 followed by
#: a separate instruction, and reading the second as cancelling the first is how
#: an explicit request becomes invisible.
_SENTENCE_END = re.compile(r"[.;!?\n]")


def _sentence_around(text: str, start: int, end: int, span: int = 120) -> tuple[str, str, str]:
    """The text either side of a span, clipped to the sentence it sits in."""
    before = text[max(0, start - span) : start]
    cut = list(_SENTENCE_END.finditer(before))
    if cut:
        before = before[cut[-1].end() :]
    after = text[end : end + span]
    stop = _SENTENCE_END.search(after)
    if stop:
        after = after[: stop.start()]
    return before, text[start:end], after


def _negated_near(text: str, start: int, end: int) -> bool:
    """True when the sentence around a span turns it into a denial."""
    before, inside, after = _sentence_around(text, start, end)
    return any(_NEGATED.search(chunk) for chunk in (inside, before, after))


def _first_unnegated(task: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        for match in re.finditer(pattern, task, re.I):
            if _negated_near(task, match.start(), match.end()):
                continue
            return match.group(0).strip()[:160]
    return ""


def _phase_acceptance_requested(task: str, phase_id: str) -> tuple[bool, str]:
    """Does the task ask for the phase to be formally accepted, closed,
    adjudicated or signed off?"""
    if not phase_id:
        return False, ""
    ref = _phase_ref(phase_id)
    phrase = _first_unnegated(
        task,
        (
            rf"\b(?:{_PHASE_ACCEPTANCE_VERB})[^.\n]{{0,14}}?{ref}",
            rf"{ref}[^.\n]{{0,14}}?\b(?:{_PHASE_ACCEPTANCE_VERB})",
            rf"{ref}\s+phase\s+acceptance",
            rf"\bphase\s+(?:acceptance|closure|adjudication)\s+(?:of|for)\s+{ref}",
            # "Complete P6 and take it through phase acceptance."
            rf"{ref}[^.\n]{{0,60}}?\b(?:to|through|into)\s+(?:formal\s+)?(?:phase\s+)?"
            r"(?:acceptance|closure|adjudication|sign\s*-?\s*off)\b"
            r"(?!\s+(?:criteri|condition|test|bar|contract))",
        ),
    )
    return bool(phrase), phrase


def _phase_implementation_requested(task: str, phase_id: str) -> tuple[bool, str]:
    """Does the task ask for the phase's WORK to be finished — the whole phase?"""
    if not phase_id:
        return False, ""
    ref = _phase_ref(phase_id)
    # Deliberately tight. The gap between the verb and the phase id is small,
    # because a task document that discusses a phase for twenty pages will put
    # both words near each other by accident many times over, and every one of
    # those accidents would widen the run's bar to the whole phase.
    phrase = _first_unnegated(
        task,
        (
            rf"\b(?:{_PHASE_IMPLEMENTATION_VERB})[^.\n]{{0,14}}?{ref}",
            rf"{ref}[^.\n]{{0,14}}?\b(?:{_PHASE_IMPLEMENTATION_VERB})",
            rf"\ball\s+of\s+{ref}",
            rf"\bwhole\s+(?:of\s+)?{ref}",
            rf"\bentire\s+{ref}",
        ),
    )
    return bool(phrase), phrase


def _normalize_id(value: str) -> str:
    """One spelling for an id, so `P7-AC-1`, `p7 ac 1` and `P7/AC/1` compare equal."""
    return re.sub(r"[^A-Z0-9]+", "-", str(value or "").upper()).strip("-")


def _id_family(value: str) -> str:
    """An id with its trailing ordinal removed: `P7-AC-1` -> `P7-AC`.

    What a family buys: a repository that declares `P7-AC-1 .. P7-AC-17` has said
    what *shape* an acceptance criterion has in this phase, so `P7-AC-23` is
    recognisable as one too — without the driver holding a list of criterion
    prefixes, and without it knowing that this repository happens to write `AC`.
    """
    return re.sub(r"-?\d+(?:[-.]\d+)*$", "", _normalize_id(value)).strip("-")


def criterion_vocabulary(repo: Path | None, phase_id: str) -> tuple[set[str], set[str], str]:
    """The ids, and id families, THIS repository declares as acceptance criteria.

    Read from the repository's own acceptance authority, never from a list kept
    here. A repository that calls them ``AC-1``, ``CRIT-1`` or ``done-1`` is read
    the same way, and a repository that declares none contributes nothing — the
    textual signals below are then the only ones, which is the correct amount of
    evidence to have.

    Never raises: an unreadable registry means no corroboration, not a failure.
    """
    if repo is None or not phase_id:
        return set(), set(), ""
    try:
        from .phase_authority import resolve_phase_authority

        authority = resolve_phase_authority(Path(repo), phase_id)
    except Exception:  # a registry that cannot be read corroborates nothing
        return set(), set(), ""
    ids: set[str] = set()
    families: set[str] = set()
    for criterion in authority.criteria.criteria:
        cid = _normalize_id(getattr(criterion, "criterion_id", ""))
        if not cid or not re.search(r"\d", cid):
            # A criterion named only in prose ("core_implementation") cannot be
            # confused with an id reference, so it contributes no vocabulary.
            continue
        ids.add(cid)
        family = _id_family(cid)
        if family:
            families.add(family)
    source = next((p for p in authority.source_paths if p), "")
    return ids, families, source


def _declared_criterion(
    token: str, phase_id: str, ids: set[str], families: set[str]
) -> str:
    """Whether the repository declares this reference as an acceptance criterion."""
    for candidate in (_normalize_id(f"{phase_id}-{token}"), _normalize_id(token)):
        if not candidate:
            continue
        if candidate in ids:
            return f"the repository declares {candidate} as an acceptance criterion"
        if _id_family(candidate) and _id_family(candidate) in families:
            return (
                f"the repository's acceptance criteria for {phase_id} are ids of the form "
                f"{_id_family(candidate)}-N, so {candidate} names a criterion"
            )
    return ""


def _reads_as_criterion(text: str, start: int, end: int, token: str, phase_id: str) -> str:
    """Whether the task's own words make this reference a criterion, not a unit.

    Two signals, both about how the task SPEAKS of the id rather than about the
    id itself:

    * the task introduces it with a criterion noun — "its explicit P7-AC-1
      through P7-AC-17 acceptance criteria";
    * the task enumerates it as one of a RANGE or LIST of siblings. A run is
      asked to build one nested unit; it is never asked to build "M3 through
      M17" as a single nested scope. An enumeration is a description of the bar,
      and reading its first element as the run's scope is how seventeen
      criteria became one.
    """
    # Two windows, deliberately different sizes. A criterion noun has to be
    # ATTACHED to the reference — "its P7-AC-1 through P7-AC-17 acceptance
    # criteria" — because a sentence that merely goes on to mention criteria
    # ("build P6/M3; do not score a criterion") says nothing about what M3 is.
    near_before, inside, near_after = _sentence_around(text, start, end, span=36)
    if re.search(_CRITERION_NOUN, f"{near_before}{inside}{near_after}", re.I):
        return "the task introduces it with a criterion noun"

    before, inside, after = _sentence_around(text, start, end, span=110)
    around = f"{before}{inside}{after}"

    family = _id_family(f"{phase_id}-{token}")
    if family:
        stem = family.split("-")[-1]
        if stem and not stem.isdigit():
            sibling = rf"\b{re.escape(phase_id)}?\s*[-/\u00b7]?\s*{re.escape(stem)}-?\d{{1,3}}\b"
            siblings = re.findall(sibling, around, re.I)
            if len(siblings) > 1 and re.search(
                rf"\d\s*{_RANGE_JOINER}\s*(?:{re.escape(phase_id)}[-/\u00b7]\s*)?{re.escape(stem)}",
                around,
                re.I,
            ):
                return "the task enumerates it as one of a range of sibling ids"
    return ""


#: Asking for the phase to be BUILT, with the phase itself as the object.
#: Distinct from asking for it to be completed, and read only when the task
#: names no unit inside the phase: "build P7" with nothing narrower in it is a
#: request for P7, and a run that treats it as a request for nothing in
#: particular reports on a task nobody set.
_PHASE_BUILD_VERB = r"build|implement|deliver|ship|construct|produce"


def _phase_build_requested(task: str, phase_id: str) -> tuple[bool, str]:
    """Does the task ask for the phase itself to be built?

    Stricter about what counts as a reference to the phase than the completion
    patterns are: "Build P6 / M9" is a request to build M9, and the only thing
    separating it from "Build P6" is the joiner that follows. An explicit
    "finish P6" survives a trailing dash; a bare "build P6" must not.
    """
    if not phase_id:
        return False, ""
    ref = rf"\b{re.escape(phase_id)}\b(?!\s*[-/\u00b7:|\u2013\u2014]\s*[A-Za-z0-9])"
    patterns = (
        rf"\b(?:{_PHASE_BUILD_VERB})\b[^.\n]{{0,20}}?{ref}",
        rf"{ref}\s+(?:is|must)\s+(?:to\s+be\s+)?(?:{_PHASE_BUILD_VERB})\w*",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, task, re.I):
            if _negated_near(task, match.start(), match.end()):
                continue
            return True, match.group(0).strip()[:160]
    return False, ""


def _nested_unit(
    task: str,
    phase_id: str,
    *,
    criterion_ids: set[str] | None = None,
    criterion_families: set[str] | None = None,
) -> tuple[str, str, list[str]]:
    """The unit inside the phase that the task names, how it was named, and what
    it refused to read as a unit on the way.

    A phase has two kinds of identifier inside it and they look alike: units it
    is BUILT from, and criteria it is MEASURED by. Only the first can be a run's
    scope. Where the two cannot be told apart, nothing is returned and the
    caller falls back to the phase — the stricter reading.
    """
    if not phase_id:
        return "", "", []
    ids = criterion_ids or set()
    families = criterion_families or set()
    uid = re.escape(phase_id)
    patterns = (
        # "P6 / M3", "P6/M3", "P6 — M3", "P6-CP-3"
        (rf"\b{uid}{_JOINER}({_NESTED_TOKEN})\b", "the task names a unit inside the phase"),
        # "P6 checkpoint 3", "unit M3 of P6"
        (
            rf"\b{uid}\b[^.\n]{{0,24}}?\b{_NESTED_NOUN}\s+({_NESTED_TOKEN})\b",
            "the task names a nested unit by its noun",
        ),
        (
            rf"\b{_NESTED_NOUN}\s+({_NESTED_TOKEN})\b[^.\n]{{0,24}}?\b{uid}\b",
            "the task names a nested unit by its noun",
        ),
    )
    refusals: list[str] = []
    for pattern, why in patterns:
        for match in re.finditer(pattern, task, re.I):
            token = match.group(1).upper()
            # "P6 - COMPLETE" is not a unit id, and neither is a bare year.
            if re.fullmatch(r"\d+", token):
                continue
            declared = _declared_criterion(token, phase_id, ids, families)
            textual = _reads_as_criterion(task, match.start(), match.end(), token, phase_id)
            if declared or textual:
                refusal = (
                    f"{phase_id}-{token} is not read as a build unit: "
                    + "; ".join(r for r in (declared, textual) if r)
                )
                if refusal not in refusals:
                    refusals.append(refusal)
                continue
            return token, why, refusals
    return "", "", refusals


def _registry_unit_id(repo: Path | None, phase_id: str, nested: str) -> tuple[str, str]:
    """The id the repository itself gives this nested unit, if it names one.

    Pure corroboration. A unit the registry has not named yet is still a unit —
    that is the normal state of work in flight — so absence proves nothing and
    changes nothing.
    """
    if repo is None or not phase_id or not nested:
        return "", ""
    # The short-form status document first: it introduces a unit next to the id
    # the repository gives it, where the registry mentions a dozen ids in one
    # narrative comment and the nearest one is not necessarily the right one.
    for rel in (
        "docs/implementation/CURRENT.md",
        "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
    ):
        path = Path(repo) / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:400_000]
        except OSError:
            continue
        # A line that mentions the nested unit and a phase-scoped id together.
        pattern = re.compile(
            rf"\b{re.escape(nested)}\b[^\n]{{0,80}}?\b({re.escape(phase_id)}-[A-Z]{{1,4}}-?\d{{1,3}})\b",
            re.I,
        )
        match = pattern.search(text)
        if match:
            return match.group(1).upper(), rel
    return "", ""


def _phase_state(unit: Any) -> tuple[str, str, str]:
    """The parent phase's id and state, exactly as the repository records it."""
    unit_id = str(getattr(unit, "unit_id", "") or "")
    status = str(getattr(unit, "status", "") or "")
    execution = str(getattr(unit, "execution_state", "") or "")
    return unit_id, status, execution


def resolve_task_scope(
    task: str,
    unit: Any | None = None,
    repo: Path | None = None,
) -> TaskScope:
    """Work out what this run was asked for. Never raises.

    ``task`` is the product owner's instruction for the run — not the builder's
    report, and not the repository's roadmap. ``unit`` is the active unit the
    repository declares, which supplies the parent phase and its recorded state.

    The default is the strict one. A task that names no nested unit, or names
    one while also asking for the phase, is treated as a phase-scope run and
    must clear phase acceptance.
    """
    text = _clean(task)
    phase_id, phase_status, phase_execution = _phase_state(unit)
    derivation: list[str] = []
    evidence: list[str] = []

    if phase_id:
        derivation.append(
            f"parent phase {phase_id} taken from the repository's active unit "
            f"(status {phase_status or 'unrecorded'}"
            + (f", execution_state {phase_execution}" if phase_execution else "")
            + ")"
        )
    else:
        # No registry, or none that declares a unit: fall back to the phase the
        # task itself names, and say so.
        match = re.search(rf"\b({_PHASE_TOKEN})\b", text)
        if match:
            phase_id = match.group(1).upper()
            derivation.append(
                f"the repository declares no active unit; parent phase {phase_id} read "
                "from the task text"
            )

    # Two different requests about the phase, read separately. Acceptance is
    # the wider one — it includes the work — so where both are present it
    # governs; where only the work is asked for, acceptance is phase closure's.
    acceptance_asked, acceptance_phrase = _phase_acceptance_requested(text, phase_id)
    claims_phase, phase_phrase = (
        (True, acceptance_phrase)
        if acceptance_asked
        else _phase_implementation_requested(text, phase_id)
    )

    # What the repository itself calls an acceptance criterion of this phase.
    # Read before any id in the task is read as a build unit, because the two
    # are spelled alike and only the repository knows which is which.
    criterion_ids, criterion_families, criterion_source = criterion_vocabulary(repo, phase_id)
    if criterion_ids:
        derivation.append(
            f"the repository declares {len(criterion_ids)} acceptance criterion id(s) for "
            f"{phase_id}"
            + (f" ({criterion_source})" if criterion_source else "")
            + "; none of them can be this run's build scope"
        )
        if criterion_source and criterion_source not in evidence:
            evidence.append(criterion_source)

    nested, nested_why, refusals = _nested_unit(
        text,
        phase_id,
        criterion_ids=criterion_ids,
        criterion_families=criterion_families,
    )
    derivation.extend(refusals)

    # A task that names no unit inside the phase and asks for the phase to be
    # BUILT has asked for the phase. Read only here, after the unit search has
    # come back empty, so "build P6/M3" is never heard as "build P6".
    if not nested and not claims_phase:
        built, build_phrase = _phase_build_requested(text, phase_id)
        if built:
            claims_phase, phase_phrase = True, build_phrase
            derivation.append(
                f"the task names no unit inside {phase_id} and asks for {phase_id} itself "
                f"to be built ({build_phrase!r})"
            )

    if nested and not claims_phase:
        scope_id = f"{phase_id}/{nested}"
        repository_unit_id, repo_source = _registry_unit_id(repo, phase_id, nested)
        derivation.append(f"{nested_why}: {scope_id}")
        derivation.append(
            "the task does not ask for the parent phase to be completed or accepted, "
            "so phase acceptance is not this run's bar"
        )
        if repository_unit_id and _declared_criterion(
            repository_unit_id, phase_id, criterion_ids, criterion_families
        ):
            # The corroborating line named a criterion, not a unit. Corroboration
            # that names the wrong kind of thing is not weaker corroboration.
            derivation.append(
                f"the repository's mention of {repository_unit_id} is an acceptance "
                "criterion id, so it does not corroborate a unit id"
            )
            repository_unit_id, repo_source = "", ""
        if repository_unit_id:
            derivation.append(
                f"the repository names this unit {repository_unit_id} ({repo_source})"
            )
            evidence.append(repo_source)
        return TaskScope(
            scope_id=scope_id,
            label=_label_for(text, scope_id),
            level=ScopeLevel.TASK,
            parent_phase_id=phase_id,
            parent_phase_state=phase_status,
            parent_phase_execution_state=phase_execution,
            claims_phase_completion=False,
            intent=ScopeIntent.UNIT_IMPLEMENTATION,
            repository_unit_id=repository_unit_id,
            derivation=derivation,
            evidence_paths=evidence,
        )

    if not claims_phase:
        intent = ScopeIntent.UNSPECIFIED
    elif acceptance_asked:
        intent = ScopeIntent.PHASE_ACCEPTANCE
    else:
        intent = ScopeIntent.PHASE_IMPLEMENTATION

    if nested and claims_phase:
        derivation.append(
            f"the task names unit {nested} AND asks for {phase_id} itself "
            f"({phase_phrase!r}); the wider claim governs"
        )
    elif claims_phase:
        derivation.append(f"the task asks for {phase_id} itself ({phase_phrase!r})")
    if intent is ScopeIntent.PHASE_ACCEPTANCE:
        derivation.append(
            f"the task asks for {phase_id} to be formally accepted ({acceptance_phrase!r}), "
            "so the phase's full acceptance bar applies"
        )
    elif intent is ScopeIntent.PHASE_IMPLEMENTATION:
        derivation.append(
            f"the task asks for {phase_id} to be BUILT, not accepted: its acceptance "
            "criteria are the bar the work is built to, they stay as the repository "
            "records them, and acceptance belongs to phase closure"
        )
    elif refusals:
        derivation.append(
            "no single build unit could be derived: every id the task names inside the "
            "phase reads as an acceptance criterion, or as one of an enumerated set, so "
            "the run is held to the phase's own acceptance bar"
        )
    else:
        derivation.append(
            "no unit inside the phase could be derived from the task, so the run is held "
            "to the phase's own acceptance bar"
        )

    return TaskScope(
        scope_id=phase_id,
        label=_label_for(text, phase_id),
        level=ScopeLevel.PHASE,
        parent_phase_id=phase_id,
        parent_phase_state=phase_status,
        parent_phase_execution_state=phase_execution,
        claims_phase_completion=True,
        phase_completion_requested=claims_phase,
        intent=intent,
        derivation=derivation,
        evidence_paths=evidence,
    )


def normalize_task(task: str) -> str:
    """One spelling of a task, for comparing two statements of the same job.

    Whitespace, case and surrounding punctuation are how the same task gets
    retyped; anything else is a different job.
    """
    return re.sub(r"\s+", " ", str(task or "")).strip().strip("\"'").casefold()


def task_identity_differs(persisted: str, supplied: str) -> bool:
    """Whether two task statements describe materially different jobs.

    Deliberately literal. This is asked at exactly one place — a resume that was
    handed a ``--task`` — and the only safe answer to "are these the same job?"
    is one a human can check by reading both. Anything cleverer would be a
    judgement about intent, and a wrong judgement here silently rewrites what a
    run is being verified against.
    """
    return normalize_task(persisted) != normalize_task(supplied)


def _label_for(task: str, scope_id: str) -> str:
    """The task's own first line, which is normally its title."""
    for line in (task or "").splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if len(cleaned) >= 8:
            return cleaned[:160]
    return scope_id


def scoped_completion(
    scope: TaskScope,
    task_result: TaskResult,
    *,
    evidence: list[str] | None = None,
    outstanding: list[str] | None = None,
    phase_accepted: bool = False,
) -> ScopedCompletion:
    """Assemble the two-level completion record.

    ``phase_accepted`` is refused unless the task actually asked for the phase
    to be ACCEPTED. A nested task cannot accept a phase however it is called,
    and neither can a whole-phase build however complete it is; the guard lives
    here rather than at the call sites so there is one place to read.
    """
    if scope.is_nested:
        exclusions = standard_exclusions(scope.parent_phase_id)
    elif scope.phase_implementation_requested:
        exclusions = implementation_exclusions(scope.parent_phase_id or scope.scope_id)
    else:
        exclusions = []
    verified_implementation = (
        scope.phase_implementation_requested
        and task_result is TaskResult.VERIFIED
        and not outstanding
    )
    return ScopedCompletion(
        task_scope=scope.scope_id,
        task_result=task_result,
        task_evidence=list(evidence or []),
        task_outstanding=list(outstanding or []),
        parent_phase=scope.parent_phase_id,
        parent_phase_state=scope.parent_phase_state,
        parent_phase_execution_state=scope.parent_phase_execution_state,
        parent_phase_accepted=bool(phase_accepted and scope.may_record_phase_acceptance),
        does_not_imply=exclusions,
        intent=scope.intent,
        handoff=(
            phase_closure_handoff(scope.parent_phase_id or scope.scope_id)
            if verified_implementation
            else ""
        ),
    )
