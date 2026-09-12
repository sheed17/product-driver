"""Materializing the acceptance record the repository already knows how to write.

``READY_FOR_ACCEPTANCE_COMMIT`` means every prerequisite has been satisfied on a
frozen candidate tree: the scope is built, the external verifier is green on the
exact commit, an independent session adjudicated the phase, and every required
criterion is established. What is left is bookkeeping — the repository's own
status record still says the phase is pending — and until this module existed
nobody wrote it. The closure reached its last state, called the commit
preparation, and was told the working tree was clean: *"the acceptance is
already recorded, or the record was never written"*. It was the second one, and
nothing in the pipeline could ever make it the first.

So this module writes that record, under four constraints that are the whole
design:

* **it invents no format.** Every value it writes is one the repository itself
  already uses on a unit it already accepted: the status tokens, the name of the
  result field, the token that field carries for a passing criterion, the name
  of the evidence field. A repository that has never accepted a unit has not
  stated what an acceptance looks like, and that is an ``AUTHORITY_GAP`` — a
  founder decision — rather than an invitation to guess;
* **it invents no criteria and changes no verdict.** The rows written are the
  frozen set's, the results written are the adjudication's, and a required
  criterion the adjudication did not score PASS refuses the whole write;
* **it writes only the acceptance record.** The file is classified by
  :mod:`~neyma_product_driver.acceptance_commit` first and must come out
  ``ACCEPTANCE_RECORD``; a working tree already carrying runtime, test,
  migration, CI or specification changes refuses the write before it starts, so
  a status edit is never laid on top of something unverified;
* **it edits, it does not regenerate.** A registry is prose as much as data —
  superseded wording kept deliberately, comments that carry the reason a field
  holds the value it holds. Re-serializing the parsed document would delete all
  of it. So the edit is textual and line-local, and the result is parsed and
  compared against the original: if anything changed that this module did not
  intend, nothing is written at all.

The next unit advances only where the repository's own authority says it
should — the accepted unit's own "what this unlocks" field names it, the
repository has made that same move before, the successor's declared dependencies
are all satisfied by this acceptance, and it declares no outstanding blocker.
Where any of that is missing the successor is left alone and the reason is
recorded.

Nothing here commits, and nothing here pushes.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import yaml

from .acceptance_commit import Surface, _dirty_paths, classify_surface
from .models import redact
from .phase_acceptance import normalize_repo_path
from .phase_authority import (
    ACCEPTED_UNIT_STATES,
    DEFAULT_REGISTRY_PATHS,
    _CRITERIA_KEYS,
    _CRITERION_EVIDENCE_KEYS,
    _CRITERION_ID_KEYS,
    _CRITERION_REQUIRED_KEYS,
    _CRITERION_RESULT_KEYS,
    _NEXT_KEYS,
    _UNIT_ID_KEYS,
    _UNITS_KEYS,
    _first,
)

#: The unit fields that carry a phase's acceptance state, as a repository might
#: name them. Each group is one fact; the first name present on the unit wins.
#: The FIRST group is the selection field — the one a successor moves in.
_STATE_KEY_GROUPS: tuple[tuple[str, ...], ...] = (
    ("status", "unit_status", "phase_status"),
    ("execution_state", "execution_status"),
    ("checkpoint_state", "review_state", "acceptance_state"),
)

_DEPENDENCY_KEYS = ("dependencies", "depends_on", "requires", "prerequisites")
_BLOCKER_KEYS = ("validation_blockers", "blockers", "open_blockers")

#: How wide a written folded scalar may run before it wraps.
_FOLD_WIDTH = 96


# --------------------------------------------------------------------------
# What one write would be
# --------------------------------------------------------------------------


@dataclass
class RecordEdit:
    """One field of one unit, moving from what it says to what it will say."""

    path: str
    unit_id: str
    field_path: str
    before: str
    after: str
    why: str

    def brief(self) -> str:
        was = self.before if self.before else "(absent)"
        return f"{self.unit_id}.{self.field_path}: {was} -> {self.after}"


@dataclass
class AcceptanceRecordPlan:
    """The status-only change that records one phase's acceptance."""

    phase_id: str = ""
    source_path: str = ""
    edits: list[RecordEdit] = field(default_factory=list)
    #: Why no record may be written from this tree. Empty when it may.
    refusal: str = ""
    #: Set when the repository does not state enough to write its own record.
    #: A founder or architect decision, not a refusal this module can lift.
    authority_gap: str = ""
    notes: list[str] = field(default_factory=list)
    derivation: list[str] = field(default_factory=list)
    #: The unit the successor moved to, when one did.
    next_phase_advanced: str = ""
    #: What the machine record now says, and what it said before.
    facts: list[StatusFact] = field(default_factory=list)
    #: Every document the repository declares as live status.
    declared_surfaces: list[str] = field(default_factory=list)
    #: Live restatements that cannot be reconciled without composing prose.
    stale_restatements: list[StaleRestatement] = field(default_factory=list)
    #: What the repository's own guards over the changed record answered.
    verification: Any = None
    #: Acceptance-record paths the working tree ALREADY carries. Set when this
    #: module wrote nothing: a record somebody else wrote is still a record, and
    #: the commit preparation is still entitled to validate it.
    existing_record: list[str] = field(default_factory=list)
    #: Rendered file text, per repo-relative path. Produced at plan time and
    #: verified before it is ever written.
    rendered: dict[str, str] = field(default_factory=dict)
    #: The bytes each of those files had on the candidate tree, so a record the
    #: repository's own guards refuse can be rolled back exactly.
    original: dict[str, str] = field(default_factory=dict)
    written: bool = False

    @property
    def permitted(self) -> bool:
        return not self.refusal and not self.authority_gap

    @property
    def complete(self) -> bool:
        """Whether this record leaves the repository's status authorities agreeing.

        Separate from :attr:`permitted` on purpose. A record may be perfectly
        permitted to write and still leave a declared restatement stale, and the
        write happens anyway — so the repository's own guards can be asked about
        it — before both problems are reported together and it is rolled back.
        """
        return self.permitted and not self.stale_restatements

    @property
    def already_recorded(self) -> bool:
        """The repository already says everything this write would say."""
        return self.permitted and not self.edits

    @property
    def changed_paths(self) -> list[str]:
        return sorted({e.path for e in self.edits})

    def render(self) -> str:
        lines = [f"ACCEPTANCE RECORD: {self.phase_id or '(no phase)'}"]
        if self.authority_gap:
            lines.append(f"  AUTHORITY GAP: {self.authority_gap}")
            for entry in self.stale_restatements[:12]:
                lines.append(f"    {entry.brief()}")
            if len(self.stale_restatements) > 12:
                lines.append(f"    ... and {len(self.stale_restatements) - 12} more")
            for path in self.existing_record:
                lines.append(f"  the working tree already carries a record at {path}")
            return "\n".join(lines)
        if self.refusal:
            lines.append(f"  REFUSED: {self.refusal}")
            return "\n".join(lines)
        if not self.edits:
            lines.append("  the repository already records this acceptance; nothing to write")
        for path in self.changed_paths:
            lines.append(f"  {path} [ACCEPTANCE_RECORD]")
        for edit in self.edits:
            lines.append(f"    {edit.brief()}")
        if self.next_phase_advanced:
            lines.append(f"  next unit advanced: {self.next_phase_advanced}")
        for note in self.notes:
            lines.append(f"  note: {note}")
        for entry in self.stale_restatements[:12]:
            lines.append(f"  STALE {entry.brief()}")
        if len(self.stale_restatements) > 12:
            lines.append(f"  STALE ... and {len(self.stale_restatements) - 12} more")
        if self.written:
            lines.append("  written to the working tree. Nothing staged, nothing committed.")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# What the repository states an acceptance looks like
