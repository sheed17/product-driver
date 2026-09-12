"""What a phase acceptance is made of: criteria, evidence, findings, residuals.

This module is the vocabulary. It holds no policy about *when* to look at a
repository and no ability to launch anything; it is the set of records the
phase-closure controller reasons over, plus the handful of deterministic rules
that must never be re-derived at a call site.

Five of those rules are load-bearing, and each exists because closing a phase by
hand went wrong in exactly that way.

**1. The criterion set is frozen by DEFINITION, not by outcome.**
:class:`CriterionSet` fingerprints the criteria's identity — id, name, whether
they are required, their weight, and the requirement they state. It deliberately
does NOT fingerprint ``result``. Scoring a criterion is the thing an adjudication
is *for*; if scoring changed the fingerprint, the frozen set would be broken by
its own success and every adjudication would be comparing against a set that no
longer existed. Adding a criterion, removing one, or changing what one demands
does change the fingerprint, and that is the change the freeze exists to catch.

**2. Blocking is not severity.**
:func:`blocks_acceptance` computes it. A finding blocks phase acceptance only
when it names a criterion in the frozen *required* set, mechanically
demonstrates that criterion is false, and belongs to a class that can falsify a
criterion at all. A ``blocker``-severity opinion about a nicer guard names no
criterion and demonstrates nothing, so it does not block, however loudly it is
worded. This is the anti-infinite-review rule.

**3. A reviewer may not add a criterion.**
Anything a reviewer scores that is not in the frozen set is recorded — it is
often a good idea — but as a finding outside the current authority, which can
never carry ``blocks_phase_acceptance``. Widening the bar mid-acceptance is an
authority change, and authority changes are the founder's.

**4. A finding may not contradict the adjudication that scored the criterion.**
An adjudication is one structured artifact: a per-criterion score and a set of
findings. When it scores a criterion PASS and also attaches a finding to that
criterion, the two must agree, and :func:`finding_disposition` is where that is
settled. A finding that says in its own words that it does not falsify the
criterion it names is debt, whatever its class or severity — it failed the
"actually demonstrates the criterion false" half of rule 2. A finding that DOES
demonstrate the criterion false against a PASS score is a contradiction, and the
one thing this module may not do with a contradiction is pick a side: it reports
``ADJUDICATION_INCONSISTENT``, which fails closed and sends the phase back to the
adjudicator rather than to the product builder.

**5. Evidence must be able to be wrong, and something must have looked.**
:class:`EvidenceRef` carries both. ``falsifiable`` separates a measurement from
a pointer: a citation that a document says something is a fact about the
document, so a criterion whose entire evidence is citations is not established
and :meth:`EvidenceMap.status_for` says ``VACUOUS`` rather than counting it.
``observed`` separates "we ran it and it failed" from "nobody has run it" —
which matters because a phase being closed for the FIRST time records every
criterion ``PENDING`` until the acceptance writes ``PASS``, and reading that as
a refutation made every such phase look broken. And where a mutation battery is
the oracle, :class:`AntiVacuity` records the population and what escaped,
because a battery that catches nothing because it mutates nothing is a green
light with no lamp behind it.

Nothing here reads a repository, writes a repository, or talks to a model.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# 1. Criteria
# --------------------------------------------------------------------------

#: How a repository writes "this criterion passed". Deliberately wider than
#: :data:`~neyma_product_driver.completion_auditor.PASSING_RESULTS`, which does
#: not accept ``SATISFIED``: the auditor scores weight from a completion
#: registry, while phase acceptance also reads criteria a reviewer marked
#: satisfied. Kept as its own constant so this module does not import the
#: auditor, and so neither set drifts into the other by accident.
PASSING_RESULTS = ("PASS", "PASSED", "COMPLETE", "SATISFIED")

#: How a repository writes "this criterion is known to be false". Distinct from
#: "not yet scored", which is neither pass nor fail.
FAILING_RESULTS = ("FAIL", "FAILED", "REFUSED", "NOT_MET", "NOT MET")

#: Words that mean a criterion cannot be settled by a machine, only judged. A
#: criterion whose requirement is written in these terms is reported as
#: non-instantiable by the preflight rather than silently scored.
_SUBJECTIVE = re.compile(
    r"(?i)\b(?:reasonabl\w+|appropriat\w+|adequat\w+|sufficient(?:ly)?|good\s+enough|"
    r"clean(?:er|ly)?|elegant|idiomatic|maintainab\w+|readab\w+|nice(?:r|ly)?|"
    r"as\s+needed|where\s+appropriate|best\s+effort|to\s+taste)\b"
)

#: Words that name a mechanism a machine can actually execute or query. Their
#: presence is what makes a criterion machine-addressable; their absence is not
#: proof of the opposite, so the preflight reports it as a question rather than
#: a refusal.
_MECHANICAL = re.compile(
    r"(?i)\b(?:test|tests|suite|probe|battery|mutation|mutant|guard|invariant|assert\w*|"
    r"migration|schema|query|scan|count|equal\w*|bijection|exact|byte|sha|digest|"
    r"exit\s*code|ci\b|workflow|job|green|red|pass(?:es|ed|ing)?|fail(?:s|ed|ing)?|"
    r"registry|manifest|report|receipt|coverage|transitions?|criteri\w+)\b"
)


class AcceptanceCriterion(BaseModel):
    """One criterion the repository states for a phase.

    Everything here is copied from the repository. Nothing is invented: a
    criterion Product Driver could not read is not a criterion Product Driver
    may supply.
    """

    model_config = ConfigDict(extra="ignore")

    criterion_id: str
    #: The repository's short name for it (``scope_conformance``, …).
    name: str = ""
    required: bool = True
    weight: float = 0.0
    #: The repository's own record of whether it has been scored, verbatim.
    result: str = "PENDING"
    #: What the repository says this criterion demands.
    requirement: str = ""
    #: Where the repository states it.
    authority: str = ""
    #: What the repository already recorded as evidence, verbatim. Corroboration
    #: for a reader; never on its own a reason to call a criterion established.
    recorded_evidence: str = ""

    @property
    def passed(self) -> bool:
        return str(self.result).strip().upper() in PASSING_RESULTS

    @property
    def failed(self) -> bool:
        return str(self.result).strip().upper() in FAILING_RESULTS

    @property
    def scored(self) -> bool:
        return self.passed or self.failed

    @property
    def machine_addressable(self) -> bool:
        """Whether a machine could in principle settle this criterion.

        Judged from the requirement text the repository wrote, not from whether
        this run happens to have evidence for it. A criterion that names a test,
        a battery, a guard, a count or an exact identity is addressable; one
        written entirely in terms of taste is not, and the preflight says so
        before a reviewer is paid to discover it.
        """
        text = f"{self.name} {self.requirement}".strip()
        if not text:
            return False
        if _MECHANICAL.search(text):
            return True
        return not _SUBJECTIVE.search(text)

    @property
    def ambiguity(self) -> str:
        """Why this criterion cannot be instantiated, when it cannot."""
        text = f"{self.name} {self.requirement}".strip()
        if not text:
            return "the repository states no requirement for this criterion"
        if self.machine_addressable:
            return ""
        match = _SUBJECTIVE.search(text)
        term = match.group(0) if match else "no mechanically checkable term"
        return (
            f"the requirement is stated in terms only a person can settle ({term!r}); "
            "no machine-addressable oracle can be derived from it"
        )

    def identity(self) -> tuple[str, str, bool, float, str]:
        """The part of this criterion the freeze is over. Excludes ``result``."""
        return (
            self.criterion_id,
            self.name,
            bool(self.required),
            float(self.weight),
            " ".join((self.requirement or "").split()),
        )

    def brief(self) -> str:
        req = "required" if self.required else "optional"
        return f"{self.criterion_id} ({self.name or 'unnamed'}, {req}): {self.result}"


class CriterionSet(BaseModel):
    """The phase's acceptance criteria, frozen for one acceptance attempt.

    The fingerprint is the whole point. An adjudication is evidence about one
    exact set of demands; if the set moves, the adjudication is evidence about
    something that is no longer being asked. Freezing it also removes the
    failure that made phase closure unbounded by hand: a reviewer noticing a
    useful extra guard and the phase reopening to build it.
    """

    model_config = ConfigDict(extra="ignore")

    phase_id: str = ""
    criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    #: Repository paths the set was read from.
    source_paths: list[str] = Field(default_factory=list)
    #: When the repository states no explicit set at all. The controller turns
    #: this into an AUTHORITY_GAP stop; it never manufactures criteria.
    resolution_problem: str = ""

    @property
    def declared(self) -> bool:
        return bool(self.criteria) and not self.resolution_problem

    @property
    def required(self) -> list[AcceptanceCriterion]:
        return [c for c in self.criteria if c.required]

    @property
    def ids(self) -> set[str]:
        return {c.criterion_id for c in self.criteria}

    @property
    def required_ids(self) -> set[str]:
        return {c.criterion_id for c in self.required}

    def get(self, criterion_id: str) -> AcceptanceCriterion | None:
        for c in self.criteria:
            if c.criterion_id == criterion_id:
                return c
        return None

    def fingerprint(self) -> str:
        """A digest over what the criteria DEMAND, never over how they scored.

        Deliberately order-insensitive: a registry that reorders its rows has
        not changed what it asks for, and treating that as a criteria change
        would invalidate an adjudication for a diff nobody made.
        """
        payload = sorted(c.identity() for c in self.criteria)
        blob = json.dumps([self.phase_id, payload], sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def non_instantiable(self) -> list[AcceptanceCriterion]:
        return [c for c in self.required if not c.machine_addressable]

    def summary_block(self) -> str:
        if not self.declared:
            return (
                "ACCEPTANCE CRITERIA: none the repository states explicitly\n"
                f"  reason: {self.resolution_problem or 'no criterion set found'}"
            )
        return "\n".join(
            [
                f"ACCEPTANCE CRITERIA: {len(self.criteria)} "
                f"({len(self.required)} required) for {self.phase_id or 'the phase'}",
                f"  fingerprint: {self.fingerprint()}",
                f"  sources: {', '.join(self.source_paths[:4]) or '(none recorded)'}",
            ]
        )


# --------------------------------------------------------------------------
# 2. Evidence
# --------------------------------------------------------------------------


class EvidenceKind(str, Enum):
    """What kind of thing is being offered as evidence for a criterion."""

    TEST_NODE = "TEST_NODE"
    PROBE = "PROBE"
    MUTATION_BATTERY = "MUTATION_BATTERY"
    STRUCTURAL_GUARD = "STRUCTURAL_GUARD"
    INVARIANT_QUERY = "INVARIANT_QUERY"
    SCENARIO = "SCENARIO"
    CI_JOB = "CI_JOB"
    REVIEW = "REVIEW"
    #: A pointer at a document that says something. Never falsifiable on its
    #: own: the document saying it is a fact about the document.
    AUTHORITY_CITATION = "AUTHORITY_CITATION"


#: Kinds that can, in principle, come out red. Everything else is a pointer.
#:
#: ``REVIEW`` is in here and ``AUTHORITY_CITATION`` is not, and the difference is
#: exactly the one this module is about. An independent adjudication returns
#: PASS or FAIL per criterion, from a session that could have said either, with
#: its independence checkable from the artifact — it is a measurement that came
#: out one way. A document saying a criterion passed is a fact about the
#: document, and no reading of it could have come out the other way.
_FALSIFIABLE_KINDS = frozenset(
    {
        EvidenceKind.TEST_NODE,
        EvidenceKind.PROBE,
        EvidenceKind.MUTATION_BATTERY,
        EvidenceKind.STRUCTURAL_GUARD,
        EvidenceKind.INVARIANT_QUERY,
        EvidenceKind.SCENARIO,
        EvidenceKind.CI_JOB,
        EvidenceKind.REVIEW,
    }
)


class AntiVacuity(BaseModel):
    """Whether an oracle could have failed, expressed in numbers.

    A mutation battery that catches 41 of 41 is worth something only if the 41
    were real mutants of a real population. ``population_denominator`` is what
    turns "everything we tried was caught" into a claim about coverage, and
    ``control_green`` is what separates "the guard held" from "the guard never
    ran".
    """

    model_config = ConfigDict(extra="ignore")

    #: Whether the unmutated control was observed green. ``None`` means it was
    #: not observed, which is not the same as observed false.
    control_green: bool | None = None
    mutant_expected_red: int = 0
    mutant_observed_red: int = 0
    escaped_mutants: int = 0
    population_denominator: int = 0

    @property
    def vacuous(self) -> bool:
        """True when the numbers cannot support a claim of coverage."""
        if self.control_green is False:
            return True
        if self.population_denominator <= 0:
            return True
        if self.mutant_expected_red <= 0:
            return True
        return self.mutant_observed_red < self.mutant_expected_red

    def brief(self) -> str:
        control = {True: "control green", False: "CONTROL NOT GREEN", None: "control unobserved"}[
            self.control_green
        ]
        return (
            f"{self.mutant_observed_red}/{self.mutant_expected_red} mutants caught, "
            f"{self.escaped_mutants} escaped, population {self.population_denominator}, {control}"
        )


class EvidenceRef(BaseModel):
    """One concrete thing offered in support of one criterion."""

    model_config = ConfigDict(extra="ignore")

    criterion_id: str
    kind: EvidenceKind = EvidenceKind.AUTHORITY_CITATION
    #: How to find it: a test node id, a script path, a CI run id, a run
    #: artifact path, a document path.
    locator: str = ""
    detail: str = ""
    #: Whether anything actually LOOKED. An artifact that exists in the tree
    #: and has not been run is a pointer at where evidence would come from, not
    #: evidence — and reading it as ``established=False`` made every criterion
    #: of a phase being closed for the first time read as refuted, because its
    #: registry records PENDING until the acceptance writes PASS.
    observed: bool = False
    #: Whether what was observed held. Meaningless unless ``observed``.
    established: bool = False
    #: The tree identity this was observed against — a
    #: :attr:`~neyma_product_driver.review_cycle.TreeFingerprint.identity`.
    observed_at_tree: str = ""
    #: Repository paths this evidence's validity depends on. Used for blast
    #: radius when the tree moves: evidence that declares its dependencies and
    #: whose dependencies did not change survives a later commit. Evidence that
    #: declares none does not, because nothing can vouch for it.
    depends_on_paths: list[str] = Field(default_factory=list)
    #: Set when a later tree retired this evidence, naming the tree that did.
    superseded_by: str = ""
    anti_vacuity: AntiVacuity | None = None

    @property
    def falsifiable(self) -> bool:
        """Whether this evidence could have come out the other way."""
        if self.kind not in _FALSIFIABLE_KINDS:
            return False
        if self.anti_vacuity is not None and self.anti_vacuity.vacuous:
            return False
        return True

    @property
    def stale(self) -> bool:
        return bool(self.superseded_by)

    @property
    def counts(self) -> bool:
        """Whether this ref can contribute to a criterion being established."""
        return self.observed and self.established and self.falsifiable and not self.stale

    def brief(self) -> str:
        if not self.observed:
            mark = "not observed by this attempt"
        else:
            mark = "established" if self.established else "NOT established"
        stale = "  [STALE]" if self.stale else ""
        vac = f"  [{self.anti_vacuity.brief()}]" if self.anti_vacuity is not None else ""
        return f"{self.kind.value} {self.locator or '(no locator)'} — {mark}{stale}{vac}"


class CriterionEvidenceStatus(str, Enum):
    """What the evidence map can say about one criterion."""

    #: At least one falsifiable, established, current piece of evidence.
    ESTABLISHED = "ESTABLISHED"
    #: Evidence exists but none of it could have failed — citations only, or a
    #: vacuous battery.
    VACUOUS = "VACUOUS"
    #: Falsifiable evidence exists and was observed NOT to hold.
    REFUTED = "REFUTED"
    #: The evidence a criterion names is in the tree and nothing has run it.
    #: Not a refutation and not an absence — the ordinary state of a phase
    #: being closed for the first time, and what the adjudication is for.
    UNOBSERVED = "UNOBSERVED"
    #: Evidence exists and belongs to an earlier tree.
    STALE = "STALE"
    #: Nothing at all points at this criterion.
    MISSING = "MISSING"


class EvidenceMap(BaseModel):
    """Every criterion's evidence, and what that evidence can support."""

    model_config = ConfigDict(extra="ignore")

    refs: list[EvidenceRef] = Field(default_factory=list)

    def add(self, ref: EvidenceRef) -> EvidenceRef:
        self.refs.append(ref)
        return ref

    def for_criterion(self, criterion_id: str) -> list[EvidenceRef]:
        return [r for r in self.refs if r.criterion_id == criterion_id]

    def status_for(self, criterion_id: str) -> CriterionEvidenceStatus:
        """What this criterion's evidence can support, in one word.

        The order encodes the burden of proof. Something that held outranks
        everything; a refutation outranks every kind of silence; and the three
        kinds of silence are kept apart because they have three different
        repairs — run it, cite something runnable, or attach anything at all.
        """
        refs = self.for_criterion(criterion_id)
        if not refs:
            return CriterionEvidenceStatus.MISSING
        live = [r for r in refs if not r.stale]
        if not live:
            return CriterionEvidenceStatus.STALE
        if any(r.counts for r in live):
            return CriterionEvidenceStatus.ESTABLISHED
        if any(r.observed and r.falsifiable and not r.established for r in live):
            return CriterionEvidenceStatus.REFUTED
        if any(r.observed and not r.falsifiable for r in live):
            return CriterionEvidenceStatus.VACUOUS
        return CriterionEvidenceStatus.UNOBSERVED

    def invalidate_for_tree(
        self, current_tree: str, changed_paths: Sequence[str] = ()
    ) -> list[EvidenceRef]:
        """Retire evidence that no longer describes ``current_tree``.

        Blast radius, not a blanket sweep: evidence that declares the paths it
        depends on and whose declared paths are untouched by the change survives,
        because nothing about it moved. Evidence that declares nothing is
        retired, because there is no basis on which to keep it. Evidence already
        observed against the current tree is left alone.
        """
        changed = list(changed_paths)
        retired: list[EvidenceRef] = []
        for ref in self.refs:
            if ref.stale or not ref.observed_at_tree:
                continue
            if ref.observed_at_tree == current_tree:
                continue
            if ref.depends_on_paths and not _touches(ref.depends_on_paths, changed):
                continue
            ref.superseded_by = current_tree
            retired.append(ref)
        return retired

    def summary_for(self, criteria: Iterable[AcceptanceCriterion]) -> dict[str, str]:
        return {c.criterion_id: self.status_for(c.criterion_id).value for c in criteria}


