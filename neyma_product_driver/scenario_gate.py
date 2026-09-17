"""The authoritative scenario gate: may this run's evidence support an ACCEPT?

One deterministic function decides this, from the recorded outcomes and nothing
else. It exists because the previous arrangement had several partial answers —
``blocking_failures()``, ``everything_required_passed``, ``full_run`` — and a
situation none of them covered: a required scenario that never ran at all. A run
that executed nothing reported zero blocking failures and a full run, and
accepted.

The rule here is deliberately narrow and states the burden of proof:

    a required scenario contributes to acceptance only when it PASSED and its
    evidence resolves.

Everything else — failed, blocked, skipped, never executed, no result recorded,
result present but evidence missing or belonging to something else — is *not
verified*. Not-verified is not a failure claim; it is the absence of the proof
acceptance was supposed to rest on, and it blocks just the same.

The same burden applies to the run's *risks*. A risk the run identified is
verified only by an outcome that passed and can show its evidence, and only
through one of two explicit attachments:

    1. the scenario's own reviewed ``verifies:`` declaration, which names the
       risk category, the checks that must pass and the literal text the
       product must emit — resolved by the executor against what actually ran;
    2. a generated scenario carrying that risk category, which passed.

Neither is a similarity judgement. There is no fuzzy matching, no neighbouring
category, no "the tests passed so it must be covered", and no channel by which
an evaluator or a generator can assert a risk into the covered list. What
changed, and why this module was wrong before, is narrower than it sounds: the
gate could previously see coverage only through attachment (2), so a permanent
scenario that migrated a legacy database, read the schema back and passed
counted for nothing against a ``persistence_failure`` risk — the risk stayed
"uncovered" because no *generated* scenario wore that label. The only remaining
move was to ask a builder for coverage that already existed, which does not
converge. Attachment (1) is how already-existing evidence gets to speak, and it
speaks only in the words a human wrote down in a reviewed file.

A third attachment now exists, and it is the same kind of thing as the first:
an explicit measurement, checked against what executed.

    3. **a repository-native guard this run CHANGED and RAN.** When the thing a
       task delivered IS a guard, the driver executes it (see
       :mod:`~neyma_product_driver.changed_verification`), and the executed guard
       is frequently the direct oracle for a risk the run named. A ledger that
       recognised only scenarios reported "no scenario exercising this risk was
       executed" about a risk whose oracle was sitting in the run's own evidence
       directory with a passing exit status, and asked for a generated scenario
       on top of it — which is asking a founder to relay a measurement the driver
       is holding.

That attachment is as narrow as the other two and is decided in
:mod:`~neyma_product_driver.guard_coverage`, never here: the guard must be the
deliverable rather than collateral, must have been run by this driver rather
than reported by a builder, must have passed with its own anti-vacuity case
where it asserts an absence, and must be observed to read every concrete
artifact the risk names. Anything less leaves the risk open with the unmeasured
part named, and the generated scenario is still owed.

Two further attachments exist for STRUCTURAL risks, and they only exist once a
risk has been grounded (:mod:`~neyma_product_driver.risk_grounding`): its
subjects are what it names plus the changed product modules it refers to, and
its hypothesis is one the tree can decide — today, "this capability is wired
into the live path". Run 20260917-063502 named that hypothesis twice, in words
that named no file, and reported both as uncovered beside the green,
positive-controlled repository guard that measured exactly it.

    4. **a changed repository guard with one structural test** that names every
       grounded subject, measures the hypothesised property, and asserts its own
       positive control; executed and passing on the judged tree;
    5. **the driver's own structural scan** of the judged tree, which says DARK
       only when no product import chain from an entry point reaches the
       module and its resolver's positive control fired. A reachable module is
       never read as a refutation: an import is not a live binding.

Two wordings of one grounded structural risk are ONE obligation — the second
joins the first entry as a duplicate rather than blocking a second time — and
a risk that cannot be grounded keeps blocking, saying so.

And where two kinds of evidence about one risk DISAGREE — a guard that measures
it passing while a scenario carrying it failed, or the reverse — this fails
closed. A contradiction is not a coverage question and is never resolved by
preferring the more convenient half of it.

Nothing in this module reads a mutable convenience flag, so forcing one to True
cannot make an unverified run look verified. The evaluator does not participate:
it judges what it saw, and this measures what actually ran.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .guard_coverage import GuardVerdict, guard_evidence, guard_measurements
from .risk_grounding import (
    Grounding,
    ground,
    guard_structural_evidence,
    reachability_evidence,
)
from .scenario_plan import IdentifiedRisk
from .scenario_suite import Origin, Outcome, ScenarioOutcome, SuiteResult


#: When the evidence behind a covered risk was taken. See CoveredRisk.freshness.
FRESH = "fresh"
INHERITED = "inherited"
REEXERCISED = "reexercised"

class GateStatus(str, Enum):
    """Whether the scenario evidence can support an acceptance."""

    #: Every required scenario passed, and every one of them can show its evidence.
    VERIFIED = "VERIFIED"
    #: At least one required scenario did not establish a pass. This includes
    #: failures, and equally includes verification that never happened.
    NOT_VERIFIED = "NOT_VERIFIED"


class UnverifiedCase(BaseModel):
    """One required scenario that did not establish a pass, and why."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    origin: str = ""
    outcome: str = ""
    reason: str = ""
    evidence_path: str = ""

    def brief(self) -> str:
        label = f"[{self.outcome or 'NO RESULT'}] {self.scenario_id}"
        return f"{label} — {self.reason}"


