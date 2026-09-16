"""Where a repository declares its CURRENT machine status, and what does not.

A status document is not one kind of sentence. The same page carries the
machine record's current values, the history it replaced and kept in place as
evidence, orientation prose that tells a reader which phase holds the selector,
progress narration, and acceptance claims awaiting proof. Every one of them is
written in the same vocabulary — COMPLETE, READY, BLOCKED, ACTIVE, NOT_STARTED,
PENDING — so a reader that treats every appearance of the vocabulary as a
machine status claim will read a document's memory as its present tense.

That is not hypothetical. Neyma's CURRENT.md heading

    ### **P7 IS NOW ACCEPTED TOO, AND P8 HOLDS THE SELECTOR.** P7 is `status: COMPLETE`

says P7 is COMPLETE and says nothing whatever about P8's lifecycle. Read with a
window that crosses the full stop, it became "CURRENT.md declares P8 COMPLETE",
and the completion auditor sent a builder an honest-rollback correction telling
it to restore status documents that were already right.

So this module prefers what a repository declares MECHANICALLY over what its
prose contains:

1.  The unit registry is the machine record.
2.  A document may MARK a bounded region as the projection of that record
    (``<!-- LIVE-STATUS:BEGIN -->`` ... ``<!-- LIVE-STATUS:END -->``). A marked
    region counts only on structural proof — it carries a table and nothing
    else, every column names a field the registry's units actually have, and
    every row's subject is a unit the registry actually declares. Where it
    counts, the region's rows are that document's live status and the prose
    around them asserts no lifecycle at all.
3.  A row whose values are NOT what the registry renders is a real
    contradiction between two machine surfaces, and stays one.
4.  A document that marks no such region has not said where its prose stops
    being status. Nothing is relaxed for it: prose remains its status
    authority, read conservatively, and the caller may still fail closed.

For the conservative path, :func:`prose_status_claims` reads prose the way a
reader does rather than the way a grep does: preserved history is not a live
claim, a status token does not reach across a sentence boundary, and a token
belongs to the nearest subject in front of it — a clause naming two phases
states a fact about one of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import yaml

from .phase_authority import _UNIT_ID_KEYS, _UNITS_KEYS, _first

# --------------------------------------------------------------------------
# The shape of a declared machine-status region
# --------------------------------------------------------------------------

#: A region marker as documents that carry a machine-maintained region write
#: one: an HTML comment opening with a NAME and an edge, in either order —
#: ``<!-- LIVE-STATUS:BEGIN ... -->`` or ``<!-- BEGIN LIVE-STATUS -->``. Only
#: the opening of the comment is read, so the sentence a repository writes after
#: the marker explaining what the region is stays free prose.
_REGION_MARKER = re.compile(
    r"<!--\s*(?:(?P<name_first>[A-Z][A-Z0-9_-]*)\s*:\s*(?P<edge_last>BEGIN|END)"
    r"|(?P<edge_first>BEGIN|END)[\s:]+(?P<name_last>[A-Z][A-Z0-9_-]*))(?![A-Z0-9_-])"
)

#: A markdown table's separator row, and nothing else.
_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")

#: A subject cell that names one unit and says nothing else. A projection's
#: subject is an identifier, never a sentence and never a link.
_BARE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")

#: A column heading that names a registry field. Markup is stripped first, so a
#: repository that writes its headings in backticks still declares a field.
_FIELD_HEADING = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: How many rows a marked region needs before it is believable as a PROJECTION
#: of the machine record rather than an ordinary table that happens to sit
#: between two comments. One row is indistinguishable from a restatement, and
#: the conservative path already handles that correctly.
_MIN_PROJECTION_ROWS = 2


def _unit_id(unit: Any) -> str:
    return str(_first(unit, _UNIT_ID_KEYS, "") or "").strip()


def _row_cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    return [c.strip() for c in stripped.strip("|").split("|")]


def _heading_field(cell: str) -> str:
    """One column heading as the registry field it names, or empty if it names none."""
    stripped = (cell or "").strip().strip("*").strip("`").strip("*").strip()
    return stripped if _FIELD_HEADING.fullmatch(stripped) else ""


def _substitute_row(line: str, values: "Sequence[str]") -> str:
    """One table row with its value cells replaced, pipes and padding preserved.

    Textual and cell-local for the same reason the registry edit is: everything
    this does not set is everything it does not touch.
    """
    parts = line.split("|")
    # parts[0] is what precedes the first pipe, parts[1] the subject cell,
    # parts[2:] the value cells, parts[-1] what follows the last pipe.
    if len(parts) < len(values) + 3:
        return line
    for offset, value in enumerate(values):
        index = 2 + offset
        cell = parts[index]
        body = cell.strip()
        if not body:
            continue
        lead = cell[: len(cell) - len(cell.lstrip())]
        trail = cell[len(cell.rstrip()) :]
        parts[index] = f"{lead}{value}{trail}"
    return "|".join(parts)


def _marker_pairs(lines: "Sequence[str]") -> list[tuple[str, int, int]]:
    """Every ``NAME:BEGIN`` ... ``NAME:END`` pair, outermost first."""
    opens: dict[str, int] = {}
    pairs: list[tuple[str, int, int]] = []
    for index, line in enumerate(lines):
        match = _REGION_MARKER.search(line)
        if match is None:
            continue
        name = match.group("name_first") or match.group("name_last") or ""
        edge = match.group("edge_last") or match.group("edge_first") or ""
        if not name:
            continue
        if edge == "BEGIN":
            opens.setdefault(name, index)
        elif name in opens:
            pairs.append((name, opens.pop(name), index))
    return sorted(pairs, key=lambda p: p[1])


def units_by_id(registry_text: str) -> dict[str, dict]:
    """The registry's units, keyed by upper-case id. Empty when it will not parse."""
    try:
        data = yaml.safe_load(registry_text)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    key = next((k for k in _UNITS_KEYS if k in data), "")
    raw = data.get(key)
    ordered = list(raw.values()) if isinstance(raw, dict) else list(raw or [])
    out: dict[str, dict] = {}
    for unit in ordered:
        if not isinstance(unit, dict):
            continue
        uid = _unit_id(unit)
        if uid:
            out[uid.upper()] = unit
    return out