def normalize_repo_path(path: Any) -> str:
    """A repository-relative path in one form, without eating leading dots.

    ``str.lstrip("./")`` strips a SET of characters, not a prefix, so it turns
    ``.github/workflows/ci.yml`` into ``github/workflows/ci.yml`` — which made
    every CI path unclassifiable, and an unclassifiable path is one this system
    refuses to reason about. Stripping the prefix is what was meant everywhere
    it was written.
    """
    text = str(path or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _touches(declared: Sequence[str], changed: Sequence[str]) -> bool:
    """Whether any changed path falls under a declared dependency path."""
    for dep in declared:
        dep = normalize_repo_path(dep)
        if not dep:
            continue
        for path in changed:
            p = normalize_repo_path(path)
            if p == dep or p.startswith(dep.rstrip("/") + "/") or dep.startswith(p.rstrip("/") + "/"):
                return True
    return False


# --------------------------------------------------------------------------
# 3. Findings
# --------------------------------------------------------------------------


class FindingClass(str, Enum):
    """What kind of thing a finding is, and therefore who owns it.

    These are semantic distinctions, not severity bands. Collapsing any two of
    them is how a Product Driver measurement bug becomes a series of product
    changes that fix nothing, or how a nice-to-have guard reopens a phase.
    """

    #: The product does the wrong thing.
    PRODUCT_DEFECT = "PRODUCT_DEFECT"
    #: The product can do a *dangerous* wrong thing — an invariant, a safety
    #: boundary, a fail-open. Separated from PRODUCT_DEFECT because it is the
    #: one class that blocks regardless of how the phase is otherwise doing.
    RUNTIME_SAFETY_DEFECT = "RUNTIME_SAFETY_DEFECT"
    #: The repository does not state what would settle this. Nobody may invent
    #: it; the founder or architect decides.
    AUTHORITY_GAP = "AUTHORITY_GAP"
    #: The product is probably right and nothing proves it. Owned by tests and
    #: guards, not by a product change.
    VERIFICATION_GAP = "VERIFICATION_GAP"
    #: Product Driver itself measured wrongly. Never repaired by editing the
    #: product.
    HARNESS_DEFECT = "HARNESS_DEFECT"
    #: A probe or receipt that describes an older tree. Cannot false-green;
    #: blocks only where the acceptance authority says it does.
    STALE_VERIFICATION = "STALE_VERIFICATION"
    #: CI or another external verifier is broken, not the thing it verifies.
    CI_INFRASTRUCTURE_DEFECT = "CI_INFRASTRUCTURE_DEFECT"
    #: The evidence a criterion expects was never attached to this tree.
    EVIDENCE_GAP = "EVIDENCE_GAP"
    #: A record, status field or document is out of date. Costs nothing at
    #: runtime.
    BOOKKEEPING_DEBT = "BOOKKEEPING_DEBT"
    #: A real, recorded improvement that no current criterion demands.
    NONBLOCKING_DEBT = "NONBLOCKING_DEBT"
    #: Something outside this machine — a credential, a partner, a deploy.
    EXTERNAL_BLOCKER = "EXTERNAL_BLOCKER"
    #: Recorded rather than guessed at. An unclassified finding never blocks.
    UNCLASSIFIED = "UNCLASSIFIED"


class RepairLayer(str, Enum):
    """Where a finding is repaired. One layer per finding, never two."""

    #: The product builder, as a grounded correction.
    PRODUCT_BUILDER = "PRODUCT_BUILDER"
    #: A product verification task: tests and guards only, no runtime change.
    PRODUCT_VERIFICATION = "PRODUCT_VERIFICATION"
    #: Product Driver itself. The product run stops; the harness is repaired
    #: separately, outside the run that found it.
    PRODUCT_DRIVER = "PRODUCT_DRIVER"
    #: The CI configuration or its infrastructure.
    CI_INFRASTRUCTURE = "CI_INFRASTRUCTURE"
    #: The founder or architect. Authority, and nothing else, lives here.
    FOUNDER_DECISION = "FOUNDER_DECISION"
    #: The independent adjudicator. Reached only by a finding that contradicts
    #: the score its own adjudication gave the criterion: until the adjudicator
    #: says which of the two it meant, no other layer can be told it owns work.
    INDEPENDENT_ADJUDICATION = "INDEPENDENT_ADJUDICATION"
    #: Recorded and carried. No repair is owed now.
    RECORD_ONLY = "RECORD_ONLY"
    #: Someone outside this machine.
    EXTERNAL = "EXTERNAL"


#: The one place the mapping lives. A finding's class determines its repair
#: layer; nothing else does, and no caller may choose a different one.
_REPAIR_LAYER: dict[FindingClass, RepairLayer] = {
    FindingClass.PRODUCT_DEFECT: RepairLayer.PRODUCT_BUILDER,
    FindingClass.RUNTIME_SAFETY_DEFECT: RepairLayer.PRODUCT_BUILDER,
    FindingClass.AUTHORITY_GAP: RepairLayer.FOUNDER_DECISION,
    FindingClass.VERIFICATION_GAP: RepairLayer.PRODUCT_VERIFICATION,
    FindingClass.HARNESS_DEFECT: RepairLayer.PRODUCT_DRIVER,
    FindingClass.STALE_VERIFICATION: RepairLayer.PRODUCT_VERIFICATION,
    FindingClass.CI_INFRASTRUCTURE_DEFECT: RepairLayer.CI_INFRASTRUCTURE,
    FindingClass.EVIDENCE_GAP: RepairLayer.PRODUCT_VERIFICATION,
    FindingClass.BOOKKEEPING_DEBT: RepairLayer.RECORD_ONLY,
    FindingClass.NONBLOCKING_DEBT: RepairLayer.RECORD_ONLY,
    FindingClass.EXTERNAL_BLOCKER: RepairLayer.EXTERNAL,
    FindingClass.UNCLASSIFIED: RepairLayer.RECORD_ONLY,
}


def repair_layer_for(classification: FindingClass) -> RepairLayer:
    """Who repairs a finding of this class. The only routing table."""
    return _REPAIR_LAYER.get(classification, RepairLayer.RECORD_ONLY)


#: The only classes that can make a required criterion false. Everything else
#: describes something worth doing, and something worth doing is not a reason a
#: phase that meets its stated bar has not met it.
FALSIFYING_CLASSES = frozenset(
    {FindingClass.PRODUCT_DEFECT, FindingClass.RUNTIME_SAFETY_DEFECT}
)


class PhaseFinding(BaseModel):
    """One material observation during a phase acceptance attempt."""

    model_config = ConfigDict(extra="ignore")

    finding_id: str
    classification: FindingClass = FindingClass.UNCLASSIFIED
    #: The reporter's severity word, kept verbatim. Deliberately NOT an input to
    #: whether this blocks: see :func:`blocks_acceptance`.
    severity: str = "major"
    phase_id: str = ""
    summary: str = ""
    #: Criteria in the FROZEN set this finding claims are false.
    criterion_ids: list[str] = Field(default_factory=list)
    #: Criteria the reporter named that the frozen set does not contain. Kept,
    #: because a reviewer noticing a real gap is worth having; never blocking,
    #: because widening the bar mid-acceptance is an authority change.
    unrecognised_criterion_ids: list[str] = Field(default_factory=list)
    #: Criteria this finding NAMES and, in its own words, says it does not
    #: falsify — "distinct from the sites AC-12 scopes", "carried debt rather
    #: than an AC-12 violation". A reporter is allowed to attach context to a
    #: criterion without claiming the criterion is false, and reading the
    #: attachment as the claim is how a phase that met its bar was refused.
    not_falsified_criterion_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    #: Whether the finding SHOWS the criterion false — a failing command, a
    #: red probe, an exact-identity mismatch — rather than arguing it.
    mechanically_demonstrated: bool = False
    #: Computed by :func:`classify_and_route`; persisted so a resume restores
    #: the decision rather than re-deriving it from prose.
    blocks_phase_acceptance: bool = False
    #: Set when this finding and the adjudication that carried it say opposite
    #: things about the same criterion: the adjudication scored it PASS and this
    #: finding demonstrates it false. Never blank-and-blocking at once — a
    #: contradiction is not a criterion failure, it is a reason nobody yet knows
    #: whether the criterion holds, and it stops the attempt in its own state.
    adjudication_inconsistency: str = ""
    repair_layer: RepairLayer = RepairLayer.RECORD_ONLY
    closure_condition: str = ""
    #: ``None`` means genuinely unknown, which is different from ``False``.
    introduced_by_current_change: bool | None = None
    #: The tree identity this was observed against.
    observed_at_tree: str = ""
    #: The criteria fingerprint in force when this was recorded.
    criteria_fingerprint: str = ""
    #: Who or what reported it.
    source: str = ""

    @property
    def contradicts_adjudication(self) -> bool:
        return bool(self.adjudication_inconsistency)

    def disclaims(self, criterion_id: str) -> bool:
        """Whether this finding says in its own words it does not falsify one."""
        return criterion_id in self.not_falsified_criterion_ids

    def brief(self) -> str:
        if self.adjudication_inconsistency:
            mark = "ADJUDICATION INCONSISTENT"
        elif self.blocks_phase_acceptance:
            mark = "BLOCKING"
        else:
            mark = "nonblocking"
        crit = f" [{', '.join(self.criterion_ids)}]" if self.criterion_ids else ""
        return f"{self.finding_id} {self.classification.value}{crit} — {mark}: {self.summary}"


class AdjudicationScores(BaseModel):
    """What an independent adjudication scored, reduced to what blocking needs.

    Deliberately just two sets of criterion ids rather than the adjudication
    record itself. This module must be able to state the consistency rule
    without importing the controller that carries the adjudication, and a
    caller that has no adjudication passes nothing — which is not the same as
    passing an adjudication that scored nothing, and the difference is whether
    a contradiction is even possible.
    """

    model_config = ConfigDict(extra="ignore")

    passed: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)

    def scored_pass(self, criterion_id: str) -> bool:
        return criterion_id in self.passed and criterion_id not in self.failed

    def scored_fail(self, criterion_id: str) -> bool:
        return criterion_id in self.failed


