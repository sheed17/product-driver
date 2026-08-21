"""Repository protocol discovery.

The Neyma repository defines its own commit, finalization, status, review and
history rules — and it may change them. Nothing here encodes what those rules
currently say. Every rule is read out of the repository on every run, carrying
the file, the section and the sentence that states it.

The distinctions this module enforces:

    a rule the repository states  != a rule the driver assumes
    a document mentioning a rule  != that document having authority for it
    a rule stated once            != a rule stated consistently
    two sources disagreeing       => the driver has no authority to pick one
    protocol prose                != the implementation that enforces it

Both are read: prose states intent, the finalizer and the guards state what is
actually enforced. Where they disagree, that disagreement is itself reported.

Nothing here writes to the Neyma repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# Where protocol can live
# --------------------------------------------------------------------------
#
# Patterns, not paths: the repository is free to move its own documents. Every
# match is recorded in ProtocolSources so a report can name what was actually
# read, and what was looked for but not found.

SOURCE_PATTERNS: dict[str, tuple[str, ...]] = {
    "authority": ("CLAUDE.md", "AGENTS.md", "docs/**/AUTHORITY*.md"),
    "current": ("CURRENT.md", "docs/**/CURRENT.md"),
    "build_status": ("BUILD-STATUS.y*ml", "docs/**/BUILD-STATUS.y*ml"),
    "registry": ("docs/**/IMPLEMENTATION-REGISTRY.y*ml", "IMPLEMENTATION-REGISTRY.y*ml"),
    "progress_protocol": (
        "docs/**/*PROGRESS*.md",
        "docs/**/*PROTOCOL*.md",
        "docs/**/*progress*.md",
        "docs/**/*protocol*.md",
    ),
    "commit_protocol": (
        "docs/**/*COMMIT*.md",
        "docs/**/*FINALIZ*.md",
        "docs/**/*commit*.md",
        "docs/**/*finaliz*.md",
    ),
    "finalizer": ("**/finalize*status*.py", "**/*finalizer*.py"),
    "status_guard": (
        "**/*status_reality*.py",
        "**/*status-reality*.py",
        "**/test_status*.py",
    ),
    "canonical_suite": (
        "pytest-canonical.ini",
        "**/*canonical*.ini",
        "**/run_canonical_suite.py",
        "**/*canonical*suite*.py",
    ),
    "clean_clone": ("**/*clean_clone*.py", "**/*clean-clone*", "**/*clean_clone*.sh"),
    "receipts": (
        "docs/**/SUITE-RESULT.json",
        "docs/**/GATE-RESULT.json",
        "docs/**/*RECEIPT*.md",
        "docs/**/*receipt*.md",
    ),
}

# Directories never worth scanning, and a hard cap so a huge repository cannot
# turn discovery into a directory walk of everything.
_PRUNED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}
_MAX_PER_CATEGORY = 12
_MAX_FILE_CHARS = 300_000


class AuthorityLevel(str, Enum):
    """How much weight the repository gives a source.

    Derived from the repository's own declared authority ordering where it
    states one, and from the kind of source otherwise.
    """

    CANONICAL = "CANONICAL"  # the document that outranks the others
    PROTOCOL = "PROTOCOL"  # a dedicated protocol document
    IMPLEMENTATION = "IMPLEMENTATION"  # the code that actually enforces a rule
    DERIVED = "DERIVED"  # generated status; describes, does not legislate
    ADVISORY = "ADVISORY"
    UNKNOWN = "UNKNOWN"
    #: A record of a past state. It is evidence of what was done, and it has no
    #: standing to tell the present what to do. A document earns this only by
    #: saying so about itself, in its own heading.
    HISTORICAL = "HISTORICAL"

    @property
    def rank(self) -> int:
        return {
            AuthorityLevel.CANONICAL: 5,
            AuthorityLevel.PROTOCOL: 4,
            AuthorityLevel.IMPLEMENTATION: 3,
            AuthorityLevel.DERIVED: 2,
            AuthorityLevel.ADVISORY: 1,
            AuthorityLevel.UNKNOWN: 0,
            AuthorityLevel.HISTORICAL: -1,
        }[self]


_CATEGORY_AUTHORITY: dict[str, AuthorityLevel] = {
    "authority": AuthorityLevel.CANONICAL,
    "commit_protocol": AuthorityLevel.PROTOCOL,
    "progress_protocol": AuthorityLevel.PROTOCOL,
    "finalizer": AuthorityLevel.IMPLEMENTATION,
    "status_guard": AuthorityLevel.IMPLEMENTATION,
    "canonical_suite": AuthorityLevel.IMPLEMENTATION,
    "clean_clone": AuthorityLevel.IMPLEMENTATION,
    "registry": AuthorityLevel.DERIVED,
    "build_status": AuthorityLevel.DERIVED,
    "current": AuthorityLevel.DERIVED,
    "receipts": AuthorityLevel.DERIVED,
}


class RuleKind(str, Enum):
    """The semantic families the resolver can reason about.

    A repository is free to state a rule this driver has no family for; it is
    still recorded (as UNKNOWN) and still reported, it simply cannot participate
    in deadlock reasoning.
    """

    COMMIT_TOPOLOGY = "COMMIT_TOPOLOGY"
    FINALIZER_OWNERSHIP = "FINALIZER_OWNERSHIP"
    MANUAL_STATUS_PROHIBITED = "MANUAL_STATUS_PROHIBITED"
    MANUAL_FINALIZATION_ALLOWED = "MANUAL_FINALIZATION_ALLOWED"
    CANONICAL_SUITE_GATE = "CANONICAL_SUITE_GATE"
    STATUS_REALITY_GUARD = "STATUS_REALITY_GUARD"
    CLEAN_CLONE_GATE = "CLEAN_CLONE_GATE"
    RECEIPT_FRESHNESS = "RECEIPT_FRESHNESS"
    INDEPENDENT_REVIEW = "INDEPENDENT_REVIEW"
    HISTORY_REWRITE_APPROVAL = "HISTORY_REWRITE_APPROVAL"
    UNKNOWN = "UNKNOWN"


class ViolationType(str, Enum):
    CODE = "CODE"
    EVIDENCE = "EVIDENCE"
    ENVIRONMENT = "ENVIRONMENT"
    PROCESS = "PROCESS"
    HISTORY = "HISTORY"
    AUTHORITY = "AUTHORITY"
    UNKNOWN = "UNKNOWN"


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class ProtocolRule(BaseModel):
    """One rule, as the repository states it."""

    model_config = ConfigDict(extra="ignore")

    rule_id: str
    source_path: str
    source_lines_or_section: str = ""
    description: str = ""
    authority_level: AuthorityLevel = AuthorityLevel.UNKNOWN
    applies_to: str = ""
    allowed_states: list[str] = Field(default_factory=list)
    prohibited_states: list[str] = Field(default_factory=list)

    # Discovery metadata. `parameters` carries whatever the sentence quantified
    # (how many content commits, which paths, which gate precedes which), so the
    # detectors never re-parse prose.
    kind: RuleKind = RuleKind.UNKNOWN
    parameters: dict[str, Any] = Field(default_factory=dict)
    quote: str = ""

    #: True when the source declared *itself* a record of a past state. The rule
    #: is still recorded and still reported — history is worth seeing — but it
    #: does not bind the present.
    historical: bool = False
    #: Why the source is historical, in the source's own words.
    historical_reason: str = ""
    #: True when a higher-authority source has since retired this rule family.
    #: The statement stays visible; it stops being enforced.
    retired: bool = False
    #: Set on a *retirement statement* — the rule family it retires. A retirement
    #: statement is not itself an affirmative rule of that family.
    retires: str = ""

    @property
    def is_active(self) -> bool:
        """True when the repository states this rule *now*, and means it."""
        return not (self.historical or self.retired or self.retires)

    def cite(self) -> str:
        where = f" {self.source_lines_or_section}" if self.source_lines_or_section else ""
        return f"{self.source_path}{where}"


class ProtocolViolation(BaseModel):
    """A rule the repository states, and a state that breaks it."""

    model_config = ConfigDict(extra="ignore")

    rule_id: str
    observed_state: str
    expected_state: str
    evidence_paths: list[str] = Field(default_factory=list)
    severity: str = "blocker"
    violation_type: ViolationType = ViolationType.UNKNOWN

    # Free-text detail for the report; the four fields above stay machine-shaped.
    detail: str = ""
    rule_citation: str = ""

    def render(self) -> str:
        cite = f"  [rule: {self.rule_id} @ {self.rule_citation}]" if self.rule_citation else ""
        return (
            f"[{self.violation_type.value}/{self.severity}] {self.detail or self.rule_id}\n"
            f"    observed: {self.observed_state}\n"
            f"    expected: {self.expected_state}{cite}"
        )


class ProtocolConflict(BaseModel):
    """Two sources of protocol that cannot both be satisfied."""

    model_config = ConfigDict(extra="ignore")

    kind: RuleKind
    description: str
    rule_ids: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    resolved_by_authority: bool = False
    winning_rule_id: str = ""

    def render(self) -> str:
        resolution = (
            f"resolved by authority in favour of {self.winning_rule_id}"
            if self.resolved_by_authority
            else "NOT resolvable: the sources carry equal authority"
        )
        return f"{self.description}\n    sources: {', '.join(self.sources)}\n    {resolution}"


class ProtocolSources(BaseModel):
    """Exactly which files were read, per category."""

    model_config = ConfigDict(extra="ignore")

    by_category: dict[str, list[str]] = Field(default_factory=dict)
    missing_categories: list[str] = Field(default_factory=list)

    def all_paths(self) -> list[str]:
        seen: list[str] = []
        for paths in self.by_category.values():
            for p in paths:
                if p not in seen:
                    seen.append(p)
        return seen

    def first(self, category: str) -> str:
        paths = self.by_category.get(category) or []
        return paths[0] if paths else ""


@dataclass
class DiscoveredProtocol:
    """Everything the repository currently says about its own process."""

    repo: Path
    sources: ProtocolSources
    rules: list[ProtocolRule] = field(default_factory=list)
    conflicts: list[ProtocolConflict] = field(default_factory=list)
    authority_order: list[str] = field(default_factory=list)
    finalizer_owned_paths: list[str] = field(default_factory=list)
    status_paths: list[str] = field(default_factory=list)
    receipt_paths: list[str] = field(default_factory=list)

    #: Rule families a higher-authority source has retired, mapped to the
    #: statement that retired them. Read straight out of the repository: nothing
    #: here knows which families a particular repository has retired.
    retired_kinds: dict[str, ProtocolRule] = field(default_factory=dict)
    #: Sources that declared themselves records of a past state, with the reason.
    historical_sources: dict[str, str] = field(default_factory=dict)

    def of_kind(self, kind: RuleKind, *, include_inactive: bool = False) -> list[ProtocolRule]:
        """Rules of a family that the repository states *now*.

        A rule from a self-declared historical document, and a rule a
        higher-authority source has since retired, are both excluded: neither is
        something the repository is currently asking for. Pass
        ``include_inactive`` to see them anyway — a report should.
        """
        return [
            r
            for r in self.rules
            if r.kind is kind and (include_inactive or r.is_active)
        ]

    def is_retired(self, kind: RuleKind) -> bool:
        return kind.value in self.retired_kinds

    def retirement(self, kind: RuleKind) -> ProtocolRule | None:
        return self.retired_kinds.get(kind.value)

    def active_kinds(self) -> set[str]:
        """Every rule family the repository states now, by name."""
        return {r.kind.value for r in self.rules if r.is_active and r.kind is not RuleKind.UNKNOWN}

    def effective(self, kind: RuleKind) -> ProtocolRule | None:
        """The highest-authority rule of a kind, or None when unstated.

        Absence is meaningful: a rule the repository does not state is a rule the
        driver must not enforce.
        """
        candidates = self.of_kind(kind)
        if not candidates:
            return None
        return max(candidates, key=lambda r: (r.authority_level.rank, len(r.parameters)))

    def states(self, kind: RuleKind, parameter: str, value: Any = None) -> ProtocolRule | None:
        """The highest-authority rule of a kind that actually sets ``parameter``.

        One document may state a rule in general terms and another may quantify
        it. Asking the single 'effective' rule for a parameter would miss the
        quantified one; asking every rule of the kind does not.
        """
        candidates = [
            r
            for r in self.of_kind(kind)
            if parameter in r.parameters
            and (r.parameters[parameter] == value if value is not None else bool(r.parameters[parameter]))
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: r.authority_level.rank)

    def rule(self, rule_id: str) -> ProtocolRule | None:
        for r in self.rules:
            if r.rule_id == rule_id:
                return r
        return None

    @property
    def unresolved_conflicts(self) -> list[ProtocolConflict]:
        return [c for c in self.conflicts if not c.resolved_by_authority]


# --------------------------------------------------------------------------
# Statement matching
# --------------------------------------------------------------------------
#
# A statement becomes a rule only when it is *normative* (the repository is
# telling someone what must or must not happen) and it names a subject the
# resolver can reason about. Descriptive prose about commits is not a rule.

_NORMATIVE = re.compile(
    r"\b(MUST|SHALL|NEVER|ALWAYS|ONLY|EXACTLY|SOLE(?:LY)?|EXCLUSIVE(?:LY)?|FORBIDDEN|"
    r"PROHIBITED|REQUIRED|REQUIRES|MAY NOT|MAY ONLY|CANNOT|CAN NOT|IS NOT PERMITTED|"
    r"NOT ALLOWED|REFUSES?|OWNS|AUTHORIZED|UNAUTHORI[SZ]ED|DO NOT|DOES NOT|BEFORE|MAY)\b",
    re.I,
)

# Implementations are statements too: a repository that ships a clean-clone gate
# has a clean-clone gate, whether or not any document says so in a sentence the
# parser recognizes.
_STRUCTURAL_RULES: dict[str, tuple[RuleKind, str, str]] = {
    "finalizer": (
        RuleKind.FINALIZER_OWNERSHIP,
        "derived_status",
        "the repository implements a finalizer at {path}",
    ),
    "status_guard": (
        RuleKind.STATUS_REALITY_GUARD,
        "status_reality",
        "the repository implements a status-reality guard at {path}",
    ),
    "canonical_suite": (
        RuleKind.CANONICAL_SUITE_GATE,
        "canonical_suite",
        "the repository configures a canonical suite at {path}",
    ),
    "clean_clone": (
        RuleKind.CLEAN_CLONE_GATE,
        "clean_clone_gate",
        "the repository implements a clean-clone gate at {path}",
    ),
}


@dataclass(frozen=True)
class _Matcher:
    kind: RuleKind
    applies_to: str
    require: tuple[re.Pattern[str], ...]
    any_of: tuple[re.Pattern[str], ...] = ()

    def matches(self, text: str) -> bool:
        if not all(p.search(text) for p in self.require):
            return False
        return not self.any_of or any(p.search(text) for p in self.any_of)


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)


_MATCHERS: tuple[_Matcher, ...] = (
    _Matcher(
        RuleKind.COMMIT_TOPOLOGY,
        "commit_topology",
        (_rx(r"\bcommits?\b"),),
        (
            _rx(r"\bcontent\s+commits?\b"),
            _rx(r"\b(metadata|status)[-\s/]*commits?\b"),
            _rx(r"\btwo[-\s]commit\b"),
            _rx(r"\bcommit\s+(convention|topology|structure|sequence)\b"),
        ),
    ),
    _Matcher(
        RuleKind.FINALIZER_OWNERSHIP,
        "derived_status",
        (_rx(r"\bfinali[sz]"),),
        (
            _rx(r"\b(only|sole|solely|exclusive|exclusively|owns|ownership)\b"),
            _rx(r"\bderived\b"),
            _rx(r"\bmay\s+(?:not\s+)?write\b"),
        ),
    ),
    _Matcher(
        RuleKind.MANUAL_STATUS_PROHIBITED,
        "derived_status",
        (_rx(r"\b(manual(?:ly)?|hand[-\s]?(?:edit|written|writing|maintain)\w*)\b"),),
        (
            _rx(r"\bstatus\b"),
            _rx(r"\bderived\b"),
            _rx(r"\bBUILD-STATUS\b"),
            _rx(r"\bprogress\b"),
        ),
    ),
    _Matcher(
        RuleKind.MANUAL_FINALIZATION_ALLOWED,
        "finalization",
        (
            _rx(r"\bmanual\s+finali[sz]ation\b"),
            _rx(r"\b(authori[sz]ed|permitted|allowed|sanctioned)\b"),
        ),
    ),
    _Matcher(
        RuleKind.CANONICAL_SUITE_GATE,
        "canonical_suite",
        (_rx(r"\bcanonical\s+suite\b|\bfull\s+(?:test\s+)?suite\b"),),
        (
            _rx(r"\bbefore\b"),
            _rx(r"\bgreen\b"),
            _rx(r"\bmust\s+pass\b"),
            _rx(r"\brefus"),
        ),
    ),
    _Matcher(
        RuleKind.STATUS_REALITY_GUARD,
        "status_reality",
        (_rx(r"status[-_\s]realit"),),
    ),
    _Matcher(
        RuleKind.CLEAN_CLONE_GATE,
        "clean_clone_gate",
        (_rx(r"clean[-_\s]?clone"),),
    ),
    _Matcher(
        RuleKind.RECEIPT_FRESHNESS,
        "receipts",
        (_rx(r"\breceipts?\b"),),
        (_rx(r"\bHEAD\b"), _rx(r"\btree\b"), _rx(r"\bstale\b"), _rx(r"\bfresh\b"), _rx(r"\bmatch")),
    ),
    _Matcher(
        RuleKind.INDEPENDENT_REVIEW,
        "independent_review",
        (_rx(r"\bindependent\s+review\w*\b|\bfresh\s+session\b"),),
    ),
    _Matcher(
        RuleKind.HISTORY_REWRITE_APPROVAL,
        "git_history",
        (
            _rx(
                r"\b(rebase|force[-\s]?push|reset|amend|squash|cherry[-\s]?pick|"
                r"rewrit\w*|force[-\s]?update)\b"
            ),
        ),
        (
            _rx(r"\bapprov\w*\b"),
            _rx(r"\bhuman\b|\bfounder\b"),
            _rx(r"\bmust\s+not\b|\bnever\b|\bforbidden\b|\bprohibited\b"),
        ),
    ),
)

# How many content commits a statement permits. Parsed, never assumed: a
# repository that permits two must not be judged against one.
_COUNT_WORDS = {
    "exactly one": 1,
    "at most one": 1,
    "no more than one": 1,
    "a single": 1,
    "single": 1,
    "one": 1,
    "two": 2,
    "three": 3,
}
_CONTENT_COUNT_RE = re.compile(
    r"\b(exactly one|at most one|no more than one|a single|single|one|two|three|\d{1,2})\s+"
    r"(?:new\s+|additional\s+|further\s+)?content\s+commits?\b",
    re.I,
)
_METADATA_COMMIT_RE = re.compile(r"\b(metadata|status)[-\s/]*(?:and[-\s/]*status[-\s/]*)?commits?\b", re.I)
_TWO_COMMIT_RE = re.compile(r"\btwo[-\s]commit\b", re.I)
_FOLLOWED_BY_RE = re.compile(r"\bfollow(?:ed|s|ing)?\s+by\b|\bthen\b|\bplus\b|\band\s+then\b", re.I)

_DESTRUCTIVE_OPS = (
    "reset",
    "rebase",
    "squash",
    "amend",
    "cherry-pick",
    "cherry pick",
    "force-push",
    "force push",
    "force-update",
    "branch -d",
    "tag -d",
    "filter-branch",
    "rewrite",
)

_PATH_IN_CODE_RE = re.compile(r"[\"']((?:docs|src|eval|scripts|configs|data)/[\w./-]+\.\w{1,6})[\"']")

#: A finalizer names two very different kinds of file: the derived status it
#: WRITES, and the guard suites it RUNS. Only the first is finalizer-owned, and
#: conflating them is not harmless — a content commit that touches a guard test
#: gets reported as "content also carries derived status", a blocker that stops
#: a legitimate independent review on a file the finalizer never writes.
#:
#: When the finalizer declares its own set (`STATUS_METADATA_FILES = (...)`),
#: that declaration is authoritative and scraping is not needed. This matches
#: the rule used everywhere else here: the repository's own statement about
#: itself outranks an inference drawn from its source text.
_OWNED_DECL_RE = re.compile(
    r"^(?P<name>[A-Z][A-Z0-9_]*(?:STATUS|METADATA|OWNED|FINALIZER)[A-Z0-9_]*"
    r"(?:FILES|PATHS))\s*=\s*[\(\[](?P<body>.*?)[\)\]]",
    re.S | re.M,
)


# --------------------------------------------------------------------------
# Historical sources, and retired rule families
# --------------------------------------------------------------------------
#
# Two different ways a repository can stop asking for something, and both are
# read out of the repository rather than assumed:
#
#   1. a document says of ITSELF that it records a past state and does not
#      direct the present ("HISTORICAL — NOT CURRENT AUTHORITY");
#   2. a higher-authority document says the PROCESS is gone ("there is no
#      finalizer to run", "do not reintroduce committed suite receipts").
#
# Both require the repository to SAY it. A gate whose runner this driver cannot
# find on disk is not thereby retired: the glob list here is a heuristic, the
# repository may run its gate from CI or a Makefile, and inferring "I could not
# find it, so you must not want it" would be exactly the guess this module
# exists to avoid. Absence of evidence about a rule is not a repository saying
# the rule is gone.
#
# Neither can weaken anything. A retirement may only remove the demand for a
# *process artifact* — a receipt, a gate run, a commit shape. It may never
# remove a prohibition, a review requirement, or an approval requirement:
# those families are absent from RETIRABLE_KINDS, so no sentence anywhere can
# retire them. And a retirement loses to any affirmative statement of the same
# family carried by a source of equal or higher authority, so a repository that
# both retires and still requires something keeps requiring it.

#: The families a repository may retire: each one demands a ceremony artifact or
#: a process step, and a repository is entitled to stop performing it. The
#: families NOT here — INDEPENDENT_REVIEW, HISTORY_REWRITE_APPROVAL,
#: MANUAL_STATUS_PROHIBITED, MANUAL_FINALIZATION_ALLOWED, UNKNOWN — protect
#: something rather than produce something, and stay unretirable by
#: construction.
RETIRABLE_KINDS: frozenset[RuleKind] = frozenset(
    {
        RuleKind.COMMIT_TOPOLOGY,
        RuleKind.FINALIZER_OWNERSHIP,
        RuleKind.CANONICAL_SUITE_GATE,
        RuleKind.CLEAN_CLONE_GATE,
        RuleKind.RECEIPT_FRESHNESS,
        RuleKind.STATUS_REALITY_GUARD,
    }
)

_HISTORICAL_MARKER = re.compile(
    r"\b(HISTORICAL|SUPERSEDED|OBSOLETE|ARCHIVED|DEPRECATED|PRE-RESET|RETIRED)\b", re.I
)
_NOT_AUTHORITY_MARKER = re.compile(
    r"\bnot\s+(?:the\s+)?current\s+authority\b|\bnot\s+authoritative\b|"
    r"\bnot\s+an?\s+authority\b|\bno\s+longer\s+authorit\w*\b|"
    r"\bmust\s+not\s+direct\b|\bnot\s+roadmap\s+authority\b|\bnot\s+status\b",
    re.I,
)
_HEADING_RE = re.compile(r"^\s*>?\s*#{1,6}\s+\S")

#: How far into a document a self-declaration must appear to be a declaration
#: about the document. A banner is at the top; a sentence on line 400 is prose.
_HISTORICAL_HEADING_LINES = 40


def _self_declared_historical(text: str) -> str:
    """The heading in which a document declares itself a record of a past state.

    Only a *heading*, and only near the top: a document that discusses other
    documents' historicity in its body ("every phase review under docs/ is
    historical evidence") is making a statement about them, not about itself,
    and demoting it on that basis would silence the very file that said so.
    """
    for raw in text.splitlines()[:_HISTORICAL_HEADING_LINES]:
        if not _HEADING_RE.match(raw):
            continue
        line = raw.strip()
        if _HISTORICAL_MARKER.search(line) and _NOT_AUTHORITY_MARKER.search(line):
            return line.lstrip("> #").strip()[:200]
    return ""


#: A sentence that says a process is gone. "There is no finalizer to run."
_RETIREMENT_MARKER = re.compile(
    r"\bthere\s+(?:is|are)\s+no\b|\bno\s+longer\b|\b(?:was|were|is|are)\s+removed\b|"
    r"\bdo(?:es)?\s+not\s+reintroduce\b|\bmust\s+not\s+be\s+reintroduced\b|"
    r"\bnothing\s+about\b|\bit\s+is\s+stale\b|\b(?:has|have)\s+been\s+retired\b|"
    r"\b(?:is|are)\s+retired\b|\bno\s+(?:longer\s+)?required\b",
    re.I,
)

#: What family a clause is ABOUT, with no normativity required. Retirement
#: sentences are written in the negative ("there is no finalizer to run"), so the
#: affirmative matchers — which look for "only", "must", "owns" — never fire on
#: them. These identify the subject and nothing else.
_KIND_SUBJECTS: tuple[tuple[RuleKind, re.Pattern[str]], ...] = (
    (RuleKind.FINALIZER_OWNERSHIP, _rx(r"\bfinali[sz]\w*\b")),
    (
        RuleKind.CANONICAL_SUITE_GATE,
        _rx(r"\bcanonical\s+suite\b|\bsuite\s+receipts?\b|\bSUITE-RESULT\b"),
    ),
    (RuleKind.CLEAN_CLONE_GATE, _rx(r"clean[-_\s]?clone")),
    (
        RuleKind.COMMIT_TOPOLOGY,
        _rx(
            r"\btwo[-\s]commit\b|\bcommit\s+(?:convention|topology|structure|sequence)\b|"
            r"\bgit\s+topology\b|\bcontent\s*\+?\s*metadata\b|\bpreserve\s+refs?\b|"
            r"\bmetadata\s+convention\b|\btopology\b"
        ),
    ),
    (RuleKind.RECEIPT_FRESHNESS, _rx(r"\breceipts?\b")),
    (RuleKind.STATUS_REALITY_GUARD, _rx(r"status[-_\s]realit")),
)


def _retired_kinds_in(statement: str) -> list[RuleKind]:
    """Every retirable family a single sentence declares gone.

    One sentence commonly retires several at once — "there is no finalizer to
    run, no status receipt to hand-maintain, no two-commit convention" — so the
    sentence is split into clauses and each clause is read for its subject.
    """
    text = re.sub(r"[*_`]{1,3}", "", statement)
    marker = _RETIREMENT_MARKER.search(text)
    if not marker:
        return []

    # The marker governs the whole sentence, not one clause of it. A directive
    # heads its list ("Do not reintroduce A, B, or C"), a verdict trails it
    # ("...that a receipt must be committed — it is stale"), and an existence
    # denial distributes across it ("there is no A, no B, and no C"). Splitting
    # into clauses is only how each subject is read separately.
    clauses = [c for c in re.split(r",|;|\band\b|\bor\b|—|--", text) if c.strip()]
    found: list[RuleKind] = []
    for clause in clauses:
        for kind, subject in _KIND_SUBJECTS:
            if kind in RETIRABLE_KINDS and subject.search(clause) and kind not in found:
                found.append(kind)
    return found


def _apply_retirements(
    rules: list[ProtocolRule], candidates: list[ProtocolRule]
) -> dict[str, ProtocolRule]:
    """Retire a family only when nothing of equal authority still requires it.

    The test is deliberately one-sided. A retirement carried by the repository's
    canonical document silences a finalizer protocol stated only by a pass report
    it has outranked. It does *not* silence a rule the same canonical document
    states affirmatively somewhere else — a document that both drops a ceremony
    and keeps a requirement keeps the requirement.
    """
    retired: dict[str, ProtocolRule] = {}
    for candidate in candidates:
        kind_value = candidate.retires
        try:
            kind = RuleKind(kind_value)
        except ValueError:
            continue
        if kind not in RETIRABLE_KINDS:
            continue
        affirmative = [
            r
            for r in rules
            if r.kind is kind and not r.historical and not r.retires
        ]
        if any(r.authority_level.rank >= candidate.authority_level.rank for r in affirmative):
            continue
        existing = retired.get(kind_value)
        if existing is None or candidate.authority_level.rank > existing.authority_level.rank:
            retired[kind_value] = candidate

    for rule in rules:
        if rule.kind.value in retired and not rule.retires:
            rule.retired = True
    return retired


def _is_test_path(path: str) -> bool:
    """A test module, by the conventions pytest itself collects on."""
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith(("_test.py", "_test.js", "_test.ts"))


def _declared_owned_paths(text: str) -> list[str]:
    """Paths the finalizer declares it owns, or [] if it declares none."""
    out: list[str] = []
    for decl in _OWNED_DECL_RE.finditer(text):
        for match in _PATH_IN_CODE_RE.findall(decl.group("body")):
            if match not in out:
                out.append(match)
    return out
_STATUS_FILE_RE = re.compile(
    r"\b(BUILD-STATUS\.ya?ml|CURRENT\.md|IMPLEMENTATION-REGISTRY\.ya?ml)\b", re.I
)
_RECEIPT_FILE_RE = re.compile(r"\b([A-Z][A-Z0-9-]*-RESULT\.json|[\w-]*RECEIPT[\w-]*\.json)\b")


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def _iter_files(repo: Path, patterns: Iterable[str]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        try:
            matches = sorted(repo.glob(pattern))
        except (OSError, ValueError):
            continue
        for path in matches:
            if not path.is_file():
                continue
            if any(part in _PRUNED for part in path.parts):
                continue
            if path not in found:
                found.append(path)
            if len(found) >= _MAX_PER_CATEGORY:
                return found
    return found


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:_MAX_FILE_CHARS]
    except OSError:
        return ""


def _statements(text: str, markdown: bool = True) -> list[tuple[int, str, str]]:
    """Yield ``(line_no, heading, sentence)`` for every candidate statement.

    Wrapped prose is joined before it is split into sentences: a rule that runs
    across two lines of a markdown paragraph is one rule, not two fragments
    neither of which parses.
    """
    out: list[tuple[int, str, str]] = []
    heading = ""
    block: list[str] = []
    block_start = 0

    def flush() -> None:
        if not block:
            return
        joined = " ".join(block)
        for sentence in re.split(r"(?<=[.;:])\s+", joined):
            cleaned = sentence.strip().strip("`").strip()
            if len(cleaned) >= 12:
                out.append((block_start, heading, cleaned[:400]))

    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if markdown and re.match(r"^#{1,6}\s+\S", line):
            flush()
            block = []
            heading = line.lstrip("# ").strip()
            continue
        if not line:
            flush()
            block = []
            continue
        if not block:
            block_start = i
        # Strip decoration so patterns match the sentence, not the bullet or
        # the comment marker.
        block.append(re.sub(r"^[-*+>]\s+|^#+\s*|^\d+\.\s+", "", line).strip())

    flush()
    return out


def _authority_ordering(text: str) -> list[str]:
    """Read the repository's own declared ordering of authoritative documents."""
    order: list[str] = []
    heading_re = re.compile(r"authority|precedence|outrank|hierarchy", re.I)
    file_re = re.compile(r"\b([A-Za-z0-9._/-]+\.(?:md|ya?ml|json|ini|py))\b")
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if re.match(r"^#{1,6}\s+\S", line):
            in_section = bool(heading_re.search(line))
            continue
        if not in_section:
            continue
        for name in file_re.findall(line):
            base = name.split("/")[-1]
            if base not in order:
                order.append(base)
    return order


