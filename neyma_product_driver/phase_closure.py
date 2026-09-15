"""The phase-closure controller: from implementation complete to ready to accept.

This is the transition the founder used to perform by hand. Thirteen units land,
the last one passes, and then somebody has to work out whether the phase's own
acceptance criteria are actually instantiated, whether the evidence is attached
to the exact tree being accepted, whether a reviewer's finding is a product
defect or a harness defect or a nice-to-have, whether CI ran on this commit or
the one before it, whether a residual can be closed or needs a decision, and
whether any of it is finished. Every one of those questions has a determinate
answer and none of them is a product question, which is why they belong here.

WHAT THIS CONTROLLER WILL NOT DO
--------------------------------

* It will not supply the definition of done. A phase with no explicit acceptance
  criterion set stops at ``AUTHORITY_GAP``. See
  :mod:`~neyma_product_driver.phase_authority`.
* It will not let the bar move during an attempt. The criterion set is frozen by
  fingerprint at the start of an attempt, and a reviewer that scores something
  outside it has its finding recorded as non-authoritative. See
  :mod:`~neyma_product_driver.phase_acceptance`.
* It will not read severity as blocking. A finding blocks only by mechanically
  demonstrating a required criterion false.
* It will not accept evidence about a different tree, from CI or from a
  reviewer.
* It will not close a residual whose closure condition is somebody's judgement.
* It will not push, and it will not edit anything but the narrow acceptance
  record — and only when asked. See
  :mod:`~neyma_product_driver.acceptance_commit`.

THE ORDER, AND WHY IT IS THAT ORDER
-----------------------------------

    preflight → evidence assembly → external gate → independent adjudication
             → classify → route → prepare acceptance record

Preflight is first because an independent reviewer is the most expensive thing
this system can spend and its independence cannot be spent twice. Discovering
from a reviewer that a checkpoint never landed, or that four criteria have no
evidence at all, is paying a reviewer to read a file. The external gate is
before adjudication because a reviewer asked to adjudicate a tree CI has not
seen is being asked to substitute its judgement for a measurement.

RESUME
------

Everything above is one persisted record, ``phase-closure.json``, at the run
root beside ``protocol-resolution.json``. A resume reloads it and re-checks the
two things that can have changed underneath it: the tree, and the criteria. A
moved tree invalidates evidence by blast radius; moved criteria end the attempt
rather than silently adopting a new bar. No decision anywhere is reconstructed
from prose.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .criterion_kinds import CriterionKind, gate_kind
from .external_verification import (
    ExternalEvidence,
    ExternalRequirement,
    ExternalStatus,
    requirement_from_criteria,
    run_probe,
    same_commit,
)
from .models import utcnow
from .phase_acceptance import (
    AdjudicationScores,
    AntiVacuity,
    ClosureState,
    CriterionEvidenceStatus,
    CriterionSet,
    EvidenceKind,
    EvidenceMap,
    EvidenceRef,
    FindingClass,
    PhaseFinding,
    PhaseLedger,
    RepairLayer,
    ResidualLedger,
    ResidualStatus,
    classify_and_route,
    normalize_repo_path,
)
from .phase_authority import PhaseAuthority, resolve_phase_authority
from .review_cycle import TreeFingerprint, capture_fingerprint

#: The persisted artifact. One file, at the run root, like the protocol
#: resolution — not a second store.
CLOSURE_FILE = "phase-closure.json"

#: Which version of the deterministic closure rule produced a record's stored
#: decisions. A finding's ``blocks_phase_acceptance`` is persisted precisely so
#: a resume restores the decision rather than re-deriving it from prose — but
#: that is only sound while the rule is the same rule. A record written under an
#: earlier rule carries a decision nobody would take today, and restoring it
#: would make a fixed harness keep reporting the defect it was fixed for. So the
#: version is stamped, a mismatch re-derives every finding once, and the
#: re-derivation is recorded in the record's own history rather than done
#: quietly. Bump this whenever a change here can alter a finding's disposition.
#:
#: 2: a finding may not contradict the adjudication that carried it — see rule 4
#:    in :mod:`~neyma_product_driver.phase_acceptance`.
#: 3: an adjudication counts as TAKEN only when its response covered the frozen
#:    criterion contract — one score per frozen criterion, no omissions and no
#:    duplicates. See :func:`check_adjudication_protocol`. A record written
#:    under rule 2 may carry an adjudication that was never a complete answer
#:    and was nonetheless read as the phase's one independent opinion, so the
#:    migration re-derives it rather than inheriting that reading.
#: 4: a criterion a structural gate OWNS is settled by that gate and by nothing
#:    else — see :func:`gate_owned`. Under rule 3 the reviewer's own score for
#:    the criterion that demands an independent review decided whether that
#:    review had happened, so the reviewer was asked to certify its own
#:    independence and a record could sit BLOCKED on a mechanically valid
#:    adjudication. The migration re-derives the gate evidence and the
#:    statements made about it, and it launches nothing.
CLOSURE_RULE_VERSION = "4"


# --------------------------------------------------------------------------
# Who owns which criterion
# --------------------------------------------------------------------------


def gate_owned(criteria: Any) -> dict[str, CriterionKind]:
    """The frozen criteria a structural gate owns, and which gate owns each.

    One rule, applied to a criterion set wherever a decision needs it, so that
    every consumer answers "who settles this?" the same way:

        a criterion whose IDENTIFIERS classify it as an independent review, an
        external verification or a residual-ledger statement is settled by that
        gate. The gate's facts are mechanical, and no session's score for that
        criterion — the independent reviewer's included — establishes it or
        refutes it.

    The reviewer-facing half of that rule is the one worth spelling out, because
    without it the machine is circular. ``independent_phase_review_by_a_non_builder``
    is a statement about how Product Driver CONSTRUCTED the reviewing session:
    outside the builder lineage, inheriting no builder conversation, bound to
    the exact candidate tree and the exact frozen criteria, answering the
    response contract. Every one of those is checked here, by this module,
    before a single score is read — so asking the reviewer whether it was
    independent asks the one participant who cannot observe the answer, and
    lets a truthful ``CANNOT_DETERMINE`` refuse a review that demonstrably
    happened.

    What this rule does NOT do is let a valid review award anything else. The
    reviewer still owns every criterion outside :data:`GATE_KINDS`, and its
    PASS / FAIL / CANNOT_DETERMINE on those is exactly as decisive as before.
    """
    owners: dict[str, CriterionKind] = {}
    for criterion in getattr(criteria, "criteria", []) or []:
        cid = str(getattr(criterion, "criterion_id", "") or "")
        if not cid:
            continue
        kind = gate_kind(
            cid,
            str(getattr(criterion, "name", "") or ""),
            str(getattr(criterion, "requirement", "") or ""),
        )
        if kind is not None:
            owners[cid] = kind
    return owners


#: What settles each gate-owned kind, in the words a report uses.
GATE_AUTHORITY_NAMES: dict[CriterionKind, str] = {
    CriterionKind.INDEPENDENT_REVIEW: "the independent-review gate",
    CriterionKind.EXTERNAL_VERIFICATION: "the external-verification gate",
    CriterionKind.RESIDUAL_LEDGER: "the residual ledger",
}


# --------------------------------------------------------------------------
# The adjudication record
# --------------------------------------------------------------------------


class CriterionVerdict(BaseModel):
    """One criterion, as the independent adjudication scored it."""

    model_config = ConfigDict(extra="ignore")

    criterion_id: str
    verdict: str = "CANNOT_DETERMINE"  # PASS | FAIL | CANNOT_DETERMINE
    basis: str = ""
    #: True when this criterion is not in the frozen set — a reviewer scoring
    #: something nobody asked for. Recorded, never authoritative.
    outside_authority: bool = False

    @property
    def passed(self) -> bool:
        return self.verdict.strip().upper() in ("PASS", "PASSED", "SUPPORTED")

    @property
    def failed(self) -> bool:
        return self.verdict.strip().upper() in ("FAIL", "FAILED", "NOT_SUPPORTED", "REFUSED")

    @property
    def undetermined(self) -> bool:
        """The adjudicator looked and could not tell. A real score, not a gap.

        Kept apart from "no entry at all", because the two have opposite
        owners: a criterion nobody could determine is a statement about what
        the repository can currently show, and a criterion nobody scored is a
        statement about the response.
        """
        return self.verdict.strip().upper() in (
            "CANNOT_DETERMINE",
            "CANNOT DETERMINE",
            "CANNOTDETERMINE",
            "UNDETERMINED",
            "INDETERMINATE",
            "INCONCLUSIVE",
            "INSUFFICIENT_EVIDENCE",
            "INSUFFICIENT EVIDENCE",
            "UNKNOWN",
        )

    @property
    def scored(self) -> bool:
        """Whether this entry says one of the three things a score may say."""
        return self.passed or self.failed or self.undetermined


class AdjudicationProtocol(BaseModel):
    """Whether an adjudication's RESPONSE satisfied the response contract.

    This is not a question about the phase. It is the prior question of whether
    an answer was given at all, and it exists because the two were being read
    as one. The prompt closes the criterion set and asks for one entry per
    criterion; a reply that scores none of them has not refused the phase, it
    has not answered — and the difference decides who owns the result. Reading
    an unanswered adjudication as the phase's one independent opinion turns a
    reviewer-protocol failure into a product verdict, spends the independence
    that cannot be spent twice, and leaves the phase permanently short of an
    adjudication it never received.

    ``CANNOT_DETERMINE`` is a score and is complete. Every frozen criterion
    scored ``CANNOT_DETERMINE`` is a full answer with an uncertain content: the
    phase may legitimately stop there, and nothing about it justifies buying
    another reviewer.
    """

    model_config = ConfigDict(extra="ignore")

    #: False on a record written before this check existed, so a reader can
    #: tell "the response was checked and passed" from "nobody looked".
    checked: bool = False
    complete: bool = False
    #: Every frozen criterion the response was asked to score, in order.
    expected: list[str] = Field(default_factory=list)
    #: Frozen criteria the response scored exactly once, with a readable score.
    scored: list[str] = Field(default_factory=list)
    #: Frozen criteria with no readable score in the response.
    missing: list[str] = Field(default_factory=list)
    #: Frozen criteria the response scored more than once. Two answers to one
    #: question are not an answer: nothing here may pick which one was meant.
    duplicated: list[str] = Field(default_factory=list)
    #: Frozen criteria the response named with something that is not one of
    #: PASS, FAIL or CANNOT_DETERMINE.
    unreadable: list[str] = Field(default_factory=list)
    #: Empty when the response covered the contract; otherwise exactly what is
    #: wrong with it, in the words a corrective request would use.
    problem: str = ""

    @property
    def violated(self) -> bool:
        return self.checked and not self.complete

    def outstanding_ids(self) -> list[str]:
        """The criterion ids a contract-complete answer would have to repair."""
        return _dedupe_ids([*self.missing, *self.duplicated, *self.unreadable])

    def brief(self) -> str:
        if not self.checked:
            return "the response contract was not checked"
        if self.complete:
            return (
                f"the response scored all {len(self.expected)} frozen criteria exactly once"
            )
        return self.problem or "the response did not cover the frozen criterion contract"


def _dedupe_ids(ids: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in ids:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def check_adjudication_protocol(
    criterion_results: Sequence[CriterionVerdict], criteria: Any
) -> AdjudicationProtocol:
    """Whether one response covered the frozen criterion contract. Generic.

    The contract is the prompt's own: *one entry per criterion above*, scored
    PASS, FAIL or CANNOT_DETERMINE, for exactly the frozen set. So three things
    break it, and each is reported separately because each names a different
    repair:

    * a frozen criterion with no entry — the response is short of an answer;
    * a frozen criterion with two entries — there are two answers and no rule
      here may choose between them;
    * an entry whose score is not one of the three — a word nobody can act on.

    Criteria the response scored that the frozen set does not contain are NOT
    part of this: they are recorded as outside the authority elsewhere, they
    never widen the bar, and they cannot make a response incomplete either.

    A phase with no frozen criteria at all is complete here, vacuously. That
    state is an authority gap and it is refused by a louder mechanism than this
    one; failing it here as well would only make the quieter message the one a
    reader sees.
    """
    expected = [
        str(getattr(c, "criterion_id", "") or "")
        for c in getattr(criteria, "criteria", []) or []
    ]
    expected = [cid for cid in expected if cid]
    protocol = AdjudicationProtocol(checked=True, expected=expected)
    if not expected:
        protocol.complete = True
        protocol.problem = ""
        return protocol

    counts: dict[str, int] = {cid: 0 for cid in expected}
    unreadable: list[str] = []
    for result in criterion_results or []:
        if getattr(result, "outside_authority", False):
            continue
        cid = str(getattr(result, "criterion_id", "") or "")
        if cid not in counts:
            continue
        if not result.scored:
            unreadable.append(cid)
            continue
        counts[cid] += 1

    protocol.scored = [cid for cid in expected if counts[cid] == 1]
    protocol.missing = [cid for cid in expected if counts[cid] == 0 and cid not in unreadable]
    protocol.duplicated = [cid for cid in expected if counts[cid] > 1]
    protocol.unreadable = _dedupe_ids([cid for cid in expected if cid in unreadable])
    protocol.complete = not (protocol.missing or protocol.duplicated or protocol.unreadable)

    if protocol.complete:
        protocol.problem = ""
        return protocol

    parts: list[str] = []
    if protocol.missing:
        parts.append(f"it did not score {_name_ids(protocol.missing)}")
    if protocol.duplicated:
        parts.append(f"it scored {_name_ids(protocol.duplicated)} more than once")
    if protocol.unreadable:
        parts.append(
            f"it answered {_name_ids(protocol.unreadable)} with something that is not "
            "PASS, FAIL or CANNOT_DETERMINE"
        )
    protocol.problem = (
        f"the response did not cover the frozen criterion contract "
        f"({len(protocol.scored)} of {len(expected)} criteria scored exactly once): "
        + "; ".join(parts)
    )
    return protocol


def _adjudication_identity(adjudication: Any) -> str:
    """How a finding names the adjudication response it is a statement about."""
    if adjudication is None:
        return ""
    who = getattr(adjudication, "reviewer_session_id", "") or "an unnamed session"
    return f"attempt {getattr(adjudication, 'attempt', 1)} by {who}"


def _name_ids(ids: Sequence[str], limit: int = 8) -> str:
    listed = list(ids)
    shown = ", ".join(listed[:limit])
    return shown + (f" and {len(listed) - limit} more" if len(listed) > limit else "")


class PhaseAdjudication(BaseModel):
    """One independent phase adjudication, and the proof it was independent.

    Independence here is mechanical, not asserted. The session is constructed so
    that it cannot be the builder's conversation (``resume=None``,
    ``continue_conversation=False``, ``fork_session=False`` — see
    :class:`~neyma_product_driver.reviewer.IndependentReviewerSession`), and
    every check this record can make from the artifact alone is made and stored:
    the reviewer's session id against the run's WHOLE builder lineage, the
    inherited-context flag, the tree it read, and the criteria fingerprint it
    was given.
    """

    model_config = ConfigDict(extra="ignore")

    reviewer_session_id: str = ""
    builder_session_ids: list[str] = Field(default_factory=list)
    inherited_builder_context: bool = False
    reviewed_tree: str = ""
    criteria_fingerprint: str = ""
    verdict: str = "INSUFFICIENT_EVIDENCE"
    summary: str = ""
    criterion_results: list[CriterionVerdict] = Field(default_factory=list)
    evidence_reproduced: bool = False
    adjudicated_at: str = Field(default_factory=utcnow)
    #: Criteria the reviewer scored that the frozen set does not contain.
    unrecognised_criteria: list[str] = Field(default_factory=list)
    #: Empty when the adjudication is structurally independent and about the
    #: right tree and the right criteria; otherwise says exactly what is wrong.
    independence_problem: str = ""
    #: Whether the RESPONSE covered the frozen criterion contract, as checked
    #: when it was ingested. Stored so the artifact says it, and re-derived by
    #: :meth:`protocol_for` wherever a decision depends on it — a record written
    #: before this check existed carries ``checked=False`` and must not be read
    #: as a response that passed it.
    protocol: AdjudicationProtocol = Field(default_factory=AdjudicationProtocol)
    #: Which response, within this attempt, this is. 1 is the first answer the
    #: fresh session gave; 2 is the answer to a bounded corrective request.
    attempt: int = 1
    #: Set when a later response replaced this one, naming the reviewer session
    #: and attempt that did. The replaced record is kept, never overwritten.
    superseded_by: str = ""

    @property
    def independent(self) -> bool:
        return not self.independence_problem

    @property
    def supported(self) -> bool:
        return self.independent and self.verdict.strip().upper() == "SUPPORTED"

    def protocol_for(self, criteria: Any) -> AdjudicationProtocol:
        """The response contract, re-derived from what this record stores.

        Derived rather than read from :attr:`protocol`, because the stored copy
        is an artifact and this is a decision. A record persisted before the
        check existed has no stored answer, and a record whose criteria were
        re-frozen has a stale one; both re-derive correctly from the scores the
        record already carries.
        """
        return check_adjudication_protocol(self.criterion_results, criteria)

    def taken(self, criteria: Any) -> tuple[bool, str]:
        """Whether the phase's one independent adjudication HAPPENED, and why not.

        Two conditions, and they are different questions from whether the phase
        passed:

        * the session was independent, about the exact tree, against the exact
          frozen criteria — :func:`check_independence`;
        * the response covered the frozen criterion contract —
          :func:`check_adjudication_protocol`.

        Either one failing means no adjudication was taken, and the repair is a
        fresh one. Neither failing says anything whatsoever about the product:
        an adjudication that was taken and came back entirely
        ``CANNOT_DETERMINE`` is taken, and asking for another because the answer
        was uncertain is how one honest "I could not tell" becomes an unbounded
        series of reviewer sessions.
        """
        if not self.independent:
            return False, self.independence_problem
        protocol = self.protocol_for(criteria)
        if not protocol.complete:
            return False, protocol.problem
        return True, (
            f"an independent session scored all {len(protocol.expected)} frozen criteria "
            "exactly once"
        )

    def discharges(self, criteria: Any) -> tuple[bool, str]:
        """Whether this adjudication settles the phase's required criteria.

        The authoritative output of an adjudication is its PER-CRITERION
        scoring against the frozen set, not its summary adjective. A reviewer
        that scores every required criterion PASS and then writes
        ``NOT_SUPPORTED`` because it also wants a guard nobody asked for has
        said two things: one inside the authority and one outside it. Reading
        the adjective as the answer lets the outside one reopen the phase,
        which is exactly the loop this controller exists to end.

        Nothing here reads the adjective, and that is the change rule 3 makes.
        An adjudication that scored no frozen criterion used to fall through to
        its summary — so a malformed reply became a verdict about the product,
        in whichever direction its adjective happened to point. A response that
        did not cover the contract is not an answer to read in either
        direction: :meth:`taken` refuses it first, and what is owed is a fresh
        adjudication rather than a decision about the phase.

        And it settles only the criteria it OWNS, which is rule 4. A criterion
        a structural gate owns — the external verifier's, this adjudication's
        own existence, the residual ledger's — is settled by that gate and is
        not this response's to award or withhold; see :func:`gate_owned`. So a
        reviewer that answered everything asked of it and could not certify its
        own independence has discharged its adjudication, and the phase's
        remaining question is what the gates say, not whether to buy another
        reviewer. The reviewer's score for a gate-owned criterion is still
        recorded, still readable and still reported — it is simply not the
        authority over a fact this module established mechanically.
        """
        taken, why = self.taken(criteria)
        if not taken:
            return False, why
        gates = gate_owned(criteria)
        required = {
            c.criterion_id
            for c in getattr(criteria, "required", [])
            if c.criterion_id not in gates
        }
        if not required:
            return True, (
                "every required criterion is owned by a structural gate; this adjudication "
                "was asked to settle none of them and covered the response contract"
            )
        scored = {
            r.criterion_id: r
            for r in self.criterion_results
            if not r.outside_authority and r.criterion_id in required
        }
        failed = sorted(cid for cid, r in scored.items() if r.failed)
        if failed:
            return False, f"the adjudication scored {', '.join(failed)} FAIL"
        unscored = sorted(required - set(scored))
        if unscored:
            return False, (
                f"the adjudication did not score {', '.join(unscored[:6])}"
                + (" ..." if len(unscored) > 6 else "")
            )
        undetermined = sorted(cid for cid, r in scored.items() if not r.passed)
        if undetermined:
            return False, (
                f"the adjudication could not determine {', '.join(undetermined[:6])}"
            )
        return True, (
            f"the adjudication scored all {len(required)} required criteria it owns PASS"
        )

    def result_for(self, criterion_id: str) -> CriterionVerdict | None:
        for r in self.criterion_results:
            if r.criterion_id == criterion_id:
                return r
        return None

    def brief(self) -> str:
        if not self.independent:
            return f"NOT INDEPENDENT — {self.independence_problem}"
        if self.protocol.violated:
            return (
                f"RESPONSE PROTOCOL INCOMPLETE — {self.protocol.problem} "
                f"(reported {self.verdict} by "
                f"{self.reviewer_session_id or 'an unnamed session'})"
            )
        passed = sum(1 for r in self.criterion_results if r.passed and not r.outside_authority)
        failed = sum(1 for r in self.criterion_results if r.failed and not r.outside_authority)
        outside = sum(1 for r in self.criterion_results if r.outside_authority)
        extra = f", {outside} outside the authority" if outside else ""
        return (
            f"{self.verdict} ({passed} pass, {failed} fail{extra}) by "
            f"{self.reviewer_session_id or 'an unnamed session'}"
        )


def check_independence(
    *,
    reviewer_session_id: str,
    builder_session_ids: Sequence[str],
    inherited_builder_context: bool,
    reviewed_tree: str,
    candidate_tree: str,
    criteria_fingerprint: str,
    frozen_fingerprint: str,
) -> str:
    """Why this adjudication does not count, or ``""`` when it does.

    Four separate ways an adjudication can fail to be one, kept apart because
    they have different repairs: it came from the builder, it inherited the
    builder's conversation, it read a different tree, or it was given a
    different set of demands.
    """
    if inherited_builder_context:
        return "the reviewing session inherited the builder's conversation"
    ids = {str(b).strip() for b in builder_session_ids if str(b).strip()}
    reviewer = str(reviewer_session_id or "").strip()
    if reviewer and reviewer in ids:
        return (
            f"the reviewing session {reviewer} is one of this run's builder sessions; "
            "a session cannot be a second opinion about its own work"
        )
    if candidate_tree and reviewed_tree and reviewed_tree != candidate_tree:
        return (
            f"the adjudication read {reviewed_tree} and the candidate tree is "
            f"{candidate_tree}; it is evidence about a different implementation"
        )
    if frozen_fingerprint and criteria_fingerprint and criteria_fingerprint != frozen_fingerprint:
        return (
            f"the adjudication was given criteria {criteria_fingerprint} and this attempt "
            f"froze {frozen_fingerprint}; it scored a different set of demands"
        )
    return ""


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


class RoutedWork(BaseModel):
    """Findings that belong to one repair layer, and what to do with them."""

    model_config = ConfigDict(extra="ignore")

    layer: RepairLayer
    finding_ids: list[str] = Field(default_factory=list)
    instruction: str = ""
    blocking: bool = False

    def brief(self) -> str:
        mark = "BLOCKING" if self.blocking else "nonblocking"
        return f"{self.layer.value} ({len(self.finding_ids)}, {mark}): {self.instruction}"


class RoutingPlan(BaseModel):
    """Every finding, sorted by who repairs it. Nothing is repaired here."""

    model_config = ConfigDict(extra="ignore")

    routes: list[RoutedWork] = Field(default_factory=list)
    #: True when a harness defect was found. The product run stops: patching the
    #: product to satisfy a broken measurement is the failure this prevents.
    stop_product_run: bool = False
    #: True when something is the founder's to decide.
    founder_decision_required: bool = False
    #: True when a finding contradicts the adjudication that carried it. No
    #: other layer may be given work until the adjudicator resolves it: a
    #: product repair launched from a self-contradictory adjudication repairs
    #: whatever the contradiction happened to point at.
    adjudication_must_resolve: bool = False

    def for_layer(self, layer: RepairLayer) -> RoutedWork | None:
        for route in self.routes:
            if route.layer is layer:
                return route
        return None

    def render(self) -> str:
        if not self.routes:
            return "ROUTING: nothing to route."
        lines = ["ROUTING:"]
        lines += [f"  {r.brief()}" for r in self.routes]
        if self.stop_product_run:
            lines.append(
                "  the product run STOPS: a Product Driver defect cannot be repaired by "
                "changing the product it measured."
            )
        if self.adjudication_must_resolve:
            lines.append(
                "  the ADJUDICATOR answers first: it scored a criterion PASS and attached a "
                "finding demonstrating the same criterion false. Until it says which it "
                "meant, no product repair may be launched from either."
            )
        return "\n".join(lines)


_LAYER_INSTRUCTION: dict[RepairLayer, str] = {
    RepairLayer.PRODUCT_BUILDER: (
        "send to the product builder as a grounded correction naming the criterion, "
        "the observation and the retest"
    ),
    RepairLayer.PRODUCT_VERIFICATION: (
        "a product verification task: add the test or guard that would fail if this were "
        "wrong; no runtime change"
    ),
    RepairLayer.PRODUCT_DRIVER: (
        "stop the product run and repair Product Driver separately, then re-take the "
        "measurement"
    ),
    RepairLayer.CI_INFRASTRUCTURE: (
        "a CI-only correction: the verifier is broken, not the thing it verified"
    ),
    RepairLayer.FOUNDER_DECISION: "a founder or architect states the missing authority",
    RepairLayer.INDEPENDENT_ADJUDICATION: (
        "back to the adjudicator: it scored the criterion PASS and attached a finding "
        "demonstrating it false; it states which it meant and the phase is re-adjudicated"
    ),
    RepairLayer.RECORD_ONLY: "record and move on; nothing is owed for this phase acceptance",
    RepairLayer.EXTERNAL: "someone outside this machine acts; Product Driver waits",
}


def route_findings(findings: Sequence[PhaseFinding]) -> RoutingPlan:
    """Sort findings by repair layer. Never repairs, never merges two layers."""
    plan = RoutingPlan()
    by_layer: dict[RepairLayer, list[PhaseFinding]] = {}
    for finding in findings:
        by_layer.setdefault(finding.repair_layer, []).append(finding)
    for layer in RepairLayer:
        group = by_layer.get(layer)
        if not group:
            continue
        plan.routes.append(
            RoutedWork(
                layer=layer,
                finding_ids=[f.finding_id for f in group],
                instruction=_LAYER_INSTRUCTION.get(layer, ""),
                blocking=any(f.blocks_phase_acceptance for f in group),
            )
        )
        if layer is RepairLayer.PRODUCT_DRIVER:
            plan.stop_product_run = True
        if layer is RepairLayer.FOUNDER_DECISION:
            plan.founder_decision_required = True
        if layer is RepairLayer.INDEPENDENT_ADJUDICATION:
            plan.adjudication_must_resolve = True
    return plan


# --------------------------------------------------------------------------
# Evidence extraction
# --------------------------------------------------------------------------

#: The evidence statuses that mean a criterion has nothing behind it yet. Kept
#: as one constant so the report and the decision cannot drift apart.
_UNEVIDENCED = (
    CriterionEvidenceStatus.MISSING,
    CriterionEvidenceStatus.VACUOUS,
    CriterionEvidenceStatus.STALE,
    CriterionEvidenceStatus.UNOBSERVED,
)

#: A pytest-style node id: a file and a test inside it.
_TEST_NODE = re.compile(r"\b([\w./-]+\.py)::([\w\[\]:.-]+)")
#: A repository path that names a file. Kept to the directory names a product
#: repository actually uses for executable material, so a sentence mentioning
#: "docs/foo.md" is a citation and "scripts/mutate_x.py" is a probe.
_REPO_PATH = re.compile(
    r"\b((?:src|lib|app|eval|tests?|scripts?|tools?|bin|docs?|packages)/[\w./-]+\.[A-Za-z0-9]+)\b"
)
#: A bare filename with an extension a repository keeps executable material in.
#: Registries write ``test_phase0_tenant_posture.py`` and mean the one file in
#: the tree with that name; resolving it is a lookup, not a guess, and
#: :meth:`RepoIndex.resolve` refuses when the name is not unique.
_BARE_FILE = re.compile(
    r"\b([\w-]+\.(?:py|ts|tsx|js|jsx|sql|sh|go|rs|rb|java|kt|yaml|yml|json))\b"
)
#: A bare test function name. The single most common way a repository cites a
#: test — no path, no ``::``, just the name a person would grep for. Resolved
#: through the symbol index, which is that same grep done once.
_BARE_SYMBOL = re.compile(r"\b((?:test|it|should)_[a-z0-9][a-z0-9_]{3,})\b")
#: An external run identifier stated in prose: "CI run 34314374504".
_RUN_ID = re.compile(r"(?i)\b(?:ci\s+)?run\s+(?:id\s+)?(\d{6,})\b")
#: A ratio next to a mutation word: "41/41 caught", "9/9 mutants".
_MUTATION_RATIO = re.compile(
    r"(?i)\b(\d{1,5})\s*/\s*(\d{1,5})\b(?=[^.\n]{0,60}\b(?:mutant|mutation|caught|battery|escaped)\b)"
    r"|\b(?:mutation|mutant|battery)\b[^.\n]{0,60}?\b(\d{1,5})\s*/\s*(\d{1,5})\b"
)
#: Words that mean an oracle came out red rather than green.
_RED_WORDS = re.compile(r"(?i)\b(?:escaped|not caught|failed|failing|red|refuted|did not hold)\b")

#: Executable material: a locator here can be run and can therefore be wrong.
_EXECUTABLE_SUFFIXES = (".py", ".sh", ".ts", ".js", ".sql", ".rb", ".go", ".rs")


def _kind_for(locator: str, resolved: str = "") -> EvidenceKind:
    """What kind of evidence a locator is, judged from where it actually lives.

    ``resolved`` is the repository path the locator turned out to name, which is
    what decides it. A registry writing a bare test name is naming a test, and
    classifying it by the shape of the string rather than by the file it
    resolves to made every such citation unfalsifiable — which made every
    criterion evidenced only that way come out VACUOUS.
    """
    low = str(locator or "").lower()
    where = str(resolved or "").lower()
    if "mutate" in low or "mutation" in low or "mutate" in where:
        return EvidenceKind.MUTATION_BATTERY
    if "::" in low:
        return EvidenceKind.TEST_NODE
    if re.match(r"\A(?:test|it|should)_", low):
        return EvidenceKind.TEST_NODE
    if where.startswith(("scripts/", "script/", "tools/", "bin/")) or low.startswith(
        ("scripts/", "script/", "tools/", "bin/")
    ):
        return EvidenceKind.PROBE
    target = where or low
    if "test" in target and target.endswith(".py"):
        return EvidenceKind.TEST_NODE
    if target.endswith(_EXECUTABLE_SUFFIXES):
        return EvidenceKind.STRUCTURAL_GUARD
    return EvidenceKind.AUTHORITY_CITATION


def _anti_vacuity_from(text: str) -> AntiVacuity | None:
    """Read a mutation battery's own numbers out of the prose that reports them.

    Nothing is invented: if the repository did not state a ratio, this returns
    ``None`` and the evidence simply carries no anti-vacuity metadata, which is
    different from carrying metadata that says it is fine.
    """
    match = _MUTATION_RATIO.search(text or "")
    if match is None:
        return None
    groups = [g for g in match.groups() if g is not None]
    if len(groups) < 2:
        return None
    observed, expected = int(groups[0]), int(groups[1])
    return AntiVacuity(
        control_green=None if not re.search(r"(?i)\bcontrol\b", text) else True,
        mutant_expected_red=expected,
        mutant_observed_red=observed,
        escaped_mutants=max(0, expected - observed),
        population_denominator=expected,
    )


def _locators_in(text: str) -> list[str]:
    """Every machine-checkable locator a piece of recorded evidence names.

    Four forms, because repositories use all four and reading only the tidiest
    one reports a phase full of well-evidenced criteria as having no evidence:
    a full node id, a path, a bare filename, and a bare test name. Whether any
    of them actually resolves is :class:`RepoIndex`'s question, not this one's.
    """
    blob = str(text or "")
    found: list[str] = []
    for match in _TEST_NODE.finditer(blob):
        found.append(f"{match.group(1)}::{match.group(2)}")
    for match in _REPO_PATH.finditer(blob):
        path = match.group(1)
        if not any(path in f for f in found):
            found.append(path)
    for match in _BARE_FILE.finditer(blob):
        name = match.group(1)
        if not any(name in f for f in found):
            found.append(name)
    for match in _BARE_SYMBOL.finditer(blob):
        symbol = match.group(1)
        if not any(symbol in f for f in found):
            found.append(symbol)
    seen: set[str] = set()
    unique: list[str] = []
    for item in found:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


class RepoIndex:
    """Where the files a piece of recorded evidence names actually live.

    A registry writes ``test_widget_lifecycle.py::test_a_closed_widget_refuses``
    because that is how a person refers to a test. The file is somewhere like
    ``eval/tests/test_widget_lifecycle.py``. Resolving the first against the
    working directory finds nothing, and reporting "the evidence cites something
    that is not in the tree" about a file that is plainly in the tree is a false
    EVIDENCE_GAP against every criterion in the phase.

    So a locator resolves either as a path or, when it names no directory, as a
    unique basename. *Unique* is load-bearing: two files with the same name are
    two files, and picking one would be guessing which test the evidence meant.
    """

    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)
        self._by_name: dict[str, list[str]] | None = None
        self._by_symbol: dict[str, list[str]] | None = None

    def _index(self) -> dict[str, list[str]]:
        if self._by_name is not None:
            return self._by_name
        import subprocess

        names: dict[str, list[str]] = {}
        try:
            proc = subprocess.run(
                ["git", "ls-files"],
                cwd=str(self.repo),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            paths = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        except (OSError, subprocess.SubprocessError):
            paths = []
        for path in paths:
            names.setdefault(path.rsplit("/", 1)[-1], []).append(path)
        self._by_name = names
        return names

    def _symbols(self) -> dict[str, list[str]]:
        """Every named test-like function in the tree, and where it is defined.

        One ``git grep`` rather than one per lookup. A repository that defines
        the same test name in two files gets no resolution for it, for the same
        reason a duplicate basename gets none: the citation is then ambiguous
        and picking is guessing.
        """
        if self._by_symbol is not None:
            return self._by_symbol
        import subprocess

        symbols: dict[str, list[str]] = {}
        try:
            proc = subprocess.run(
                [
                    "git",
                    "grep",
                    "-nE",
                    r"^[[:space:]]*(async def|def|func|fn)[[:space:]]+[A-Za-z_][A-Za-z0-9_]*",
                ],
                cwd=str(self.repo),
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            lines = proc.stdout.splitlines()
        except (OSError, subprocess.SubprocessError):
            lines = []
        pattern = re.compile(r"^([^:]+):\d+:\s*(?:async\s+def|def|func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)")
        for line in lines:
            match = pattern.match(line)
            if match is None:
                continue
            symbols.setdefault(match.group(2), []).append(match.group(1))
        self._by_symbol = symbols
        return symbols

    def resolve(self, locator: str) -> str:
        """The repository-relative path this locator names, or ``""``."""
        raw = str(locator or "").strip()
        path = normalize_repo_path(raw.split("::", 1)[0])
        if not path:
            return ""
        if "." in path.rsplit("/", 1)[-1] or "/" in path:
            try:
                if (self.repo / path).exists():
                    return path
            except OSError:
                return ""
            if "/" in path:
                return ""
            candidates = self._index().get(path, [])
            return candidates[0] if len(candidates) == 1 else ""
        # A bare name: resolved as a defined symbol, and only when it is unique.
        defined = self._symbols().get(path, [])
        return defined[0] if len(set(defined)) == 1 else ""


def _resolves(index: "RepoIndex", locator: str) -> bool:
    """Whether a locator names something that is actually in the repository."""
    return bool(index.resolve(locator))


# --------------------------------------------------------------------------
# The persisted state
# --------------------------------------------------------------------------


class PhaseClosureRecord(BaseModel):
    """Everything one phase acceptance attempt knows. The resume unit."""

    model_config = ConfigDict(extra="ignore")

    run_id: str = ""
    phase_id: str = ""
    attempt: int = 1
    state: ClosureState = ClosureState.NOT_STARTED
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)

    #: Frozen at the start of the attempt. Never rewritten inside one.
    criteria: CriterionSet = Field(default_factory=CriterionSet)
    criteria_fingerprint: str = ""

    candidate: dict[str, Any] = Field(default_factory=dict)
    evidence: EvidenceMap = Field(default_factory=EvidenceMap)
    findings: list[PhaseFinding] = Field(default_factory=list)
    residuals: ResidualLedger = Field(default_factory=ResidualLedger)

    #: Criteria only a session outside the build lineage may award. Persisted
    #: rather than recomputed, because a resumed controller has not re-read the
    #: repository yet — and a resume that forgot the phase owed an adjudication
    #: would walk straight past it to READY_FOR_ACCEPTANCE_COMMIT.
    independent_review_criterion_ids: list[str] = Field(default_factory=list)
    #: Every criterion a LATER gate settles — the external gate, the
    #: adjudication, the residual ledger. Persisted for the same reason: a
    #: resumed controller must not report them as "evidence missing".
    gate_criterion_ids: list[str] = Field(default_factory=list)

    external_requirement: ExternalRequirement = Field(default_factory=ExternalRequirement)
    external_evidence: ExternalEvidence | None = None
    #: Every external record ever offered, including the refused ones. A
    #: refusal is a fact worth keeping.
    external_history: list[ExternalEvidence] = Field(default_factory=list)

    adjudication: PhaseAdjudication | None = None
    #: Every adjudication response this attempt ever ingested, in order,
    #: including the ones a later response replaced. A malformed response is a
    #: fact about what a reviewer session actually returned, and it is the only
    #: record of where the independence was spent, so it is kept rather than
    #: overwritten — the same rule ``external_history`` follows for a refused
    #: CI record.
    adjudication_history: list[PhaseAdjudication] = Field(default_factory=list)
    builder_session_ids: list[str] = Field(default_factory=list)

    authority: dict[str, Any] = Field(default_factory=dict)
    ledger: PhaseLedger = Field(default_factory=PhaseLedger)
    #: One line per thing that happened, in order.
    history: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    #: The closure rule under which this record's stored dispositions were
    #: taken. Empty means "written before the rule was versioned", which is a
    #: mismatch like any other.
    decision_rule_version: str = ""

    def fingerprint(self) -> TreeFingerprint:
        return TreeFingerprint.from_dict(self.candidate)

    def note(self, line: str) -> None:
        if line and line not in self.history:
            self.history.append(f"{utcnow()}  {line}")

    def finding(self, finding_id: str) -> PhaseFinding | None:
        for f in self.findings:
            if f.finding_id == finding_id:
                return f
        return None

    @property
    def live_findings(self) -> list[PhaseFinding]:
        """Every finding that still applies. A withdrawn one is kept, not read.

        Withdrawn means the statement's subject was superseded — the only thing
        that can be withdrawn is this controller's own sentence about one
        adjudication response, and only a later response withdraws it. Nothing
        observed about the product is ever withdrawn.
        """
        return [f for f in self.findings if f.live]

    @property
    def blocking_findings(self) -> list[PhaseFinding]:
        return [f for f in self.live_findings if f.blocks_phase_acceptance]

    @property
    def inconsistent_findings(self) -> list[PhaseFinding]:
        """Findings that contradict the adjudication that carried them."""
        return [f for f in self.live_findings if f.contradicts_adjudication]

    @property
    def nonblocking_findings(self) -> list[PhaseFinding]:
        """Findings that are neither blocking, an authority gap, nor a contradiction.

        A gap is excluded because it is not debt: it stops the attempt, and
        listing it under "record and move on" says the opposite of what it
        means. A contradiction is excluded for the same reason and one more —
        printing it as debt would tell the founder to carry on past the fact
        that the adjudication does not agree with itself.
        """
        return [
            f
            for f in self.live_findings
            if not f.blocks_phase_acceptance
            and not f.contradicts_adjudication
            and f.classification is not FindingClass.AUTHORITY_GAP
        ]

    def adjudication_scores(self) -> AdjudicationScores | None:
        """What the INDEPENDENT adjudication scored, or nothing.

        ``None`` when there is no adjudication, it is not independent, or its
        response did not cover the frozen criterion contract — and that is not
        the same as an adjudication that scored nothing: a contradiction needs a
        score to contradict, and a score that does not count cannot be
        contradicted. Criteria the reviewer scored outside the frozen authority
        are dropped here, because they were never part of the bar this attempt
        is measured against.
        """
        adjudication = self.adjudication
        if adjudication is None or not adjudication.independent:
            return None
        if not adjudication.protocol_for(self.criteria).complete:
            return None
        passed = [
            r.criterion_id
            for r in adjudication.criterion_results
            if r.passed and not r.outside_authority
        ]
        failed = [
            r.criterion_id
            for r in adjudication.criterion_results
            if r.failed and not r.outside_authority
        ]
        return AdjudicationScores(passed=passed, failed=failed)

    @property
    def authority_gaps(self) -> list[PhaseFinding]:
        return [
            f for f in self.live_findings if f.classification is FindingClass.AUTHORITY_GAP
        ]


# --------------------------------------------------------------------------
# The controller
# --------------------------------------------------------------------------


class PhaseClosureController:
    """Drives one phase from implementation complete to ready for acceptance.

    Read-only against the product repository except for the one narrow write
    :mod:`~neyma_product_driver.acceptance_commit` performs, and only when a
    caller explicitly asks for it. Everything else here reads, records and
    decides.
    """

    def __init__(
        self,
        repo: Path,
        *,
        store: Any = None,
        phase_id: str = "",
        builder_session_ids: Sequence[str] = (),
        registry_paths: Sequence[str] | None = None,
        stale_verification_blocks: bool = False,
        acceptance_record_globs: Sequence[str] = (),
        status_restatement_globs: Sequence[str] = (),
        external_probe_command: str = "",
        external_probe_timeout_s: int = 120,
        emit: Callable[[str], None] | None = None,
    ) -> None:
        self.repo = Path(repo)
        self.store = store
        self.phase_id = phase_id
        self.builder_session_ids = [str(b) for b in builder_session_ids if str(b).strip()]
        self.registry_paths = registry_paths
        self.stale_verification_blocks = bool(stale_verification_blocks)
        self.acceptance_record_globs = list(acceptance_record_globs)
        self.status_restatement_globs = list(status_restatement_globs)
        self.external_probe_command = external_probe_command or ""
        self.external_probe_timeout_s = int(external_probe_timeout_s)
        self._emit = emit or (lambda _m: None)
        self._index = RepoIndex(self.repo)
        self.record = PhaseClosureRecord(
            run_id=getattr(store, "run_id", "") or "", phase_id=phase_id
        )
        self.authority: PhaseAuthority | None = None

    # -- persistence ------------------------------------------------------

    def save(self) -> None:
        self.record.updated_at = utcnow()
        self.record.decision_rule_version = CLOSURE_RULE_VERSION
        if self.store is not None:
            self.store.write_json(CLOSURE_FILE, self.record.model_dump(mode="json"))

    def load(self) -> PhaseClosureRecord | None:
        """Restore a previous attempt from the run store, if there is one."""
        if self.store is None:
            return None
        path = Path(self.store.run_dir) / CLOSURE_FILE
        if not path.exists():
            return None
        import json

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            self.record = PhaseClosureRecord.model_validate(raw)
        except Exception:
            return None
        self.phase_id = self.record.phase_id or self.phase_id
        if self.record.builder_session_ids and not self.builder_session_ids:
            self.builder_session_ids = list(self.record.builder_session_ids)
        self._migrate_dispositions()
        return self.record

    def _migrate_dispositions(self) -> None:
        """Re-take every stored decision when the rule that took them has moved.

        Nothing else about the record is touched: the criteria stay frozen, the
        evidence stays pointed at the tree it was observed against, and the
        findings keep every fact they recorded. What is re-derived is only what
        this module derives — whether each finding blocks, whether it contradicts
        its adjudication, and who repairs it — from the structured fields the
        finding already carries.

        Deliberately without the no-downgrade guard that
        :meth:`_reclassify_findings` applies. That guard stops a LATER STEP of
        one attempt from quietly softening an earlier refusal, which is a real
        failure. This is a different thing: the earlier refusal was taken under
        a rule this codebase has since decided was wrong, and keeping it would
        mean a repaired harness goes on reporting the defect it was repaired
        for. The change is written into the record's history, so a reader can
        see the refusal was re-taken rather than lost.
        """
        record = self.record
        if record.decision_rule_version == CLOSURE_RULE_VERSION:
            return
        previous = record.decision_rule_version or "(unversioned)"
        changed = self._rederive_findings()
        # The review criteria too. Under rule 2 an adjudication that answered
        # nothing was still recorded as an OBSERVATION of the criterion that
        # demands an independent review — observed and not established, which
        # reads as refuted — so the stored evidence map says the product failed
        # a review that never happened. Re-derived from the same stored
        # adjudication, so a run reopens eligible for an adjudication instead of
        # needing its evidence edited by hand.
        repointed = self._refresh_adjudication_evidence()
        # Rule 2's own sentence about the adjudication. It wrote exactly one,
        # under a fixed id, about whatever adjudication the record carried — so
        # it is stamped with that subject here and then retired by the ordinary
        # rule if the subject no longer holds. A migration is the one place that
        # may recognise an earlier writer by the shape of what it wrote.
        for finding in record.findings:
            if finding.finding_id.endswith("-ADJUDICATION-INCOMPLETE") and not (
                finding.about_adjudication
            ):
                finding.about_adjudication = _adjudication_identity(record.adjudication)
        if record.adjudication is not None and not record.adjudication.taken(record.criteria)[0]:
            # The statement was about an adjudication rule 3 does not consider
            # taken at all. What that response did is now said by the protocol
            # finding, under the layer that owns it, so the verification-owned
            # sentence about it is withdrawn rather than left to duplicate it.
            for finding in record.findings:
                if finding.finding_id.endswith("-ADJUDICATION-INCOMPLETE") and finding.live:
                    finding.withdrawn = (
                        "under closure rule 3 this response was not an adjudication at all; "
                        "the protocol finding says what happened, under the layer that owns it"
                    )
                    record.note(f"withdrawn, and kept: {finding.finding_id}")
        record.decision_rule_version = CLOSURE_RULE_VERSION
        record.note(
            f"closure rule {previous} -> {CLOSURE_RULE_VERSION}: "
            f"{len(changed)} of {len(record.findings)} findings re-derived"
            + (
                f", and the review evidence for {', '.join(repointed)} re-derived"
                if repointed
                else ""
            )
        )
        for line in changed:
            record.note(f"  re-derived: {line}")
        if record.state is not ClosureState.NOT_STARTED:
            self.decide()

    def _rederive_findings(self) -> list[str]:
        """Re-run the whole rule over every stored finding. Returns what moved."""
        record = self.record
        scores = record.adjudication_scores()
        changed: list[str] = []
        for finding in record.findings:
            before = (finding.blocks_phase_acceptance, finding.adjudication_inconsistency)
            if not finding.not_falsified_criterion_ids:
                finding.not_falsified_criterion_ids = _criteria_disclaimed_in(
                    " ".join([finding.summary, *finding.evidence]), finding.criterion_ids
                )
            classify_and_route(
                finding,
                record.criteria,
                scores=scores,
                stale_verification_blocks=self.stale_verification_blocks,
            )
            after = (finding.blocks_phase_acceptance, finding.adjudication_inconsistency)
            if before != after:
                changed.append(finding.brief())
        return changed

    # -- 1. preflight -----------------------------------------------------

    def preflight(
        self,
        *,
        suite_result: Any = None,
        gate: Any = None,
        implementation_evidence: Mapping[str, Sequence[str]] | None = None,
    ) -> PhaseClosureRecord:
        """The deterministic pass. Answers everything a machine can settle.

        Nothing here launches a session, and nothing here costs anything but
        reading. That is the point: an independent adjudication is the scarcest
        thing in this system, and it must not be spent discovering that a
        checkpoint never landed.

        ``implementation_evidence`` is how a run that BUILT the phase hands its
        candidate over: criterion id -> the locators its builder pointed at. See
        :meth:`_attach_implementation_evidence` for what they become, and what
        they cannot become.
        """
        record = self.record
        record.builder_session_ids = list(self.builder_session_ids)

        # Which tree is being looked at, captured before anything can return.
        # An AUTHORITY_GAP that reported "HEAD (none)" was describing a capture
        # it had skipped, and a preflight that cannot say which tree it read is
        # not a preflight.
        fingerprint = capture_fingerprint(self.repo)
        previous = record.fingerprint()
        record.candidate = fingerprint.to_dict()

        authority = resolve_phase_authority(
            self.repo,
            self.phase_id,
            **({"registry_paths": self.registry_paths} if self.registry_paths else {}),
        )
        self.authority = authority
        record.phase_id = authority.phase_id or self.phase_id
        record.authority = authority.to_dict()

        # (a) is there an acceptance authority at all?
        if not authority.declared:
            record.state = ClosureState.AUTHORITY_GAP
            record.criteria = authority.criteria
            record.criteria_fingerprint = ""
            self._add_finding(
                PhaseFinding(
                    finding_id=f"{record.phase_id or 'phase'}-AUTHORITY-GAP",
                    classification=FindingClass.AUTHORITY_GAP,
                    severity="blocker",
                    phase_id=record.phase_id,
                    summary=authority.problem
                    or "the repository states no acceptance criteria for this phase",
                    closure_condition=(
                        "a founder or architect states this phase's acceptance criteria in the "
                        "repository's own authority"
                    ),
                    source="phase-closure preflight",
                )
            )
            record.note("preflight stopped at AUTHORITY_GAP")
            self._rebuild_ledger()
            self.save()
            return record

        # (b) freeze, or verify the freeze.
        frozen = self._freeze(authority.criteria)
        if frozen:
            record.note(f"criteria frozen at {record.criteria_fingerprint}")

        # (c) did the tree move under a previous attempt, and does it matter?
        if previous.identity != "-/-/-" and not previous.matches(fingerprint):
            self._tree_moved(previous, fingerprint)

        # (d) evidence, from the repository's own record and this run's artifacts.
        self._assemble_evidence(authority, fingerprint, suite_result=suite_result, gate=gate)
        if implementation_evidence:
            self._attach_implementation_evidence(implementation_evidence, fingerprint)

        # (e) residuals: what is carried, what could be closed, what needs a person.
        self._classify_residuals(authority)

        # (f) the external gate.
        self._resolve_external_requirement(authority, fingerprint)

        record.independent_review_criterion_ids = [
            c.criterion_id
            for c in authority.independent_review_criteria
            if c.criterion_id in record.criteria.ids
        ]
        record.gate_criterion_ids = sorted(
            {
                c.criterion_id
                for group in (
                    authority.external_criteria,
                    authority.independent_review_criteria,
                    authority.residual_criteria,
                )
                for c in group
            }
            & record.criteria.ids
        )

        # (g) the criteria this controller's OWN gates settle. A criterion about
        #     CI is settled by the CI record, one about the phase review by the
        #     adjudication, and one about the carried residuals by the ledger.
        #     Asking for a test node to establish any of the three is asking the
        #     wrong question, and reporting them unevidenced made a well-run
        #     phase read as unverified.
        self._attach_gate_evidence(authority, fingerprint)

        # (h) the deterministic verdict on whether an adjudication is worth paying for.
        record.state = self._preflight_state(authority, fingerprint)
        self._rebuild_ledger()
        self.save()
        return record

    def _freeze(self, criteria: CriterionSet) -> bool:
        """Freeze the criterion set for this attempt, or check it has not moved.

        Returns True when this call did the freezing. When a set was already
        frozen and the repository's has since changed, the attempt does NOT
        adopt the new one: the bar moving mid-acceptance is an authority event,
        and it is recorded as one.
        """
        record = self.record
        current = criteria.fingerprint()
        if not record.criteria_fingerprint:
            record.criteria = criteria
            record.criteria_fingerprint = current
            return True
        if current == record.criteria_fingerprint:
            # Same demands, so the freeze holds — and the SCORING is re-read,
            # because the fingerprint deliberately excludes ``result``. Freezing
            # a criterion's outcome as well as its definition would mean a
            # criterion the repository scored during the attempt stayed PENDING
            # in the attempt's own copy for the rest of the acceptance.
            recorded = {c.criterion_id: c.result for c in criteria.criteria}
            for criterion in record.criteria.criteria:
                fresh = recorded.get(criterion.criterion_id)
                if fresh is not None and fresh != criterion.result:
                    record.note(
                        f"the repository re-scored {criterion.criterion_id}: "
                        f"{criterion.result} -> {fresh}"
                    )
                    criterion.result = fresh
            return False
        if current != record.criteria_fingerprint:
            self._add_finding(
                PhaseFinding(
                    finding_id=f"{record.phase_id or 'phase'}-CRITERIA-MOVED",
                    classification=FindingClass.AUTHORITY_GAP,
                    severity="blocker",
                    phase_id=record.phase_id,
                    summary=(
                        f"the phase's acceptance criteria changed during this attempt: frozen "
                        f"{record.criteria_fingerprint}, the repository now states {current}"
                    ),
                    closure_condition=(
                        "a founder or architect confirms which criterion set governs this "
                        "acceptance; the attempt is then re-frozen"
                    ),
                    criteria_fingerprint=record.criteria_fingerprint,
                    source="phase-closure preflight",
                )
            )
            record.note(
                f"criteria changed under the attempt ({record.criteria_fingerprint} -> {current})"
            )
        return False

    # -- 2. evidence ------------------------------------------------------

    def _assemble_evidence(
        self,
        authority: PhaseAuthority,
        fingerprint: TreeFingerprint,
        *,
        suite_result: Any = None,
        gate: Any = None,
    ) -> None:
        """Point every criterion at concrete evidence, or record that none does.

        The rule that matters here is the one about vacuity. A criterion whose
        entire evidence is the repository saying it passed has a citation, not a
        measurement, and it comes out of this as ``VACUOUS`` rather than as
        established. What promotes a citation to evidence is a locator that
        actually resolves — a test node, a probe, a battery — because that is
        something a reader can go and run.
        """
        record = self.record
        tree = fingerprint.identity
        existing = {(r.criterion_id, r.kind, r.locator) for r in record.evidence.refs}

        def add(ref: EvidenceRef) -> None:
            key = (ref.criterion_id, ref.kind, ref.locator)
            if key in existing:
                return
            existing.add(key)
            record.evidence.add(ref)

        for criterion in record.criteria.criteria:
            text = criterion.recorded_evidence or ""
            locators = _locators_in(text)
            resolvable = [loc for loc in locators if _resolves(self._index, loc)]
            missing = [loc for loc in locators if loc not in resolvable]

            for loc in resolvable:
                kind = _kind_for(loc, self._index.resolve(loc))
                add(
                    EvidenceRef(
                        criterion_id=criterion.criterion_id,
                        kind=kind,
                        locator=loc,
                        # The repository's own scoring is what says whether
                        # anything looked, and what it saw. A criterion the
                        # repository has not scored has an artifact and no
                        # observation — which is the ordinary state of a phase
                        # being closed for the first time, not a refutation.
                        observed=criterion.scored,
                        established=criterion.passed,
                        observed_at_tree=tree,
                        depends_on_paths=[
                            self._index.resolve(loc) or loc.split("::", 1)[0]
                        ],
                        detail=(
                            "the repository records this criterion "
                            f"{criterion.result} citing this artifact"
                        ),
                        anti_vacuity=(
                            _anti_vacuity_from(text)
                            if kind is EvidenceKind.MUTATION_BATTERY
                            else None
                        ),
                    )
                )

            if text and not resolvable:
                # A statement with nothing behind it that a reader could run.
                add(
                    EvidenceRef(
                        criterion_id=criterion.criterion_id,
                        kind=EvidenceKind.AUTHORITY_CITATION,
                        locator=criterion.authority or "(the repository's own record)",
                        observed=criterion.scored,
                        established=criterion.passed,
                        observed_at_tree=tree,
                        detail=(
                            "the repository states this criterion passed and names nothing a "
                            "reader can execute"
                        ),
                    )
                )

            for loc in missing:
                self._add_finding(
                    PhaseFinding(
                        finding_id=f"{criterion.criterion_id}-EVIDENCE-MISSING-{_slug(loc)}",
                        classification=FindingClass.EVIDENCE_GAP,
                        severity="major",
                        phase_id=record.phase_id,
                        summary=(
                            f"{criterion.criterion_id} cites {loc}, which is not in the "
                            "candidate tree"
                        ),
                        criterion_ids=[criterion.criterion_id],
                        evidence=[loc],
                        observed_at_tree=tree,
                        source="phase-closure evidence assembly",
                    )
                )

            if _RED_WORDS.search(text) and criterion.passed:
                # The repository's own evidence describes something coming out
                # red while recording a pass. Not a refutation on its own — the
                # sentence may be describing a mutant that was SUPPOSED to go
                # red — so it is a question for the adjudication, not a blocker.
                record.notes.append(
                    f"{criterion.criterion_id}: the recorded evidence uses failure language "
                    "while recording a PASS; the adjudication should read it"
                )

        # This run's own suite, when it ran one. Attached to the phase rather
        # than to a criterion: it is evidence the product works, and which
        # criterion it settles is the adjudication's call, not a regex's.
        if suite_result is not None:
            passed = sum(
                1
                for o in getattr(suite_result, "outcomes", [])
                if str(getattr(getattr(o, "outcome", ""), "value", "")) == "PASSED"
            )
            verified = gate is not None and not getattr(gate, "blocks_acceptance", True)
            record.notes.append(
                f"this run's scenario suite: {passed} scenario(s) passed; "
                f"acceptance gate {'VERIFIED' if verified else 'did not verify'}"
            )

    def _attach_implementation_evidence(
        self, evidence: Mapping[str, Sequence[str]], fingerprint: TreeFingerprint
    ) -> None:
        """Record where the building run says each criterion's implementation is.

        The hand-off from implementation to acceptance. A repository commonly
        keeps its criteria bare until the adjudication writes what it found, so
        a phase built to completion arrives here with nothing pointing at any
        criterion — and the preflight, correctly, will not pay a reviewer to go
        looking. The run that built the phase knows where each criterion's work
        is; this is where it says so.

        Every pointer is recorded as NOT OBSERVED: an artifact in the tree that
        no session outside the build lineage has run. That is exactly the
        preflight's UNOBSERVED — enough to be worth adjudicating, never enough
        to be established, and no path from here reaches a score. A pointer at
        something not in the tree is not recorded at all, and gate criteria
        (the review, the external verifier, the residual ledger) are never
        attached from a build: those are settled by their own authority.
        """
        record = self.record
        tree = fingerprint.identity
        gates = self._gate_criteria()
        # A pointer a later commit retired is re-pointed on this tree rather
        # than left standing as STALE: the builder is naming it again, now.
        existing = {
            (r.criterion_id, r.kind, r.locator) for r in record.evidence.refs if not r.stale
        }
        attached = 0
        for criterion_id, locators in evidence.items():
            if criterion_id not in record.criteria.ids or criterion_id in gates:
                continue
            for loc in locators:
                resolved = self._index.resolve(loc)
                if not resolved:
                    continue
                kind = _kind_for(loc, resolved)
                key = (criterion_id, kind, loc)
                if key in existing:
                    continue
                existing.add(key)
                record.evidence.add(
                    EvidenceRef(
                        criterion_id=criterion_id,
                        kind=kind,
                        locator=loc,
                        observed=False,
                        established=False,
                        observed_at_tree=tree,
                        depends_on_paths=[resolved],
                        detail=(
                            "pointed at by the run that built the phase; not observed by any "
                            "session outside the build lineage"
                        ),
                    )
                )
                attached += 1
        if attached:
            record.note(
                f"the implementing run pointed {attached} artifact(s) at "
                f"{len([c for c in evidence if c in record.criteria.ids])} criteria; "
                "recorded unobserved for the adjudication"
            )

    def _attach_gate_evidence(
        self, authority: PhaseAuthority, fingerprint: TreeFingerprint
    ) -> None:
        """Point the structurally-settled criteria at the thing that settles them.

        Each of these is falsifiable in exactly the way its gate is: a CI record
        that is red establishes nothing, an adjudication that refuses
        establishes nothing, and a ledger with a blocking row open establishes
        nothing. So they are recorded as real evidence with ``established``
        computed from the gate, and they go stale with the tree like everything
        else.
        """
        record = self.record
        tree = fingerprint.identity

        def replace(criterion_id: str, ref: EvidenceRef) -> None:
            record.evidence.refs = [
                r
                for r in record.evidence.refs
                if not (r.criterion_id == criterion_id and r.kind is ref.kind)
            ]
            record.evidence.add(ref)

        external = record.external_evidence
        external_ok = bool(
            external is not None and external.satisfies(record.external_requirement)[0]
        )
        for criterion in authority.external_criteria:
            if criterion.criterion_id not in record.criteria.ids:
                continue
            replace(
                criterion.criterion_id,
                EvidenceRef(
                    criterion_id=criterion.criterion_id,
                    kind=EvidenceKind.CI_JOB,
                    locator=(
                        external.url or external.run_id or external.brief()
                        if external is not None
                        else record.external_requirement.gate_name
                    ),
                    observed=external is not None,
                    established=external_ok,
                    observed_at_tree=tree,
                    detail=self._external_line(),
                ),
            )

        adjudication = record.adjudication
        # Whether an adjudication HAPPENED — which is the whole of what the
        # independent-review gate measures, and all of it is mechanical:
        # :meth:`PhaseAdjudication.taken` is this module's own record that the
        # session was outside the builder lineage, inherited no builder
        # conversation, read the exact candidate tree, was given the exact
        # frozen criteria, and answered the response contract.
        #
        # Rule 4 is the line below NOT reading the reviewer's own score for this
        # criterion. It used to: `established` was the reviewer's PASS, falling
        # back to a mechanical test only when the reviewer had not scored it —
        # and a protocol-complete response scores every frozen criterion, so the
        # mechanical path was unreachable. That asked the reviewer to certify
        # that Product Driver had constructed it independently, which is neither
        # the reviewer's fact nor one it can observe, and let an honest
        # CANNOT_DETERMINE refuse a review that had demonstrably happened.
        #
        # None of the independence checks is weakened by this: every one of them
        # is inside `taken`, and any of them failing leaves the gate
        # unestablished exactly as before.
        taken = bool(
            adjudication is not None and adjudication.taken(record.criteria)[0]
        )
        for criterion in authority.independent_review_criteria:
            if criterion.criterion_id not in record.criteria.ids:
                continue
            replace(
                criterion.criterion_id,
                EvidenceRef(
                    criterion_id=criterion.criterion_id,
                    kind=EvidenceKind.REVIEW,
                    locator=(
                        adjudication.reviewer_session_id
                        if adjudication is not None
                        else "(no adjudication taken)"
                    ),
                    observed=taken,
                    established=taken,
                    observed_at_tree=tree,
                    detail=self._gate_evidence_detail(criterion.criterion_id),
                ),
            )

        blocking = record.residuals.blocking
        for criterion in authority.residual_criteria:
            if criterion.criterion_id not in record.criteria.ids:
                continue
            replace(
                criterion.criterion_id,
                EvidenceRef(
                    criterion_id=criterion.criterion_id,
                    kind=EvidenceKind.INVARIANT_QUERY,
                    locator="the phase residual ledger",
                    # The ledger is always readable, so this is always observed.
                    observed=True,
                    established=not blocking,
                    observed_at_tree=tree,
                    detail=(
                        f"{len(record.residuals)} residual(s) recorded, "
                        f"{len(blocking)} blocking, "
                        f"{len(record.residuals.closable_now)} mechanically closable now"
                    ),
                ),
            )

    def _tree_moved(self, previous: TreeFingerprint, current: TreeFingerprint) -> None:
        """The tree changed. Decide whether that changes what is being accepted.

        There is exactly one change that does not: WRITING THE ACCEPTANCE RECORD
        ITSELF. The last step of a closure is a status-only edit, and treating
        it like any other change retires the CI record and the adjudication for
        the tree they were about — so the final state became unreachable by the
        act of reaching it. A status edit does not change the product being
        accepted; it records that it was.

        The exception is as narrow as the surface classifier is, and that
        classifier fails closed: an unfamiliar path is UNKNOWN, UNKNOWN is not
        an acceptance record, and one such path anywhere in the change sends the
        whole thing down the ordinary invalidation path.
        """
        from .acceptance_commit import Surface, classify_surface

        changed = _changed_between(self.repo, previous, current)
        record = self.record
        globs = self.acceptance_record_globs
        record_only = bool(changed) and all(
            classify_surface(path, acceptance_globs=globs) is Surface.ACCEPTANCE_RECORD
            for path in changed
        )
        if record_only:
            # Re-point the evidence at the tree it is now about, rather than
            # retiring it: the same measurements describe the same product.
            for ref in record.evidence.refs:
                if not ref.stale and ref.observed_at_tree == previous.identity:
                    ref.observed_at_tree = current.identity
            record.note(
                f"the tree moved from {previous.identity} to {current.identity}, and every "
                f"changed path is the acceptance record itself "
                f"({', '.join(changed[:6])}); the evidence still describes this product"
            )
            return
        self._invalidate_for_tree(previous, current)

    def _invalidate_for_tree(
        self, previous: TreeFingerprint, current: TreeFingerprint
    ) -> list[EvidenceRef]:
        """The tree moved. Retire the evidence that no longer describes it.

        Blast radius rather than a blanket sweep — evidence that declares the
        paths it depends on and whose paths are untouched survives — but the
        external gate is never narrowed: a CI record is about a commit, and a
        different commit is a different fact whatever changed in it.
        """
        record = self.record
        changed = _changed_between(self.repo, previous, current)
        retired = record.evidence.invalidate_for_tree(current.identity, changed)
        for ref in retired:
            self._add_finding(
                PhaseFinding(
                    finding_id=f"{ref.criterion_id}-STALE-{_slug(ref.locator)}",
                    classification=FindingClass.STALE_VERIFICATION,
                    severity="major",
                    phase_id=record.phase_id,
                    summary=(
                        f"{ref.locator or 'evidence'} for {ref.criterion_id} was observed "
                        f"against {previous.identity} and the candidate tree is now "
                        f"{current.identity}"
                    ),
                    criterion_ids=[ref.criterion_id],
                    evidence=[ref.locator],
                    observed_at_tree=current.identity,
                    source="phase-closure tree invalidation",
                )
            )
        if record.external_evidence is not None:
            record.note(
                f"the candidate tree moved to {current.identity}; the external record for "
                f"{record.external_evidence.sha[:12] or 'an earlier commit'} no longer describes it"
            )
            record.external_evidence = None
        if record.adjudication is not None:
            record.note(
                "the candidate tree moved; the independent adjudication describes an earlier "
                "implementation and no longer discharges the requirement"
            )
            record.adjudication = None
        record.note(
            f"the candidate tree moved from {previous.identity} to {current.identity}; "
            f"{len(retired)} evidence record(s) no longer describe it"
        )
        return retired

    # -- 3. residuals -----------------------------------------------------

    def _classify_residuals(self, authority: PhaseAuthority) -> None:
        """Carry the phase's residuals, and say which could be closed now.

        A residual is proposed as ``CLOSABLE_NOW`` only when its closure
        condition is something a machine actually observed: a named unit the
        repository now records complete, or a named artifact that is now in the
        tree. A closure condition written in terms of a decision is marked
        ``REQUIRES_FOUNDER_DECISION`` and stays there however much evidence
        arrives, because the evidence was never what was missing.
        """
        record = self.record
        # Rebuilt from the repository every preflight rather than accumulated.
        # The rows are the repository's — a residual it has since closed is
        # closed, and an attempt that kept its first reading would carry a
        # discharged row as an open one for the rest of the acceptance. Nothing
        # is lost by rebuilding: every mark this method makes is DERIVED from
        # the repository state below, so re-deriving it produces the same marks.
        record.residuals = authority.residuals
        completed = _completed_unit_ids(self.repo, self.registry_paths)
        for residual in record.residuals.residuals:
            if residual.status is ResidualStatus.CLOSED:
                continue
            if residual.needs_judgement:
                residual.status = ResidualStatus.REQUIRES_FOUNDER_DECISION
                residual.owner_layer = RepairLayer.FOUNDER_DECISION
                continue
            condition = residual.closure_condition or ""
            if not condition:
                continue
            satisfied_by = sorted(
                _units_named_in(condition, record.phase_id) & completed
            )
            if satisfied_by:
                record.residuals.mark_closable(
                    residual.residual_id,
                    f"the repository records {', '.join(satisfied_by)} complete, which is what "
                    f"{residual.residual_id} said would close it",
                )
                continue
            locators = [loc for loc in _locators_in(condition) if _resolves(self._index, loc)]
            if locators:
                record.residuals.mark_closable(
                    residual.residual_id,
                    f"the artifact its closure condition names is in the candidate tree: "
                    f"{locators[0]}",
                )

    # -- 4. the external gate ---------------------------------------------

    def _resolve_external_requirement(
        self, authority: PhaseAuthority, fingerprint: TreeFingerprint
    ) -> None:
        record = self.record
        criteria = [
            c
            for c in authority.external_criteria
            if c.criterion_id in record.criteria.ids
        ]
        requirement = requirement_from_criteria(criteria, expected_sha=fingerprint.head)
        # A tree that already carries an accepted external record for the same
        # commit keeps it; a new expected SHA replaces the requirement.
        record.external_requirement = requirement
        if not requirement.required:
            return
        evidence = record.external_evidence
        if evidence is not None:
            ok, _reason = evidence.satisfies(requirement)
            if ok:
                return
            record.external_evidence = None

    def fetch_external_evidence(self) -> ExternalEvidence | None:
        """Ask the configured probe, when there is one. Never invents a result."""
        record = self.record
        if not record.external_requirement.required:
            return None
        if not self.external_probe_command:
            return None
        evidence = run_probe(
            self.external_probe_command,
            expected_sha=record.external_requirement.expected_sha,
            cwd=self.repo,
            timeout_s=self.external_probe_timeout_s,
            gate_name=record.external_requirement.gate_name,
        )
        self.record_external_evidence(evidence)
        return evidence

    def record_external_evidence(self, evidence: ExternalEvidence) -> tuple[bool, str]:
        """Accept or refuse one external record. Refusal is recorded, not silent."""
        record = self.record
        record.external_history.append(evidence)
        ok, reason = evidence.satisfies(record.external_requirement)
        if ok:
            record.external_evidence = evidence
            record.note(f"external verification accepted: {reason}")
            if self.authority is not None:
                self._attach_gate_evidence(self.authority, record.fingerprint())
            self._rebuild_ledger()
            self.save()
            return True, reason

        if evidence.status is ExternalStatus.INFRASTRUCTURE:
            classification = FindingClass.CI_INFRASTRUCTURE_DEFECT
        elif not same_commit(evidence.sha, record.external_requirement.expected_sha):
            classification = FindingClass.STALE_VERIFICATION
        elif evidence.status is ExternalStatus.FAILURE:
            classification = FindingClass.PRODUCT_DEFECT
        else:
            classification = FindingClass.EVIDENCE_GAP

        self._add_finding(
            PhaseFinding(
                finding_id=(
                    f"EXTERNAL-{evidence.status.value}-{_slug(evidence.sha[:12] or 'nosha')}"
                ),
                classification=classification,
                severity="blocker",
                phase_id=record.phase_id,
                summary=f"external verification refused: {reason}",
                criterion_ids=list(record.external_requirement.criterion_ids),
                evidence=[evidence.url or evidence.run_id or evidence.brief()],
                # A red gate on the exact tree IS a mechanical demonstration
                # that the criterion demanding a green gate is false. A record
                # about another tree demonstrates nothing about this one.
                mechanically_demonstrated=(
                    classification is FindingClass.PRODUCT_DEFECT
                    and same_commit(evidence.sha, record.external_requirement.expected_sha)
                ),
                observed_at_tree=record.fingerprint().identity,
                source="external verification gate",
            )
        )
        record.note(f"external verification refused: {reason}")
        if self.authority is not None:
            self._attach_gate_evidence(self.authority, record.fingerprint())
        self._rebuild_ledger()
        self.save()
        return False, reason

    # -- 5. adjudication --------------------------------------------------

    def ingest_review(self, review: Any, *, reviewed_tree: str = "") -> PhaseAdjudication:
        """Turn one independent review into a phase adjudication. Pure.

        Separated from launching a session so the whole classification and
        independence path is testable without spending a model. The review's own
        criterion assessments are matched against the FROZEN set: anything else
        it scored is recorded as outside the current authority, and can never
        make the phase not-closable.

        Two things this does NOT do, and both of them are rule 3:

        * it does not replace a previous response. Whatever was ingested before
          moves into ``adjudication_history`` first, marked with what
          superseded it, so a malformed reply that cost a session's
          independence stays readable afterwards.
        * it does not read a response that failed the contract as a verdict
          about the product. A reply that omitted frozen criteria, duplicated
          them, or scored none of them has not said the phase failed and has
          not said it passed; its own scores and findings are recorded on the
          adjudication record and are not minted into criterion failures.
        """
        record = self.record
        fingerprint = record.fingerprint()
        tree = reviewed_tree or _review_tree(review) or fingerprint.identity

        results: list[CriterionVerdict] = []
        unrecognised: list[str] = []
        for assessment in list(getattr(review, "criteria_assessment", []) or []):
            raw = str(getattr(assessment, "criterion", "") or "").strip()
            matched = _match_criterion(raw, record.criteria)
            verdict = CriterionVerdict(
                criterion_id=matched or raw,
                verdict=str(getattr(assessment, "assessment", "") or "CANNOT_DETERMINE"),
                basis=str(getattr(assessment, "basis", "") or ""),
                outside_authority=matched is None,
            )
            results.append(verdict)
            if matched is None and raw:
                unrecognised.append(raw)

        adjudication = PhaseAdjudication(
            reviewer_session_id=str(getattr(review, "reviewer_session_id", "") or ""),
            builder_session_ids=list(record.builder_session_ids),
            inherited_builder_context=bool(
                getattr(review, "inherited_builder_context", False)
            ),
            reviewed_tree=tree,
            criteria_fingerprint=record.criteria_fingerprint,
            verdict=str(getattr(review, "verdict", "") or "INSUFFICIENT_EVIDENCE"),
            summary=str(getattr(review, "summary", "") or "")[:2000],
            criterion_results=results,
            evidence_reproduced=bool(getattr(review, "reproduced_runtime_evidence", False)),
            unrecognised_criteria=unrecognised,
        )
        adjudication.independence_problem = check_independence(
            reviewer_session_id=adjudication.reviewer_session_id,
            builder_session_ids=record.builder_session_ids,
            inherited_builder_context=adjudication.inherited_builder_context,
            reviewed_tree=tree,
            candidate_tree=fingerprint.identity,
            criteria_fingerprint=adjudication.criteria_fingerprint,
            frozen_fingerprint=record.criteria_fingerprint,
        )
        adjudication.protocol = check_adjudication_protocol(results, record.criteria)

        previous = record.adjudication
        # Every response this attempt has ever ingested, plus this one. Counted
        # from the history AND the current record, because the current one has
        # not been moved into the history yet.
        adjudication.attempt = (
            len(record.adjudication_history) + (1 if previous is not None else 0) + 1
        )
        if previous is not None:
            previous.superseded_by = (
                f"attempt {adjudication.attempt} by "
                f"{adjudication.reviewer_session_id or 'an unnamed session'}"
            )
            record.adjudication_history.append(previous)
            record.note(
                f"adjudication attempt {previous.attempt} was superseded and kept: "
                f"{previous.brief()}"
            )
        record.adjudication = adjudication
        if not adjudication.independent:
            self._add_finding(
                PhaseFinding(
                    finding_id=f"{record.phase_id or 'phase'}-REVIEW-NOT-INDEPENDENT",
                    classification=FindingClass.VERIFICATION_GAP,
                    severity="blocker",
                    phase_id=record.phase_id,
                    summary=(
                        "the phase adjudication does not count: "
                        + adjudication.independence_problem
                    ),
                    closure_condition=(
                        "a session that did not build this phase adjudicates the exact "
                        "candidate tree against the frozen criterion set"
                    ),
                    observed_at_tree=fingerprint.identity,
                    source="phase adjudication",
                )
            )

        for raw in unrecognised:
            self._add_finding(
                PhaseFinding(
                    finding_id=f"{record.phase_id or 'phase'}-OUTSIDE-AUTHORITY-{_slug(raw)}",
                    classification=FindingClass.NONBLOCKING_DEBT,
                    severity="minor",
                    phase_id=record.phase_id,
                    summary=(
                        f"the adjudication scored {raw!r}, which this phase's acceptance "
                        "authority does not contain; recorded, and it does not move the bar"
                    ),
                    unrecognised_criterion_ids=[raw],
                    closure_condition=(
                        "a founder or architect adds it to the phase's criteria if it should "
                        "bind a future acceptance"
                    ),
                    observed_at_tree=fingerprint.identity,
                    source="phase adjudication",
                )
            )

        self._retire_superseded_adjudication_statements()
        if adjudication.protocol.complete:
            self._ingest_review_findings(review, adjudication)
        else:
            # Fail closed, and fail on the right layer. A reply that did not
            # answer cannot be mined for product defects: classifying its prose
            # would invent a Neyma failure out of a Product Driver / reviewer
            # protocol failure, which is the one substitution this controller
            # exists to prevent. Everything the reply did say is on the
            # adjudication record and in the preserved response artifact.
            self._record_adjudication_protocol_failure()
        # Everything recorded before this adjudication was scored against no
        # score. Now there is one, and a finding that contradicts it must say so.
        self._reclassify_findings()
        if self.authority is not None:
            self._attach_gate_evidence(self.authority, fingerprint)
        else:
            # A resumed controller has not re-read the repository, so there is
            # no live authority to walk — but the review criteria are persisted,
            # and what this adjudication is worth to them has just changed.
            self._refresh_adjudication_evidence()
        record.note(f"phase adjudication ingested: {adjudication.brief()}")
        self._rebuild_ledger()
        self.save()
        return adjudication

    def _ingest_review_findings(self, review: Any, adjudication: PhaseAdjudication) -> None:
        """Classify what a reviewer found, and let nothing classify itself."""
        record = self.record
        tree = record.fingerprint().identity
        for index, raw in enumerate(list(getattr(review, "findings", []) or [])):
            text = str(getattr(raw, "finding", "") or "")
            evidence_path = str(getattr(raw, "evidence_path", "") or "")
            severity = str(getattr(raw, "severity", "major") or "major")
            reasoning = str(getattr(raw, "reasoning", "") or "")
            named = _criteria_named_in(f"{text} {reasoning}", record.criteria)
            classification = classify_review_finding(f"{text} {reasoning}")
            # Read from the FULL prose, not the truncated summary: the sentence
            # in which a reviewer exempts a criterion is as often in the
            # reasoning as in the finding, and it is the reviewer's own
            # statement about its own finding.
            disclaimed = _criteria_disclaimed_in(f"{text} {reasoning}", named)
            # "Mechanically demonstrated" is not a word count. It requires a
            # named criterion, a concrete evidence path, and language that says
            # something was observed rather than argued.
            demonstrated = bool(
                named
                and evidence_path.strip()
                and _DEMONSTRATION.search(f"{text} {reasoning}")
            )
            self._add_finding(
                PhaseFinding(
                    finding_id=f"{record.phase_id or 'phase'}-REVIEW-{index + 1:02d}",
                    classification=classification,
                    severity=severity,
                    phase_id=record.phase_id,
                    summary=text[:500],
                    criterion_ids=named,
                    not_falsified_criterion_ids=disclaimed,
                    evidence=[evidence_path] if evidence_path else [],
                    mechanically_demonstrated=demonstrated,
                    observed_at_tree=tree,
                    criteria_fingerprint=record.criteria_fingerprint,
                    source=f"phase adjudication {adjudication.reviewer_session_id or ''}".strip(),
                )
            )

        # A criterion the adjudication scored FAIL is the adjudication's own
        # refusal, and it is the one thing an adjudication exists to say — about
        # the criteria it owns. A FAIL on a criterion a structural gate owns is
        # a reviewer disagreeing with a measurement rather than taking one, so
        # it is recorded in full, attributed, and left unable to refute the gate
        # that owns the fact: minting it as a demonstrated product defect would
        # reinstate the override rule 4 removes, by the back door of a finding.
        # An independence defect that is real comes from :func:`check_independence`,
        # a red external record comes from the external evidence, and a blocking
        # residual comes from the ledger.
        for result in adjudication.criterion_results:
            if not result.failed or result.outside_authority:
                continue
            criterion = record.criteria.get(result.criterion_id)
            if criterion is None or not criterion.required:
                continue
            owner = self._gate_owner(result.criterion_id)
            if owner is not None:
                authority = GATE_AUTHORITY_NAMES.get(owner, "a structural gate")
                self._add_finding(
                    PhaseFinding(
                        finding_id=f"{result.criterion_id}-REVIEWER-DISSENT",
                        classification=FindingClass.VERIFICATION_GAP,
                        severity="major" if result.basis else "minor",
                        phase_id=record.phase_id,
                        about_adjudication=_adjudication_identity(adjudication),
                        summary=(
                            f"the independent adjudication scored {result.criterion_id} FAIL; "
                            f"{authority} owns that criterion and its own record is what "
                            f"settles it. The score is recorded and does not replace the "
                            f"gate: {result.basis[:300]}"
                        ),
                        evidence=[result.basis[:300]] if result.basis else [],
                        closure_condition=(
                            f"{authority} is re-read on the accepted tree; if the reviewer is "
                            "describing a real defect in it, that defect is demonstrated "
                            "against the gate rather than scored against the criterion"
                        ),
                        observed_at_tree=record.fingerprint().identity,
                        criteria_fingerprint=record.criteria_fingerprint,
                        source="phase adjudication",
                    )
                )
                continue
            self._add_finding(
                PhaseFinding(
                    finding_id=f"{result.criterion_id}-ADJUDICATED-FAIL",
                    classification=FindingClass.PRODUCT_DEFECT,
                    severity="blocker",
                    phase_id=record.phase_id,
                    summary=(
                        f"the independent adjudication scored required criterion "
                        f"{result.criterion_id} FAIL: {result.basis[:300]}"
                    ),
                    criterion_ids=[result.criterion_id],
                    evidence=[result.basis[:300]] if result.basis else [],
                    mechanically_demonstrated=True,
                    observed_at_tree=record.fingerprint().identity,
                    criteria_fingerprint=record.criteria_fingerprint,
                    source="phase adjudication",
                )
            )

    # -- 6. the decision --------------------------------------------------

    def decide(self) -> ClosureState:
        """The stop rule. Four things, and everything else is debt.

        A phase is ready to close when its canonical scope is built, every
        REQUIRED current criterion is satisfied, the required external
        verification is green on the exact tree, and no blocking product or
        safety defect remains. A reviewer's opinion that a guard could be nicer
        is not on that list and cannot be put on it.

        The order is the pipeline's order and it matters: the external gate is
        asked before the adjudication, because a reviewer asked to adjudicate a
        tree the verifier has not seen is being asked to substitute its
        judgement for a measurement — and the criterion sweep is asked last,
        because the two gates above it are how several of the criteria become
        satisfied in the first place.
        """
        record = self.record
        if record.state is ClosureState.AUTHORITY_GAP:
            return record.state
        if (
            self._already_accepted()
            and not record.blocking_findings
            and not record.inconsistent_findings
        ):
            record.state = ClosureState.ALREADY_ACCEPTED
            self._rebuild_ledger()
            return record.state

        def stop_at(state: ClosureState) -> ClosureState:
            record.state = state
            self._rebuild_ledger()
            return state

        # A demonstrated defect, or a residual the repository says blocks.
        if record.blocking_findings or record.residuals.blocking:
            return stop_at(ClosureState.BLOCKED)

        # An adjudication that says two incompatible things about one criterion.
        # Checked after the blocking test, because a demonstrated failure that
        # nothing contradicts is the more concrete fact and names the layer that
        # owns it; checked before everything else, because a contradiction means
        # the adjudication this attempt rests on cannot be read.
        if record.inconsistent_findings:
            return stop_at(ClosureState.ADJUDICATION_INCONSISTENT)

        # 1. the canonical scope is built.
        if self._checkpoints_short():
            return stop_at(ClosureState.PREFLIGHT_BLOCKED)

        # 2. the required external verification is green on THIS tree.
        if record.external_requirement.required:
            evidence = record.external_evidence
            if evidence is None or not evidence.satisfies(record.external_requirement)[0]:
                return stop_at(ClosureState.WAITING_FOR_EXTERNAL_VERIFICATION)

        # 3. the independent adjudication the phase's own criteria demand.
        #    A response that failed the contract is recorded as the protocol
        #    failure it is BEFORE the outstanding test, because that test then
        #    says the phase still owes an adjudication and returns here — so
        #    without this the founder would read READY_FOR_ADJUDICATION over a
        #    run that had already spent a reviewer, with no sentence saying what
        #    happened to it.
        self._record_adjudication_protocol_failure()
        if self._adjudication_outstanding():
            return stop_at(ClosureState.READY_FOR_ADJUDICATION)
        self._record_adjudication_gap()

        # 4. every required criterion is satisfied. Last, because the two gates
        #    above are what satisfy several of them, and because a criterion
        #    still unsatisfied once both have passed is a genuine block rather
        #    than a step that has not happened yet.
        if self.unsatisfied_required():
            return stop_at(ClosureState.BLOCKED)

        return stop_at(ClosureState.READY_FOR_ACCEPTANCE_COMMIT)

    def settle(self) -> ClosureState:
        """Take the stop rule over everything known so far, and persist it.

        The state :meth:`preflight` leaves behind is a reading of the tree taken
        BEFORE the external gate has answered and before the stop rule has run.
        It is the cheapest thing in the machine that can refuse, and it is not
        the state. Anything that asks "which transition is next?" must ask this
        instead, because the answer has to account for evidence that arrived
        after the preflight read the tree.

        That is not a distinction without a difference: a gate that went green
        in this invocation — or in the one before it, leaving the attempt
        resting at ``READY_FOR_ADJUDICATION`` — is invisible to the preflight's
        verdict, so a caller branching on it declined to take the very
        transition the record it then printed said was due.
        """
        state = self.decide()
        self.save()
        return state

    def _record_adjudication_protocol_failure(self) -> None:
        """Record that the reviewer's RESPONSE failed the adjudication contract.

        Owned by Product Driver, not by the product and not by the phase's
        verification. A reply that scored none of the frozen criteria says
        nothing about Neyma; what it says is that this system asked for an
        answer in a shape it did not get, and did not detect that it had not
        got one. So the finding is a harness defect, it routes to Product
        Driver, and it names no criterion — a protocol failure may not be
        allowed to demonstrate a criterion false, which is exactly the
        substitution that made a malformed reply read as a product refusal.

        It does not block: the phase stops because it still owes an
        adjudication, which is a resting place a later ``phase close`` can
        leave. One finding per response, so a second malformed reply is
        recorded as its own event rather than folded into the first.
        """
        record = self.record
        adjudication = record.adjudication
        if adjudication is None or not self._adjudication_demanded():
            return
        if not adjudication.independent:
            # A session that was not independent is refused by its own finding,
            # and saying its answer was also short of the contract would only
            # report the same non-event twice under two owners.
            return
        protocol = adjudication.protocol_for(record.criteria)
        if protocol.complete:
            return
        phase = record.phase_id or "phase"
        self._add_finding(
            PhaseFinding(
                finding_id=f"{phase}-ADJUDICATION-PROTOCOL-VIOLATION-{adjudication.attempt:02d}",
                classification=FindingClass.HARNESS_DEFECT,
                severity="blocker",
                phase_id=record.phase_id,
                summary=(
                    "the independent adjudication was not taken: "
                    f"{protocol.problem}. The session "
                    f"({adjudication.reviewer_session_id or 'unnamed'}) was independent and "
                    f"read the candidate tree, and its reply reported "
                    f"{adjudication.verdict}; a reply that does not cover the contract is "
                    "not a verdict about the product in either direction"
                    # What the session itself said about why, verbatim and
                    # short. A reply that ran out of turns and a reply that
                    # ignored the contract are the same shape and different
                    # repairs, and this is the only place the difference is
                    # written down.
                    + (
                        f'. The session reported: "{adjudication.summary[:200]}"'
                        if adjudication.summary
                        else ""
                    )
                ),
                closure_condition=(
                    "a fresh independent session adjudicates the exact candidate tree and "
                    "returns exactly one score — PASS, FAIL or CANNOT_DETERMINE — for each "
                    "of the frozen criteria"
                ),
                observed_at_tree=record.fingerprint().identity,
                criteria_fingerprint=record.criteria_fingerprint,
                source="phase adjudication protocol",
            )
        )
        record.note(
            f"adjudication attempt {adjudication.attempt} did not satisfy the response "
            f"contract: {protocol.problem}"
        )

    def _record_adjudication_gap(self) -> None:
        """Say out loud when a taken adjudication left a criterion unsettled.

        Recorded as a verification gap, owned by tests and guards rather than by
        the product: an adjudicator that could not determine a criterion is
        describing something nothing in the repository can currently show, and
        the repair is to make it showable. It does not itself block — the
        unsatisfied criterion does — but without it the founder reads BLOCKED
        with no sentence explaining which step fell short.

        Only for the criteria the RESPONSE owed, which is rule 4 reaching this
        sentence. A criterion a structural gate owns was never this reviewer's
        to settle, so "the adjudication did not settle the phase: it could not
        determine the criterion demanding an independent review" described a
        completed, independent, protocol-complete adjudication as a shortfall —
        over the one criterion the gate it came from had already established.
        :meth:`PhaseAdjudication.discharges` now measures the response against
        what it owned, and this finding follows it.
        """
        record = self.record
        adjudication = record.adjudication
        if adjudication is None or not self._adjudication_demanded():
            return
        if not adjudication.taken(record.criteria)[0]:
            # Not this finding's subject. A response that never answered has not
            # "left a criterion unsettled" — it left the phase owing an
            # adjudication, which _record_adjudication_protocol_failure says,
            # under the layer that actually owns it.
            return
        discharged, reason = adjudication.discharges(record.criteria)
        if discharged:
            self._withdraw_settled_adjudication_gap(reason)
            return
        self._add_finding(
            PhaseFinding(
                finding_id=f"{record.phase_id or 'phase'}-ADJUDICATION-INCOMPLETE",
                classification=FindingClass.VERIFICATION_GAP,
                severity="major",
                phase_id=record.phase_id,
                about_adjudication=_adjudication_identity(adjudication),
                summary=f"the independent adjudication did not settle the phase: {reason}",
                closure_condition=(
                    "the criteria it could not settle are made showable, and an "
                    "adjudication scores them"
                ),
                observed_at_tree=record.fingerprint().identity,
                criteria_fingerprint=record.criteria_fingerprint,
                source="phase adjudication",
            )
        )

    def _withdraw_settled_adjudication_gap(self, reason: str) -> None:
        """Retire a stored "it did not settle the phase" that no longer holds.

        The one case this exists for is a record written under closure rule 3,
        where a criterion a gate owns counted against the response that was
        never asked to settle it. Nothing is deleted: the finding keeps every
        word, gains the reason it stopped applying, and the withdrawal is
        written into the record's history — so a reader sees both that the
        sentence was made and why it is no longer the state.

        Only this module's own statement about the CURRENT adjudication. A
        statement about a superseded response is retired by its own rule, and
        nothing observed about the product is ever withdrawn here.
        """
        record = self.record
        current = _adjudication_identity(record.adjudication)
        for finding in record.findings:
            if not finding.finding_id.endswith("-ADJUDICATION-INCOMPLETE"):
                continue
            if not finding.live or finding.about_adjudication != current:
                continue
            finding.withdrawn = (
                "under closure rule 4 the criteria a structural gate owns are not this "
                f"response's to settle, and it settled everything it owned: {reason}"
            )
            record.note(
                f"withdrawn, and kept: {finding.finding_id} — it counted a gate-owned "
                "criterion against a response that was never asked to settle it"
            )

    def materialize_acceptance_record(self) -> Any:
        """Write the repository's own acceptance record, once nothing else is owed.

        ``READY_FOR_ACCEPTANCE_COMMIT`` is a statement that every prerequisite is
        already satisfied on the frozen candidate tree, so the only thing between
        it and a commit is that the repository's status record has not caught up
        with its own evidence. Nothing in the pipeline used to write it, and the
        commit preparation then reported a clean tree and refused — the last step
        was unreachable by the act of reaching it.

        What is written is bounded three ways, and none of them is this method's
        judgement: :mod:`~neyma_product_driver.acceptance_record` may only use
        vocabulary the repository already used on a unit it already accepted, may
        only say what this attempt's adjudication established, and may only touch
        a file that classifies as ``ACCEPTANCE_RECORD``. A repository that has
        never stated what an acceptance looks like produces ``AUTHORITY_GAP``
        here, exactly as one that states no criteria does at the preflight.

        The write moves the working tree, and that is the one movement that does
        not retire the evidence: see :meth:`_tree_moved`. Nothing is staged,
        nothing is committed and nothing is pushed.
        """
        from .acceptance_record import (
            AcceptanceRecordPlan,
            plan_acceptance_record,
            restore_candidate_tree,
            stale_restatement_gap,
            verify_acceptance_record,
            write_acceptance_record,
        )

        record = self.record
        if record.state is not ClosureState.READY_FOR_ACCEPTANCE_COMMIT:
            return AcceptanceRecordPlan(
                phase_id=record.phase_id,
                refusal=(
                    f"the phase is {record.state.value}, not READY_FOR_ACCEPTANCE_COMMIT. An "
                    "acceptance record states that every prerequisite was satisfied, so it may "
                    "not be written while one is outstanding"
                ),
            )

        previous = record.fingerprint()
        plan = plan_acceptance_record(
            self.repo,
            phase_id=record.phase_id,
            criteria=record.criteria,
            adjudication=record.adjudication,
            candidate=previous,
            registry_paths=self.registry_paths or (),
            acceptance_globs=self.acceptance_record_globs,
            declared_globs=self.status_restatement_globs,
            gate_established=self._gate_established(),
        )

        if plan.authority_gap and plan.existing_record:
            # The repository has not stated how Product Driver would write its
            # record — and the working tree already carries one somebody else
            # wrote. That is not a phase that must stop: it is a phase whose
            # record exists and still has to be validated as record-only.
            record.note(
                "Product Driver cannot write this repository's acceptance record "
                f"({plan.authority_gap}); the working tree already carries one at "
                + ", ".join(plan.existing_record)
            )
            self.save()
            return plan

        if plan.authority_gap:
            self._add_finding(
                PhaseFinding(
                    finding_id=f"{record.phase_id or 'phase'}-RECORD-AUTHORITY-GAP",
                    classification=FindingClass.AUTHORITY_GAP,
                    severity="blocker",
                    phase_id=record.phase_id,
                    summary=(
                        "the repository does not state how it records an acceptance: "
                        + plan.authority_gap
                    ),
                    closure_condition=(
                        "a founder or architect states how this repository records an accepted "
                        "phase — the status values, and what a passing criterion says"
                    ),
                    observed_at_tree=previous.identity,
                    criteria_fingerprint=record.criteria_fingerprint,
                    source="phase-closure acceptance record",
                )
            )
            record.state = ClosureState.AUTHORITY_GAP
            record.note(f"the acceptance record cannot be written: {plan.authority_gap}")
            self._rebuild_ledger()
            self.save()
            return plan

        if plan.refusal:
            record.note(f"the acceptance record was refused: {plan.refusal}")
            self.save()
            return plan

        if plan.already_recorded:
            record.note(
                "the repository already records this acceptance; nothing was written and "
                "nothing was duplicated"
            )
            self.save()
            return plan

        write_acceptance_record(self.repo, plan)
        if plan.refusal:
            record.note(f"the acceptance record was refused: {plan.refusal}")
            self.save()
            return plan

        # The repository's own guards over what was just written. A status diff
        # that turns them red is not a status diff the repository accepts.
        verify_acceptance_record(self.repo, plan)
        if plan.verification is not None:
            record.note(
                "the repository's own guards over the acceptance record: "
                + plan.verification.headline()
            )

        # Both problems reach the founder together. A record that leaves a
        # declared restatement stale and a record that turns a guard red are
        # each a reason this acceptance cannot be committed, and reporting one
        # at a time costs a round trip through a human for no reason.
        stale = stale_restatement_gap(plan)
        if stale or plan.refusal:
            restore_candidate_tree(self.repo, plan)
            plan.written = False
            gap = "; and ".join(part for part in (plan.refusal, stale) if part)
            plan.refusal = ""
            plan.authority_gap = gap
            self._add_finding(
                PhaseFinding(
                    finding_id=f"{record.phase_id or 'phase'}-RECORD-NOT-SELF-CONSISTENT",
                    classification=FindingClass.AUTHORITY_GAP,
                    severity="blocker",
                    phase_id=record.phase_id,
                    summary=gap,
                    closure_condition=(
                        "a founder or architect brings the repository's declared status "
                        "authorities into line with its machine record, and settles any guard "
                        "that asserts the state before the acceptance"
                    ),
                    observed_at_tree=previous.identity,
                    criteria_fingerprint=record.criteria_fingerprint,
                    source="phase-closure acceptance record",
                )
            )
            record.state = ClosureState.AUTHORITY_GAP
            record.note(f"the acceptance record was rolled back: {gap[:400]}")
            self._rebuild_ledger()
            self.save()
            return plan

        record.note(
            f"the acceptance record was written into {', '.join(plan.changed_paths)}: "
            + "; ".join(e.brief() for e in plan.edits[:6])
            + (" ..." if len(plan.edits) > 6 else "")
        )
        current = capture_fingerprint(self.repo)
        self._tree_moved(previous, current)
        record.candidate = current.to_dict()
        self._rebuild_ledger()
        self.save()
        return plan

    def routing(self) -> RoutingPlan:
        return route_findings(self.record.live_findings)

    # -- helpers ----------------------------------------------------------

    def _adjudication_outstanding(self) -> bool:
        """Whether this phase still owes an independent adjudication.

        Read from the persisted record first. ``self.authority`` is only
        populated by a preflight in this process, and a resumed controller that
        went straight to a decision would otherwise conclude the phase owed no
        adjudication at all.
        """
        if not self._adjudication_demanded():
            return False
        adjudication = self.record.adjudication
        if adjudication is None:
            return True
        # Outstanding means NOT TAKEN, and the two ways of not being taken are
        # the two halves of PhaseAdjudication.taken: the session was not
        # independent evidence about this tree, or its response never covered
        # the frozen criterion contract.
        #
        # What is deliberately NOT outstanding: an adjudication that was taken,
        # answered every frozen criterion, and could not determine some of them.
        # Asking for another because the answer was uncertain is how one honest
        # "I could not tell" becomes an unbounded series of reviewer sessions.
        # It leaves the phase with an unsatisfied criterion, which the stop
        # rule's last step answers.
        return not adjudication.taken(self.record.criteria)[0]

    def _retire_superseded_adjudication_statements(self) -> list[str]:
        """Withdraw this controller's sentences about an adjudication that moved on.

        "The adjudication did not settle the phase" is a statement about ONE
        response. When a later response replaces it, the sentence describes
        nothing: leaving it live reports a phase as short of an adjudication it
        now has, and — because a gap is owned by verification — it hands the
        product-verification layer work that no longer exists.

        Nothing is deleted. The finding keeps every word it recorded and gains
        the reason it stopped applying, so a reader sees that attempt 1 was
        judged incomplete AND that attempt 2 answered.

        Only statements. A finding about the product, and the protocol failure
        of the superseded response itself, are facts that a new response cannot
        make untrue, and they stay live.
        """
        record = self.record
        current = _adjudication_identity(record.adjudication)
        retired: list[str] = []
        for finding in record.findings:
            if not finding.about_adjudication or finding.withdrawn:
                continue
            if finding.about_adjudication == current:
                continue
            finding.withdrawn = (
                f"it was a statement about {finding.about_adjudication}, which "
                f"{current or 'no adjudication'} replaced"
            )
            retired.append(finding.finding_id)
        for finding_id in retired:
            record.note(f"withdrawn, and kept: {finding_id} — a later adjudication replaced it")
        return retired

    def _refresh_adjudication_evidence(self) -> list[str]:
        """Re-point the review criteria at what the stored adjudication is worth.

        The persisted ``independent_review_criterion_ids`` rather than the live
        authority, because this has to work on a resume that has not re-read the
        repository — which is exactly the case where a record written under an
        earlier rule is loaded and reopened.

        Only refs this module wrote, and never a stale one: evidence retired by
        a tree movement stays retired, because whether it described the product
        is a question the tree already answered.
        """
        record = self.record
        ids = [
            cid
            for cid in record.independent_review_criterion_ids
            if cid in record.criteria.ids
        ]
        if not ids:
            return []
        adjudication = record.adjudication
        # The same mechanical gate :meth:`_attach_gate_evidence` applies, for
        # the same reason: what an independent-review criterion asserts is that
        # this adjudication happened and counted, and `taken` is the whole of
        # that question. The reviewer's own score for it is recorded on the
        # adjudication and reported in the detail, never read as the answer.
        taken = bool(adjudication is not None and adjudication.taken(record.criteria)[0])
        changed: list[str] = []
        for cid in ids:
            detail = self._gate_evidence_detail(cid)
            for ref in record.evidence.for_criterion(cid):
                if ref.kind is not EvidenceKind.REVIEW or ref.stale:
                    continue
                if (ref.observed, ref.established) != (taken, taken):
                    changed.append(cid)
                ref.observed = taken
                ref.established = taken
                ref.detail = detail
        return _dedupe_ids(changed)

    def _adjudication_demanded(self) -> bool:
        """Whether the phase's own criteria call for an independent session."""
        if self.record.independent_review_criterion_ids:
            return True
        return bool(
            self.authority is not None and self.authority.independent_review_criteria
        )

    def _authority_facts(self) -> tuple[int, int, str, bool]:
        """Checkpoints expected and landed, the next phase, production state.

        From the live :class:`~neyma_product_driver.phase_authority.PhaseAuthority`
        when this process ran a preflight, and otherwise from the snapshot the
        attempt persisted. A resumed controller that rebuilt its ledger from a
        ``None`` authority reported a phase with zero checkpoints, which reads
        as a phase that built nothing.
        """
        authority = self.authority
        if authority is not None:
            return (
                authority.checkpoints_expected,
                len(authority.checkpoints),
                authority.next_phase,
                authority.production_enabled,
            )
        snapshot = self.record.authority or {}
        return (
            int(snapshot.get("checkpoints_expected", 0) or 0),
            len(list(snapshot.get("checkpoints", []) or [])),
            str(snapshot.get("next_phase", "") or ""),
            bool(snapshot.get("production_enabled", False)),
        )

    def _already_accepted(self) -> bool:
        if self.authority is not None:
            return self.authority.already_accepted
        return bool((self.record.authority or {}).get("already_accepted", False))

    def _checkpoints_short(self) -> bool:
        expected, landed, _next, _prod = self._authority_facts()
        return bool(expected) and landed < expected

    def _preflight_state(
        self, authority: PhaseAuthority, fingerprint: TreeFingerprint
    ) -> ClosureState:
        record = self.record
        if (
            authority.already_accepted
            and not record.blocking_findings
            and not record.inconsistent_findings
        ):
            return ClosureState.ALREADY_ACCEPTED
        if record.blocking_findings or record.residuals.blocking:
            return ClosureState.BLOCKED
        if record.inconsistent_findings:
            return ClosureState.ADJUDICATION_INCONSISTENT
        if self._checkpoints_short():
            return ClosureState.PREFLIGHT_BLOCKED
        non_instantiable = record.criteria.non_instantiable()
        unevidenced = self._preflight_blocking_gaps()
        if non_instantiable or unevidenced:
            # A reviewer must not be spent discovering this. It is not a
            # refusal of the phase — it is a statement that the preflight found
            # what a preflight is for.
            return ClosureState.PREFLIGHT_BLOCKED
        if record.external_requirement.required and record.external_evidence is None:
            return ClosureState.WAITING_FOR_EXTERNAL_VERIFICATION
        return ClosureState.READY_FOR_ADJUDICATION

    def _gate_criteria(self) -> set[str]:
        """Criteria settled by a gate this attempt has not reached yet.

        The external gate, the independent adjudication and the residual ledger
        each settle a criterion, and each of them happens LATER than the
        preflight. Counting them as "evidence missing" and refusing to proceed
        made the preflight block on the adjudication not having happened, so the
        adjudication was never launched — the closure loop could not start.
        """
        record = self.record
        # The structural classification of the FROZEN set is the answer, and it
        # is available on a resume that never re-read the repository. The
        # persisted list is unioned in rather than preferred, so a record frozen
        # under an earlier vocabulary keeps every gate it already recorded.
        ids = set(gate_owned(record.criteria))
        ids.update(record.gate_criterion_ids)
        authority = self.authority
        if authority is not None:
            for group in (
                authority.external_criteria,
                authority.independent_review_criteria,
                authority.residual_criteria,
            ):
                ids.update(c.criterion_id for c in group)
        return ids & record.criteria.ids

    def _gate_owner(self, criterion_id: str) -> CriterionKind | None:
        """Which structural gate owns one criterion, or ``None`` if none does.

        Read from the frozen criterion set's own identifiers — see
        :func:`gate_owned` — so a resumed controller that has not re-read the
        repository answers it the same way a fresh one does. The persisted gate
        list is consulted as well, and a criterion recorded as a gate under an
        earlier vocabulary keeps the kind it was recorded under where that can
        be recovered, because narrowing a gate mid-attempt would hand a
        reviewer authority the attempt never gave it.
        """
        record = self.record
        owners = gate_owned(record.criteria)
        if criterion_id in owners:
            return owners[criterion_id]
        if criterion_id not in set(record.gate_criterion_ids):
            return None
        if criterion_id in set(record.independent_review_criterion_ids):
            return CriterionKind.INDEPENDENT_REVIEW
        # Recorded as a gate by an earlier freeze whose kind cannot be recovered
        # from the identifiers. It is still a gate; which one is read off the
        # evidence this module attached for it.
        for ref in record.evidence.for_criterion(criterion_id):
            if ref.stale:
                continue
            if ref.kind is EvidenceKind.CI_JOB:
                return CriterionKind.EXTERNAL_VERIFICATION
            if ref.kind is EvidenceKind.REVIEW:
                return CriterionKind.INDEPENDENT_REVIEW
            if ref.kind is EvidenceKind.INVARIANT_QUERY:
                return CriterionKind.RESIDUAL_LEDGER
        return None

    def _gate_established(self) -> dict[str, str]:
        """Gate-owned criteria this attempt established, and what established them.

        Read from the evidence map, so the sentence handed to the repository's
        acceptance record is the same fact the closure decided on rather than a
        second derivation of it. A gate-owned criterion the gate has NOT
        established is absent, and the record is refused for it exactly as
        before — this widens nothing.
        """
        record = self.record
        out: dict[str, str] = {}
        for criterion in record.criteria.criteria:
            cid = criterion.criterion_id
            owner = self._gate_owner(cid)
            if owner is None:
                continue
            if record.evidence.status_for(cid) is not CriterionEvidenceStatus.ESTABLISHED:
                continue
            authority = GATE_AUTHORITY_NAMES.get(owner, "a structural gate")
            # Short on purpose. This sentence is written into the REPOSITORY's
            # own criterion row, where a paragraph is noise; the full mechanical
            # basis stays in the closure record's evidence detail and ledger,
            # which is where an auditor reads it.
            if owner is CriterionKind.INDEPENDENT_REVIEW:
                adjudication = record.adjudication
                who = (
                    adjudication.reviewer_session_id
                    if adjudication is not None and adjudication.reviewer_session_id
                    else "an unnamed session"
                )
                out[cid] = f"{authority} (session {who}, outside this run's builder lineage)"
            elif owner is CriterionKind.EXTERNAL_VERIFICATION:
                external = record.external_evidence
                out[cid] = f"{authority}" + (
                    f" ({external.brief()})" if external is not None else ""
                )
            else:
                out[cid] = (
                    f"{authority} ({len(record.residuals)} recorded, none of them blocking)"
                )
        return out

    def _gate_settlement_lines(self) -> list[str]:
        """One line per required gate-owned criterion: who owns it, what it says.

        So the record states the two facts separately, which is the whole point
        of rule 4 being visible rather than merely correct: the gate established
        the criterion, and the reviewer's own score for it was whatever it was.
        A reader who only sees a pass count cannot tell those apart, and the
        reviewer's answer must never be rewritten to agree.
        """
        record = self.record
        adjudication = record.adjudication
        lines: list[str] = []
        for criterion in record.criteria.required:
            cid = criterion.criterion_id
            owner = self._gate_owner(cid)
            if owner is None:
                continue
            status = record.evidence.status_for(cid)
            established = status is CriterionEvidenceStatus.ESTABLISHED
            line = (
                f"{cid}: {GATE_AUTHORITY_NAMES.get(owner, 'a structural gate')} "
                + ("ESTABLISHED it" if established else f"has NOT established it ({status.value})")
            )
            result = adjudication.result_for(cid) if adjudication is not None else None
            if result is not None:
                line += (
                    f"; the reviewer's own score was "
                    f"{result.verdict.strip().upper() or 'unreadable'}, recorded and not read "
                    "as the answer"
                )
            lines.append(line)
        return lines

    def _gate_evidence_detail(self, criterion_id: str) -> str:
        """What settled one gate-owned criterion, and what the reviewer said of it.

        Both halves, always, and kept apart. The gate's sentence is the finding
        of fact; the reviewer's own score is recorded beside it so the record
        stays append-only and a reader can see that the reviewer answered
        ``CANNOT_DETERMINE`` about its own independence AND that the gate
        established the criterion mechanically anyway. Nothing here rewrites
        what the reviewer said.
        """
        line = self._review_line()
        adjudication = self.record.adjudication
        if adjudication is None:
            return line
        result = adjudication.result_for(criterion_id)
        if result is None:
            return line
        return (
            f"{line}. The reviewer's own score for {criterion_id} was "
            f"{result.verdict.strip().upper() or 'unreadable'}; it is recorded and it is not "
            "the authority over a fact this gate establishes mechanically"
        )

    def _unevidenced_required(self, *, exclude_gates: bool = False) -> list[str]:
        """Required criteria whose evidence cannot support them.

        ``exclude_gates`` drops the criteria a later step settles, which is what
        the preflight's own decision must use; the report uses the full list, so
        a reader still sees every criterion whose evidence is not yet in.
        """
        record = self.record
        gates = self._gate_criteria() if exclude_gates else set()
        out: list[str] = []
        for criterion in record.criteria.required:
            if criterion.criterion_id in gates:
                continue
            status = record.evidence.status_for(criterion.criterion_id)
            if status in _UNEVIDENCED:
                out.append(criterion.criterion_id)
        return out

    def _preflight_blocking_gaps(self) -> list[str]:
        """Required criteria a reviewer must not be paid to discover a gap in.

        MISSING and VACUOUS only. ``UNOBSERVED`` — the artifact is in the tree
        and nothing has run it — is the ordinary state of a phase being closed
        for the first time, and it is exactly what the adjudication exists to
        settle; blocking on it would mean no phase could ever reach one.
        """
        record = self.record
        gates = self._gate_criteria()
        return [
            c.criterion_id
            for c in record.criteria.required
            if c.criterion_id not in gates
            and record.evidence.status_for(c.criterion_id)
            in (
                CriterionEvidenceStatus.MISSING,
                CriterionEvidenceStatus.VACUOUS,
                CriterionEvidenceStatus.STALE,
            )
        ]

    def _criterion_satisfied(self, criterion: Any) -> tuple[bool, str]:
        """Whether this attempt can show one criterion holds, and on what.

        A criterion is scored by the ACCEPTANCE, so a phase being closed for the
        first time has every criterion sitting at ``PENDING`` in its registry —
        that is what the closure is for. Requiring the repository to already
        record PASS would mean the only phase this controller could close is one
        that had already been closed.

        So there are three ways a criterion is satisfied, and one way it is not:

        * the independent adjudication scored it PASS — the strongest, because a
          session outside the build lineage re-derived it;
        * the evidence map establishes it on this exact tree, with evidence that
          could have come out the other way;
        * the repository itself records it passed — the authority's own record,
          which an adjudication re-derives rather than replaces.

        And it is not satisfied when something refutes it: evidence observed not
        to hold, an adjudication that scored it FAIL, or a blocking finding that
        names it. Those come first, because a refutation outranks every record.

        Rule 4 carves out the criteria a structural gate OWNS — the external
        verifier's, the independent review's own existence, the residual
        ledger's. For those, the gate's evidence is the only thing read: not the
        reviewer's score for it, which is a comment on a measurement rather than
        the measurement, and not the repository's own record of it either, since
        a repository writing PASS beside "independently reviewed by a
        non-builder" would be certifying exactly the thing an independent review
        exists not to take on trust. A blocking finding still refutes a
        gate-owned criterion: a finding is something somebody DEMONSTRATED, and
        the gates are falsifiable like everything else.
        """
        record = self.record
        cid = criterion.criterion_id

        if any(cid in f.criterion_ids for f in record.blocking_findings):
            return False, "a blocking finding demonstrates it is false"

        if any(cid in f.criterion_ids for f in record.inconsistent_findings):
            # Neither side of a contradiction may be read as the answer. The
            # criterion is not shown false and it is not shown to hold; what is
            # known is that the adjudication said both, and that is not a state
            # in which a phase may be accepted.
            return False, (
                "the adjudication scored it PASS and also attached a finding demonstrating "
                "it false; the contradiction is unresolved"
            )

        status = record.evidence.status_for(cid)
        owner = self._gate_owner(cid)
        if owner is not None:
            authority = GATE_AUTHORITY_NAMES.get(owner, "a structural gate")
            if status is CriterionEvidenceStatus.ESTABLISHED:
                return True, f"{authority} established it on this tree"
            return False, (
                f"{authority} owns it and has not established it on this tree "
                f"(evidence: {status.value})"
            )

        adjudication = record.adjudication
        result = (
            adjudication.result_for(cid)
            if adjudication is not None and adjudication.independent
            else None
        )
        if result is not None and result.outside_authority:
            result = None
        if result is not None and result.failed:
            return False, "the independent adjudication scored it FAIL"

        if status is CriterionEvidenceStatus.REFUTED:
            return False, "its own evidence was observed not to hold"

        if result is not None and result.passed:
            return True, "the independent adjudication scored it PASS"
        if status is CriterionEvidenceStatus.ESTABLISHED:
            return True, "falsifiable evidence establishes it on this tree"
        if criterion.passed:
            return True, f"the repository records it {criterion.result}"
        return False, (
            f"the repository records it {criterion.result} and nothing in this attempt "
            f"establishes it (evidence: {status.value})"
        )

    def unsatisfied_required(self) -> list[tuple[str, str]]:
        """Required criteria this attempt cannot show hold, and why not."""
        out: list[tuple[str, str]] = []
        for criterion in self.record.criteria.required:
            ok, reason = self._criterion_satisfied(criterion)
            if not ok:
                out.append((criterion.criterion_id, reason))
        return out

    def _refuted_required(self) -> list[str]:
        """Required criteria whose own evidence was observed NOT to hold."""
        record = self.record
        return [
            c.criterion_id
            for c in record.criteria.required
            if record.evidence.status_for(c.criterion_id) is CriterionEvidenceStatus.REFUTED
        ]

    def _add_finding(self, finding: PhaseFinding) -> PhaseFinding:
        record = self.record
        finding.criteria_fingerprint = finding.criteria_fingerprint or record.criteria_fingerprint
        if not finding.not_falsified_criterion_ids:
            # A finding that arrived without the structured exemption still says
            # what it says. Read from its own words, once, here, so every
            # producer of a finding gets the same reading.
            finding.not_falsified_criterion_ids = _criteria_disclaimed_in(
                " ".join([finding.summary, *finding.evidence]), finding.criterion_ids
            )
        classify_and_route(
            finding,
            record.criteria,
            scores=record.adjudication_scores(),
            stale_verification_blocks=self.stale_verification_blocks,
        )
        existing = record.finding(finding.finding_id)
        if existing is not None:
            # Re-observing something already recorded must not duplicate it, and
            # must not quietly downgrade it either.
            existing.blocks_phase_acceptance = (
                existing.blocks_phase_acceptance or finding.blocks_phase_acceptance
            )
            existing.adjudication_inconsistency = (
                existing.adjudication_inconsistency or finding.adjudication_inconsistency
            )
            if (
                finding.about_adjudication
                and finding.about_adjudication != existing.about_adjudication
            ):
                # The same statement, re-made about a LATER response. Its
                # subject is that response now, so it is live again and says
                # what the new response left unsettled — without this, a
                # withdrawn statement would swallow the one that replaced it and
                # the second unsettled adjudication would be reported by
                # nothing.
                existing.about_adjudication = finding.about_adjudication
                existing.withdrawn = ""
                existing.summary = finding.summary
                existing.closure_condition = (
                    finding.closure_condition or existing.closure_condition
                )
            return existing
        record.findings.append(finding)
        return finding

    def _reclassify_findings(self) -> None:
        """Re-derive every finding's disposition once an adjudication lands.

        A finding recorded before the adjudication was scored against no score
        at all, so a contradiction between the two could not be seen when it was
        written down. Re-deriving is safe in exactly one direction: a finding
        may gain a contradiction, and it may not lose a block. The second half
        is enforced here rather than trusted, because "a later step quietly
        downgraded an earlier refusal" is the failure this controller exists to
        make impossible.
        """
        record = self.record
        scores = record.adjudication_scores()
        for finding in record.findings:
            was_blocking = finding.blocks_phase_acceptance
            classify_and_route(
                finding,
                record.criteria,
                scores=scores,
                stale_verification_blocks=self.stale_verification_blocks,
            )
            if was_blocking and not finding.contradicts_adjudication:
                finding.blocks_phase_acceptance = True

    def _rebuild_ledger(self) -> PhaseLedger:
        """The compact record, recomputed from the state. Never hand-maintained."""
        record = self.record
        fingerprint = record.fingerprint()
        required = record.criteria.required
        adjudication = record.adjudication
        expected, landed, next_phase, production = self._authority_facts()

        ledger = PhaseLedger(
            phase=record.phase_id,
            state=record.state,
            candidate_tree=fingerprint.identity,
            candidate_head=fingerprint.head,
            tree_clean=not (fingerprint.tracked_dirty or fingerprint.untracked),
            checkpoints_expected=expected,
            checkpoints_landed=landed,
            criteria_fingerprint=record.criteria_fingerprint,
            criteria_required=len(required),
            # What this ATTEMPT can show, not merely what the registry already
            # records — a phase being closed for the first time records PENDING
            # everywhere, and reporting 0/17 over a fully evidenced phase is
            # exactly the sentence the founder had to correct by hand.
            criteria_pass=sum(1 for c in required if self._criterion_satisfied(c)[0]),
            criteria_fail=len(
                [c for c in required if not self._criterion_satisfied(c)[0] and (
                    c.failed
                    or record.evidence.status_for(c.criterion_id)
                    is CriterionEvidenceStatus.REFUTED
                    or any(c.criterion_id in f.criterion_ids for f in record.blocking_findings)
                )]
            ),
            criteria_contradicted=sorted(
                {
                    cid
                    for f in record.inconsistent_findings
                    for cid in f.criterion_ids
                    if cid in record.criteria.required_ids
                }
            ),
            criteria_gate_settled=self._gate_settlement_lines(),
            criteria_unevidenced=self._unevidenced_required(),
            criteria_non_instantiable=[c.criterion_id for c in record.criteria.non_instantiable()],
            blocking_residuals=len(record.residuals.blocking),
            nonblocking_residuals=len(record.residuals.nonblocking),
            closable_residuals=[r.residual_id for r in record.residuals.closable_now],
            external_required=record.external_requirement.required,
            external_status=(
                record.external_evidence.status.value
                if record.external_evidence is not None
                else ("AWAITING" if record.external_requirement.required else "NOT_REQUIRED")
            ),
            external_sha=record.external_requirement.expected_sha,
            independent_review_status=(
                adjudication.verdict if adjudication is not None else "NOT_TAKEN"
            ),
            independent_review_protocol=(
                ""
                if adjudication is None
                or not adjudication.independent
                or adjudication.protocol_for(record.criteria).complete
                else (
                    "INCOMPLETE — "
                    + adjudication.protocol_for(record.criteria).problem
                    + "; this adjudication was not taken and one is still owed"
                )
            ),
            reviewer_session_id=(
                adjudication.reviewer_session_id if adjudication is not None else ""
            ),
            builder_session_ids=list(record.builder_session_ids),
            inherited_builder_context=(
                adjudication.inherited_builder_context if adjudication is not None else False
            ),
            blocking_findings=[f.brief() for f in record.blocking_findings],
            nonblocking_findings=[f.brief() for f in record.nonblocking_findings],
            inconsistent_findings=[f.brief() for f in record.inconsistent_findings],
            authority_gaps=[f.brief() for f in record.authority_gaps],
            production_enabled=production,
            ready_for_acceptance_commit=(
                record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
            ),
            next_phase=next_phase,
            notes=list(record.notes),
        )
        record.ledger = ledger
        return ledger

    # -- reporting --------------------------------------------------------

    def preflight_block(self) -> str:
        """The preflight's own answers, in the order a reader needs them."""
        record = self.record
        fingerprint = record.fingerprint()
        expected, landed, _next, _prod = self._authority_facts()
        required = record.criteria.required
        evidenced = [
            c.criterion_id
            for c in required
            if record.evidence.status_for(c.criterion_id) is CriterionEvidenceStatus.ESTABLISHED
        ]
        missing = self._unevidenced_required()
        refuted = self._refuted_required()
        gated = sorted(self._gate_criteria() & {c.criterion_id for c in required})
        lines = [
            f"PHASE:                         {record.phase_id or '(none resolved)'}",
            f"CHECKPOINTS EXPECTED:          {expected}",
            f"CHECKPOINTS LANDED:            {landed}",
            f"REQUIRED CRITERIA:             {len(required)}",
            f"CRITERIA WITH EVIDENCE:        {len(evidenced)}",
            f"CRITERIA WITH MISSING EVIDENCE:{len(missing)}"
            + (f"  ({', '.join(missing[:6])})" if missing else ""),
            f"CRITERIA REFUTED BY EVIDENCE:  {len(refuted)}"
            + (f"  ({', '.join(refuted[:6])})" if refuted else ""),
            f"CRITERIA SETTLED BY A LATER GATE:{len(gated)}"
            + (f"  ({', '.join(gated[:6])})" if gated else ""),
            *[f"  GATE: {line}" for line in self._gate_settlement_lines()],
            f"NON-INSTANTIABLE CRITERIA:     {len(record.criteria.non_instantiable())}",
            f"OPEN BLOCKING RESIDUALS:       {len(record.residuals.blocking)}",
            f"OPEN NONBLOCKING RESIDUALS:    {len(record.residuals.nonblocking)}",
            f"CI REQUIRED?:                  "
            f"{'yes' if record.external_requirement.required else 'no'}",
            f"CI EVIDENCE CURRENT?:          {self._external_line()}",
            f"TREE CLEAN?:                   "
            f"{'yes' if not (fingerprint.tracked_dirty or fingerprint.untracked) else 'no'}"
            f"  ({fingerprint.describe()})",
            f"INDEPENDENT REVIEW READY?:     {self._review_line()}",
            f"REQUIRED CRITERIA SATISFIED:   "
            f"{len(required) - len(self.unsatisfied_required())} of {len(required)}",
            f"READY_FOR_ADJUDICATION?:       "
            f"{'yes' if record.state is ClosureState.READY_FOR_ADJUDICATION else 'no'}"
            f"  [{record.state.value}]",
        ]
        return "\n".join(lines)

    def _external_line(self) -> str:
        record = self.record
        if not record.external_requirement.required:
            return "not required"
        evidence = record.external_evidence
        if evidence is None:
            return f"no — nothing supplied for {record.external_requirement.expected_sha[:12]}"
        ok, reason = evidence.satisfies(record.external_requirement)
        return ("yes — " if ok else "no — ") + reason

    def _review_line(self) -> str:
        demanded = bool(self.record.independent_review_criterion_ids) or bool(
            self.authority is not None and self.authority.independent_review_criteria
        )
        if not demanded:
            return "no phase criterion demands one"
        adjudication = self.record.adjudication
        if adjudication is None:
            return "owed, and none has been taken for this tree"
        if not adjudication.independent:
            return f"taken and it does not count — {adjudication.independence_problem}"
        protocol = adjudication.protocol_for(self.record.criteria)
        if not protocol.complete:
            return (
                "still owed — a reviewer session was spent and its response did not cover "
                f"the frozen criterion contract: {protocol.problem}"
            )
        # Past the two refusals above, the gate is established: independence and
        # a covered response contract are the whole of what it measures, and
        # they are exactly what the two returns before this one rule out. So the
        # gate's own sentence comes first — it is the mechanical fact — and what
        # the reviewer scored is the next sentence, settling what the reviewer
        # owns and nothing else.
        discharged, reason = adjudication.discharges(self.record.criteria)
        gate = (
            "yes — the independent-review gate is established: "
            f"session {adjudication.reviewer_session_id or 'unnamed'} was outside the "
            f"builder lineage, inherited no builder context, read {adjudication.reviewed_tree} "
            f"against criteria {adjudication.criteria_fingerprint}, and covered the response "
            "contract"
        )
        return gate + (
            f". It settled the criteria it owns: {reason}"
            if discharged
            else f". It did not settle every criterion it owns — {reason}"
        )