class FindingDisposition(str, Enum):
    """What one finding does to a phase acceptance attempt.

    Three outcomes, not two. The third exists because an adjudication that
    scores a criterion PASS and attaches a finding demonstrating it false has
    said two incompatible things, and both of the two-valued answers are wrong:
    blocking believes the finding and silently discards the score, nonblocking
    believes the score and silently discards the finding. Neither is a decision
    anybody made.
    """

    #: It mechanically demonstrates a required criterion false. The phase stops.
    BLOCKING = "BLOCKING"
    #: Real, recorded, and not a reason this phase has not met its stated bar.
    NONBLOCKING = "NONBLOCKING"
    #: It contradicts the adjudication that carried it. Fails closed, and the
    #: contradiction is the adjudicator's to resolve.
    ADJUDICATION_INCONSISTENT = "ADJUDICATION_INCONSISTENT"


def finding_disposition(
    finding: PhaseFinding,
    criteria: CriterionSet,
    *,
    scores: AdjudicationScores | None = None,
    stale_verification_blocks: bool = False,
) -> tuple[FindingDisposition, str]:
    """What one finding does to the attempt, and why. The whole rule, once.

    The rule, stated once:

        a finding blocks phase acceptance only when it names a criterion that
        is in the frozen REQUIRED set, mechanically demonstrates that criterion
        is false, and is of a class that can falsify a criterion at all.

    Severity is not consulted. Neither is the fact of being attached to a
    criterion, nor the finding's class on its own: a ``blocker``-severity note
    that another guard would be useful names no criterion and demonstrates
    nothing; it is a good idea and it is debt. A ``minor``-severity finding that
    shows a required invariant failing under a probe blocks, because it did the
    one thing that makes a finding decisive.

    Two things sharpen "actually demonstrates that criterion false":

    * a finding that says, in its own words, that it does NOT falsify the
      criterion it names has not demonstrated it false however it is classified.
      That is rule 4, and it is the difference between a reviewer attaching
      useful context to a criterion and a reviewer refusing the criterion;
    * where ``scores`` carries the adjudication that scored the same criterion
      PASS, a finding that would otherwise block is a contradiction rather than
      a refutation, and is reported as such.

    ``stale_verification_blocks`` exists because whether a stale-but-harmless
    probe blocks is genuinely the acceptance authority's call, not this
    module's. Default false: a probe that cannot false-green is debt.
    """
    nonblocking = FindingDisposition.NONBLOCKING

    if finding.classification is FindingClass.AUTHORITY_GAP:
        # Not a criterion falsification — a statement that nobody knows what the
        # bar is. It stops the attempt through the controller's own state, which
        # is a different and louder thing than a blocking finding.
        return nonblocking, "an authority gap stops the attempt rather than failing a criterion"

    if finding.classification is FindingClass.STALE_VERIFICATION:
        if not stale_verification_blocks:
            return nonblocking, "stale verification that cannot false-green is verification debt"
        # Fall through to the ordinary test: even when the authority says stale
        # probes block, one that names no required criterion still blocks nothing.

    named = [cid for cid in finding.criterion_ids if cid in criteria.required_ids]
    if not named:
        if finding.criterion_ids:
            return nonblocking, (
                "the criteria it names are not in the frozen required set for this "
                "acceptance attempt"
            )
        return nonblocking, "it names no criterion in the current acceptance authority"

    # Rule 4, first half. A criterion the finding itself exempts is not a
    # criterion the finding claims is false — unless the adjudication scored
    # that criterion FAIL, in which case the phase fails on the score and the
    # exemption is the reporter disagreeing with its own adjudication, which
    # must not be able to rescue a criterion.
    def exempt(cid: str) -> bool:
        if not finding.disclaims(cid):
            return False
        return not (scores is not None and scores.scored_fail(cid))

    claimed = [cid for cid in named if not exempt(cid)]
    if not claimed:
        return nonblocking, (
            f"the finding states it does not falsify {', '.join(named)}; it is attached to "
            "the criterion as context and recorded as debt"
        )

    if not finding.mechanically_demonstrated:
        return nonblocking, (
            f"it asserts {', '.join(claimed)} is false without demonstrating it; "
            "an argument is not a refutation"
        )

    if finding.classification is FindingClass.STALE_VERIFICATION:
        reason = (
            f"this acceptance authority treats stale verification of {', '.join(claimed)} "
            "as blocking"
        )
        return _against_scores(claimed, scores, reason)

    if finding.classification not in FALSIFYING_CLASSES:
        return nonblocking, (
            f"a {finding.classification.value} cannot make {', '.join(claimed)} false; it "
            f"is repaired at {repair_layer_for(finding.classification).value}"
        )

    reason = f"it mechanically demonstrates required criterion {', '.join(claimed)} is false"
    return _against_scores(claimed, scores, reason)