# --------------------------------------------------------------------------


@dataclass
class RecordConvention:
    """The repository's own acceptance vocabulary, read off its own precedent."""

    #: unit field name -> the value an accepted unit carries.
    state_values: dict[str, str] = field(default_factory=dict)
    #: The criterion field that carries a result, and the token it carries when
    #: the criterion passed.
    result_key: str = ""
    pass_token: str = ""
    #: Where the repository records what established a criterion. Optional: a
    #: repository that keeps none has not asked for one.
    evidence_key: str = ""
    #: The unit field a successor moves in, and what it moves to.
    selection_key: str = ""
    successor_token: str = ""
    #: Non-empty when the repository does not state enough to be read.
    problem: str = ""
    derivation: list[str] = field(default_factory=list)


def _unit_id(unit: Any) -> str:
    return str(_first(unit, _UNIT_ID_KEYS, "") or "").strip()


def _is_accepted(unit: Any) -> bool:
    """Whether the repository records this unit as accepted, in its own words."""
    for group in _STATE_KEY_GROUPS:
        value = _first(unit, group, "")
        if isinstance(value, str) and value.strip().upper() in ACCEPTED_UNIT_STATES:
            return True
    return False


def _criteria_rows(unit: Any) -> list[dict]:
    raw = _first(unit, _CRITERIA_KEYS, None)
    rows = list(raw.values()) if isinstance(raw, dict) else list(raw or [])
    return [r for r in rows if isinstance(r, dict)]


def _row_key(row: Any, names: Sequence[str]) -> str:
    for name in names:
        if isinstance(row, dict) and name in row:
            return name
    return ""


def _is_required(row: dict) -> bool:
    key = _row_key(row, _CRITERION_REQUIRED_KEYS)
    if not key:
        return True
    return bool(row.get(key))


def _next_ids(unit: Any) -> list[str]:
    raw = _first(unit, _NEXT_KEYS, None)
    if isinstance(raw, str):
        raw = [raw]
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def _listed(unit: Any, names: Sequence[str]) -> list[str]:
    raw = _first(unit, names, None)
    if isinstance(raw, str):
        raw = [raw]
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def derive_convention(units: Sequence[dict], target: dict) -> RecordConvention:
    """Read the acceptance vocabulary off the units the repository already accepted.

    Precedent, not preference. Every value here is one the repository itself
    wrote about a unit it itself accepted, so a repository that spells its
    accepted state ``DONE`` gets ``DONE`` and a repository that scores a passing
    criterion ``SATISFIED`` gets ``SATISFIED``. Disagreement among its own
    accepted units is reported rather than resolved by voting: two accepted
    units that record different things have not stated one convention.
    """
    conv = RecordConvention()
    target_id = _unit_id(target).upper()
    accepted = [u for u in units if _is_accepted(u) and _unit_id(u).upper() != target_id]
    if not accepted:
        conv.problem = (
            "the repository records no already-accepted unit, so it does not state what an "
            "accepted unit looks like: which status values mean accepted, which field carries "
            "a criterion's result and what that field says when the criterion passes are all "
            "unstated. Product Driver will not invent an acceptance format"
        )
        return conv
    conv.derivation.append(
        f"read from {len(accepted)} unit(s) the repository already accepts: "
        + ", ".join(sorted(_unit_id(u) for u in accepted)[:8])
    )

    # -- the unit's own state fields --------------------------------------
    for group in _STATE_KEY_GROUPS:
        key = next((k for k in group if isinstance(target.get(k), str)), "")
        if not key:
            continue
        votes = {
            str(u[key]).strip()
            for u in accepted
            if isinstance(u.get(key), str) and str(u[key]).strip()
        }
        if not votes:
            conv.problem = (
                f"the target unit records `{key}` but no unit the repository already accepts "
                f"does, so what `{key}` says on an accepted unit is unstated"
            )
            return conv
        if len(votes) > 1:
            conv.problem = (
                f"the units the repository already accepts disagree about `{key}` "
                f"({', '.join(sorted(votes))}), so there is no single value to write"
            )
            return conv
        conv.state_values[key] = votes.pop()
    if not conv.state_values:
        conv.problem = (
            "the target unit records none of the fields the repository's accepted units use "
            "to say a unit is accepted, so there is nothing to move"
        )
        return conv
    conv.selection_key = next(
        (k for k in _STATE_KEY_GROUPS[0] if k in conv.state_values), ""
    )
    conv.derivation.append(
        "accepted state: "
        + ", ".join(f"{k}={v}" for k, v in conv.state_values.items())
    )

    # -- how a criterion records that it passed ---------------------------
    # Read from the unit the repository says comes IMMEDIATELY BEFORE this one —
    # the accepted unit whose own "what this unlocks" field names the target —
    # in preference to the whole set. A repository that has been running for a
    # while has older records written under conventions it has since moved on
    # from, and its own graph says which one is current. Without a predecessor,
    # every accepted unit has to agree.
    predecessor = next(
        (
            u
            for u in accepted
            if target_id and target_id in {s.upper() for s in _next_ids(u)}
        ),
        None,
    )
    sources = [predecessor] if predecessor is not None else list(accepted)
    if predecessor is not None:
        conv.derivation.append(
            f"a criterion's shape is read from {_unit_id(predecessor)}, the accepted unit "
            "this one follows"
        )
    result_keys: dict[str, int] = {}
    evidence_keys: dict[str, int] = {}
    pass_tokens: set[str] = set()
    for unit in sources:
        for row in _criteria_rows(unit):
            rkey = _row_key(row, _CRITERION_RESULT_KEYS)
            if rkey:
                result_keys[rkey] = result_keys.get(rkey, 0) + 1
                token = str(row.get(rkey) or "").strip()
                if token and _is_required(row):
                    pass_tokens.add(token)
            ekey = _row_key(row, _CRITERION_EVIDENCE_KEYS)
            if ekey:
                evidence_keys[ekey] = evidence_keys.get(ekey, 0) + 1
    if not result_keys or not pass_tokens:
        whose = (
            f"{_unit_id(predecessor)}, the accepted unit this one follows,"
            if predecessor is not None
            else "no unit the repository already accepts"
        )
        conv.problem = (
            f"{whose} records no result on its acceptance criteria, so what a passing "
            "criterion is supposed to say is unstated"
        )
        return conv
    if len(pass_tokens) > 1:
        whose = (
            f"{_unit_id(predecessor)} scores its required criteria"
            if predecessor is not None
            else "the units the repository already accepts score their required criteria"
        )
        conv.problem = (
            f"{whose} with more than one token ({', '.join(sorted(pass_tokens))}), so which "
            "one means PASS is not something Product Driver may choose"
        )
        return conv
    conv.pass_token = pass_tokens.pop()
    target_result_key = ""
    for row in _criteria_rows(target):
        target_result_key = _row_key(row, _CRITERION_RESULT_KEYS)
        if target_result_key:
            break
    conv.result_key = target_result_key or max(result_keys, key=lambda k: result_keys[k])
    conv.evidence_key = (
        max(evidence_keys, key=lambda k: evidence_keys[k]) if evidence_keys else ""
    )
    conv.derivation.append(
        f"a passing criterion records `{conv.result_key}: {conv.pass_token}`"
        + (f", with its basis under `{conv.evidence_key}`" if conv.evidence_key else "")
    )

    # -- what a successor does when its predecessor is accepted -----------
    by_id = {_unit_id(u).upper(): u for u in units if _unit_id(u)}
    tokens: set[str] = set()
    if conv.selection_key:
        for unit in accepted:
            for sid in _next_ids(unit):
                successor = by_id.get(sid.upper())
                if successor is None or _is_accepted(successor):
                    continue
                value = successor.get(conv.selection_key)
                if isinstance(value, str) and value.strip():
                    tokens.add(value.strip())
    if len(tokens) == 1:
        conv.successor_token = tokens.pop()
        conv.derivation.append(
            f"a unit whose predecessor is accepted carries "
            f"`{conv.selection_key}: {conv.successor_token}`"
        )
    elif tokens:
        conv.derivation.append(
            "the repository's own accepted-to-successor transitions disagree "
            f"({', '.join(sorted(tokens))}); no successor will be advanced"
        )
    else:
        conv.derivation.append(
            "the repository states no accepted-to-successor transition; no successor "
            "will be advanced"
        )
    return conv