def _parameters_for(kind: RuleKind, text: str) -> dict[str, Any]:
    """Whatever the sentence quantified. Parsed from the sentence, never assumed."""
    params: dict[str, Any] = {}

    if kind is RuleKind.COMMIT_TOPOLOGY:
        match = _CONTENT_COUNT_RE.search(text)
        if match:
            token = match.group(1).lower()
            params["max_content_commits"] = _COUNT_WORDS.get(token) or int(
                token if token.isdigit() else 1
            )
        elif _TWO_COMMIT_RE.search(text):
            params["max_content_commits"] = 1
            params["requires_metadata_commit"] = True
        if _METADATA_COMMIT_RE.search(text):
            params["mentions_metadata_commit"] = True
            if _FOLLOWED_BY_RE.search(text) or _TWO_COMMIT_RE.search(text):
                params["requires_metadata_commit"] = True
        if re.search(r"\bfinali[sz]er[-\s]?(owned|generated|written)\b", text, re.I):
            params["metadata_commit_owner"] = "finalizer"

    elif kind is RuleKind.FINALIZER_OWNERSHIP:
        params["exclusive"] = bool(
            re.search(r"\b(only|sole|solely|exclusive|exclusively)\b", text, re.I)
        )
        params["owner"] = "finalizer"

    elif kind is RuleKind.CANONICAL_SUITE_GATE:
        if re.search(r"\bbefore\b[^.]*\bfinali[sz]", text, re.I) or re.search(
            r"\bfinali[sz]\w*[^.]*\b(?:refus|require|only)\w*[^.]*\b(?:suite|green)\b", text, re.I
        ):
            params["gates"] = "finalizer"
        params["requires_green"] = bool(
            re.search(r"\bgreen\b|\bmust\s+pass\b|\bpasses\b|\brefus", text, re.I)
        )

    elif kind is RuleKind.RECEIPT_FRESHNESS:
        params["must_match_head"] = bool(re.search(r"\bHEAD\b|\bfinal\s+tree\b", text, re.I))

    elif kind is RuleKind.INDEPENDENT_REVIEW:
        params["requires_fresh_session"] = bool(
            re.search(r"\bfresh\b|\bseparate\b|\bdifferent\s+session\b|\bnot\s+the\s+implement", text, re.I)
        )

    elif kind is RuleKind.HISTORY_REWRITE_APPROVAL:
        ops = [op for op in _DESTRUCTIVE_OPS if op in text.lower()]
        if ops:
            params["operations"] = ops
        params["requires_approval"] = bool(
            re.search(r"\bapprov\w*|\bhuman\b|\bfounder\b|\bconfirm\w*", text, re.I)
        )
        params["prohibited"] = bool(
            re.search(r"\bmust\s+not\b|\bnever\b|\bforbidden\b|\bprohibited\b|\bmay\s+not\b", text, re.I)
        )

    elif kind is RuleKind.MANUAL_STATUS_PROHIBITED:
        params["prohibited"] = bool(
            re.search(
                r"\bmust\s+not\b|\bnever\b|\bforbidden\b|\bprohibited\b|\bmay\s+not\b|"
                r"\bdo\s+not\b|\bis\s+not\s+permitted\b|\bnot\s+allowed\b",
                text,
                re.I,
            )
        )

    elif kind is RuleKind.MANUAL_FINALIZATION_ALLOWED:
        params["explicitly_authorized"] = bool(
            re.search(r"\b(explicitly\s+)?(authori[sz]ed|permitted|allowed|sanctioned)\b", text, re.I)
        )

    return params