def _against_scores(
    claimed: Sequence[str],
    scores: AdjudicationScores | None,
    reason: str,
) -> tuple[FindingDisposition, str]:
    """The last gate: a demonstrated failure against what the adjudicator scored.

    Only PASS is a contradiction. A criterion the adjudication scored FAIL, or
    could not determine, or never scored, is one the finding is free to refute —
    that is a finding doing its job.
    """
    if scores is None:
        return FindingDisposition.BLOCKING, reason
    contradicted = [cid for cid in claimed if scores.scored_pass(cid)]
    if not contradicted:
        return FindingDisposition.BLOCKING, reason
    return FindingDisposition.ADJUDICATION_INCONSISTENT, (
        f"the adjudication scored {', '.join(contradicted)} PASS and this finding "
        "demonstrates the same criterion false; the adjudication contradicts itself and "
        "no side of it may be chosen here"
    )


def blocks_acceptance(
    finding: PhaseFinding,
    criteria: CriterionSet,
    *,
    scores: AdjudicationScores | None = None,
    stale_verification_blocks: bool = False,
) -> tuple[bool, str]:
    """Whether one finding may stop a phase from being accepted, and why.

    The two-valued view of :func:`finding_disposition`, kept because most
    callers only ask "does this block". A contradiction answers ``False`` here
    and is NOT thereby harmless: it is carried on the finding as
    ``adjudication_inconsistency`` and stops the attempt in its own closure
    state. Anything that needs to tell the two apart calls
    :func:`finding_disposition`.
    """
    disposition, reason = finding_disposition(
        finding,
        criteria,
        scores=scores,
        stale_verification_blocks=stale_verification_blocks,
    )
    return disposition is FindingDisposition.BLOCKING, reason