# --------------------------------------------------------------------------
# The documents that RESTATE the machine record
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusFact:
    """One thing the machine record now says, and what it said before.

    The unit of reconciliation. A restatement surface is stale exactly when it
    still says ``before`` about ``unit_id`` where the registry now says
    ``after``, and there is nothing else a restatement is entitled to say.
    """

    unit_id: str
    field_path: str
    before: str
    after: str

    def brief(self) -> str:
        return f"{self.unit_id}.{self.field_path}: {self.before} -> {self.after}"


@dataclass
class StaleRestatement:
    """A live claim a restatement surface makes that the record now contradicts."""

    path: str
    line: int
    unit_id: str
    stale_value: str
    current_value: str
    text: str
    why: str

    def brief(self) -> str:
        return (
            f"{self.path}:{self.line} still states {self.stale_value} for {self.unit_id} "
            f"(the record now says {self.current_value}) — {self.why}"
        )


#: A line that marks itself as a record of what WAS true is not a live claim,
#: and rewriting it would destroy the history it exists to keep. Deliberately
#: generous: a false positive here leaves a line alone, and a line left alone
#: that IS live is then reported as an unreconciled restatement rather than
#: silently rewritten. There is no path on which this marker edits something it
#: should not have — it only ever withholds an edit.
_HISTORICAL = re.compile(
    r"(?i)\b(?:until this|until the|previously|formerly|historical|superseded|retired|"
    r"replaced rather than|kept verbatim|no longer|used to|was true|were true|"
    r"true when written|is false now|stale now|earlier read|as of the|"
    r"this (?:cell|row|line|paragraph|document|record) read)\b|\bREPLACED\b"
)

#: A unit identifier as repositories write them. Used to count how many units a
#: table row is ABOUT: a row whose subject is a RANGE (``P8-P14``) states one
#: fact about several units, and moving it would move units this acceptance
#: says nothing about.
_UNIT_TOKEN = re.compile(r"(?<![A-Za-z0-9_])[A-Z]{1,4}-?[0-9]{1,3}(?:\.[0-9]{1,3})?(?![A-Za-z0-9_])")

#: What a human status restatement is written in. A restatement is prose or a
#: table; a binary is not one, whatever it is classified as.
_RESTATEMENT_SUFFIXES = (".md", ".markdown", ".rst", ".txt", ".yaml", ".yml", ".json")


def _token(value: str) -> "re.Pattern[str]":
    """A status value matched as a whole token, never inside a longer word.

    Hyphen-tight: ``STARTED`` must not match inside ``NOT_STARTED``.
    """
    return re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(value) + r"(?![A-Za-z0-9_-])")


def _unit_token(unit_id: str) -> "re.Pattern[str]":
    """A unit id matched as a whole token, hyphen INCLUDED as a boundary.

    The opposite tightness from :func:`_token`, and deliberately so: a row whose
    subject is the range ``P10-P14`` is about P10, and not seeing it there is
    how one edit silently moves four units this acceptance says nothing about.
    """
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(unit_id) + r"(?![A-Za-z0-9_])")


def _row_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    return [c.strip() for c in stripped.strip("|").split("|")]


def _tracked_files(repo: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]


def _read_text(path: Path, limit: int = 4_000_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


#: Spans in which a document keeps the wording it replaced. Repositories that
#: preserve superseded claims IN PLACE — rather than deleting them — mark them,
#: and these are the two marks in general use: an italic parenthetical aside,
#: and a blockquote. Text inside one is a record of what WAS said. It is never
#: read as a live claim and never edited.
_ASIDE = re.compile(r"\*\([^)]*\)\*", re.S)
_QUOTED = re.compile(r"\"[^\"\n]{0,400}\"")


def _mask_history(text: str) -> str:
    """The document with its preserved-history spans blanked, positions intact.

    Length-preserving, so an offset in the masked text is the same offset in the
    original. What remains is what the document says in its own voice, now.
    """
    masked = list(text)

    def blank(match: "re.Match[str]") -> None:
        for position in range(match.start(), match.end()):
            if masked[position] != "\n":
                masked[position] = " "

    for pattern in (_ASIDE, _QUOTED):
        for match in pattern.finditer(text):
            blank(match)
    offset = 0
    for line in text.split("\n"):
        if line.lstrip().startswith(">"):
            for position in range(offset, offset + len(line)):
                masked[position] = " "
        offset += len(line) + 1
    return "".join(masked)


#: A cell that carries a STATUS VALUE rather than a sentence about one. Status
#: values are shouted tokens, counts and separators; the moment a cell contains
#: ordinary prose it is a sentence, and replacing a token inside a sentence
#: leaves the sentence saying something nobody wrote.
_PROSE_WORD = re.compile(r"(?<![A-Za-z0-9_])[a-z][a-z']{2,}(?![A-Za-z0-9_])")


def _is_status_cell(cell: str) -> bool:
    stripped = re.sub(r"`|\*|\[|\]\([^)]*\)", "", cell)
    return not _PROSE_WORD.search(stripped)


def _states_fact(text: str, fact: "StatusFact") -> bool:
    """Whether this document makes a LIVE statement of one fact's old value."""
    unit = _unit_token(fact.unit_id)
    before = _token(fact.before)
    for line in _mask_history(text).splitlines():
        if _HISTORICAL.search(line):
            continue
        if unit.search(line) and before.search(line):
            return True
    return False


def reconcile_restatement(
    text: str, facts: "Sequence[StatusFact]"
) -> tuple[str, list[tuple[int, str, str]], list["StaleRestatement"]]:
    """Bring one restatement surface back in line. Returns (text, moved, stuck).

    Exactly one shape is rewritten, and it is the one a machine can rewrite
    without composing a sentence: a table row whose SUBJECT names one unit and
    one unit only, keeping no preserved history, whose stale token sits in a
    cell that carries STATUS VALUES rather than prose. That is a restatement in
    the strict sense — the registry's own value, copied into a document for a
    human to read — and moving it states nothing the machine record does not
    already state.

    Everything else is reported as STUCK rather than guessed at:

    * a narrative sentence, because rewriting one means composing prose, and how
      a repository replaces a superseded claim is its own convention rather than
      something Product Driver may instantiate;
    * a cell that explains the status as well as stating it, because swapping
      the token there leaves the explanation beside it saying the opposite —
      ``COMPLETE — the sole selected unit`` is a sentence nobody wrote and
      nothing established;
    * a row about a RANGE or a list of units, because one edit there would move
      units this acceptance says nothing about;
    * a row that also keeps the wording it replaced, because the stale token
      there IS the history.

    A stuck restatement is an AUTHORITY_GAP for the caller. It is never left
    quietly stale, and it is never rewritten by guess.
    """
    lines = text.split("\n")
    masked = _mask_history(text).split("\n")
    moved: list[tuple[int, str, str]] = []
    stuck: list["StaleRestatement"] = []
    for index, line in enumerate(lines):
        live = masked[index]
        cells = _row_cells(line)
        is_row = cells is not None
        if not is_row and _HISTORICAL.search(live):
            continue
        subject_units = set(_UNIT_TOKEN.findall(cells[0])) if cells else set()
        updated = line
        for fact in facts:
            unit_here = _unit_token(fact.unit_id).search(live)
            stale_here = _token(fact.before).search(live)
            if not unit_here or not stale_here:
                continue

            def note(why: str) -> None:
                stuck.append(
                    StaleRestatement(
                        path="",
                        line=index + 1,
                        unit_id=fact.unit_id,
                        stale_value=fact.before,
                        current_value=fact.after,
                        text=line.strip()[:240],
                        why=why,
                    )
                )

            if not is_row:
                note("it is a narrative sentence, not a status restatement")
                continue
            if _HISTORICAL.search(line):
                note("the row also keeps the wording it replaced, so the stale token is history")
                continue
            if subject_units != {fact.unit_id}:
                named = ", ".join(sorted(subject_units)) if subject_units else "no single unit"
                note(f"the row is about {named} rather than this unit alone")
                continue
            carrying = [
                cell
                for cell in (cells or [])[1:]
                if _token(fact.before).search(cell)
            ]
            if not carrying:
                note("the stale value is in the row's subject rather than in a status cell")
                continue
            if any(not _is_status_cell(cell) for cell in carrying):
                note("the cell explains the status as well as stating it, so it is prose")
                continue
            replaced = _token(fact.before).sub(fact.after, updated)
            if replaced != updated:
                moved.append((index + 1, updated, replaced))
                updated = replaced
        if updated != line:
            lines[index] = updated
    return "\n".join(lines), moved, stuck


# --------------------------------------------------------------------------
# Which documents the repository declares as its live status
# --------------------------------------------------------------------------

#: A classification token, as an authority map writes one: SHOUTED, and its own
#: word rather than a sentence. ``CURRENT_STATUS``, ``HISTORICAL``, ``EVIDENCE``.
_CLASS_TOKEN = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z_]{3,31})(?![A-Za-z0-9_])")