def _states_for(kind: RuleKind, params: dict[str, Any], text: str) -> tuple[list[str], list[str]]:
    """Allowed and prohibited states, phrased from what the sentence said."""
    if kind is RuleKind.COMMIT_TOPOLOGY:
        n = params.get("max_content_commits")
        allowed: list[str] = []
        prohibited: list[str] = []
        if n is not None:
            owner = params.get("metadata_commit_owner", "")
            tail = (
                f" followed by one {owner + '-owned ' if owner else ''}metadata/status commit"
                if params.get("requires_metadata_commit")
                else ""
            )
            allowed.append(f"baseline → {n} content commit(s){tail}")
            prohibited.append(f"more than {n} content commit(s) since the authorized baseline")
        if params.get("requires_metadata_commit"):
            prohibited.append("a content commit with no metadata/status commit after it")
        return allowed, prohibited

    if kind is RuleKind.FINALIZER_OWNERSHIP:
        return (
            ["derived status written by the finalizer"],
            ["derived status written by any other actor"] if params.get("exclusive") else [],
        )

    if kind is RuleKind.MANUAL_STATUS_PROHIBITED:
        return ([], ["derived status edited by hand"] if params.get("prohibited") else [])

    if kind is RuleKind.MANUAL_FINALIZATION_ALLOWED:
        return (["manual finalization under the stated authorization"], [])

    if kind is RuleKind.CANONICAL_SUITE_GATE:
        return (["canonical suite green before finalization"], ["finalization while the suite is red"])

    if kind is RuleKind.STATUS_REALITY_GUARD:
        return (["status surfaces consistent with the observable repository state"], [])

    if kind is RuleKind.CLEAN_CLONE_GATE:
        return (["clean-clone gate executed and passing"], ["a clean-clone PASS with no gate run"])

    if kind is RuleKind.RECEIPT_FRESHNESS:
        return (
            ["a receipt naming the commit/tree it validates"],
            ["a receipt naming a commit/tree that is not the validated state"],
        )

    if kind is RuleKind.INDEPENDENT_REVIEW:
        return (
            ["review performed by a session other than the implementing one"],
            ["review performed by the implementing session"],
        )

    if kind is RuleKind.HISTORY_REWRITE_APPROVAL:
        ops = ", ".join(params.get("operations", []) or [])
        return (
            [f"{ops or 'history rewriting'} after explicit human approval"],
            [f"{ops or 'history rewriting'} without explicit human approval"],
        )

    return ([], [])