def classify_and_route(
    finding: PhaseFinding,
    criteria: CriterionSet,
    *,
    scores: AdjudicationScores | None = None,
    stale_verification_blocks: bool = False,
) -> PhaseFinding:
    """Fill in the derived fields of a finding, in place, and return it.

    Split out from :func:`finding_disposition` so the *reason* can be surfaced
    without mutating anything, and so the mutation happens exactly once.
    """
    finding.repair_layer = repair_layer_for(finding.classification)
    disposition, reason = finding_disposition(
        finding,
        criteria,
        scores=scores,
        stale_verification_blocks=stale_verification_blocks,
    )
    finding.blocks_phase_acceptance = disposition is FindingDisposition.BLOCKING
    if disposition is FindingDisposition.ADJUDICATION_INCONSISTENT:
        finding.adjudication_inconsistency = reason
        # Until the contradiction is resolved nobody knows whether this is a
        # product defect or a mis-scored criterion, so it may not be routed to
        # the product builder as though the question were settled. The class's
        # own repair layer is restored the moment the contradiction is.
        finding.repair_layer = RepairLayer.INDEPENDENT_ADJUDICATION
    else:
        finding.adjudication_inconsistency = ""
    if not finding.closure_condition:
        finding.closure_condition = _default_closure_condition(finding, reason)
    # Anything the reporter named that the authority does not contain moves to
    # the unrecognised list, so a reviewer's extra criterion is recorded and
    # can never be read as part of the bar.
    recognised, unrecognised = [], list(finding.unrecognised_criterion_ids)
    for cid in finding.criterion_ids:
        (recognised if cid in criteria.ids else unrecognised).append(cid)
    finding.criterion_ids = recognised
    finding.unrecognised_criterion_ids = _dedupe(unrecognised)
    return finding