#: A class that says "this document states what is true NOW". The one word that
#: separates a live restatement from a record of one is STATUS.
_LIVE_CLASS = re.compile(r"(?i)status")

#: Classes that say the opposite, and outrank the above on the same row: a
#: document classified HISTORICAL is a record of what was true, and a phase
#: acceptance may not rewrite it.
_RECORD_CLASS = re.compile(
    r"(?i)historical|superseded|evidence|archive|deprecat|quarantin|legacy|retired|obsolete"
)

#: A file named in an authority map's row, as a `backtick` or a [link](target).
_MAP_FILE = re.compile(
    r"`([A-Za-z0-9_./-]+\.(?:md|markdown|rst|txt|ya?ml|json))`"
    r"|\]\(([A-Za-z0-9_./-]+\.(?:md|markdown|rst|txt|ya?ml|json))\)"
)

#: How many classified rows a document needs before it is believable as the
#: repository's authority map rather than a table that mentions a file.
_MIN_AUTHORITY_ROWS = 5


def _named_files(cell: str) -> list[str]:
    return [a or b for a, b in _MAP_FILE.findall(cell)]


def _resolve_named(name: str, tracked: "set[str]") -> str:
    """One name from an authority map, as the repository actually tracks it."""
    candidate = name.lstrip("./")
    for guess in (candidate, f"docs/{candidate}"):
        if guess in tracked:
            return guess
    base = candidate.rsplit("/", 1)[-1]
    hits = [t for t in tracked if t == base or t.endswith("/" + base)]
    return hits[0] if len(hits) == 1 else ""


def declared_status_surfaces(repo: Path) -> tuple[list[str], str, list[str]]:
    """Documents the repository's own authority map classifies as live status.

    Returns ``(paths, map_path, notes)``.

    A repository that keeps more than one status surface usually says so, in a
    document that classifies its own documents — which file states what, and
    which files are records of what WAS true rather than statements about now.
    That map is the authority for this population, and reading it is the
    difference between reconciling a status document and rewriting a completed
    review's findings.

    The map is discovered structurally rather than by name: the tracked document
    with the most table rows that both NAME another tracked file and carry a
    classification token. A repository with no such document has not declared a
    status population, and gets none — configuration is then the only way it
    names one, which is the honest answer rather than a guess.
    """
    repo = Path(repo)
    notes: list[str] = []
    tracked = set(_tracked_files(repo))
    best: tuple[int, str] = (0, "")
    rows_by_map: dict[str, list[tuple[str, str]]] = {}
    for rel in sorted(tracked):
        if not rel.lower().endswith((".md", ".markdown", ".rst")):
            continue
        text = _read_text(repo / rel, limit=1_000_000)
        if "|" not in text:
            continue
        rows: list[tuple[str, str]] = []
        for line in text.splitlines():
            cells = _row_cells(line)
            if not cells or len(cells) < 2:
                continue
            named = _named_files(cells[0])
            if not named:
                continue
            classes = _CLASS_TOKEN.findall(line.replace(cells[0], "", 1))
            if not classes:
                continue
            rows.append((line, cells[0]))
        if len(rows) >= _MIN_AUTHORITY_ROWS:
            rows_by_map[rel] = rows
            if (len(rows), -len(rel)) > (best[0], -len(best[1]) if best[1] else -10**6):
                best = (len(rows), rel)

    map_path = best[1]
    if not map_path:
        return [], "", notes

    surfaces: list[str] = []
    for line, subject in rows_by_map[map_path]:
        remainder = line.replace(subject, "", 1)
        classes = _CLASS_TOKEN.findall(remainder)
        if not any(_LIVE_CLASS.search(c) for c in classes):
            continue
        if any(_RECORD_CLASS.search(c) for c in classes):
            continue
        for name in _named_files(subject):
            resolved = _resolve_named(name, tracked)
            if resolved and resolved not in surfaces:
                surfaces.append(resolved)
    notes.append(
        f"{map_path} classifies {len(surfaces)} document(s) as this repository's live status"
    )
    return surfaces, map_path, notes


def restatement_surfaces(
    repo: Path,
    facts: "Sequence[StatusFact]",
    *,
    exclude: "Sequence[str]" = (),
    acceptance_globs: "Sequence[str]" = (),
    declared_globs: "Sequence[str]" = (),
) -> tuple[list[str], list[str], list[str]]:
    """This repository's live status documents that have gone stale.

    Returns ``(stale, declared, notes)`` — the ones carrying a fact this
    acceptance moves, the whole declared population, and how it was arrived at.

    The population is the repository's own, never this module's: the paths named
    in configuration if the repository names any, otherwise the ones its own
    authority map classifies as live status. Discovering it by looking for
    documents that MENTION a phase would sweep in every completed review report
    in the repository — those say what was true when they were written, and a
    phase acceptance may not edit them.

    It is then narrowed twice, and both narrowings only ever remove: a declared
    surface the classifier does not call ``ACCEPTANCE_RECORD`` is not writable
    here whatever it is classified as upstream, and a surface that states none
    of the moving facts is already consistent and is left alone.
    """
    repo = Path(repo)
    notes: list[str] = []
    skip = {normalize_repo_path(p) for p in exclude}
    tracked = [r for r in _tracked_files(repo) if r not in skip]

    declared: list[str] = []
    if declared_globs:
        declared = [
            rel for rel in tracked if any(fnmatch.fnmatch(rel, g) for g in declared_globs)
        ]
        notes.append(
            f"the repository names {len(declared)} status surface(s) in configuration"
        )
    else:
        found, map_path, map_notes = declared_status_surfaces(repo)
        declared = [rel for rel in found if rel not in skip]
        notes.extend(map_notes)
        if not map_path:
            notes.append(
                "this repository declares no authority map and names no status surface in "
                "configuration, so the machine record is the only surface reconciled"
            )

    writable: list[str] = []
    for rel in declared:
        surface = classify_surface(rel, acceptance_globs=acceptance_globs)
        if surface is not Surface.ACCEPTANCE_RECORD:
            notes.append(
                f"{rel} is declared live status but classifies as {surface.value}; an "
                "acceptance record may not write it"
            )
            continue
        if not rel.lower().endswith(_RESTATEMENT_SUFFIXES):
            notes.append(f"{rel} is declared live status but is not a document this can read")
            continue
        writable.append(rel)

    stale = [
        rel
        for rel in writable
        if any(_states_fact(_read_text(repo / rel), fact) for fact in facts)
    ]
    return stale, declared, notes