@dataclass(frozen=True)
class DeclaredRow:
    """One unit's machine status, as a declared region's own row writes it."""

    unit_id: str
    #: 0-based line index of the row in the document.
    line: int
    #: ``field -> the value this row carries``, in the region's column order.
    declared: tuple[tuple[str, str], ...]
    #: ``field -> the value the registry records``, same order.
    recorded: tuple[tuple[str, str], ...]
    #: Whether re-rendering this row from the registry reproduces it byte for
    #: byte. The whole proof that a row projects the record rather than
    #: restating it from memory.
    faithful: bool

    def value(self, field: str) -> str:
        return dict(self.declared).get(field, "")

    def registry_value(self, field: str) -> str:
        return dict(self.recorded).get(field, "")

    @property
    def divergent_fields(self) -> tuple[str, ...]:
        record = dict(self.recorded)
        return tuple(f for f, v in self.declared if record.get(f, v) != v)


@dataclass(frozen=True)
class StatusRegion:
    """A bounded region a document MARKS as its machine status, proven structural.

    Structural only: the values may or may not agree with the registry. A region
    that agrees is a projection of the record; one that disagrees is two machine
    surfaces contradicting each other, which is a finding and not a fallback.
    """

    name: str
    #: 0-based line indices of the two markers. The region is strictly between.
    begin_line: int
    end_line: int
    #: The registry field each column after the subject carries.
    fields: tuple[str, ...]
    rows: tuple[DeclaredRow, ...]

    @property
    def faithful(self) -> bool:
        """Whether every row re-derives from the registry exactly as it stands."""
        return all(row.faithful for row in self.rows)

    @property
    def divergent_rows(self) -> tuple[DeclaredRow, ...]:
        return tuple(row for row in self.rows if not row.faithful)

    def row(self, unit_id: str) -> "DeclaredRow | None":
        for row in self.rows:
            if row.unit_id.upper() == (unit_id or "").upper():
                return row
        return None

    def declares(self, unit_id: str) -> bool:
        return self.row(unit_id) is not None

    def covers_line(self, index: int) -> bool:
        return self.begin_line <= index <= self.end_line