class UncoveredRisk(BaseModel):
    """A risk this run identified and then did not verify.

    Not a failure claim and not a prediction: a statement that something the run
    itself named as able to go wrong has no passing scenario behind it.
    """

    model_config = ConfigDict(extra="forbid")

    risk_id: str = ""
    description: str = ""
    risk_category: str = ""
    severity: str = ""
    #: True when this risk's severity is one that blocks acceptance (P0/P1).
    required: bool = False
    reason: str = ""
    #: Set when two kinds of evidence about this risk disagreed. Never a
    #: coverage question: something measured this and something else measured it
    #: differently, and exactly one of those two records is wrong.
    contradiction: str = ""
    #: What the risk was grounded to before it was allowed to block.
    grounding: list[str] = Field(default_factory=list)
    #: Other register entries that are this same obligation in other words.
    duplicates: list[str] = Field(default_factory=list)

    def brief(self) -> str:
        head = f"[{self.severity or '??'}] {self.risk_category or 'uncategorised'}"
        tail = self.contradiction or self.reason
        merged = (
            f" [one obligation with {', '.join(self.duplicates)}]" if self.duplicates else ""
        )
        return f"{head} — {self.description}{merged}  ({tail})"


class CoveredRisk(BaseModel):
    """A risk this run identified and did verify, and exactly what verified it.

    The citation is the point. "This risk is covered" is worth nothing on its
    own; "this risk is covered by scenario ``p6_m3_external_effect``, claim
    ``a pre-M3 database migrates to the canonical effect shape``, which passed
    with evidence at ``runs/…/case``" can be checked by a human who does not
    trust the driver.
    """

    model_config = ConfigDict(extra="forbid")

    risk_id: str = ""
    description: str = ""
    risk_category: str = ""
    severity: str = ""
    #: The scenario whose passing outcome carries the evidence.
    scenario_id: str = ""
    origin: str = ""
    #: How the evidence attaches to this risk. Exactly three kinds exist, and
    #: every one of them is an explicit declaration or an executed command,
    #: checked against what happened:
    #: "declared" — the scenario's own ``verifies:`` claim, established;
    #: "risk_category" — a generated scenario planned for this risk category;
    #: "repository_guard" — a guard this change delivered, run by this driver,
    #: observed to read everything the risk names;
    #: "repository_guard_structural" — such a guard's single test that
    #: structurally measures the grounded risk, with its own positive control;
    #: "driver_structural" — this driver's positive-controlled scan of the tree.
    basis: str = ""
    #: The same answer in the words a reader wants: WHICH KIND of measurement
    #: discharged this risk. A "covered" that cannot name its own source reads
    #: the same whether it came from an executed command or from a whitelist.
    evidence_source: str = ""
    #: What to point at. A scenario id, or the path of the guard that ran.
    measurement: str = ""
    #: The command this driver executed, when the measurement was a guard.
    command: str = ""
    #: Anti-vacuity cases observed passing beside a guard that asserts an
    #: absence. Empty for a positive guard, which proves itself by passing.
    discrimination: list[str] = Field(default_factory=list)
    #: The declared claim's own words, when the basis is a declaration.
    claim: str = ""
    evidence_path: str = ""
    #: Where the evidence comes from in time. ``fresh`` — executed in the
    #: iteration being judged; ``inherited`` — earlier evidence from this run
    #: that nothing since has invalidated; ``reexercised`` — earlier evidence a
    #: change invalidated, executed again in this iteration.
    freshness: str = FRESH
    #: For ``inherited``: the iteration that executed it, and the tree it ran on.
    evidence_iteration: int = 0
    evidence_tree: str = ""
    #: What the risk was grounded to, when grounding decided which measurement applied.
    grounding: list[str] = Field(default_factory=list)
    #: Other register entries that are this same obligation in other words.
    duplicates: list[str] = Field(default_factory=list)

    def brief(self) -> str:
        head = f"[{self.severity or '??'}] {self.risk_category or 'uncategorised'}"
        if self.duplicates:
            head += f" [one obligation with {', '.join(self.duplicates)}]"
        detail = self.measurement or self.scenario_id
        if self.claim:
            detail += f" — {self.claim}"
        elif self.command:
            detail += f" — `{self.command}`"
        if self.discrimination:
            detail += " (discrimination: " + ", ".join(self.discrimination[:2]) + ")"
        source = self.evidence_source or self.basis or "unstated source"
        when = {
            INHERITED: (
                f"; INHERITED from iteration {self.evidence_iteration} on tree "
                f"{self.evidence_tree[:12]}, unchanged inside its subject since"
            ),
            REEXERCISED: "; earlier evidence was INVALIDATED by a change and this was re-executed",
        }.get(self.freshness, "; executed fresh")
        return f"{head} — {self.description}  (verified by {source}: {detail}{when})"