# --------------------------------------------------------------------------
# A line-local YAML editor
# --------------------------------------------------------------------------

_KEY_RE = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z_][A-Za-z0-9_.\-]*):(?P<rest>(?:\s.*)?)$")
_ITEM_RE = re.compile(r"^(?P<indent> *)-(?P<space>\s)")


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _skippable(line: str) -> bool:
    return (not line.strip()) or line.lstrip().startswith("#")


def _normalized(line: str) -> str:
    """A sequence item's first line, as though the dash were a space.

    Length-preserving on purpose: every index taken from the normalized line is
    valid in the original, so a value can be rewritten without losing the dash.
    """
    match = _ITEM_RE.match(line)
    if not match:
        return line
    cut = len(match.group("indent"))
    return line[:cut] + " " + line[cut + 1 :]


def _split_value(rest: str | None) -> tuple[str, str]:
    """A scalar's text and its trailing comment, from everything after the colon."""
    body = rest or ""
    quote = ""
    for index, char in enumerate(body):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            continue
        if char == "#" and (index == 0 or body[index - 1] in " \t"):
            start = index
            while start > 0 and body[start - 1] in " \t":
                start -= 1
            return body[:start].rstrip(), body[start:]
    return body.rstrip(), ""


def _scalar_text(rest: str | None) -> str:
    value, _comment = _split_value(rest)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _unit_blocks(lines: Sequence[str]) -> list[tuple[str, int, int, int]]:
    """Every unit in the file: its id, its line span, and its key column."""
    blocks: list[tuple[str, int, int, int]] = []
    for index, line in enumerate(lines):
        item = _ITEM_RE.match(line)
        if not item:
            continue
        key_match = _KEY_RE.match(_normalized(line))
        if not key_match or key_match.group("key") not in _UNIT_ID_KEYS:
            continue
        indent = len(item.group("indent"))
        body = len(key_match.group("indent"))
        end = len(lines)
        for probe in range(index + 1, len(lines)):
            if _skippable(lines[probe]):
                continue
            if _indent_of(lines[probe]) <= indent:
                end = probe
                break
        blocks.append((_scalar_text(key_match.group("rest")), index, end, body))
    return blocks


def _find_key(
    lines: Sequence[str], start: int, end: int, indent: int, names: Sequence[str]
) -> int | None:
    """The line holding one key at exactly one indentation, or nothing."""
    wanted = tuple(names)
    for index in range(start, end):
        line = lines[index]
        if _skippable(line):
            continue
        match = _KEY_RE.match(_normalized(line))
        if not match or len(match.group("indent")) != indent:
            continue
        if match.group("key") in wanted:
            return index
    return None


def _key_extent(lines: Sequence[str], key_line: int, indent: int, end: int) -> int:
    """Where a key's value stops. A sequence at the key's own column continues it."""
    for probe in range(key_line + 1, end):
        line = lines[probe]
        if _skippable(line):
            continue
        probe_indent = _indent_of(line)
        if probe_indent > indent:
            continue
        if probe_indent == indent and _ITEM_RE.match(line):
            continue
        return probe
    return end


def _sequence_items(
    lines: Sequence[str], start: int, end: int
) -> list[tuple[int, int, int]]:
    """Every sequence item in a span: its first line, its span, and its key column."""
    items: list[tuple[int, int, int]] = []
    item_indent: int | None = None
    for index in range(start, end):
        line = lines[index]
        if _skippable(line):
            continue
        match = _ITEM_RE.match(line)
        if not match:
            continue
        indent = len(match.group("indent"))
        if item_indent is None:
            item_indent = indent
        if indent != item_indent:
            continue
        items.append((index, end, indent + 1 + len(match.group("space"))))
    for position in range(len(items) - 1):
        items[position] = (items[position][0], items[position + 1][0], items[position][2])
    return items


def _folded(indent: int, key: str, text: str) -> list[str]:
    """``key: >-`` and its wrapped content, the way a registry writes prose.

    Folding collapses every newline back to a single space, so what is read back
    is exactly the text handed in — provided the text was normalized to single
    spaces first, which is what the caller does.
    """
    pad = " " * indent
    if not text or any(ch in text for ch in "\t\r\n\x0b\x0c") or not text.isprintable():
        return [f"{pad}{key}: {json.dumps(text)}"]
    body: list[str] = []
    current = ""
    for word in text.split(" "):
        if current and len(current) + 1 + len(word) > _FOLD_WIDTH:
            body.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        body.append(current)
    content_pad = " " * (indent + 2)
    return [f"{pad}{key}: >-"] + [f"{content_pad}{line}" for line in body]


# --------------------------------------------------------------------------
# Verifying that only what was intended changed
# --------------------------------------------------------------------------


def _semantic_paths(old: Any, new: Any, path: str = "") -> list[str]:
    """Every place two parsed documents differ, as dotted paths."""
    if type(old) is not type(new) and not (
        isinstance(old, (int, float)) and isinstance(new, (int, float))
    ):
        return [path or "(root)"]
    if isinstance(old, dict):
        differences: list[str] = []
        for key in sorted(set(old) | set(new)):
            child = f"{path}.{key}" if path else str(key)
            if key not in old or key not in new:
                differences.append(child)
                continue
            differences += _semantic_paths(old[key], new[key], child)
        return differences
    if isinstance(old, list):
        if len(old) != len(new):
            return [path or "(root)"]
        differences = []
        for index, (a, b) in enumerate(zip(old, new)):
            differences += _semantic_paths(a, b, f"{path}[{index}]")
        return differences
    return [] if old == new else [path or "(root)"]


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def _evidence_text(verdict: Any, candidate: Any, adjudication: Any) -> str:
    """What established this criterion, and which tree it was established on."""
    head = str(getattr(candidate, "head", "") or "")[:12]
    tree = str(getattr(candidate, "tree", "") or "")[:12]
    where = f"{head or '(no commit)'}/{tree or '(no tree)'}"
    who = str(getattr(adjudication, "reviewer_session_id", "") or "").strip()
    lead = (
        f"Adjudicated {str(getattr(verdict, 'verdict', '') or '').strip()} on {where} "
        f"by an independent session"
        + (f" ({who})" if who else "")
        + "."
    )
    basis = " ".join(redact(str(getattr(verdict, "basis", "") or "")).split())
    return f"{lead} {basis}".strip() if basis else lead