# --------------------------------------------------------------------------
# The adjudication prompt
# --------------------------------------------------------------------------


def phase_adjudication_prompt(
    record: PhaseClosureRecord,
    *,
    authority: PhaseAuthority | None = None,
    evidence_dir: str = "",
    repository_context: str = "",
    policy: Any = None,
) -> str:
    """What a fresh session is asked, when it adjudicates a whole phase.

    Three instructions here are not decoration and are the reason phase closure
    used to run forever by hand:

    * **the criterion set is closed.** The reviewer scores exactly these
      criteria. It may say anything it likes about anything else, and that will
      be recorded as a finding outside the current authority — it will not be
      read as a criterion, because widening the bar during an acceptance is an
      authority change and authority is not the reviewer's.
    * **a refusal must be demonstrated.** "I think this is wrong" is a
      question; "I ran this and it came out red" is a refusal. Only the second
      can block, and the prompt says so, so a reviewer knows what its own words
      will do.
    * **do not fix anything.** The session is read-only by construction, and
      being told why keeps it from spending its pass proposing patches.
    """
    fingerprint = record.fingerprint()
    lines: list[str] = [
        "=== INDEPENDENT PHASE ADJUDICATION ===",
        "",
        f"You are adjudicating phase {record.phase_id or '(unnamed)'}. You did not build it, "
        "you are not in the conversation that did, and you are not being asked to fix "
        "anything. You are being asked one question per criterion.",
        "",
        "--- THE EXACT TREE YOU ARE ADJUDICATING ---",
        f"  {fingerprint.describe()}",
        "",
        "This adjudication is evidence about THAT tree and no other. Check it yourself "
        "before you rely on it. If the implementation changes afterwards, this "
        "adjudication stops describing it and another is taken.",
        "",
        "--- THE ACCEPTANCE CRITERIA, WHICH ARE CLOSED FOR THIS ADJUDICATION ---",
        f"  criteria fingerprint: {record.criteria_fingerprint}",
        "",
    ]
    gates = gate_owned(record.criteria)
    for criterion in record.criteria.criteria:
        status = record.evidence.status_for(criterion.criterion_id)
        owner = gates.get(criterion.criterion_id)
        lines.append(
            f"  {criterion.criterion_id}  [{'required' if criterion.required else 'optional'}, "
            f"repository records {criterion.result}, evidence {status.value}"
            + (
                f", SETTLED BY {GATE_AUTHORITY_NAMES.get(owner, 'a structural gate').upper()}"
                if owner is not None
                else ""
            )
            + "]"
        )
        if criterion.name:
            lines.append(f"      name: {criterion.name}")
        if criterion.requirement:
            lines.append(f"      demands: {criterion.requirement[:600]}")
        refs = record.evidence.for_criterion(criterion.criterion_id)
        for ref in refs[:6]:
            lines.append(f"      evidence: {ref.brief()}")
    lines += [
        "",
        "SCORE EXACTLY THESE CRITERIA AND NO OTHERS.",
        "",
    ]
    if gates:
        lines += [
            "--- THE CRITERIA MARKED 'SETTLED BY' ARE NOT YOURS TO AWARD ---",
            "",
            "Some criteria above are settled by a gate outside this adjudication: an "
            "external verifier's record of this exact commit, the residual ledger, and — "
            "for the criterion that demands an independent review by a non-builder — this "
            "harness's own construction of you. That last one is worth being explicit "
            "about, because you are the one participant who cannot check it: whether you "
            "were built outside the build lineage, inherited none of the builder's "
            "conversation, were handed this exact tree and these exact frozen criteria, "
            "and answered the response contract is measured mechanically, by the harness, "
            "and it is already measured by the time you read this.",
            "",
            "Still score them — the response contract below is one entry per criterion and "
            "an omission is not an answer. CANNOT_DETERMINE is the honest score for a fact "
            "you cannot observe, and scoring it that way costs the phase nothing: your "
            "score for a gate-settled criterion is recorded and reported beside the gate's "
            "own finding, and it does not establish the criterion or refuse it. If you "
            "believe a gate is actually wrong — the external record is not this commit, a "
            "blocking residual is open — say so as a FINDING with what you ran, which is "
            "how a measurement is challenged.",
            "",
        ]
    lines += [
        "You will very likely see something worth improving that no criterion above "
        "demands — a guard that could be stronger, a test that could be cleaner, a "
        "scanner that would be useful. Say so: it is recorded as carried debt and it is "
        "genuinely valuable. It is NOT a criterion, and it will not stop this phase, "
        "because the set of demands was fixed before you were asked and changing it is "
        "the founder's decision rather than yours.",
        "",
        "A criterion is FAIL only when you can DEMONSTRATE it is false — a command you "
        "ran that came out red, an identity that does not match, an invariant you "
        "observed break. An argument that it might be false is CANNOT_DETERMINE, and "
        "CANNOT_DETERMINE is an honest answer. Do not round either way.",
        "",
        "Your scoring and your findings are read as ONE answer, and they must agree. If "
        "you attach a finding to a criterion you scored PASS, say in the finding itself "
        "that it does not fail that criterion — a finding that names a criterion and "
        "reads as a refutation of it, against your own PASS, is a contradiction, and the "
        "closure will stop and come back to you rather than guess which half you meant. "
        "If the finding really does show the criterion is false, score it FAIL.",
        "",
    ]
    if authority is not None:
        lines += [
            "--- WHAT THE REPOSITORY SAYS ABOUT THIS PHASE ---",
            authority.summary_block(),
            "",
        ]
        if authority.acceptance_contract:
            lines += [
                "--- THE PHASE'S ACCEPTANCE CONTRACT, IN THE REPOSITORY'S OWN WORDS ---",
                authority.acceptance_contract[:3000],
                "",
            ]
        landed = authority.checkpoints
        if landed:
            lines.append(
                f"--- CHECKPOINTS LANDED ({len(landed)} of "
                f"{authority.checkpoints_expected or len(landed)}) ---"
            )
            for cp in landed[:20]:
                lines.append(
                    f"  {cp.checkpoint_id}  {cp.state or 'no state recorded'}  "
                    f"{cp.candidate_commit[:12]}"
                )
            lines.append("")
    if record.external_requirement.required:
        lines += [
            "--- EXTERNAL VERIFICATION ---",
            f"  gate: {record.external_requirement.gate_name}",
            f"  expected commit: {record.external_requirement.expected_sha}",
            f"  status: {record.external_evidence.brief() if record.external_evidence else 'nothing supplied'}",
            "",
        ]
    residuals = record.residuals
    if len(residuals):
        lines += [
            f"--- CARRIED RESIDUALS ({len(residuals)}; "
            f"{len(residuals.blocking)} blocking) ---",
            *[f"  {r.brief()}" for r in list(residuals)[:25]],
            "",
        ]
    if repository_context:
        lines += ["--- REPOSITORY AUTHORITY ---", repository_context[:8000], ""]
    if evidence_dir:
        lines += [f"--- RUN EVIDENCE ---", f"  {evidence_dir}", ""]
    if policy is not None and getattr(policy, "vocabulary_block", None) is not None:
        lines += [policy.vocabulary_block(), ""]
    required_count = len(record.criteria.criteria)
    lines += [
        "--- WHAT TO RETURN ---",
        "A JSON object with:",
        '  verdict: "SUPPORTED" | "NOT_SUPPORTED" | "INSUFFICIENT_EVIDENCE"',
        "  summary: what you concluded and on what basis",
        "  criteria_assessment: one entry per criterion above, with "
        '{criterion, assessment: "PASS"|"FAIL"|"CANNOT_DETERMINE", basis}',
        "  findings: anything material, with {finding, severity, evidence_path, reasoning}",
        "  evidence_reproduced: whether you ran verification yourself",
        "",
        "Use the criterion IDs exactly as written above. Do not invent criteria, do not "
        "merge two into one, and do not omit one because it looked settled.",
        "",
        "THE RESPONSE CONTRACT, WHICH IS CHECKED MECHANICALLY:",
        f"  criteria_assessment MUST contain exactly {required_count} entries — one for "
        f"each of the {required_count} criterion IDs listed above, each appearing exactly "
        "once, each scored PASS, FAIL or CANNOT_DETERMINE.",
        "",
        "If you run short of turns, or cannot verify something, or are unsure: SCORE IT "
        "CANNOT_DETERMINE. That is a complete answer and an honest one. An omitted "
        "criterion, a duplicated one, or an empty criteria_assessment is NOT an answer — "
        "it is not read as a failure of the product, it is read as this adjudication not "
        "having happened, and the work of it is spent for nothing. Budget your session so "
        "that the scored list is returned: leave yourself the turn to answer.",
    ]
    return "\n".join(lines)