def _default_closure_condition(finding: PhaseFinding, reason: str) -> str:
    layer = finding.repair_layer
    if finding.adjudication_inconsistency:
        return (
            "the adjudicator states which it meant — the criterion it scored PASS, or the "
            f"finding it attached to {', '.join(finding.criterion_ids) or 'that criterion'} — "
            "and the phase is re-adjudicated on that answer"
        )
    if finding.blocks_phase_acceptance:
        return (
            f"the named criterion is re-established on the accepted tree "
            f"({', '.join(finding.criterion_ids) or 'the affected criterion'})"
        )
    if layer is RepairLayer.RECORD_ONLY:
        return "recorded as debt; no closure is owed for this phase acceptance"
    if layer is RepairLayer.FOUNDER_DECISION:
        return "a founder or architect states the missing authority"
    if layer is RepairLayer.PRODUCT_DRIVER:
        return "Product Driver is repaired outside this product run and the measurement re-taken"
    if layer is RepairLayer.CI_INFRASTRUCTURE:
        return "the external verifier runs to completion on the exact candidate tree"
    if layer is RepairLayer.PRODUCT_VERIFICATION:
        return "a test or guard is added that would fail if this were wrong"
    return reason or "closure condition not stated"


# --------------------------------------------------------------------------
# 4. Residuals
# --------------------------------------------------------------------------