def _registry_source(repo: Path, registry_paths: Sequence[str]) -> tuple[str, Any, str]:
    """The first registry file that parses into units: its path, data and problem."""
    for rel in registry_paths or DEFAULT_REGISTRY_PATHS:
        path = Path(repo) / rel
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, yaml.YAMLError) as exc:
            return rel, None, f"{rel} did not parse: {exc}"
        if not isinstance(data, dict):
            continue
        raw = _first(data, _UNITS_KEYS)
        if raw is None:
            continue
        units = list(raw.values()) if isinstance(raw, dict) else list(raw or [])
        if [u for u in units if isinstance(u, dict)]:
            return rel, data, ""
    return (
        "",
        None,
        "the repository declares no readable unit registry at any of: "
        + ", ".join(registry_paths or DEFAULT_REGISTRY_PATHS),
    )


def existing_record_paths(
    repo: Path, *, acceptance_globs: Sequence[str] = ()
) -> list[str]:
    """Acceptance-record paths the working tree already carries.

    A record Product Driver did not write is still a record. Where this module
    cannot write one, somebody else's is the difference between a phase that
    stops and a phase whose existing record is validated and handed over.
    """
    return sorted(
        path
        for path, _status in _dirty_paths(Path(repo))
        if classify_surface(path, acceptance_globs=acceptance_globs) is Surface.ACCEPTANCE_RECORD
    )


def plan_acceptance_record(
    repo: Path,
    *,
    phase_id: str,
    criteria: Any,
    adjudication: Any = None,
    candidate: Any = None,
    registry_paths: Sequence[str] = (),
    acceptance_globs: Sequence[str] = (),
    declared_globs: Sequence[str] = (),
) -> AcceptanceRecordPlan:
    """Work out the status-only change that records this phase's acceptance.

    A dry run in the strict sense: the file is rendered and parsed here, and
    :func:`write_acceptance_record` only flushes what this already proved safe.
    """
    repo = Path(repo)
    plan = AcceptanceRecordPlan(phase_id=phase_id)

    # (a) nothing is written on top of something unverified.
    outside = [
        (path, surface)
        for path, _status in _dirty_paths(repo)
        for surface in [classify_surface(path, acceptance_globs=acceptance_globs)]
        if surface is not Surface.ACCEPTANCE_RECORD
    ]
    if outside:
        listed = "; ".join(f"{p} ({s.value})" for p, s in outside[:8])
        plan.refusal = (
            "the working tree already changes "
            f"{len(outside)} file(s) outside the acceptance record: {listed}"
            + (" ..." if len(outside) > 8 else "")
            + ". An acceptance record may not be written over unverified changes; commit or "
            "revert them under their own review first."
        )
        return plan

    # (b) the repository's registry, and that it is an acceptance-record surface.
    plan.existing_record = existing_record_paths(repo, acceptance_globs=acceptance_globs)
    source, data, problem = _registry_source(repo, registry_paths)
    if problem or data is None:
        plan.authority_gap = problem or "the repository declares no readable unit registry"
        return plan
    plan.source_path = source
    surface = classify_surface(source, acceptance_globs=acceptance_globs)
    if surface is not Surface.ACCEPTANCE_RECORD:
        plan.refusal = (
            f"the repository keeps its unit registry at {source}, which classifies as "
            f"{surface.value} rather than ACCEPTANCE_RECORD. Product Driver writes the "
            "acceptance record and nothing else"
        )
        return plan

    units_key = next((k for k in _UNITS_KEYS if k in data), "")
    raw_units = data.get(units_key)
    ordered = list(raw_units.values()) if isinstance(raw_units, dict) else list(raw_units or [])
    units = [u for u in ordered if isinstance(u, dict)]
    target = next((u for u in units if _unit_id(u).upper() == str(phase_id).upper()), None)
    if target is None:
        plan.authority_gap = f"the repository's registry ({source}) declares no unit {phase_id}"
        return plan

    conv = derive_convention(units, target)
    plan.derivation = list(conv.derivation)
    if conv.problem:
        plan.authority_gap = conv.problem
        return plan

    # (c) the adjudication must actually carry every required criterion.
    rows = {}
    for row in _criteria_rows(target):
        key = _row_key(row, _CRITERION_ID_KEYS)
        if key:
            rows[str(row.get(key) or "").strip()] = row
    unsupported: list[str] = []
    for criterion in getattr(criteria, "criteria", []) or []:
        if not getattr(criterion, "required", True):
            continue
        cid = criterion.criterion_id
        if cid not in rows:
            unsupported.append(f"{cid} (no row in {source})")
            continue
        verdict = adjudication.result_for(cid) if adjudication is not None else None
        if verdict is None or not verdict.passed or verdict.outside_authority:
            unsupported.append(f"{cid} (the adjudication does not score it PASS)")
    if unsupported:
        plan.refusal = (
            "the acceptance record may only say what the adjudication established, and it "
            f"does not establish {len(unsupported)} required criterion/criteria: "
            + "; ".join(unsupported[:8])
            + (" ..." if len(unsupported) > 8 else "")
        )
        return plan

    # (d) the edits themselves.
    text = (Path(repo) / source).read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    blocks = {
        uid.upper(): (start, end, body) for uid, start, end, body in _unit_blocks(lines)
    }
    target_block = blocks.get(str(phase_id).upper())
    if target_block is None:
        plan.refusal = (
            f"{source} parses a unit {phase_id} that its text does not present as an editable "
            "block, so the record cannot be written line-locally"
        )
        return plan

    pending: list[tuple[int, int, list[str]]] = []
    intended: list[str] = []
    unit_index = {_unit_id(u).upper(): i for i, u in enumerate(units)}
    prefix = f"{units_key}[{unit_index[str(phase_id).upper()]}]"

    def scalar_edit(
        block: tuple[int, int, int],
        unit: str,
        keys: Sequence[str],
        value: str,
        field_path: str,
        why: str,
        semantic: str,
    ) -> str:
        """Move one scalar. Returns a problem, or empty when it was planned."""
        start, end, body = block
        index = _find_key(lines, start, end, body, keys)
        if index is None:
            return f"{unit} records no {'/'.join(keys)} field to move"
        match = _KEY_RE.match(_normalized(lines[index]))
        assert match is not None
        current, comment = _split_value(match.group("rest"))
        if current.strip() in ("", ">", ">-", "|", "|-", ">+", "|+"):
            return f"{unit}.{field_path} is not a plain scalar, so it will not be rewritten"
        before = _scalar_text(match.group("rest"))
        if before == value:
            return ""
        colon = len(match.group("indent")) + len(match.group("key"))
        pending.append((index, index + 1, [f"{lines[index][:colon + 1]} {value}{comment}"]))
        intended.append(semantic)
        plan.edits.append(
            RecordEdit(
                path=source,
                unit_id=unit,
                field_path=field_path,
                before=before,
                after=value,
                why=why,
            )
        )
        return ""

    problems: list[str] = []
    for key, value in conv.state_values.items():
        problems.append(
            scalar_edit(
                target_block,
                _unit_id(target),
                (key,),
                value,
                key,
                "the repository records an accepted unit this way",
                f"{prefix}.{key}",
            )
        )

    # the criteria.
    start, end, body = target_block
    criteria_key_line = _find_key(lines, start, end, body, _CRITERIA_KEYS)
    if criteria_key_line is None:
        plan.refusal = f"{source} presents no acceptance criteria block for {phase_id} to score"
        return plan
    criteria_end = _key_extent(lines, criteria_key_line, body, end)
    items = _sequence_items(lines, criteria_key_line + 1, criteria_end)
    row_order = {cid: index for index, (cid, _row) in enumerate(rows.items())}
    for item_start, item_end, item_body in items:
        id_line = _find_key(lines, item_start, item_end, item_body, _CRITERION_ID_KEYS)
        if id_line is None:
            continue
        id_match = _KEY_RE.match(_normalized(lines[id_line]))
        assert id_match is not None
        cid = _scalar_text(id_match.group("rest"))
        verdict = adjudication.result_for(cid) if adjudication is not None else None
        if verdict is None or not verdict.passed or verdict.outside_authority:
            continue
        row = rows.get(cid)
        if row is None:
            continue
        position = row_order.get(cid)
        if position is None:
            continue
        criteria_field = _row_key(target, _CRITERIA_KEYS) or _CRITERIA_KEYS[0]
        row_path = f"{prefix}.{criteria_field}[{position}]"
        problems.append(
            scalar_edit(
                (item_start, item_end, item_body),
                cid,
                (conv.result_key,),
                conv.pass_token,
                conv.result_key,
                "the adjudication scored it PASS on the accepted tree",
                f"{row_path}.{conv.result_key}",
            )
        )
        if not conv.evidence_key:
            continue
        existing = str(row.get(conv.evidence_key) or "").strip()
        if existing:
            continue
        written = " ".join(_evidence_text(verdict, candidate, adjudication).split())
        anchor = _find_key(lines, item_start, item_end, item_body, (conv.result_key,))
        anchor = anchor if anchor is not None else id_line
        evidence_line = _find_key(lines, item_start, item_end, item_body, (conv.evidence_key,))
        if evidence_line is not None:
            pending.append(
                (evidence_line, evidence_line + 1, _folded(item_body, conv.evidence_key, written))
            )
        else:
            pending.append((anchor + 1, anchor + 1, _folded(item_body, conv.evidence_key, written)))
        intended.append(f"{row_path}.{conv.evidence_key}")
        plan.edits.append(
            RecordEdit(
                path=source,
                unit_id=cid,
                field_path=conv.evidence_key,
                before=existing,
                after=written[:120] + (" ..." if len(written) > 120 else ""),
                why="the adjudication's own basis, on the tree it adjudicated",
            )
        )

    stated = [p for p in problems if p]
    if stated:
        plan.refusal = (
            "the acceptance record could not be written field by field, so none of it was: "
            + "; ".join(stated[:6])
            + (" ..." if len(stated) > 6 else "")
        )
        plan.edits = []
        return plan

    # the successor, only where the repository's authority says so.
    _plan_successor(plan, conv, units, target, blocks, lines, pending, intended, unit_index,
                    units_key, source)

    if not plan.edits:
        plan.notes.append("the repository already records this acceptance")
        return plan

    # (e) render, and prove that only what was intended moved.
    updated = list(lines)
    for begin, finish, replacement in sorted(pending, key=lambda p: p[0], reverse=True):
        updated[begin:finish] = replacement
    rendered = "\n".join(updated)
    try:
        reparsed = yaml.safe_load(rendered)
    except yaml.YAMLError as exc:
        plan.refusal = f"the acceptance record edit would not parse, so nothing was written: {exc}"
        plan.edits = []
        return plan
    moved = set(_semantic_paths(data, reparsed))
    unexpected = sorted(moved - set(intended))
    missing = sorted(set(intended) - moved)
    if unexpected or missing:
        plan.refusal = (
            "the acceptance record edit did not come out as intended, so nothing was written"
            + (f"; it would also change {', '.join(unexpected[:6])}" if unexpected else "")
            + (f"; it would not change {', '.join(missing[:6])}" if missing else "")
        )
        plan.edits = []
        return plan
    plan.rendered[source] = rendered
    plan.original[source] = text
    plan.facts = _facts_from_edits(plan, _unit_id(target), conv)
    _plan_restatements(
        repo,
        plan,
        plan.facts,
        source=source,
        acceptance_globs=acceptance_globs,
        declared_globs=declared_globs,
    )
    return plan