def adjudication_correction_prompt(
    record: PhaseClosureRecord, protocol: AdjudicationProtocol
) -> str:
    """One bounded corrective request, to the session that already answered.

    What this may and may not do is the whole design. It may not restate the
    criteria, relax what PASS means, or hint at an answer — an adjudication
    corrected into agreement is not an adjudication. All it may do is say that
    the response did not cover the contract, name the ids that are missing,
    duplicated or unreadable, and repeat that ``CANNOT_DETERMINE`` is a real and
    complete score. The session is the SAME one: the same fresh, read-only,
    non-inheriting session that has already read the tree, so nothing about
    independence, the tree, or the frozen criteria changes by asking again.

    Exactly once. A second corrective request would be the beginning of the
    unbounded loop this whole controller exists to end; if the second response
    is still short of the contract, the adjudication is owed and a later
    ``phase close`` launches a genuinely fresh session.
    """
    outstanding = protocol.outstanding_ids()
    lines = [
        "=== YOUR RESPONSE DID NOT COVER THE ADJUDICATION CONTRACT ===",
        "",
        "This is not a disagreement with your judgement. It is a statement about the "
        "shape of the reply: the phase adjudication asks for exactly one score per "
        f"frozen criterion, and {protocol.brief()}",
        "",
        "Nothing about the question has changed. The criteria are the same closed set "
        f"(fingerprint {record.criteria_fingerprint}), the tree is the same one you read "
        f"({record.fingerprint().describe()}), the bar has not moved, and you are still "
        "read-only and still not being asked to fix anything.",
        "",
        "Return the SAME JSON object, with criteria_assessment covering every criterion "
        f"exactly once — all {len(protocol.expected)} of them:",
        *[f"  {cid}" for cid in protocol.expected],
        "",
    ]
    if outstanding:
        lines += [
            "The entries that were missing, duplicated or unscorable as written:",
            *[f"  {cid}" for cid in outstanding],
            "",
        ]
    lines += [
        "CANNOT_DETERMINE is a real score and a complete answer. If you did not verify a "
        "criterion, could not reach the evidence for it, or are not sure, score it "
        "CANNOT_DETERMINE with a basis saying what stopped you. Do NOT upgrade anything "
        "to PASS to fill the list, do NOT downgrade anything to FAIL, and do not change a "
        "score you already reached — keep every assessment you already made exactly as "
        "you made it and add the ones that are absent.",
        "",
        "A criterion is still FAIL only if you can demonstrate it false, and your "
        "findings and your scores must still agree with each other.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Classification of a reviewer's prose
# --------------------------------------------------------------------------

#: Language that says something was observed rather than argued. A finding that
#: cannot point at an observation cannot make a criterion false.
_DEMONSTRATION = re.compile(
    r"(?i)\b(?:reproduc\w+|observed|ran\b|executed|exit\s*code|fails?\b|failed|failing|"
    r"returns?\b|returned|raises?\b|raised|asserts?\s+fail|red\b|traceback|"
    r"stack\s?trace|output\s+was|actual(?:ly)?\b|mismatch|not\s+equal|differs?\b)\b"
)

#: Language in which a reporter says a finding does NOT make a criterion false.
#: Two shapes, both seen in real adjudications: a negated falsification verb
#: ("they do not fail AC-12", "this does not violate the criterion"), and a
#: contrast that names what it is instead ("carried debt rather than an AC-12
#: violation"). Nothing looser: "unrelated to", "distinct from" and "out of
#: scope" are things a reporter says about the SITES it looked at as often as
#: about the criterion, and reading either as an exemption would let a real
#: refutation be talked out of blocking.
#: Words that end a clause. They may not sit between the negation and the verb
#: it negates: "the guard is not run AND the invariant fails" negates the run,
#: not the failure, and a window that stepped over the conjunction would read
#: that sentence — a refutation — as an exemption.
_CLAUSE_BREAK = r"(?:and|but|or|nor|because|while|which|that|since|so|though|although|yet)"

#: At most three words between the two halves, none of them a clause break.
_NEAR = rf"(?:(?!\b{_CLAUSE_BREAK}\b)[\w-]+[\s,]+){{0,3}}?"

_NOT_FALSIFYING = re.compile(
    r"(?i)(?:"
    r"\b(?:do(?:es)?\s+not|did\s+not|don'?t|doesn'?t|is\s+not|are\s+not|was\s+not|"
    r"were\s+not|cannot|can\s?not|can'?t|never|not)\s+" + _NEAR +
    r"(?:fail|fails|failed|failing|falsif\w+|violat\w+|breach\w*|invalidat\w+|"
    r"refut\w+|contradict\w*|break|breaks|broken)\b"
    r"|\b(?:rather|other)\s+than\s+" + _NEAR +
    r"(?:failure|failing|violation|breach|refutation|falsification)\b"
    r")"
)

#: A sentence boundary, for scoping a disclaimer to the criterion it is about.
#: A reporter who exempts AC-12 in one sentence has not thereby exempted AC-9
#: because AC-9 appears in the next one.
_SENTENCE = re.compile(r"(?<=[.;!?])\s+|\n+")

#: Ordered because the first match wins and the earlier patterns name the
#: narrower thing. A sentence about Product Driver's own measurement must not be
#: read as a sentence about the product.
_CLASSIFIERS: tuple[tuple[FindingClass, re.Pattern[str]], ...] = (
    (
        FindingClass.HARNESS_DEFECT,
        re.compile(
            r"(?i)\b(?:product\s+driver|the\s+driver|the\s+harness|harness|the\s+runner|"
            r"scenario\s+(?:executor|generator|planner)|the\s+oracle\s+itself|"
            r"measurement\s+(?:bug|defect|error)|false\s+(?:red|green)\s+from\s+the\s+harness)\b"
        ),
    ),
    (
        FindingClass.CI_INFRASTRUCTURE_DEFECT,
        re.compile(
            r"(?i)\b(?:ci\s+(?:runner|infrastructure|outage|flake|cancel\w*|timeout|timed\s+out)|"
            r"workflow\s+(?:cancel\w*|timed\s+out|failed\s+to\s+start)|runner\s+(?:died|lost)|"
            r"pipeline\s+infrastructure)\b"
        ),
    ),
    (
        FindingClass.AUTHORITY_GAP,
        re.compile(
            r"(?i)\b(?:the\s+repository\s+does\s+not\s+(?:state|say|define)|"
            r"no\s+authority\s+(?:for|states)|undefined\s+(?:requirement|criterion)|"
            r"ambiguous\s+(?:requirement|criterion|acceptance)|"
            r"needs?\s+(?:a\s+)?(?:founder|architect)\s+decision)\b"
        ),
    ),
    (
        FindingClass.RUNTIME_SAFETY_DEFECT,
        re.compile(
            r"(?i)\b(?:fails?[\s-]*open|invariant\s+(?:violat\w+|broken)|"
            r"safety\s+(?:invariant|boundary|violation)|data\s+loss|corrupt\w+|"
            r"tenant\s+(?:leak|isolation\s+(?:break|violation))|privilege\s+escalation|"
            r"unauthenticated\s+access|bypass(?:es|ed)?\s+(?:the\s+)?(?:guard|check|authorization))\b"
        ),
    ),
    (
        FindingClass.STALE_VERIFICATION,
        re.compile(
            r"(?i)\b(?:stale|out\s+of\s+date|describes?\s+an\s+earlier\s+tree|"
            r"was\s+recorded\s+before|no\s+longer\s+describes?|superseded)\b"
        ),
    ),
    (
        FindingClass.EVIDENCE_GAP,
        re.compile(
            r"(?i)\b(?:no\s+evidence|evidence\s+(?:is\s+)?(?:missing|absent|not\s+attached)|"
            r"cites?\s+\S+\s+which\s+does\s+not\s+exist|artifact\s+(?:is\s+)?(?:missing|not\s+found)|"
            r"cannot\s+find\s+the\s+(?:report|receipt|record)|"
            r"(?:cited\s+)?(?:report|receipt|record|artifact|evidence|file)\s+(?:is\s+)?"
            r"(?:missing|absent|not\s+in\s+the\s+tree|does\s+not\s+exist))\b"
        ),
    ),
    (
        FindingClass.VERIFICATION_GAP,
        re.compile(
            r"(?i)\b(?:no\s+test\s+covers?|untested|not\s+covered\s+by\s+(?:a\s+)?test|"
            r"lacks?\s+(?:a\s+)?(?:test|guard|probe)|would\s+not\s+fail\s+if|"
            r"vacuous|nothing\s+would\s+catch)\b"
        ),
    ),
    (
        FindingClass.NONBLOCKING_DEBT,
        re.compile(
            r"(?i)\b(?:could\s+(?:be|have)|would\s+be\s+(?:nice|useful|clearer|better)|"
            r"nice\s+to\s+have|consider\s+adding|suggest(?:ion|ed|s)?\b|"
            r"a\s+(?:nicer|cleaner|stronger|better)\s+\w+|improvement|worth\s+adding|"
            r"in\s+a\s+future\s+phase|follow[\s-]?up)\b"
        ),
    ),
    (
        FindingClass.BOOKKEEPING_DEBT,
        re.compile(
            r"(?i)\b(?:prose|wording|typo|mis-?spell\w*|documentation\s+(?:says|is\s+stale)|"
            r"record\s+(?:is\s+)?(?:stale|out\s+of\s+date)|comment\s+(?:says|is\s+wrong))\b"
        ),
    ),
    (
        FindingClass.EXTERNAL_BLOCKER,
        re.compile(
            r"(?i)\b(?:credential\w*\s+(?:missing|unavailable)|partner\s+(?:data|api)|"
            r"requires?\s+(?:a\s+)?(?:push|deploy|remote)|outside\s+this\s+machine)\b"
        ),
    ),
    (
        FindingClass.PRODUCT_DEFECT,
        re.compile(
            r"(?i)\b(?:defect|bug|incorrect|wrong|does\s+not\s+(?:work|hold|match)|"
            r"returns?\s+the\s+wrong|off\s+by\s+one|regression|breaks?\b|"
            r"violates?\s+the\s+(?:contract|specification))\b"
        ),
    ),
)


def classify_review_finding(text: str) -> FindingClass:
    """Read one reviewer finding and say what kind of thing it is.

    Deterministic and ordered: the narrower families are tested first, so a
    sentence about Product Driver's own measurement is a harness defect rather
    than a product defect, and a sentence about a nicer guard is debt rather
    than a defect. A sentence that matches nothing is UNCLASSIFIED, which routes
    to RECORD_ONLY and can never block — an unread finding must not be able to
    stop a phase, and it must not disappear either.
    """
    blob = str(text or "")
    for classification, pattern in _CLASSIFIERS:
        if pattern.search(blob):
            return classification
    return FindingClass.UNCLASSIFIED


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "")).strip("-")
    return (cleaned or "x")[:48]