def _rule_from_statement(
    *,
    kind: RuleKind,
    applies_to: str,
    path: Path,
    repo: Path,
    line_no: int,
    heading: str,
    statement: str,
    authority: AuthorityLevel,
) -> ProtocolRule:
    rel = str(path.relative_to(repo)) if path.is_relative_to(repo) else str(path)
    params = _parameters_for(kind, statement)
    allowed, prohibited = _states_for(kind, params, statement)
    section = f"L{line_no}" + (f" (§ {heading})" if heading else "")
    return ProtocolRule(
        rule_id=f"{kind.value.lower().replace('_', '-')}:{rel}:L{line_no}",
        source_path=rel,
        source_lines_or_section=section,
        description=statement[:300],
        authority_level=authority,
        applies_to=applies_to,
        allowed_states=allowed,
        prohibited_states=prohibited,
        kind=kind,
        parameters=params,
        quote=statement[:300],
    )


def _authority_for(category: str, path: Path, repo: Path, order: list[str]) -> AuthorityLevel:
    """Repository-declared ordering wins; the source category is the fallback."""
    base = path.name
    if order:
        if base == order[0]:
            return AuthorityLevel.CANONICAL
        if base in order:
            return AuthorityLevel.PROTOCOL
    return _CATEGORY_AUTHORITY.get(category, AuthorityLevel.ADVISORY)


