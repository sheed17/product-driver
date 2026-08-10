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


class GateVerdict(BaseModel):
    """The gate's decision, with the evidence for it."""

    model_config = ConfigDict(extra="forbid")

    status: GateStatus
    required_total: int = 0
    required_passed: int = 0
    executed: int = 0
    unverified: list[UnverifiedCase] = Field(default_factory=list)
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
        return (
            f"scenario gate: NOT VERIFIED — {len(self.unverified)} of {self.required_total} "
            f"required scenario(s) did not establish a pass ({self.executed} executed)"
        )

    def summary_block(self) -> str:
        lines = [self.headline()]
        for problem in self.generation_problems:
            lines.append(f"  generation: {problem}")
        for case in self.unverified:
            lines.append(f"  {case.brief()}")
            if case.evidence_path:
                lines.append(f"      evidence: {case.evidence_path}")
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
) -> GateVerdict:
    """Decide whether the scenario evidence can support an ACCEPT.

    ``result`` may be ``None`` when a suite was never executed; that is itself
    an unverified state whenever verification was expected, which is why the
    caller passes any generation problems in rather than this inferring them.
    """
    problems = [p for p in generation_problems if str(p).strip()]

    if result is None:
        status = GateStatus.NOT_VERIFIED if problems else GateStatus.VERIFIED
        return GateVerdict(status=status, generation_problems=problems)

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
        if not unverified and not problems
        else GateStatus.NOT_VERIFIED
    )
    return GateVerdict(
        status=status,
        required_total=len(required_ids),
        required_passed=passed,
        executed=executed,
        unverified=unverified,
        generation_problems=problems,
    )


__all__ = [
    "GateStatus",
    "GateVerdict",
    "UnverifiedCase",
    "evaluate_gate",
]