def _review_tree(review: Any) -> str:
    data = getattr(review, "reviewed_fingerprint", None)
    if isinstance(data, dict):
        return str(data.get("identity", "") or "")
    fingerprint = getattr(review, "fingerprint", None)
    if callable(fingerprint):
        try:
            return fingerprint().identity
        except Exception:
            return ""
    return ""


def _match_criterion(raw: str, criteria: CriterionSet) -> str | None:
    """Match what a reviewer called a criterion to one the authority states.

    Exact id first, then exact name, then an id appearing inside the reviewer's
    sentence. Nothing fuzzier: a near-match is how a reviewer's new idea gets
    silently attached to an existing requirement.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    for criterion in criteria.criteria:
        if criterion.criterion_id and criterion.criterion_id.lower() == text.lower():
            return criterion.criterion_id
    for criterion in criteria.criteria:
        if criterion.name and criterion.name.lower() == text.lower():
            return criterion.criterion_id
    for criterion in criteria.criteria:
        if criterion.criterion_id and re.search(
            rf"\b{re.escape(criterion.criterion_id)}\b", text, re.I
        ):
            return criterion.criterion_id
    return None


def _criteria_named_in(text: str, criteria: CriterionSet) -> list[str]:
    """Every frozen criterion a piece of prose actually names."""
    blob = str(text or "")
    found: list[str] = []
    for criterion in criteria.criteria:
        cid = criterion.criterion_id
        if cid and re.search(rf"\b{re.escape(cid)}\b", blob, re.I):
            found.append(cid)
    return found


def _criterion_aliases(criterion_id: str) -> list[str]:
    """How a reporter may spell one criterion id in a sentence.

    The id itself, and the id with a leading phase segment dropped — a reviewer
    given ``PH4-AC-12`` writes "does not fail AC-12" in the very next clause,
    and a rule that only matched the full form would read that sentence as being
    about nothing. The suffix is accepted only when it still looks like an id
    (a letter and a digit), so ``AC-1`` never aliases to the bare ``1``.
    """
    cid = str(criterion_id or "").strip()
    if not cid:
        return []
    aliases = [cid]
    _, sep, rest = cid.partition("-")
    if sep and re.search(r"[A-Za-z]", rest) and re.search(r"\d", rest) and len(rest) >= 3:
        aliases.append(rest)
    return aliases


def _criteria_disclaimed_in(text: str, criterion_ids: Sequence[str]) -> list[str]:
    """Criteria the prose itself says this finding does not falsify.

    Scoped to the sentence: an exemption counts for the criterion it is written
    beside, not for every criterion the finding happens to mention. This is the
    deterministic reading of rule 4 in
    :mod:`~neyma_product_driver.phase_acceptance` — a reviewer who attaches
    context to a criterion and says in the same breath that it does not fail
    that criterion has attached context, not refused the phase.
    """
    blob = str(text or "")
    if not blob.strip():
        return []
    sentences = [part for part in _SENTENCE.split(blob) if part.strip()]
    disclaimed: list[str] = []
    for cid in criterion_ids:
        aliases = _criterion_aliases(cid)
        if not aliases:
            continue
        pattern = re.compile(
            r"\b(?:" + "|".join(re.escape(a) for a in aliases) + r")\b", re.I
        )
        for sentence in sentences:
            if pattern.search(sentence) and _NOT_FALSIFYING.search(sentence):
                disclaimed.append(cid)
                break
    return disclaimed


def _changed_between(repo: Path, previous: TreeFingerprint, current: TreeFingerprint) -> list[str]:
    """Paths that differ between two commits, for blast-radius reasoning.

    Returns an empty list when the two share a commit — the difference is then
    entirely in the working tree, which ``git status`` reports instead. A failure
    to read git returns nothing, and nothing means "cannot narrow", which the
    caller treats as "invalidate".
    """
    import subprocess

    if not previous.head or not current.head or previous.head == current.head:
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain", "-uall"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            return [ln[3:].strip().strip('"') for ln in proc.stdout.splitlines() if ln.strip()]
        except (OSError, subprocess.SubprocessError):
            return []
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", previous.head, current.head],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


#: A unit id as repositories write them, matched at its FULL length. Matching
#: the stem instead read "the change that resolves PH2-D4" as naming the unit
#: PH2, which is complete — so a residual waiting on another residual was
#: proposed as closable by the phase it belongs to.
_UNIT_TOKEN = re.compile(r"\b[A-Z]{1,4}-?\d{1,3}(?:\.\d{1,3})?(?:-[A-Z]{1,4}-?\d{1,3})*\b")

#: Words that turn a sentence naming a unit into a sentence about what it is
#: NOT. "a session that owns the bootstrap status guards — not a PH2 machine
#: unit" names PH2 and says the opposite of "PH2 closes this".
_CLOSURE_NEGATED = re.compile(
    r"(?i)\b(?:not|never|no|without|unless|other\s+than|rather\s+than|instead\s+of|"
    r"outside)\b"
)


def _units_named_in(condition: str, phase_id: str) -> set[str]:
    """Unit ids a closure condition actually says would close the residual.

    Three refusals, each for a false positive seen in a real registry: a token
    matched at its stem rather than its full length, the phase's OWN id (a
    residual of a phase is not closed by that phase completing — it is carried
    out of it), and a sentence that names a unit in order to say the closure is
    not that unit's.
    """
    text = str(condition or "")
    if _CLOSURE_NEGATED.search(text):
        return set()
    own = str(phase_id or "").strip().upper()
    return {
        token.upper()
        for token in _UNIT_TOKEN.findall(text.upper())
        if token.upper() != own
    }


def _completed_unit_ids(repo: Path, registry_paths: Sequence[str] | None) -> set[str]:
    """Every unit the repository currently records as complete.

    Used only to decide whether a residual's own stated closure condition —
    "closes at the next unit" — has actually been met. Read fresh rather than
    remembered,
    because a residual closing is exactly the kind of fact that changes.
    """
    from .phase_authority import DEFAULT_REGISTRY_PATHS, _load_units, _first, ACCEPTED_UNIT_STATES

    paths = list(registry_paths or DEFAULT_REGISTRY_PATHS)
    units, _source, problem = _load_units(Path(repo), paths)
    if problem:
        return set()
    done: set[str] = set()
    for unit in units:
        states = {
            str(_first(unit, ("status",), "") or "").upper(),
            str(_first(unit, ("execution_state",), "") or "").upper(),
            str(_first(unit, ("checkpoint_state",), "") or "").upper(),
        }
        if any(s in states for s in ACCEPTED_UNIT_STATES):
            uid = str(_first(unit, ("unit_id", "id", "phase_id"), "") or "").strip().upper()
            if uid:
                done.add(uid)
    return done
