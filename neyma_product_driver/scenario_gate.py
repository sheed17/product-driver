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


def uncovered_required_risks(
    risks: Sequence[IdentifiedRisk],
    result: SuiteResult | None,
) -> list[UncoveredRisk]:
    """Which acceptance-blocking risks have no passing scenario behind them.

    Deterministic, and deliberately so. The run's own generator named these
    risks; whether one was verified is then a matter of counting outcomes, not of
    judgement. Nothing here consults a model, and no model answer can add a risk
    to this list or remove one from it.

    A risk counts as verified only when some scenario exercising its category
    PASSED **and** could show its evidence — the same burden of proof the gate
    applies to a required scenario. A category whose scenarios all failed, were
    skipped, or were never generated is a gap, and the difference between those
    three is recorded because it tells a reader what to do about it.
    """
    if not risks:
        return []
    outcomes = list(result.outcomes) if result is not None else []

    gaps: list[UncoveredRisk] = []
    for risk in risks:
        if not risk.severity.blocks_acceptance:
            continue
        category = risk.risk_category.value
        matching = [o for o in outcomes if o.risk_category == category]
        if any(o.outcome is Outcome.PASSED and o.evidence_verified for o in matching):
            continue
        if not matching:
            reason = (
                "no scenario exercising this risk was executed, so nothing about it "
                "has been verified"
            )
        else:
            tallies = {
                state: sum(1 for o in matching if o.outcome is state)
                for state in Outcome
            }
            reason = (
                f"{len(matching)} scenario(s) exercised this risk and none established a "
                "pass with resolvable evidence ("
                + ", ".join(
                    f"{count} {state.value.lower()}"
                    for state, count in tallies.items()
                    if count
                )
                + ")"
            )
        gaps.append(
            UncoveredRisk(
                risk_id=risk.id,
                description=risk.description,
                risk_category=category,
                severity=risk.severity.value,
                required=True,
                reason=reason,
            )
        )
    return gaps


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

    gaps = uncovered_required_risks(risks, result)

    if result is None:
        status = (
            GateStatus.NOT_VERIFIED if (problems or gaps) else GateStatus.VERIFIED
        )
        return GateVerdict(
            status=status, generation_problems=problems, uncovered_risks=gaps
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
        generation_problems=problems,
    )


__all__ = [
    "GateStatus",
    "GateVerdict",
    "UncoveredRisk",
    "UnverifiedCase",
    "evaluate_gate",
    "uncovered_required_risks",
]