def _plan_successor(
    plan: AcceptanceRecordPlan,
    conv: RecordConvention,
    units: Sequence[dict],
    target: dict,
    blocks: dict[str, tuple[int, int, int]],
    lines: list[str],
    pending: list[tuple[int, int, list[str]]],
    intended: list[str],
    unit_index: dict[str, int],
    units_key: str,
    source: str,
) -> None:
    """Move the next unit into the selected state, where the repository says to.

    Four things must all hold, and each of them is the repository's own
    statement rather than this module's inference: the accepted unit names the
    successor, the repository has made this same move before, every dependency
    the successor declares is satisfied once this acceptance is recorded, and
    the successor declares no outstanding blocker of its own.
    """
    if not conv.successor_token or not conv.selection_key:
        # Said only when a record is actually being written. On a re-run that
        # finds everything already recorded there is no next unit waiting, and
        # reporting one would describe a decision nobody is being asked to take.
        if plan.edits:
            plan.notes.append(
                "the repository states no accepted-to-successor transition, so no next unit "
                "was advanced; which unit becomes current is a founder decision"
            )
        return
    by_id = {_unit_id(u).upper(): u for u in units if _unit_id(u)}
    accepted_after = {
        _unit_id(u).upper() for u in units if _is_accepted(u)
    } | {_unit_id(target).upper()}
    for sid in _next_ids(target):
        successor = by_id.get(sid.upper())
        if successor is None:
            plan.notes.append(f"{sid} is named as the next unit but the registry declares no such unit")
            continue
        if _is_accepted(successor):
            continue
        unmet = [
            dep
            for dep in _listed(successor, _DEPENDENCY_KEYS)
            if dep.upper() not in accepted_after
        ]
        if unmet:
            plan.notes.append(
                f"{sid} was not advanced: it still depends on {', '.join(sorted(unmet)[:6])}"
            )
            continue
        blockers = _listed(successor, _BLOCKER_KEYS)
        if blockers:
            plan.notes.append(
                f"{sid} was not advanced: it declares {len(blockers)} outstanding blocker(s)"
            )
            continue
        block = blocks.get(sid.upper())
        if block is None:
            plan.notes.append(f"{sid} has no editable block in {source}")
            continue
        start, end, body = block
        index = _find_key(lines, start, end, body, (conv.selection_key,))
        if index is None:
            plan.notes.append(f"{sid} records no {conv.selection_key} field to move")
            continue
        match = _KEY_RE.match(_normalized(lines[index]))
        assert match is not None
        before = _scalar_text(match.group("rest"))
        if before == conv.successor_token:
            continue
        _value, comment = _split_value(match.group("rest"))
        colon = len(match.group("indent")) + len(match.group("key"))
        pending.append(
            (index, index + 1, [f"{lines[index][:colon + 1]} {conv.successor_token}{comment}"])
        )
        intended.append(f"{units_key}[{unit_index[sid.upper()]}].{conv.selection_key}")
        plan.edits.append(
            RecordEdit(
                path=source,
                unit_id=sid,
                field_path=conv.selection_key,
                before=before,
                after=conv.successor_token,
                why=(
                    f"{_unit_id(target)} names {sid} as what it unlocks, and the repository "
                    "records a unit whose predecessor is accepted this way"
                ),
            )
        )
        plan.next_phase_advanced = sid