#: How a passing measurement may attach to a risk. All three are explicit and
#: all three are checked against what executed; none is inferred from prose,
#: from a passing test suite, or from anything a model said.
BASIS_DECLARED = "declared"
BASIS_CATEGORY = "risk_category"
BASIS_GUARD = "repository_guard"
#: A changed, executed repository guard with one test that structurally
#: measures the grounded risk and carries its own positive control.
BASIS_STRUCTURAL_GUARD = "repository_guard_structural"
#: The driver's own structural scan, positive-controlled, on the judged tree.
BASIS_DRIVER_STRUCTURAL = "driver_structural"

#: The same three, in the words the report uses. Named separately because a
#: reader asking "what discharged this?" wants the kind of measurement, and a
#: fourth kind — a probe, an external oracle — would be added here beside them
#: rather than folded into one of these.
SOURCE_DECLARED = "reviewed scenario claim"
SOURCE_CATEGORY = "generated scenario"
SOURCE_GUARD = "repository guard executed by this run"
SOURCE_STRUCTURAL_GUARD = "changed repository guard executed by this run (structural test)"
SOURCE_DRIVER_STRUCTURAL = "structural reachability measurement taken by this driver"

_SOURCE_OF = {
    BASIS_DECLARED: SOURCE_DECLARED,
    BASIS_CATEGORY: SOURCE_CATEGORY,
    BASIS_GUARD: SOURCE_GUARD,
    BASIS_STRUCTURAL_GUARD: SOURCE_STRUCTURAL_GUARD,
    BASIS_DRIVER_STRUCTURAL: SOURCE_DRIVER_STRUCTURAL,
}

#: What a gap says when the risk could not be grounded in anything concrete.
UNGROUNDED = (
    "the risk names no concrete repository artifact and none of the product modules "
    "this diff changed could be tied to it, so no guard or structural measurement can "
    "be held against it yet; it stays blocking until a scenario that names its subject "
    "measures it"
)


def _satisfying_outcome(
    category: str, outcomes: Sequence[ScenarioOutcome]
) -> tuple[ScenarioOutcome, str, str] | None:
    """The first outcome that verifies ``category``, and how. ``None`` if none does.

    The burden of proof is the gate's, unchanged: an outcome contributes only
    when it PASSED *and* its evidence resolves. What changed is what may
    attach to a risk once that burden is met.

    Two attachments, in priority order, and both are explicit declarations:

    1. **A declared claim.** The scenario file says, in a reviewed
       ``verifies:`` block, that it verifies this risk category, and names the
       checks and literal observations that establish it. The executor resolved
       that claim against what actually ran. This is what lets permanent and
       probe coverage count, and it is why a risk no longer stays "uncovered"
       merely because the scenario that exercises it carries a different label.

    2. **A generated scenario's own risk category.** A scenario the planner
       generated *for* this risk, which passed. Unchanged from before.

    Deliberately absent: any notion of similarity, neighbouring categories,
    word overlap, or "the tests passed so it is covered". A risk with no
    declaration and no generated scenario is uncovered, full stop.
    """
    for outcome in outcomes:
        if outcome.outcome is not Outcome.PASSED or not outcome.evidence_verified:
            continue
        for evidence in outcome.risk_evidence:
            if evidence.risk_category == category and evidence.established:
                return outcome, BASIS_DECLARED, evidence.claim
        if outcome.risk_category == category:
            return outcome, BASIS_CATEGORY, ""
    return None


def _scenario_refusal(
    category: str, outcomes: Sequence[ScenarioOutcome]
) -> ScenarioOutcome | None:
    """A scenario attached to this risk that RAN AND FAILED.

    Narrower than "did not pass" on purpose. A skipped or blocked scenario said
    nothing about the risk, and a scenario whose evidence would not resolve is a
    bookkeeping problem. Only an executed refusal is a second measurement able
    to contradict a first one.
    """
    for outcome in outcomes:
        if outcome.outcome is not Outcome.FAILED:
            continue
        if outcome.risk_category == category:
            return outcome
        if any(e.risk_category == category for e in outcome.risk_evidence):
            return outcome
    return None