def discover_protocol(repo: Path) -> DiscoveredProtocol:
    """Read the repository's current protocol. Never raises; never writes."""
    repo = Path(repo)
    by_category: dict[str, list[str]] = {}
    missing: list[str] = []
    files: dict[str, list[Path]] = {}

    claimed: set[Path] = set()
    for category, patterns in SOURCE_PATTERNS.items():
        # A file belongs to one category. Overlapping patterns (a document named
        # COMMIT-PROTOCOL.md matches both the commit and the progress protocol
        # globs) would otherwise be read twice and produce duplicate rule ids.
        found = [p for p in _iter_files(repo, patterns) if p not in claimed]
        claimed.update(found)
        files[category] = found
        if found:
            by_category[category] = [
                str(p.relative_to(repo)) if p.is_relative_to(repo) else str(p) for p in found
            ]
        else:
            missing.append(category)

    sources = ProtocolSources(by_category=by_category, missing_categories=missing)

    # The repository's own authority ordering, if it declares one.
    order: list[str] = []
    for path in files.get("authority", []):
        order = _authority_ordering(_read(path))
        if order:
            break

    rules: list[ProtocolRule] = []
    retirement_candidates: list[ProtocolRule] = []
    historical_sources: dict[str, str] = {}
    finalizer_owned: list[str] = []
    status_paths: list[str] = []
    receipt_paths: list[str] = []

    for category, paths in files.items():
        for path in paths:
            text = _read(path)
            if not text:
                continue
            rel_path = str(path.relative_to(repo)) if path.is_relative_to(repo) else str(path)
            historical_reason = _self_declared_historical(text)
            if historical_reason:
                historical_sources[rel_path] = historical_reason
            authority = (
                AuthorityLevel.HISTORICAL
                if historical_reason
                else _authority_for(category, path, repo, order)
            )

            for line_no, heading, statement in _statements(
                text, markdown=path.suffix.lower() in (".md", ".markdown", "")
            ):
                # Read for retirements first. A sentence that says a process is
                # gone must not also be filed as that process's rule — "do not
                # reintroduce committed suite receipts" is normative, and taken
                # affirmatively it would be a canonical demand for the very
                # receipt it forbids, outranking its own retirement.
                retired_here: list[RuleKind] = []
                if not historical_reason:
                    retired_here = _retired_kinds_in(statement)
                for kind in retired_here:
                    retirement = _rule_from_statement(
                        kind=kind,
                        applies_to="retired_process",
                        path=path,
                        repo=repo,
                        line_no=line_no,
                        heading=heading,
                        statement=statement,
                        authority=authority,
                    )
                    retirement.rule_id = f"retired-{retirement.rule_id}"
                    retirement.retires = kind.value
                    retirement.allowed_states = []
                    retirement.prohibited_states = []
                    retirement_candidates.append(retirement)
                    rules.append(retirement)

                if not _NORMATIVE.search(statement):
                    continue
                for matcher in _MATCHERS:
                    if not matcher.matches(statement):
                        continue
                    if matcher.kind in retired_here:
                        continue
                    rule = _rule_from_statement(
                        kind=matcher.kind,
                        applies_to=matcher.applies_to,
                        path=path,
                        repo=repo,
                        line_no=line_no,
                        heading=heading,
                        statement=statement,
                        authority=authority,
                    )
                    rule.historical = bool(historical_reason)
                    rule.historical_reason = historical_reason
                    rules.append(rule)

            structural = _STRUCTURAL_RULES.get(category)
            if structural is not None:
                kind, applies_to, template = structural
                rel = str(path.relative_to(repo)) if path.is_relative_to(repo) else str(path)
                rules.append(
                    ProtocolRule(
                        rule_id=f"{kind.value.lower().replace('_', '-')}:{rel}:exists",
                        source_path=rel,
                        source_lines_or_section="(the implementation itself)",
                        description=template.format(path=rel),
                        authority_level=(
                            AuthorityLevel.HISTORICAL
                            if historical_reason
                            else AuthorityLevel.IMPLEMENTATION
                        ),
                        applies_to=applies_to,
                        kind=kind,
                        parameters={"implemented_at": rel},
                        quote=template.format(path=rel),
                        historical=bool(historical_reason),
                        historical_reason=historical_reason,
                    )
                )

            # Structural facts from the implementations, not from their prose:
            # which files the finalizer actually writes.
            if category == "finalizer":
                declared = _declared_owned_paths(text)
                if declared:
                    # The finalizer states which files it writes. Believe it.
                    for match in declared:
                        if match not in finalizer_owned:
                            finalizer_owned.append(match)
                else:
                    # No declaration: fall back to scraping, but never claim a
                    # test suite is derived status. A finalizer that WROTE its
                    # own guard tests would be the false-green defect those
                    # guards exist to catch, so a test path is always something
                    # it runs, never something it owns.
                    for match in _PATH_IN_CODE_RE.findall(text):
                        if _is_test_path(match):
                            continue
                        if match not in finalizer_owned:
                            finalizer_owned.append(match)
                for match in _STATUS_FILE_RE.findall(text):
                    if match not in status_paths:
                        status_paths.append(match)
            if category in ("receipts", "finalizer", "canonical_suite", "clean_clone"):
                for match in _RECEIPT_FILE_RE.findall(text):
                    if match not in receipt_paths:
                        receipt_paths.append(match)

    # Two sentences of one paragraph share the paragraph's line number, so ids
    # built from it can collide. A rule id has to identify exactly one rule:
    # a violation citing an ambiguous id names a sentence the reader cannot find.
    counts: dict[str, int] = {}
    for rule in rules:
        seen = counts.get(rule.rule_id, 0)
        counts[rule.rule_id] = seen + 1
        if seen:
            rule.rule_id = f"{rule.rule_id}#{seen + 1}"

    for rel in by_category.get("build_status", []) + by_category.get("current", []) + by_category.get(
        "registry", []
    ):
        name = rel.split("/")[-1]
        if name not in status_paths:
            status_paths.append(name)
    for rel in by_category.get("receipts", []):
        name = rel.split("/")[-1]
        if name.endswith(".json") and name not in receipt_paths:
            receipt_paths.append(name)

    discovered = DiscoveredProtocol(
        repo=repo,
        sources=sources,
        rules=rules,
        authority_order=order,
        finalizer_owned_paths=finalizer_owned,
        status_paths=status_paths,
        receipt_paths=receipt_paths,
        retired_kinds=_apply_retirements(rules, retirement_candidates),
        historical_sources=historical_sources,
    )
    discovered.conflicts = detect_conflicts(discovered)
    return discovered