def _facts_from_edits(
    plan: "AcceptanceRecordPlan", target_id: str, conv: "RecordConvention"
) -> list[StatusFact]:
    """What the machine record now says, as facts a restatement can be checked against.

    Derived from the edits actually planned rather than from what was intended:
    a field the registry already recorded correctly moved nothing, states
    nothing new, and cannot make a restatement stale.
    """
    facts: list[StatusFact] = []
    seen: set[tuple[str, str, str]] = set()
    for edit in plan.edits:
        if edit.unit_id == target_id and edit.field_path in conv.state_values:
            key = (target_id, edit.before, edit.after)
            field = edit.field_path
        elif edit.field_path == conv.result_key:
            # Seventeen criteria moving PENDING -> PASS is ONE fact about the
            # unit as far as a human restatement is concerned: no status
            # document names them one by one.
            key = (target_id, edit.before, edit.after)
            field = f"{conv.result_key} (every criterion)"
        elif edit.field_path == conv.selection_key and edit.unit_id != target_id:
            key = (edit.unit_id, edit.before, edit.after)
            field = edit.field_path
        else:
            continue
        if key in seen or not edit.before or edit.before == edit.after:
            continue
        seen.add(key)
        facts.append(StatusFact(key[0], field, edit.before, edit.after))
    return facts


def _plan_restatements(
    repo: Path,
    plan: "AcceptanceRecordPlan",
    facts: "Sequence[StatusFact]",
    *,
    source: str,
    acceptance_globs: "Sequence[str]",
    declared_globs: "Sequence[str]",
) -> None:
    """Bring every declared status surface back in line with the machine record.

    The machine record is the authority and the restatements follow it; nothing
    here reads a status document to decide what is true. A surface that cannot
    be brought into line is an AUTHORITY_GAP, because the alternative is an
    acceptance commit that contradicts itself — the registry saying the phase is
    accepted and the document beside it saying the phase has not started.
    """
    stale, declared, notes = restatement_surfaces(
        repo,
        facts,
        exclude=[source],
        acceptance_globs=acceptance_globs,
        declared_globs=declared_globs,
    )
    plan.declared_surfaces = list(declared)
    plan.notes.extend(notes)
    for rel in stale:
        text = _read_text(repo / rel)
        updated, moved, stuck = reconcile_restatement(text, facts)
        for entry in stuck:
            entry.path = rel
            plan.stale_restatements.append(entry)
        if stuck or not moved:
            continue
        plan.rendered[rel] = updated
        plan.original[rel] = text
        for line_no, before, after in moved:
            plan.edits.append(
                RecordEdit(
                    path=rel,
                    unit_id=f"line {line_no}",
                    field_path="restatement",
                    before=before.strip()[:80],
                    after=after.strip()[:80],
                    why="this document restates the machine record and must not drift from it",
                )
            )


def stale_restatement_gap(plan: "AcceptanceRecordPlan") -> str:
    """Why an otherwise-complete record still leaves the repository contradicting itself."""
    if not plan.stale_restatements:
        return ""
    listed = "; ".join(s.brief() for s in plan.stale_restatements[:6])
    return (
        f"{len(plan.stale_restatements)} live restatement(s) of this phase's status cannot be "
        "reconciled mechanically, and an acceptance record that leaves them stale is a commit "
        "that contradicts itself — the machine record saying the phase is accepted and the "
        "repository's own status document beside it saying it has not started: "
        + listed
        + (" ..." if len(plan.stale_restatements) > 6 else "")
        + ". Product Driver does not compose status prose; a founder or architect brings these "
        "into line, and the closure then records the acceptance."
    )


def verify_acceptance_record(
    repo: Path,
    plan: "AcceptanceRecordPlan",
    *,
    max_targets: int = 6,
    timeout_s: int = 900,
) -> "AcceptanceRecordPlan":
    """Ask the repository's own guards what they think of the record just written.

    The step this path was missing. A status-only diff that contains nothing but
    the acceptance record can still turn the repository red — a criterion's own
    oracle may assert the PRE-acceptance state out of the very file the record
    moves, and the acceptance commit would then be the commit that broke the
    suite it was claiming was green.

    Only the repository's own tests run, only the ones that actually READ a file
    this diff changed, and a guard that was ALREADY failing is not attributed to
    the record: each failure is re-run against the original bytes before it is
    believed. A guard that goes from green to red because of this diff refuses
    the record, and the write is rolled back so nothing is left half-recorded.
    """
    from .repo_verification import discover_record_guards, run_verification

    if not plan.written or not plan.rendered:
        return plan
    repo = Path(repo)
    targets, notes = discover_record_guards(
        repo, plan.changed_paths, max_targets=max_targets
    )
    verification = run_verification(
        repo,
        targets,
        timeout_s=timeout_s,
        notes=notes,
        surfaces=["the acceptance record this closure wrote"],
        surface_evidence=[f"changed {', '.join(plan.changed_paths)}"],
    )
    plan.verification = verification

    attributable = []
    for result in verification.product_failures:
        if _fails_without_the_record(repo, plan, result, timeout_s=timeout_s):
            plan.notes.append(
                f"{result.target.path} already fails on the candidate tree; the acceptance "
                "record is not what made it red"
            )
            continue
        attributable.append(result)

    if attributable:
        restore_candidate_tree(repo, plan)
        plan.written = False
        listed = "; ".join(
            f"{r.target.path} ({salient_failure(r.detail)})" for r in attributable[:4]
        )
        plan.refusal = (
            f"the acceptance record turns {len(attributable)} of this repository's own guard(s) "
            f"red, so it is not ready to be committed: {listed}"
            + (" ..." if len(attributable) > 4 else "")
            + ". The record was rolled back. A status diff that breaks the repository is not a "
            "status diff the repository accepts, and which of the two is wrong — the record or "
            "the guard — is a founder or architect decision."
        )
    return plan


def salient_failure(detail: str) -> str:
    """The line of a test runner's output a person would actually read.

    A runner ends with its warnings summary far more often than with its
    failure, and quoting the last line at a founder reports a deprecation notice
    as the reason their phase did not close.
    """
    lines = [line.strip() for line in (detail or "").splitlines() if line.strip()]
    for marker in ("FAILED", "assert", "Error"):
        hit = next((line for line in lines if marker in line), "")
        if hit:
            return hit[:160]
    return (lines[-1][:160] if lines else "failed")


def _fails_without_the_record(
    repo: Path, plan: "AcceptanceRecordPlan", result: Any, *, timeout_s: int
) -> bool:
    """Whether this guard was red before the record was written.

    Re-run against the original bytes, then the record is put back. Paid only
    for guards that actually failed, so the ordinary green path runs everything
    once.
    """
    from .repo_verification import run_verification

    restore_candidate_tree(repo, plan)
    try:
        baseline = run_verification(repo, [result.target], timeout_s=timeout_s)
    finally:
        for rel, text in plan.rendered.items():
            try:
                (repo / rel).write_text(text, encoding="utf-8")
            except OSError:
                pass
    return bool(baseline.results and not baseline.results[0].passed)


def restore_candidate_tree(repo: Path, plan: "AcceptanceRecordPlan") -> None:
    """Put every written file back to the bytes the candidate tree had.

    A record that is not going to be committed must not be left lying in the
    working tree: the next thing to read that tree is a fingerprint, a guard or
    a founder, and each of them would read a half-recorded acceptance as a fact.
    """
    for rel, text in plan.original.items():
        try:
            (Path(repo) / rel).write_text(text, encoding="utf-8")
        except OSError:
            pass


def write_acceptance_record(repo: Path, plan: AcceptanceRecordPlan) -> AcceptanceRecordPlan:
    """Flush a permitted plan to the working tree. Never stages, never commits."""
    if not plan.permitted or not plan.edits or not plan.rendered:
        return plan
    repo = Path(repo)
    for rel, text in plan.rendered.items():
        try:
            (repo / rel).write_text(text, encoding="utf-8")
        except OSError as exc:
            plan.refusal = f"the acceptance record could not be written: {exc}"
            return plan
    plan.written = True
    return plan