def _contradiction(
    guard: GuardVerdict,
    satisfied: tuple[ScenarioOutcome, str, str] | None,
    refusal: ScenarioOutcome | None,
) -> str:
    """Two measurements of one risk that cannot both be right, stated as that.

    This is the branch that fails closed. Both directions are the same event
    seen from different sides, and neither is a coverage question: a risk with
    two disagreeing measurements is a risk nobody has established, and choosing
    the agreeable half of a contradiction is how a run launders a failure into
    an acceptance.
    """
    if guard.discharges and refusal is not None:
        detail = refusal.failed_assertions[0] if refusal.failed_assertions else refusal.error
        return (
            f"{guard.measurement.path} measures this risk and PASSED, while scenario "
            f"{refusal.scenario_id} exercising the same risk FAILED "
            f"({detail or 'no detail recorded'}). Exactly one of those two measurements is "
            "wrong and this run does not know which, so the risk is not verified."
        )
    if guard.refuted and satisfied is not None:
        outcome, _basis, claim = satisfied
        return (
            f"{guard.measurement.path} measures this risk and REFUSED "
            f"({guard.measurement.detail[:160]}), while {outcome.scenario_id} "
            + (f"established the claim {claim!r}" if claim else "passed carrying this risk")
            + ". Exactly one of those two measurements is wrong and this run does not know "
            "which, so the risk is not verified."
        )
    return ""


def _gap_reason(category: str, outcomes: Sequence[ScenarioOutcome]) -> str:
    """Why this category has no evidence — stated so a reader knows what to do.

    Three situations, and they call for three different responses: nothing was
    ever run for it (generate or declare coverage), something was run and did
    not pass (fix the product), or something *declared* it and the declaration
    did not hold (the evidence the scenario promised did not appear).
    """
    attempted = [o for o in outcomes if o.risk_category == category]
    declared = [
        (o, e)
        for o in outcomes
        for e in o.risk_evidence
        if e.risk_category == category
    ]
    if not attempted and not declared:
        return (
            "no scenario exercising this risk was executed, so nothing about it "
            "has been verified"
        )

    parts: list[str] = []
    if attempted:
        tallies = {
            state: sum(1 for o in attempted if o.outcome is state) for state in Outcome
        }
        parts.append(
            f"{len(attempted)} scenario(s) exercised this risk and none established a "
            "pass with resolvable evidence ("
            + ", ".join(
                f"{count} {state.value.lower()}"
                for state, count in tallies.items()
                if count
            )
            + ")"
        )
    for outcome, evidence in declared:
        if not evidence.established:
            parts.append(
                f"{outcome.scenario_id} declared it verifies this risk "
                f"({evidence.claim!r}) and the declaration did not hold: "
                f"{evidence.reason or 'no reason recorded'}"
            )
        elif outcome.outcome is not Outcome.PASSED:
            parts.append(
                f"{outcome.scenario_id} established the claim {evidence.claim!r} but the "
                f"scenario itself did not pass ({outcome.outcome.value}), so its evidence "
                "is not something acceptance may rest on"
            )
        else:
            parts.append(
                f"{outcome.scenario_id} established the claim {evidence.claim!r} but could "
                f"not show its evidence: "
                f"{outcome.evidence_problem or 'the cited evidence did not resolve'}"
            )
    return "; ".join(parts)


def risk_coverage(
    risks: Sequence[IdentifiedRisk],
    result: SuiteResult | None,
    *,
    changed_verification: Any = None,
) -> tuple[list[CoveredRisk], list[UncoveredRisk]]:
    """Split this run's acceptance-blocking risks into verified and not.

    One pass, one rule, and the same burden of proof on both sides, so the two
    lists cannot disagree. Deterministic: the inputs are the risk register the
    run wrote down, the outcome records execution produced, and the executed
    changed-verification obligation — all three persisted, so a resumed run
    reaches the same answer from the same evidence rather than re-deriving it.
    Nothing here consults a model, and no model answer can add a risk to either
    list or move one between them.

    ``changed_verification`` is optional and read through ``getattr``: a run
    without the obligation behaves exactly as this did before it existed.

    The order of the branches is the argument. A contradiction is settled first,
    because a risk two measurements disagree about is not covered by either of
    them. Scenario evidence comes next, because it is the evidence this ledger
    was built on and citing it keeps every existing record stable. A repository
    guard comes last, and only for what scenarios left open — it is the answer
    to "this risk has no evidence at all", never a way to retire a scenario.
    """
    if not risks:
        return [], []
    outcomes = list(result.outcomes) if result is not None else []
    lineage = result.lineage if result is not None else None
    measurements = guard_measurements(changed_verification)
    # The product modules the judged diff changed, as the driver's own
    # structural scan recorded them. Absent on a record taken before grounding
    # existed, in which case nothing below grounds anything and every answer is
    # the one this ledger gave before.
    reachability = list(getattr(changed_verification, "module_reachability", None) or [])
    changed_modules = [str(getattr(m, "path", "")) for m in reachability]
    tree = str(getattr(changed_verification, "commit", "") or "")

    covered: list[CoveredRisk] = []
    gaps: list[UncoveredRisk] = []
    # One obligation per semantic risk: same category, same structural
    # hypothesis, same grounded subjects. A second wording joins the first
    # entry instead of becoming a second blocker; a risk whose subjects differ,
    # or that hypothesises nothing structural, is never merged.
    by_key: dict[tuple[str, str, tuple[str, ...]], CoveredRisk | UncoveredRisk] = {}
    for risk in risks:
        if not risk.severity.blocks_acceptance:
            continue
        category = risk.risk_category.value
        grounding = ground(risk.description, changed_modules) if changed_modules else Grounding()
        key = grounding.key(category)
        if key is not None and key in by_key:
            by_key[key].duplicates.append(risk.id or risk.description[:60])
            continue
        entry = _judge_risk(
            risk,
            category,
            outcomes,
            lineage,
            measurements,
            grounding,
            reachability,
            tree,
        )
        (covered if isinstance(entry, CoveredRisk) else gaps).append(entry)
        if key is not None:
            by_key[key] = entry
    return covered, gaps