# --------------------------------------------------------------------------
# Conflicting protocol
# --------------------------------------------------------------------------


def detect_conflicts(protocol: DiscoveredProtocol) -> list[ProtocolConflict]:
    """Find rules that cannot both be satisfied.

    A conflict is resolvable only when the *repository itself* declares an
    ordering that ranks every conflicting source, and one of them strictly
    outranks the rest. The driver's own guess about which document matters more
    is not authority: where the repository has not decided, the run stops.
    """
    conflicts: list[ProtocolConflict] = []
    order = protocol.authority_order

    # 1. Same rule kind, incompatible quantities.
    topology = [r for r in protocol.of_kind(RuleKind.COMMIT_TOPOLOGY) if "max_content_commits" in r.parameters]
    counts: dict[int, list[ProtocolRule]] = {}
    for rule in topology:
        counts.setdefault(int(rule.parameters["max_content_commits"]), []).append(rule)
    if len(counts) > 1:
        conflicts.append(
            _conflict_from(
                RuleKind.COMMIT_TOPOLOGY,
                [r for rs in counts.values() for r in rs],
                "the repository states two different limits on content commits per unit: "
                + "; ".join(f"{n} ({rs[0].cite()})" for n, rs in sorted(counts.items())),
                order,
            )
        )

    # 2. Manual finalization authorized while hand-edited status is prohibited.
    allowed = [r for r in protocol.of_kind(RuleKind.MANUAL_FINALIZATION_ALLOWED)
               if r.parameters.get("explicitly_authorized")]
    prohibited = [r for r in protocol.of_kind(RuleKind.MANUAL_STATUS_PROHIBITED)
                  if r.parameters.get("prohibited")]
    if allowed and prohibited:
        conflicts.append(
            _conflict_from(
                RuleKind.MANUAL_FINALIZATION_ALLOWED,
                allowed + prohibited,
                "the repository both authorizes manual finalization and prohibits hand-edited "
                "derived status",
                order,
            )
        )

    # 3. Exclusive finalizer ownership while manual finalization is authorized.
    exclusive = [r for r in protocol.of_kind(RuleKind.FINALIZER_OWNERSHIP) if r.parameters.get("exclusive")]
    if allowed and exclusive:
        conflicts.append(
            _conflict_from(
                RuleKind.FINALIZER_OWNERSHIP,
                exclusive + allowed,
                "derived status is declared exclusively finalizer-owned, and manual "
                "finalization is separately authorized",
                order,
            )
        )

    return conflicts