def parse_status_region(
    lines: "Sequence[str]", name: str, begin: int, end: int, units: "Mapping[str, dict]"
) -> tuple["StatusRegion | None", str]:
    """One marked region, read as a declaration of machine status.

    Returns ``(region, why_not)``. Structural failures return no region and the
    reason, because a region that is not a table of registry fields keyed by
    registry units has not declared anything a machine can act on. Value
    DISAGREEMENT is not a structural failure: it is recorded on the row and
    returned, so the caller can treat it as the contradiction it is.
    """
    body = [(index, lines[index]) for index in range(begin + 1, end)]
    table = [(index, text) for index, text in body if text.strip()]
    if any(not text.strip().startswith("|") for _index, text in table):
        return None, f"the marked {name} region carries prose as well as a table"
    if len(table) < 2 + _MIN_PROJECTION_ROWS:
        return None, f"the marked {name} region carries too few rows to be a projection"

    header = _row_cells(table[0][1]) or []
    separator = _row_cells(table[1][1]) or []
    if len(header) < 2:
        return None, f"the marked {name} region's table declares no value columns"
    if len(separator) != len(header) or not all(
        _SEPARATOR_CELL.fullmatch(cell) for cell in separator
    ):
        return None, f"the marked {name} region's table has no separator row"

    fields = tuple(_heading_field(cell) for cell in header[1:])
    if not all(fields):
        return None, (
            f"the marked {name} region's columns do not all name a field of the machine record"
        )

    rows: list[DeclaredRow] = []
    seen: set[str] = set()
    for index, text in table[2:]:
        cells = _row_cells(text) or []
        if len(cells) != len(header):
            return None, f"the marked {name} region has a row of a different width"
        uid = cells[0]
        if not _BARE_ID.fullmatch(uid):
            return None, f"the marked {name} region has a row whose subject is not a unit id"
        unit = units.get(uid.upper())
        if unit is None:
            return None, f"the marked {name} region names {uid}, which the registry does not declare"
        if uid.upper() in seen:
            return None, f"the marked {name} region names {uid} twice"
        seen.add(uid.upper())
        missing = [f for f in fields if f not in unit]
        if missing:
            return None, (
                f"the marked {name} region projects {', '.join(missing)}, which {uid} does not record"
            )
        current = [str(unit[f]) for f in fields]
        rows.append(
            DeclaredRow(
                unit_id=uid,
                line=index,
                declared=tuple(zip(fields, (c for c in cells[1:]))),
                recorded=tuple(zip(fields, current)),
                faithful=_substitute_row(text, current) == text,
            )
        )

    return (
        StatusRegion(
            name=name, begin_line=begin, end_line=end, fields=fields, rows=tuple(rows)
        ),
        "",
    )


def declared_status_regions(
    text: str, units: "Mapping[str, dict]"
) -> tuple[tuple["StatusRegion", ...], str]:
    """Every region this document declares as machine status, and why any failed.

    A document that marks no region at all gets ``((), "")`` — a repository that
    has not said where its machine-derived status lives has not said it, and
    guessing is the defect this exists to avoid.
    """
    if not units:
        return (), ""
    lines = text.split("\n")
    regions: list[StatusRegion] = []
    reasons: list[str] = []
    for name, begin, end in _marker_pairs(lines):
        region, why = parse_status_region(lines, name, begin, end, units)
        if region is not None:
            regions.append(region)
        elif why:
            reasons.append(why)
    return tuple(regions), "; ".join(reasons[:3])


# --------------------------------------------------------------------------
# The authority a repository's surfaces add up to
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusDivergence:
    """Two machine surfaces recording different values for one unit."""

    surface: str
    region: str
    unit_id: str
    field: str
    declared: str
    recorded: str
    line: int


@dataclass(frozen=True)
class SurfaceStatus:
    """What one document declares about current machine status, if anything."""

    surface: str
    regions: tuple["StatusRegion", ...]
    #: Why a region this document marked was not read as machine status. Kept so
    #: a malformed declaration is reported rather than silently swallowed.
    why_not: str

    @property
    def declares_machine_status(self) -> bool:
        return bool(self.regions)

    @property
    def prose_is_authority(self) -> bool:
        """True when this document has named no machine-status region.

        Then its prose is the only current-status authority it offers, and the
        caller keeps its conservative reading of that prose exactly.
        """
        return not self.regions

    def row(self, unit_id: str) -> "DeclaredRow | None":
        for region in self.regions:
            row = region.row(unit_id)
            if row is not None:
                return row
        return None

    def declares(self, unit_id: str) -> bool:
        """Whether a region here states this unit's machine status.

        Per unit, not per document: a region governs the units it names, and
        one that does not name a unit has declared nothing about it.
        """
        return self.row(unit_id) is not None

    def status_of(self, unit_id: str, field: str = "status") -> str:
        row = self.row(unit_id)
        return row.value(field) if row is not None else ""

    def covers_line(self, index: int) -> bool:
        return any(region.covers_line(index) for region in self.regions)

    def divergences(self) -> list["StatusDivergence"]:
        out: list[StatusDivergence] = []
        for region in self.regions:
            for row in region.divergent_rows:
                for field in row.divergent_fields:
                    out.append(
                        StatusDivergence(
                            surface=self.surface,
                            region=region.name,
                            unit_id=row.unit_id,
                            field=field,
                            declared=row.value(field),
                            recorded=row.registry_value(field),
                            line=row.line + 1,
                        )
                    )
        return out