def _judge_risk(
    risk: IdentifiedRisk,
    category: str,
    outcomes: Sequence[ScenarioOutcome],
    lineage: Any,
    measurements: Sequence[Any],
    grounding: Grounding,
    reachability: Sequence[Any],
    tree: str,
) -> CoveredRisk | UncoveredRisk:
    """One risk, one verdict, in the order the evidence is trusted."""
    found = _satisfying_outcome(category, outcomes)
    refusal = _scenario_refusal(category, outcomes)
    guard = guard_evidence(risk, measurements)
    structural, structural_test, structural_gap = guard_structural_evidence(
        grounding, [m for m in measurements if m.passed or not m.executable]
    )
    refuting, refuting_test, _gap = guard_structural_evidence(
        grounding, [m for m in measurements if not m.passed and m.executable]
    )
    # A guard FILE that failed is a refutation of this risk only when the
    # structural test itself is the one reported failing; another test in the
    # same file failing says nothing about this measurement either way.
    if refuting is not None and refuting_test.name not in refuting.detail:
        refuting = refuting_test = None
    clash = _contradiction(guard, found, refusal)
    if not clash and structural is not None and refusal is not None:
        detail = refusal.failed_assertions[0] if refusal.failed_assertions else refusal.error
        clash = (
            f"{structural.path}::{structural_test.name} measures this risk and PASSED, while "
            f"scenario {refusal.scenario_id} exercising the same risk FAILED "
            f"({detail or 'no detail recorded'}). Exactly one of those two measurements is "
            "wrong and this run does not know which, so the risk is not verified."
        )
    if not clash and refuting is not None and found is not None:
        clash = (
            f"{refuting.path}::{refuting_test.name} measures this risk and REFUSED "
            f"({refuting.detail[:160]}), while {found[0].scenario_id} passed carrying it. "
            "Exactly one of those two measurements is wrong and this run does not know "
            "which, so the risk is not verified."
        )
    basis_lines = list(grounding.basis)

    if clash:
        return UncoveredRisk(
            risk_id=risk.id,
            description=risk.description,
            risk_category=category,
            severity=risk.severity.value,
            required=True,
            reason=clash,
            contradiction=clash,
            grounding=basis_lines,
        )

    if found is not None:
        outcome, basis, claim = found
        if outcome.inherited_from_iteration:
            freshness = INHERITED
        elif lineage is not None and outcome.scenario_id in lineage.invalidated_ids():
            freshness = REEXERCISED
        else:
            freshness = FRESH
        return CoveredRisk(
            risk_id=risk.id,
            description=risk.description,
            risk_category=category,
            severity=risk.severity.value,
            scenario_id=outcome.scenario_id,
            origin=outcome.origin.value,
            basis=basis,
            evidence_source=_SOURCE_OF.get(basis, basis),
            measurement=outcome.scenario_id,
            claim=claim,
            evidence_path=outcome.evidence_path,
            freshness=freshness,
            evidence_iteration=outcome.evidence_iteration,
            evidence_tree=outcome.evidence_tree,
        )

    if guard.discharges and guard.measurement is not None:
        return CoveredRisk(
            risk_id=risk.id,
            description=risk.description,
            risk_category=category,
            severity=risk.severity.value,
            origin="repository",
            basis=BASIS_GUARD,
            evidence_source=SOURCE_GUARD,
            measurement=guard.measurement.path,
            command=guard.measurement.command,
            discrimination=list(guard.measurement.discrimination),
            claim=(
                "this change delivered this guard and this run ran it; its own "
                "assertions read "
                + ", ".join(guard.covered[:4])
            ),
            evidence_path=guard.measurement.path,
        )

    if refuting is not None:
        reason = (
            f"{refuting.path}::{refuting_test.name} structurally measures this risk and REFUSED "
            f"on this tree: {refuting.detail[:200]}"
        )
        return UncoveredRisk(
            risk_id=risk.id,
            description=risk.description,
            risk_category=category,
            severity=risk.severity.value,
            required=True,
            reason=reason,
            grounding=basis_lines,
        )

    if structural is not None and structural.passed:
        return CoveredRisk(
            risk_id=risk.id,
            description=risk.description,
            risk_category=category,
            severity=risk.severity.value,
            origin="repository",
            basis=BASIS_STRUCTURAL_GUARD,
            evidence_source=SOURCE_STRUCTURAL_GUARD,
            measurement=f"{structural.path}::{structural_test.name}",
            command=structural.command,
            discrimination=["its own positive control: the scan is asserted to find what it must"],
            claim=(
                f"this change delivered this guard and this run ran it on this tree; its test "
                f"{structural_test.name} measures {grounding.hypothesis} over "
                + ", ".join(grounding.subjects[:4])
            ),
            evidence_path=structural.path,
            evidence_tree=tree,
            grounding=basis_lines,
        )

    discharged, driver_detail = reachability_evidence(grounding, reachability)
    if discharged and refusal is None:
        return CoveredRisk(
            risk_id=risk.id,
            description=risk.description,
            risk_category=category,
            severity=risk.severity.value,
            origin="driver",
            basis=BASIS_DRIVER_STRUCTURAL,
            evidence_source=SOURCE_DRIVER_STRUCTURAL,
            measurement=", ".join(grounding.capability_paths),
            discrimination=["positive control: the same resolver found each module imported by verification"],
            claim=driver_detail,
            evidence_tree=tree,
            grounding=basis_lines,
        )

    reason = _gap_reason(category, outcomes)
    if grounding.hypothesis:
        guard_side = structural_gap
        if grounding.hypothesis and not guard_side:
            guard_side = (
                f"no changed guard with a positive-controlled {grounding.hypothesis} test "
                "covers " + ", ".join(grounding.subjects[:4] or ["its subject"])
            )
        if grounding.hypothesis and not driver_detail:
            driver_detail = "no structural measurement of its subject was taken on this tree"
        extra = [d for d in (guard_side, driver_detail) if d]
        reason = f"{reason}; " + "; ".join(extra)
    elif guard.reason:
        reason = f"{reason}; {guard.reason}"
    # Grounding is only possible where the judged diff is known.
    if reachability and not grounding.grounded:
        reason = f"{reason}; {UNGROUNDED}"
    return UncoveredRisk(
        risk_id=risk.id,
        description=risk.description,
        risk_category=category,
        severity=risk.severity.value,
        required=True,
        reason=reason,
        grounding=basis_lines,
    )