class ResidualStatus(str, Enum):
    OPEN = "OPEN"
    #: Its closure condition is mechanically satisfied on the candidate tree.
    #: A proposal for the acceptance record — never a closure this module makes.
    CLOSABLE_NOW = "CLOSABLE_NOW"
    CLOSED = "CLOSED"
    #: Closing it needs a judgement. Never auto-closed, whatever the evidence.
    REQUIRES_FOUNDER_DECISION = "REQUIRES_FOUNDER_DECISION"


#: A closure condition written in these terms is somebody's judgement, not a
#: machine's observation. Matching one makes the residual permanently
#: founder-owned for automatic purposes.
_JUDGEMENT_CLOSURE = re.compile(
    r"(?i)\b(?:founder|architect|owner\s+decides?|product\s+decision|business\s+decision|"
    r"human\s+(?:decision|judgement|judgment|review)|sign\s*-?\s*off|approve[sd]?\b|"
    r"approval|accepts?\s+the\s+risk|policy\s+decision|decide[sd]?\b|at\s+our\s+discretion)\b"
)


class Residual(BaseModel):
    """One carried debt record, with what would close it."""

    model_config = ConfigDict(extra="ignore")

    residual_id: str
    finding: str = ""
    disposition: str = ""
    severity: str = ""
    classification: FindingClass = FindingClass.NONBLOCKING_DEBT
    status: ResidualStatus = ResidualStatus.OPEN
    blocks_phase_acceptance: bool = False
    closure_condition: str = ""
    #: What was observed about the closure condition, if anything.
    evidence: list[str] = Field(default_factory=list)
    owner_layer: RepairLayer = RepairLayer.RECORD_ONLY
    source: str = ""

    @property
    def needs_judgement(self) -> bool:
        return bool(_JUDGEMENT_CLOSURE.search(self.closure_condition or ""))

    def brief(self) -> str:
        block = "BLOCKING" if self.blocks_phase_acceptance else "nonblocking"
        return f"{self.residual_id} [{self.status.value}, {block}] {self.finding[:120]}"


class ResidualLedger(BaseModel):
    """Every residual the phase carries, and what this attempt can say about it."""

    model_config = ConfigDict(extra="ignore")

    residuals: list[Residual] = Field(default_factory=list)

    def __len__(self) -> int:
        return len(self.residuals)

    def __iter__(self):  # type: ignore[override]
        return iter(self.residuals)

    def add(self, residual: Residual) -> Residual:
        residual.owner_layer = repair_layer_for(residual.classification)
        if residual.needs_judgement:
            residual.status = ResidualStatus.REQUIRES_FOUNDER_DECISION
        self.residuals.append(residual)
        return residual

    @property
    def blocking(self) -> list[Residual]:
        return [
            r
            for r in self.residuals
            if r.blocks_phase_acceptance and r.status is not ResidualStatus.CLOSED
        ]

    @property
    def nonblocking(self) -> list[Residual]:
        return [
            r
            for r in self.residuals
            if not r.blocks_phase_acceptance and r.status is not ResidualStatus.CLOSED
        ]

    @property
    def closable_now(self) -> list[Residual]:
        return [r for r in self.residuals if r.status is ResidualStatus.CLOSABLE_NOW]

    def mark_closable(self, residual_id: str, evidence: str) -> bool:
        """Propose one residual as mechanically closed. Never closes it.

        Refuses outright when the closure condition is written in terms of a
        judgement: no amount of observation closes "the founder accepts the
        risk", and a controller that marked it closable would be manufacturing
        the founder's decision.
        """
        for residual in self.residuals:
            if residual.residual_id != residual_id:
                continue
            if residual.needs_judgement:
                residual.status = ResidualStatus.REQUIRES_FOUNDER_DECISION
                return False
            residual.status = ResidualStatus.CLOSABLE_NOW
            if evidence and evidence not in residual.evidence:
                residual.evidence.append(evidence)
            return True
        return False


# --------------------------------------------------------------------------
# 5. Closure state and the ledger
# --------------------------------------------------------------------------


class ClosureState(str, Enum):
    """Where a phase acceptance attempt has got to."""

    NOT_STARTED = "NOT_STARTED"
    #: The repository does not state what would make this phase done. STOP.
    AUTHORITY_GAP = "AUTHORITY_GAP"
    #: The deterministic preflight found something a reviewer should not be paid
    #: to discover.
    PREFLIGHT_BLOCKED = "PREFLIGHT_BLOCKED"
    #: Everything a machine can settle is settled; an adjudication is worth
    #: paying for.
    READY_FOR_ADJUDICATION = "READY_FOR_ADJUDICATION"
    #: External verification is owed on the exact candidate tree.
    WAITING_FOR_EXTERNAL_VERIFICATION = "WAITING_FOR_EXTERNAL_VERIFICATION"
    #: A required criterion is mechanically false, or a blocking residual stands.
    BLOCKED = "BLOCKED"
    #: The adjudication says two incompatible things about the same criterion.
    #: Neither accepted nor refused: the phase waits on the adjudicator, and no
    #: product repair may be launched off a self-contradictory adjudication.
    ADJUDICATION_INCONSISTENT = "ADJUDICATION_INCONSISTENT"
    #: All required criteria pass and the acceptance record may be prepared.
    READY_FOR_ACCEPTANCE_COMMIT = "READY_FOR_ACCEPTANCE_COMMIT"
    #: The local acceptance commit exists. The remaining step is the founder's.
    READY_FOR_FOUNDER_PUSH = "READY_FOR_FOUNDER_PUSH"
    #: The repository already records this phase as accepted.
    ALREADY_ACCEPTED = "ALREADY_ACCEPTED"