@dataclass(frozen=True)
class StatusAuthority:
    """The machine record, plus what each status surface declares about it."""

    registry_rel: str
    units: Mapping[str, dict]
    surfaces: tuple["SurfaceStatus", ...]

    def surface(self, name: str) -> "SurfaceStatus | None":
        for entry in self.surfaces:
            if entry.surface == name:
                return entry
        return None

    def recorded_status(self, unit_id: str, field: str = "status") -> str:
        unit = self.units.get((unit_id or "").upper())
        return str(unit.get(field, "") or "") if isinstance(unit, dict) else ""

    def divergences(self) -> list["StatusDivergence"]:
        out: list[StatusDivergence] = []
        for entry in self.surfaces:
            out += entry.divergences()
        return out


def resolve_status_authority(
    registry_rel: str,
    registry_text: str,
    surfaces: "Sequence[str]",
    read_text: Callable[[str], str],
) -> "StatusAuthority":
    """Read the machine record, then ask each surface what it declares about it."""
    units = units_by_id(registry_text or "")
    entries: list[SurfaceStatus] = []
    for rel in surfaces:
        text = read_text(rel) or ""
        regions, why = declared_status_regions(text, units) if text else ((), "")
        entries.append(SurfaceStatus(surface=rel, regions=regions, why_not=why))
    return StatusAuthority(registry_rel=registry_rel, units=units, surfaces=tuple(entries))


# --------------------------------------------------------------------------
# Reading prose, for the surfaces that offer nothing better
# --------------------------------------------------------------------------

#: Spans in which a document keeps the wording it replaced. Repositories that
#: preserve superseded claims IN PLACE — rather than deleting them — mark them,
#: and these are the two marks in general use: an italic parenthetical aside,
#: and a blockquote. Text inside one is a record of what WAS said. It is never
#: read as a live claim and never edited.
_ASIDE = re.compile(r"\*\([^)]*\)\*", re.S)
_QUOTED = re.compile(r"\"[^\"\n]{0,400}\"")

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
    r"this (?:cell|row|line|passage|paragraph|section|document|record) read)\b|\bREPLACED\b"
)

#: A past-tense lifecycle sentence: "P8 WAS BLOCKED", "P3 had been COMPLETE".
#: What a phase used to be is not what it is. Strictly past tense — the present
#: perfect ("P8 has been COMPLETE since Tuesday") asserts something that is
#: still true, and dropping it would lose a contradiction that is real.
_PAST_TENSE = re.compile(r"(?i)\b(?:was|were|had\s+been|used\s+to\s+be)\b")

#: A unit identifier as the driver's registries write them. Used for ATTRIBUTION
#: only: to find whether a nearer subject stands between a unit and a status
#: token, so the token is read as belonging to the subject it follows.
_SUBJECT = re.compile(
    r"(?<![A-Za-z0-9_])(?:P-?\d{1,3}(?:\.\d{1,3})?|U-?\d{1,3}\.\d{1,3}|U-[A-Z0-9][A-Z0-9-]*)"
    r"(?![A-Za-z0-9_])",
    re.I,
)

#: What separates one clause from the next. A status token does not reach
#: across it — "P8 HOLDS THE SELECTOR.** P7 is COMPLETE" is two statements, and
#: the second one is about P7.
_CLAUSE_BREAK = r"[^.!?;\n]"


def mask_history(text: str) -> str:
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


def mask_regions(text: str, surface: "SurfaceStatus | None") -> str:
    """The document with its declared machine-status regions blanked.

    A region a repository maintains mechanically is machine text. Reading it a
    second time as prose would let the one part of the document that IS status
    be judged by the rules written for the part that is not.
    """
    if surface is None or not surface.regions:
        return text
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if surface.covers_line(index):
            lines[index] = " " * len(line)
    return "\n".join(lines)