def uncovered_required_risks(
    risks: Sequence[IdentifiedRisk],
    result: SuiteResult | None,
    *,
    changed_verification: Any = None,
) -> list[UncoveredRisk]:
    """Which acceptance-blocking risks have no passing measurement behind them.

    See :func:`risk_coverage`, of which this is the half callers most often
    want. Kept as a separate name because it is the question the acceptance
    path asks.
    """
    return risk_coverage(risks, result, changed_verification=changed_verification)[1]


def covered_required_risks(
    risks: Sequence[IdentifiedRisk],
    result: SuiteResult | None,
    *,
    changed_verification: Any = None,
) -> list[CoveredRisk]:
    """Which acceptance-blocking risks were verified, and by what."""
    return risk_coverage(risks, result, changed_verification=changed_verification)[0]


class GateVerdict(BaseModel):
    """The gate's decision, with the evidence for it."""

    model_config = ConfigDict(extra="forbid")

    status: GateStatus
    required_total: int = 0
    required_passed: int = 0
    executed: int = 0
    unverified: list[UnverifiedCase] = Field(default_factory=list)
    #: Acceptance-blocking risks this run identified and did not verify. A gap
    #: here is not a failing scenario, which is exactly why it used to be
    #: invisible: every executed scenario could pass while a named P0 risk had no
    #: scenario at all.
    uncovered_risks: list[UncoveredRisk] = Field(default_factory=list)
    #: Acceptance-blocking risks this run identified and *did* verify, each
    #: citing the scenario and the declared claim that verified it. Recorded so
    #: a reader can audit the positive half of the answer too: "covered" that
    #: cannot name what covered it is indistinguishable from a whitelist.
    covered_risks: list[CoveredRisk] = Field(default_factory=list)
    #: Problems that prevented verification from being planned or produced at
    #: all — a generator that failed, a wave that errored. A run whose
    #: verification never got built has not verified anything.
    generation_problems: list[str] = Field(default_factory=list)
    #: The evidence lineage of the result this verdict judged, as lines.
    lineage: list[str] = Field(default_factory=list)

    @property
    def blocks_acceptance(self) -> bool:
        return self.status is not GateStatus.VERIFIED

    @property
    def permanent_unverified(self) -> list[UnverifiedCase]:
        return [c for c in self.unverified if c.origin == Origin.PERMANENT.value]

    @property
    def contradictions(self) -> list[UncoveredRisk]:
        """Risks two measurements disagreed about. Never merely uncovered."""
        return [r for r in self.uncovered_risks if r.contradiction]

    def evidence_sources(self) -> str:
        """Which kinds of measurement discharged this run's risks, and how many.

        A reader who is told "5 risks covered" learns nothing about whether the
        coverage was executed or asserted. This says which of them came from a
        generated scenario, a reviewed claim, or a guard the change delivered.
        """
        tally: dict[str, int] = {}
        for risk in self.covered_risks:
            key = risk.evidence_source or risk.basis or "unstated source"
            tally[key] = tally.get(key, 0) + 1
        if not tally:
            return "none"
        return ", ".join(f"{count} by {source}" for source, count in sorted(tally.items()))

    def headline(self) -> str:
        if self.status is GateStatus.VERIFIED:
            return (
                f"scenario gate: VERIFIED — {self.required_passed}/{self.required_total} "
                "required scenario(s) passed with resolvable evidence"
            )
        # Every blocker this verdict actually has, and only those. Leading
        # unconditionally with the unverified count reported "0 of 9 required
        # scenario(s) did not establish a pass" as the headline of a run whose
        # real blocker was four generated scenarios that never reached the
        # suite — a sentence whose own number says nothing is wrong. Which
        # blockers exist is a property of the verdict, so the headline reads it
        # rather than assuming one of them.
        blockers: list[str] = []
        if self.unverified:
            blockers.append(
                f"{len(self.unverified)} of {self.required_total} required scenario(s) "
                "did not establish a pass"
            )
        if self.contradictions:
            blockers.append(
                f"{len(self.contradictions)} identified acceptance-blocking risk(s) have "
                "CONTRADICTORY evidence: two measurements of the same risk disagree"
            )
        remaining = len(self.uncovered_risks) - len(self.contradictions)
        if remaining > 0:
            blockers.append(
                f"{remaining} identified acceptance-blocking risk(s) "
                "have no passing measurement"
            )
        if self.generation_problems:
            blockers.append(
                f"{len(self.generation_problems)} verification generation/assembly "
                "problem(s): coverage this run committed to was never built or executed"
            )
        if not blockers:  # pragma: no cover - NOT_VERIFIED implies a blocker
            blockers.append("verification did not establish a pass")
        return (
            "scenario gate: NOT VERIFIED — "
            + "; ".join(blockers)
            + f" ({self.required_passed} of {self.required_total} required passed, "
            f"{self.executed} executed)"
        )

    def summary_block(self) -> str:
        lines = [self.headline()]
        for problem in self.generation_problems:
            lines.append(f"  generation: {problem}")
        for case in self.unverified:
            lines.append(f"  {case.brief()}")
            if case.evidence_path:
                lines.append(f"      evidence: {case.evidence_path}")
        if self.covered_risks:
            lines.append(
                "  RISK COVERAGE — risks this run identified, and the executed evidence "
                "that verified each (" + self.evidence_sources() + "):"
            )
            for risk in self.covered_risks:
                lines.append(f"    {risk.brief()}")
                if risk.evidence_path:
                    lines.append(f"        evidence: {risk.evidence_path}")
        if self.uncovered_risks:
            lines.append(
                "  KNOWN COVERAGE GAPS — risks this run identified and did not verify:"
            )
            lines += [f"    {risk.brief()}" for risk in self.uncovered_risks]
        if self.covered_risks or self.uncovered_risks:
            lines += [f"  {line}" for line in self.risk_ledger()]
        lines += [f"  {line}" for line in self.lineage]
        return "\n".join(lines)

    def risk_ledger(self) -> list[str]:
        """Every acceptance-blocking risk, by WHEN its evidence was taken."""
        groups = {
            FRESH: [r for r in self.covered_risks if r.freshness == FRESH],
            INHERITED: [r for r in self.covered_risks if r.freshness == INHERITED],
            REEXERCISED: [r for r in self.covered_risks if r.freshness == REEXERCISED],
        }
        label = lambda r: r.risk_id or r.description[:60]  # noqa: E731
        return [
            "RISK LEDGER — "
            f"freshly exercised: {len(groups[FRESH])}; "
            f"inherited from valid earlier same-subject evidence: {len(groups[INHERITED])}; "
            f"invalidated and re-exercised: {len(groups[REEXERCISED])}; "
            f"still uncovered: {len(self.uncovered_risks)}",
            "  fresh: " + (", ".join(label(r) for r in groups[FRESH]) or "none"),
            "  inherited: "
            + (
                ", ".join(
                    f"{label(r)} (iteration {r.evidence_iteration}, {r.scenario_id})"
                    for r in groups[INHERITED]
                )
                or "none"
            ),
            "  invalidated and re-exercised: "
            + (", ".join(f"{label(r)} ({r.scenario_id})" for r in groups[REEXERCISED]) or "none"),
            "  still uncovered: "
            + (", ".join(label(r) for r in self.uncovered_risks) or "none"),
        ]