#: States in which the attempt has stopped and is waiting for somebody.
TERMINAL_STATES = frozenset(
    {
        ClosureState.AUTHORITY_GAP,
        ClosureState.PREFLIGHT_BLOCKED,
        ClosureState.WAITING_FOR_EXTERNAL_VERIFICATION,
        ClosureState.BLOCKED,
        ClosureState.ADJUDICATION_INCONSISTENT,
        ClosureState.READY_FOR_ACCEPTANCE_COMMIT,
        ClosureState.READY_FOR_FOUNDER_PUSH,
        ClosureState.ALREADY_ACCEPTED,
    }
)


class CheckpointRecord(BaseModel):
    """One landed checkpoint inside the phase, as the repository records it."""

    model_config = ConfigDict(extra="ignore")

    checkpoint_id: str
    name: str = ""
    state: str = ""
    candidate_commit: str = ""
    candidate_tree: str = ""
    independent_review_report: str = ""


class PhaseLedger(BaseModel):
    """The compact record of one phase acceptance attempt.

    Machine-readable and, through :meth:`render`, human-readable. Persisted as
    part of the controller's state rather than as a second store, so there is
    exactly one place a resume reads.
    """

    model_config = ConfigDict(extra="ignore")

    phase: str = ""
    state: ClosureState = ClosureState.NOT_STARTED
    candidate_tree: str = ""
    candidate_head: str = ""
    tree_clean: bool = False

    checkpoints_expected: int = 0
    checkpoints_landed: int = 0

    criteria_fingerprint: str = ""
    criteria_required: int = 0
    criteria_pass: int = 0
    criteria_fail: int = 0
    criteria_unevidenced: list[str] = Field(default_factory=list)
    criteria_non_instantiable: list[str] = Field(default_factory=list)
    #: Required criteria the adjudication scored PASS while also attaching a
    #: finding that demonstrates them false. Counted as neither pass nor fail,
    #: because both counts would be a claim nobody is entitled to make yet.
    criteria_contradicted: list[str] = Field(default_factory=list)

    blocking_residuals: int = 0
    nonblocking_residuals: int = 0
    closable_residuals: list[str] = Field(default_factory=list)

    external_required: bool = False
    external_status: str = "NOT_REQUIRED"
    external_sha: str = ""

    independent_review_status: str = "NOT_TAKEN"
    reviewer_session_id: str = ""
    builder_session_ids: list[str] = Field(default_factory=list)
    inherited_builder_context: bool = False

    blocking_findings: list[str] = Field(default_factory=list)
    nonblocking_findings: list[str] = Field(default_factory=list)
    #: Findings that contradict the adjudication that carried them. Kept out of
    #: both lists above: they are not debt to record and move past, and they are
    #: not demonstrated criterion failures either.
    inconsistent_findings: list[str] = Field(default_factory=list)
    #: Authority gaps, kept out of both lists above. A gap does not block by
    #: falsifying a criterion — it stops the attempt outright — and printing it
    #: under "record and move" told the founder to carry on past the one thing
    #: only they can settle.
    authority_gaps: list[str] = Field(default_factory=list)

    production_enabled: bool = False
    ready_for_acceptance_commit: bool = False
    acceptance_commit: str = ""
    next_phase: str = ""
    notes: list[str] = Field(default_factory=list)

    def render(self) -> str:
        """The human-readable form. Same facts, same order, one per line."""
        lines = [
            f"phase: {self.phase or '(none)'}",
            f"state: {self.state.value}",
            f"candidate_tree: {self.candidate_tree or '(none)'}",
            f"tree_clean: {'yes' if self.tree_clean else 'no'}",
            "checkpoints:",
            f"  expected: {self.checkpoints_expected}",
            f"  landed: {self.checkpoints_landed}",
            "acceptance:",
            f"  fingerprint: {self.criteria_fingerprint or '(none)'}",
            f"  required: {self.criteria_required}",
            f"  pass: {self.criteria_pass}",
            f"  fail: {self.criteria_fail}",
        ]
        if self.criteria_unevidenced:
            lines.append(f"  without evidence: {', '.join(self.criteria_unevidenced[:8])}")
        if self.criteria_non_instantiable:
            lines.append(f"  non-instantiable: {', '.join(self.criteria_non_instantiable[:8])}")
        if self.criteria_contradicted:
            lines.append(
                "  contradicted (scored PASS and refuted by the same adjudication): "
                + ", ".join(self.criteria_contradicted[:8])
            )
        lines += [
            f"blocking_residuals: {self.blocking_residuals}",
            f"nonblocking_residuals: {self.nonblocking_residuals}",
        ]
        if self.closable_residuals:
            lines.append(f"closable_now: {', '.join(self.closable_residuals[:8])}")
        lines += [
            "ci:",
            f"  required: {'true' if self.external_required else 'false'}",
            f"  status: {self.external_status}",
            f"  sha: {self.external_sha or '(none)'}",
            "independent_review:",
            f"  status: {self.independent_review_status}",
            f"  reviewer: {self.reviewer_session_id or '(none)'}",
            f"  inherited_builder_context: {'true' if self.inherited_builder_context else 'false'}",
        ]
        if self.authority_gaps:
            lines.append("authority_gaps (only a founder or architect can close these):")
            lines += [f"  - {f}" for f in self.authority_gaps[:10]]
        if self.inconsistent_findings:
            lines.append(
                "adjudication_inconsistencies (the adjudicator resolves these; no product "
                "repair may be launched from them):"
            )
            lines += [f"  - {f}" for f in self.inconsistent_findings[:10]]
        if self.blocking_findings:
            lines.append("blocking_findings:")
            lines += [f"  - {f}" for f in self.blocking_findings[:10]]
        if self.nonblocking_findings:
            lines.append("nonblocking_findings (record and move):")
            lines += [f"  - {f}" for f in self.nonblocking_findings[:10]]
        lines += [
            f"production_enabled: {'true' if self.production_enabled else 'false'}",
            f"ready_for_acceptance_commit: {'true' if self.ready_for_acceptance_commit else 'false'}",
        ]
        if self.acceptance_commit:
            lines.append(f"acceptance_commit: {self.acceptance_commit}")
        if self.next_phase:
            lines.append(f"next_phase: {self.next_phase}")
        lines += [f"note: {n}" for n in self.notes[:8]]
        return "\n".join(lines)


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out