def _subject_pattern(known_ids: "Sequence[str]" = ()) -> "re.Pattern[str]":
    """Every token that can be the subject of a status statement."""
    extra = [
        rf"(?<![A-Za-z0-9_]){re.escape(str(i))}(?![A-Za-z0-9_])"
        for i in sorted({str(i) for i in known_ids if str(i).strip()}, key=len, reverse=True)
    ]
    if not extra:
        return _SUBJECT
    return re.compile("|".join([_SUBJECT.pattern] + extra), re.I)


def attributed_subject(
    unit_id: str, between: str, known_ids: "Sequence[str]" = ()
) -> str:
    """Which unit a status token this far after ``unit_id`` is actually about.

    A clause that names two units states its fact about the one the token
    follows. Carrying it back past a nearer subject is how

        "P8 HOLDS THE SELECTOR.** P7 is `status: COMPLETE`"

    became a claim that P8 was complete, and it is the same error whether the
    sentence is in a status document or in a session's own report of its work.
    The nearer subject does not merely disqualify the first — it OWNS the token,
    so a reader that drops the statement entirely has lost a true claim about
    the unit that really is complete.
    """
    found = list(_subject_pattern(known_ids).finditer(between or ""))
    return found[-1].group(0) if found else unit_id


def token_belongs_to(
    unit_id: str, between: str, known_ids: "Sequence[str]" = ()
) -> bool:
    """Whether a status token this far after ``unit_id`` is still about it."""
    if not unit_id:
        return False
    return attributed_subject(unit_id, between, known_ids).upper() == unit_id.upper()


@dataclass(frozen=True)
class ProseClaim:
    """A status assertion a document makes about one unit, in its own voice."""

    unit_id: str
    status: str
    #: Offsets into the ORIGINAL text, which masking keeps aligned.
    start: int
    end: int
    span: str

    def excerpt(self, text: str, width: int = 40) -> str:
        return text[max(0, self.start - width) : self.end + width].replace("\n", " ").strip()


def prose_status_claims(
    text: str,
    unit_id: str,
    status: str = "COMPLETE",
    *,
    surface: "SurfaceStatus | None" = None,
    known_ids: "Sequence[str]" = (),
) -> list["ProseClaim"]:
    """Where this document asserts, in its own voice and now, that ``unit_id`` is ``status``.

    Four things a whole-document token scan cannot tell apart, and this does:

    * **Machine status** — anything inside a declared region is read from the
      region, never from here, and is blanked before the scan.
    * **Preserved history** — a superseded claim kept in place as evidence, an
      aside recording what a passage used to read, a quoted citation, a
      past-tense sentence. Each records what WAS true.
    * **Narrative and orientation** — a status token in a clause whose subject
      is a different unit. "P7 is COMPLETE; P8 holds the selector" asserts one
      thing about P7 and nothing about P8's lifecycle.
    * **An assertion** — what is left.

    Offsets are into ``text``, so the caller's own checks still line up.
    """
    if not text or not unit_id:
        return []
    scannable = mask_history(mask_regions(text, surface))
    uid = re.escape(unit_id)
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_])(?:{uid}|implementation\s+phase\s+{re.escape(unit_id.lstrip('P'))})"
        rf"(?![A-Za-z0-9_])({_CLAUSE_BREAK}{{0,60}}?)(?:✅\s*)?"
        rf"(?<![A-Za-z0-9_-]){re.escape(status)}(?![A-Za-z0-9_-])",
        re.I,
    )

    claims: list[ProseClaim] = []
    for match in pattern.finditer(scannable):
        if not token_belongs_to(unit_id, match.group(1) or "", known_ids):
            continue
        line_start = scannable.rfind("\n", 0, match.start()) + 1
        line_end = scannable.find("\n", match.end())
        line = scannable[line_start : line_end if line_end != -1 else len(scannable)]
        if _HISTORICAL.search(line):
            continue
        sentence = _sentence_around(scannable, match.start(), match.end())
        if _PAST_TENSE.search(sentence):
            continue
        claims.append(
            ProseClaim(
                unit_id=unit_id,
                status=status,
                start=match.start(),
                end=match.end(),
                span=match.group(0),
            )
        )
    return claims


_SENTENCE_BREAK = re.compile(r"[.!?;]\s|\n")


def _sentence_around(text: str, start: int, end: int) -> str:
    """The clause the match sits in, bounded in both directions."""
    before = text[max(0, start - 200) : start]
    parts = _SENTENCE_BREAK.split(before)
    head = parts[-1] if parts else ""
    after = text[end : end + 200]
    match = _SENTENCE_BREAK.search(after)
    tail = after[: match.start()] if match else after
    return f"{head}{text[start:end]}{tail}"