def _position_key(kind: RuleKind, rule: ProtocolRule) -> Any:
    """What side of the disagreement a rule is on."""
    if kind is RuleKind.COMMIT_TOPOLOGY:
        return rule.parameters.get("max_content_commits")
    return rule.kind.value


def _conflict_from(
    kind: RuleKind, rules: list[ProtocolRule], description: str, order: list[str]
) -> ProtocolConflict:
    """Resolvable only under an ordering the repository itself declared.

    Each *position* in the disagreement must be rankable — not every source that
    happens to state it. A position stated only by documents the repository
    never ranked has no place in the ordering, and the driver will not invent
    one for it.
    """
    resolved = False
    winner = ""

    def rank_of(rule: ProtocolRule) -> float:
        base = rule.source_path.split("/")[-1]
        return order.index(base) if base in order else float("inf")

    if order:
        positions: dict[Any, list[ProtocolRule]] = {}
        for rule in rules:
            key = _position_key(kind, rule)
            positions.setdefault(key, []).append(rule)

        if len(positions) > 1:
            best = {key: min(rank_of(r) for r in rs) for key, rs in positions.items()}
            if all(value != float("inf") for value in best.values()):
                ordered = sorted(best.items(), key=lambda kv: kv[1])
                if ordered[0][1] < ordered[1][1]:
                    resolved = True
                    winner = min(positions[ordered[0][0]], key=rank_of).rule_id

    return ProtocolConflict(
        kind=kind,
        description=description,
        rule_ids=[r.rule_id for r in rules],
        sources=sorted({r.cite() for r in rules}),
        resolved_by_authority=resolved,
        winning_rule_id=winner,
    )
