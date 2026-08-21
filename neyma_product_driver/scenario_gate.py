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

Nothing in this module reads a mutable convenience flag, so forcing one to True
cannot make an unverified run look verified. The evaluator does not participate:
it judges what it saw, and this measures what actually ran.
"""

from __future__ import annotations

from enum import Enum
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from .scenario_plan import IdentifiedRisk
from .scenario_suite import Origin, Outcome, ScenarioOutcome, SuiteResult


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

    def brief(self) -> str:
        head = f"[{self.severity or '??'}] {self.risk_category or 'uncategorised'}"
        return f"{head} — {self.description}  ({self.reason})"


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
    #: How that scenario's evidence attaches to this risk. Exactly two kinds
    #: exist, and both are explicit declarations checked against execution:
    #: "declared" — the scenario's own ``verifies:`` claim, established;
    #: "risk_category" — a generated scenario planned for this risk category.
    basis: str = ""
    #: The declared claim's own words, when the basis is a declaration.
    claim: str = ""
    evidence_path: str = ""

    def brief(self) -> str:
        head = f"[{self.severity or '??'}] {self.risk_category or 'uncategorised'}"
        detail = f"{self.scenario_id}" + (f" — {self.claim}" if self.claim else "")
        return f"{head} — {self.description}  (verified by {detail})"


#: How a passing outcome may attach to a risk. Both are explicit and both are
#: checked against what executed; neither is inferred from prose, from a passing
#: test suite, or from anything a model said.
BASIS_DECLARED = "declared"
BASIS_CATEGORY = "risk_category"


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
) -> tuple[list[CoveredRisk], list[UncoveredRisk]]:
    """Split this run's acceptance-blocking risks into verified and not.

    One pass, one rule, and the same burden of proof on both sides, so the two
    lists cannot disagree. Deterministic: the inputs are the risk register the
    run wrote down and the outcome records execution produced, and the answer is
    a function of those two. Nothing here consults a model, and no model answer
    can add a risk to either list or move one between them.
    """
    if not risks:
        return [], []
    outcomes = list(result.outcomes) if result is not None else []

    covered: list[CoveredRisk] = []
    gaps: list[UncoveredRisk] = []
    for risk in risks:
        if not risk.severity.blocks_acceptance:
            continue
        category = risk.risk_category.value
        found = _satisfying_outcome(category, outcomes)
        if found is not None:
            outcome, basis, claim = found
            covered.append(
                CoveredRisk(
                    risk_id=risk.id,
                    description=risk.description,
                    risk_category=category,
                    severity=risk.severity.value,
                    scenario_id=outcome.scenario_id,
                    origin=outcome.origin.value,
                    basis=basis,
                    claim=claim,
                    evidence_path=outcome.evidence_path,
                )
            )
            continue
        gaps.append(
            UncoveredRisk(
                risk_id=risk.id,
                description=risk.description,
                risk_category=category,
                severity=risk.severity.value,
                required=True,
                reason=_gap_reason(category, outcomes),
            )
        )
    return covered, gaps


def uncovered_required_risks(
    risks: Sequence[IdentifiedRisk],
    result: SuiteResult | None,
) -> list[UncoveredRisk]:
    """Which acceptance-blocking risks have no passing scenario behind them.

    See :func:`risk_coverage`, of which this is the half callers most often
    want. Kept as a separate name because it is the question the acceptance
    path asks.
    """
    return risk_coverage(risks, result)[1]


def covered_required_risks(
    risks: Sequence[IdentifiedRisk],
    result: SuiteResult | None,
) -> list[CoveredRisk]:
    """Which acceptance-blocking risks were verified, and by what."""
    return risk_coverage(risks, result)[0]


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

    @property
    def blocks_acceptance(self) -> bool:
        return self.status is not GateStatus.VERIFIED

    @property
    def permanent_unverified(self) -> list[UnverifiedCase]:
        return [c for c in self.unverified if c.origin == Origin.PERMANENT.value]

    def headline(self) -> str:
        if self.status is GateStatus.VERIFIED:
            return (
                f"scenario gate: VERIFIED — {self.required_passed}/{self.required_total} "
                "required scenario(s) passed with resolvable evidence"
            )
        head = (
            f"scenario gate: NOT VERIFIED — {len(self.unverified)} of {self.required_total} "
            f"required scenario(s) did not establish a pass ({self.executed} executed)"
        )
        if self.uncovered_risks:
            head += (
                f"; {len(self.uncovered_risks)} identified acceptance-blocking risk(s) "
                "have no passing scenario"
            )
        return head

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
                "that verified each:"
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
        return "\n".join(lines)


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
    """
    problems = [p for p in generation_problems if str(p).strip()]
    if result is not None:
        # A scenario that never entered the suite produced no outcome to be
        # unverified, so it would otherwise be invisible here. It is exactly as
        # unverified as one that failed.
        problems += [p for p in result.assembly_problems if str(p).strip()]

    covered, gaps = risk_coverage(risks, result)

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
    )


__all__ = [
    "BASIS_CATEGORY",
    "BASIS_DECLARED",
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