def _reason_for(outcome: ScenarioOutcome) -> str:
    """Why this outcome does not establish a pass. Called only when it does not."""
    if outcome.outcome is Outcome.PASSED:
        # Passed, so the only way to be here is unresolvable evidence.
        return (
            outcome.evidence_problem
            or "the scenario reported a pass but its evidence could not be confirmed"
        )
    if outcome.outcome is Outcome.SKIPPED:
        return (
            f"the scenario did not run ({outcome.skip_reason or 'no reason recorded'}), "
            "so nothing was verified"
        )
    if outcome.outcome is Outcome.BLOCKED:
        return (
            f"the product was never observed ({outcome.error or 'blocked before execution'})"
        )
    detail = outcome.failed_assertions[0] if outcome.failed_assertions else outcome.error
    return f"the scenario failed: {detail or 'no detail recorded'}"


def evaluate_gate(
    result: SuiteResult | None,
    *,
    generation_problems: Sequence[str] = (),
    risks: Sequence[IdentifiedRisk] = (),
    changed_verification: Any = None,
) -> GateVerdict:
    """Decide whether the scenario evidence can support an ACCEPT.

    ``result`` may be ``None`` when a suite was never executed; that is itself
    an unverified state whenever verification was expected, which is why the
    caller passes any generation problems in rather than this inferring them.

    ``risks`` is the run's own identified risk register. Passing it closes the
    hole where every executed scenario passed and the gate said VERIFIED while a
    risk the run had named P0 had no scenario behind it at all — a question the
    evaluator was being asked ("was the coverage sufficient?") without being
    shown the answer the driver already had.

    ``changed_verification`` is this run's executed changed-guard obligation.
    Passing it closes the other half of the same hole: a risk whose direct
    oracle is a guard this change delivered, which this driver ran and read the
    exit status of, was reported as having "no scenario exercising this risk"
    and sent back for a generated approximation of a measurement already taken.
    Omitting it costs nothing and changes no other answer.
    """
    problems = [p for p in generation_problems if str(p).strip()]
    if result is not None:
        # A scenario that never entered the suite produced no outcome to be
        # unverified, so it would otherwise be invisible here. It is exactly as
        # unverified as one that failed.
        problems += [p for p in result.assembly_problems if str(p).strip()]

    covered, gaps = risk_coverage(
        risks, result, changed_verification=changed_verification
    )

    lineage_lines = (
        result.lineage.lines() if result is not None and result.lineage is not None else []
    )
    if result is None:
        status = (
            GateStatus.NOT_VERIFIED if (problems or gaps) else GateStatus.VERIFIED
        )
        return GateVerdict(
            status=status,
            generation_problems=problems,
            uncovered_risks=gaps,
            covered_risks=covered,
        )

    by_id = {o.scenario_id: o for o in result.outcomes}
    # The authoritative required set is what the suite set out to verify, not
    # what happened to produce a record. A required scenario with no outcome at
    # all is the case that previously vanished from every count.
    required_ids = list(result.expected_required_ids) or [
        o.scenario_id for o in result.outcomes if o.required
    ]

    unverified: list[UnverifiedCase] = []
    passed = 0
    for scenario_id in required_ids:
        outcome = by_id.get(scenario_id)
        if outcome is None:
            unverified.append(
                UnverifiedCase(
                    scenario_id=scenario_id,
                    reason=(
                        "no result was recorded for this required scenario, so it cannot "
                        "have been verified"
                    ),
                )
            )
            continue
        if outcome.outcome is Outcome.PASSED and outcome.evidence_verified:
            passed += 1
            continue
        unverified.append(
            UnverifiedCase(
                scenario_id=scenario_id,
                origin=outcome.origin.value,
                outcome=outcome.outcome.value,
                reason=_reason_for(outcome),
                evidence_path=outcome.evidence_path,
            )
        )

    executed = sum(
        1
        for o in result.outcomes
        if o.outcome in (Outcome.PASSED, Outcome.FAILED, Outcome.BLOCKED)
    )
    status = (
        GateStatus.VERIFIED
        if not unverified and not problems and not gaps
        else GateStatus.NOT_VERIFIED
    )
    return GateVerdict(
        status=status,
        required_total=len(required_ids),
        required_passed=passed,
        executed=executed,
        unverified=unverified,
        uncovered_risks=gaps,
        covered_risks=covered,
        generation_problems=problems,
        lineage=lineage_lines,
    )


__all__ = [
    "BASIS_CATEGORY",
    "BASIS_DECLARED",
    "BASIS_GUARD",
    "SOURCE_CATEGORY",
    "SOURCE_DECLARED",
    "SOURCE_GUARD",
    "CoveredRisk",
    "GateStatus",
    "GateVerdict",
    "UncoveredRisk",
    "UnverifiedCase",
    "covered_required_risks",
    "evaluate_gate",
    "risk_coverage",
    "uncovered_required_risks",
]
