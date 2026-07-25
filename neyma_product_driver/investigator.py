"""The diagnostic investigator.

A strong senior engineer, made mechanical: observe, summarize the facts, notice
what contradicts what, form a few competing hypotheses, run the smallest probe
that would tell them apart, believe the evidence over the story, and repeat until
one explanation carries all the load — or until it genuinely needs the founder.

It is deliberately *not* a pile of failure-class handlers. The known handlers
(the protocol resolver, the completion auditor, the topology analyzer) are
available to it as probes and evidence sources, but the diagnosis itself runs
through one general loop over observations, hypotheses and probes. A failure
class no one wrote a handler for is diagnosed the same way as one that has a
handler — by evidence.

Where the *judgment* comes from:

    - a Reasoner proposes hypotheses and designs probes (a Claude subagent in a
      real run; a scripted stand-in under test)
    - the HypothesisEngine decides, ruthlessly, which survive contact with
      reality

The investigator never rewrites history, never pushes, never writes
finalizer-owned status, and never takes a consequential action on its own. When
one is the only way forward, it stops and prints ASK FOUNDER.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from .hypothesis_engine import HypothesisEngine
from .investigation_memory import (
    AskFounder,
    Challenge,
    Hypothesis,
    HypothesisStatus,
    InvestigationMemory,
    InvestigationResult,
    InvestigationState,
    InvestigationStatus,
    Observation,
    Probe,
    ProbeResult,
    Reliability,
)
from .probe_runner import EvidenceCollector, ProbeRunner, is_consequential


# --------------------------------------------------------------------------
# The reasoner seam
# --------------------------------------------------------------------------


class InvestigationBrief:
    """Everything a reasoner needs to think, and nothing that presumes an answer."""

    def __init__(self, state: InvestigationState) -> None:
        self.issue = state.issue
        self.observations = list(state.observations)
        self.contradictions = list(state.contradictions)
        self.hypotheses = list(state.hypotheses)
        self.probes = list(state.probes)
        self.probe_results = list(state.probe_results)
        self.iteration = state.iteration

    def facts(self) -> list[Observation]:
        return [o for o in self.observations if o.reliability is Reliability.DIRECT]

    def render(self) -> str:
        lines = [f"ISSUE: {self.issue or '(open-ended)'}", "", "OBSERVATIONS:"]
        lines += [f"  - {o.brief()}" for o in self.observations[-30:]]
        if self.contradictions:
            lines += ["", "CONTRADICTIONS:"]
            lines += [f"  - {c.render()}" for c in self.contradictions]
        if self.hypotheses:
            lines += ["", "CURRENT HYPOTHESES:"]
            for h in self.hypotheses:
                lines.append(f"  [{h.id} {h.status.value} conf {h.confidence:.2f}] {h.statement}")
        return "\n".join(lines)


class Reasoner(Protocol):
    """Proposes hypotheses and designs probes. The source of judgment.

    Implementations must not decide the outcome — they offer candidates. The
    engine, driven by real probe results, decides what survives.
    """

    def generate_hypotheses(self, brief: InvestigationBrief) -> list[Hypothesis]: ...

    def design_probe(self, brief: InvestigationBrief) -> Probe | None: ...

    def challenge(self, brief: InvestigationBrief) -> Challenge | None: ...


# --------------------------------------------------------------------------
# The investigator
# --------------------------------------------------------------------------


class Investigator:
    """Runs the observe → hypothesize → probe → revise loop until it concludes."""

    def __init__(
        self,
        repo: Path,
        reasoner: Reasoner,
        *,
        memory: InvestigationMemory | None = None,
        collector: EvidenceCollector | None = None,
        runner: ProbeRunner | None = None,
        emit: Callable[[str], None] = lambda _m: None,
        challenger: Reasoner | None = None,
    ) -> None:
        self.repo = Path(repo)
        self.reasoner = reasoner
        self.memory = memory
        self.collector = collector or EvidenceCollector(self.repo)
        self.runner = runner or ProbeRunner(self.repo)
        self.engine = HypothesisEngine()
        self.emit = emit
        self.challenger = challenger

    # -- the loop ----------------------------------------------------------

    def investigate(
        self,
        *,
        issue: str = "",
        trigger: str = "",
        builder_report: str = "",
        reviewer_report: str = "",
        seed_observations: list[Observation] | None = None,
        max_iterations: int = 8,
        run_id: str = "",
    ) -> InvestigationState:
        state = InvestigationState(
            run_id=run_id,
            issue=issue,
            trigger=trigger,
            max_iterations=max_iterations,
            hard_cap=max(max_iterations, max_iterations * 2),
            repo_fingerprint=self.runner.repo_fingerprint(),
        )

        # Iteration 0: gather primary evidence and form the first hypotheses.
        state.observations = self.collector.collect(
            iteration=0, builder_report=builder_report, reviewer_report=reviewer_report,
            extra=seed_observations,
        )
        state.contradictions = self.engine.detect_contradictions(state.observations)
        for o in state.observations:
            self.engine.incorporate(o.signals, o.reliability, state.hypotheses, 0, source=o.source)
        self._propose_hypotheses(state)
        self._replay_evidence(state)  # let the fresh hypotheses meet the evidence
        self._save(state)
        self._print_status(state)

        no_learning_streak = 0
        seen_signal_keys: set[str] = {k for o in state.observations for k in o.signals}
        while True:
            if self._should_stop_before_step(state):
                break
            state.iteration += 1

            # Reload the world. The investigation must never trust a stale
            # snapshot — the repository may have moved under it.
            self._reobserve(state)

            probe = self._choose_probe(state)
            if probe is None:
                self._conclude(state, InvestigationStatus.NEEDS_MORE_EVIDENCE,
                               "no probe left that would distinguish the live hypotheses")
                break

            if not probe.read_only or is_consequential(probe.command_or_action):
                self._ask_founder_for(state, probe)
                break

            result = self.runner.run(probe)
            # Check for a repeat against prior probes *before* this one joins the
            # record, or it would always match itself.
            repeat = self.engine.is_repeat(state, probe, result)
            state.probes.append(probe)
            state.probe_results.append(result)
            self._record_probe_observation(state, probe, result)

            if result.refused:
                self._ask_founder_for(state, probe, result.refusal_reason)
                break
            update = self.engine.incorporate(
                result.signals, Reliability.DIRECT, state.hypotheses, state.iteration,
                source=f"probe {probe.id}",
            )
            state.transitions.extend(update.transitions)
            self._track_diagnosis_change(state, update.transitions)

            # A probe "learned" only if it moved a hypothesis or revealed a
            # signal key never seen before. Re-emitting the same keys with the
            # same values, moving nothing, is not progress — even though the
            # probe technically produced signals.
            moved = bool(update.supported or update.contradicted)
            new_keys = set(result.signals) - seen_signal_keys
            seen_signal_keys |= set(result.signals)
            learned = (moved or bool(new_keys)) and not repeat
            no_learning_streak = 0 if learned else no_learning_streak + 1

            self._maybe_challenge(state)
            self._save(state)
            self._print_status(state)

            if self._stop_after_step(state, no_learning_streak):
                break

        if state.result is None:
            self._finalize(state)
        self._save(state)
        return state

    # -- steps -------------------------------------------------------------

    def _propose_hypotheses(self, state: InvestigationState) -> None:
        brief = InvestigationBrief(state)
        existing = {h.id for h in state.hypotheses}
        for h in self.reasoner.generate_hypotheses(brief):
            if h.id not in existing:
                state.hypotheses.append(h)
                existing.add(h.id)

    def _replay_evidence(self, state: InvestigationState) -> None:
        """Match already-collected evidence against newly proposed hypotheses."""
        for o in state.observations:
            update = self.engine.incorporate(
                o.signals, o.reliability, state.hypotheses, state.iteration, source=o.source
            )
            state.transitions.extend(update.transitions)

    def _reobserve(self, state: InvestigationState) -> None:
        fingerprint = self.runner.repo_fingerprint()
        if fingerprint != state.repo_fingerprint:
            state.repo_changed = True
            state.repo_fingerprint = fingerprint
            self.emit("  note: the repository changed during this investigation")
        fresh = self.collector.collect(iteration=state.iteration)
        # Keep prior observations; append the fresh snapshot so contradictions
        # across time (an inconsistent environment) remain visible.
        state.observations.extend(fresh)
        state.contradictions = self.engine.detect_contradictions(state.observations)
        for o in fresh:
            self.engine.incorporate(
                o.signals, o.reliability, state.hypotheses, state.iteration, source=o.source
            )

    def _choose_probe(self, state: InvestigationState) -> Probe | None:
        brief = InvestigationBrief(state)
        candidates: list[Probe] = []
        designed = self.reasoner.design_probe(brief)
        if designed is not None:
            candidates.append(designed)
        # Also let the engine consider each live hypothesis's own next_probe if
        # the reasoner attached probe objects via that field is not possible;
        # the designed probe is the primary path.
        return self.engine.select_probe(state, candidates)

    def _record_probe_observation(
        self, state: InvestigationState, probe: Probe, result: ProbeResult
    ) -> None:
        content = (
            f"probe {probe.id} refused: {result.refusal_reason}"
            if result.refused
            else f"probe {probe.id} ({probe.question}) → "
            + (", ".join(f"{k}={v}" for k, v in result.signals.items()) or "no signals")
        )
        state.observations.append(
            Observation(
                id=f"P{state.iteration}.{probe.id}",
                source=f"probe {probe.id}",
                content=content,
                kind=_probe_observation_kind(probe),
                reliability=Reliability.REPORTED if result.refused else Reliability.DIRECT,
                evidence_path=result.evidence_path,
                signals=result.signals,
                iteration=state.iteration,
            )
        )

    # -- challenger --------------------------------------------------------

    def _maybe_challenge(self, state: InvestigationState) -> None:
        """Ask a fresh perspective to attack the diagnosis, when it is warranted.

        Triggers: the diagnosis has flipped repeatedly, the evidence is
        ambiguous (no clear leader), or the issue touches safety/authority. The
        challenge is advisory — the investigator decides for itself whether it
        changes anything.
        """
        if self.challenger is None:
            return
        leader = state.leading()
        ambiguous = leader is None or (
            len([h for h in state.alive_hypotheses() if h.confidence >= 0.4]) > 1
        )
        safety = any(w in (state.issue or "").lower() for w in ("safety", "authority", "finaliz", "persist"))
        if not (state.diagnosis_changes >= 2 or ambiguous or safety):
            return
        if any(c.challenges_hypothesis == (leader.id if leader else "") for c in state.challenges):
            return  # already challenged this leader

        challenge = self.challenger.challenge(InvestigationBrief(state))
        if challenge is None:
            return
        # Independently decide: only fold the challenge in if it names a live
        # discriminating probe or a genuinely new hypothesis. A challenge is not
        # obeyed; it is weighed.
        changed = False
        if challenge.proposed_probe is not None:
            result = self.runner.run(challenge.proposed_probe)
            if result.ran and not result.refused:
                state.probes.append(challenge.proposed_probe)
                state.probe_results.append(result)
                self._record_probe_observation(state, challenge.proposed_probe, result)
                update = self.engine.incorporate(
                    result.signals, Reliability.DIRECT, state.hypotheses, state.iteration,
                    source=f"challenge probe {challenge.proposed_probe.id}",
                )
                state.transitions.extend(update.transitions)
                changed = update.learned_something
        challenge.changed_the_diagnosis = changed
        state.challenges.append(challenge)

    # -- stop logic --------------------------------------------------------

    def _promote(self, winner: Hypothesis) -> None:
        """A hypothesis that won — by threshold or by elimination — is supported."""
        if winner.status is not HypothesisStatus.SUPPORTED:
            winner.status = HypothesisStatus.SUPPORTED

    def _should_stop_before_step(self, state: InvestigationState) -> bool:
        winner = self.engine.converged(state)
        if winner is not None:
            self._promote(winner)
            self._conclude(state, InvestigationStatus.ROOT_CAUSE_FOUND, winner.statement, winner=winner)
            return True
        if state.iteration >= state.max_iterations and not self._should_extend(state):
            self._conclude_budget(state)
            return True
        if state.iteration >= state.hard_cap:
            self._conclude_budget(state)
            return True
        return False

    def _stop_after_step(self, state: InvestigationState, no_learning_streak: int) -> bool:
        winner = self.engine.converged(state)
        if winner is not None:
            self._promote(winner)
            self._conclude(state, InvestigationStatus.ROOT_CAUSE_FOUND, winner.statement, winner=winner)
            return True

        inconsistent = self.engine.environment_inconsistent(state)
        if inconsistent:
            self._conclude(
                state,
                InvestigationStatus.NEEDS_MORE_EVIDENCE,
                f"cannot attribute the failure: {inconsistent}",
            )
            return True

        # Two consecutive probes that taught nothing new: stop chasing rather
        # than rerun the same failing command forever.
        if no_learning_streak >= 2:
            self._conclude(
                state,
                InvestigationStatus.PARTIAL_DIAGNOSIS,
                "probes stopped producing new information",
            )
            return True
        return False

    def _should_extend(self, state: InvestigationState) -> bool:
        """Extend past the soft cap only while *recent* probing is still paying off.

        "Recent" means this iteration's belief movement, not the whole history —
        old transitions must not keep the investigation alive after it has gone
        quiet.
        """
        if state.repo_changed:
            return False
        recent = [t for t in state.transitions if t.iteration >= state.iteration - 1]
        rising = any(t.to_confidence > t.from_confidence for t in recent)
        return bool(recent) and rising and state.iteration < state.hard_cap

    def _track_diagnosis_change(self, state: InvestigationState, transitions: list[Any]) -> None:
        # A "diagnosis change" is the leader losing its lead, or a hypothesis
        # flipping between supported and refuted.
        flips = [
            t
            for t in transitions
            if t.to_status in (HypothesisStatus.DISPROVEN, HypothesisStatus.SUPPORTED)
        ]
        if flips:
            state.diagnosis_changes += 1

    # -- conclusions -------------------------------------------------------

    def _conclude(
        self,
        state: InvestigationState,
        status: InvestigationStatus,
        message: str,
        *,
        winner: Hypothesis | None = None,
    ) -> None:
        rejected = [
            f"{h.id}: {h.statement} — {h.opposing_evidence[0] if h.opposing_evidence else 'refuted'}"
            for h in state.hypotheses
            if h.status is HypothesisStatus.DISPROVEN
        ]
        evidence = _load_bearing_evidence(state, winner)
        confidence = winner.confidence if winner is not None else _partial_confidence(state)
        contributing = [
            h.statement
            for h in state.hypotheses
            if winner is not None and h.id != winner.id and h.status is HypothesisStatus.WEAKENED
        ]
        # When there is no root cause, the concluding message is itself the most
        # useful thing to surface — "cannot attribute the failure: the same probe
        # returned conflicting values" is the finding.
        unresolved = [
            h.statement for h in state.alive_hypotheses() if winner is None or h.id != winner.id
        ][:5]
        if status is not InvestigationStatus.ROOT_CAUSE_FOUND and message:
            unresolved = [message, *unresolved][:6]

        result = InvestigationResult(
            status=status,
            root_cause=message if status is InvestigationStatus.ROOT_CAUSE_FOUND else "",
            contributing_causes=contributing,
            rejected_hypotheses=rejected,
            unresolved_questions=unresolved,
            evidence=evidence,
            recommended_action=(
                message
                if status is InvestigationStatus.NEEDS_MORE_EVIDENCE and message
                else self._recommend(state, status, winner)
            ),
            confidence=confidence,
            next_verification=(
                "re-run the targeted check the fix addresses, then the broader gates"
                if status is InvestigationStatus.ROOT_CAUSE_FOUND
                else "gather the missing evidence named above"
            ),
            iterations_used=state.iteration,
        )
        state.result = result

    def _conclude_budget(self, state: InvestigationState) -> None:
        leader = state.leading()
        if leader is not None and leader.confidence >= 0.5:
            self._conclude(state, InvestigationStatus.PARTIAL_DIAGNOSIS, leader.statement, winner=None)
        else:
            self._conclude(
                state, InvestigationStatus.BUDGET_EXHAUSTED,
                "the iteration budget was reached without a supported root cause",
            )

    def _finalize(self, state: InvestigationState) -> None:
        winner = self.engine.converged(state)
        if winner is not None:
            self._conclude(state, InvestigationStatus.ROOT_CAUSE_FOUND, winner.statement, winner=winner)
        else:
            self._conclude_budget(state)

    def _ask_founder_for(
        self, state: InvestigationState, probe: Probe, reason: str = ""
    ) -> None:
        targets = ", ".join(probe.targets_hypotheses) or "the live hypotheses"
        ask = AskFounder(
            what=f"run a consequential probe: {probe.question}",
            why=reason or "the smallest discriminating probe here is not read-only",
            exact_command=probe.command_or_action,
            expected_benefit=f"would distinguish {targets}",
            risk=probe.risk,
            rollback="none needed if not run; the investigator did not run it",
        )
        result = InvestigationResult(
            status=InvestigationStatus.ASK_USER,
            root_cause="",
            recommended_action="a founder decision is required before the next probe",
            confidence=_partial_confidence(state),
            ask_founder=ask,
            iterations_used=state.iteration,
            evidence=_load_bearing_evidence(state, None),
            unresolved_questions=[h.statement for h in state.alive_hypotheses()][:5],
        )
        state.result = result

    def _recommend(
        self, state: InvestigationState, status: InvestigationStatus, winner: Hypothesis | None
    ) -> str:
        if status is InvestigationStatus.ROOT_CAUSE_FOUND and winner is not None:
            return f"apply the smallest fix for: {winner.statement}"
        if status is InvestigationStatus.NEEDS_MORE_EVIDENCE:
            return "collect the evidence the live hypotheses disagree on before acting"
        if status is InvestigationStatus.PARTIAL_DIAGNOSIS:
            leader = state.leading()
            return (
                f"leading explanation is {leader.statement} but it is not yet load-bearing"
                if leader
                else "no single explanation carries the evidence yet"
            )
        return "await founder input"

    # -- persistence & output ---------------------------------------------

    def _save(self, state: InvestigationState) -> None:
        if self.memory is not None:
            self.memory.save(state)

    def _print_status(self, state: InvestigationState) -> None:
        leader = state.leading()
        self.emit("INVESTIGATION: ACTIVE")
        self.emit(f"Iteration: {state.iteration}/{state.max_iterations}")
        facts = [o for o in state.observations if o.reliability is Reliability.DIRECT][-5:]
        if facts:
            self.emit("Known facts:")
            for o in facts:
                self.emit(f"  - {o.content}")
        rejected = [h for h in state.hypotheses if h.status is HypothesisStatus.DISPROVEN]
        if rejected:
            self.emit("Rejected:")
            for h in rejected:
                self.emit(f"  - {h.id} {h.statement}")
        if leader is not None:
            self.emit("Leading hypothesis:")
            self.emit(f"  - {leader.id} {leader.statement} (conf {leader.confidence:.2f})")


# --------------------------------------------------------------------------
# Builder correction from a supported diagnosis
# --------------------------------------------------------------------------

_FIX_CONFIDENCE_FLOOR = 0.7


def builder_correction_from_investigation(state: InvestigationState) -> str | None:
    """A grounded correction prompt — only from a genuinely supported root cause.

    Returns None when the leading diagnosis is not load-bearing, so a low-
    confidence guess is never handed to the builder as if it were a fix.
    """
    result = state.result
    if result is None or result.status is not InvestigationStatus.ROOT_CAUSE_FOUND:
        return None
    if result.confidence < _FIX_CONFIDENCE_FLOOR:
        return None
    winner = next(
        (h for h in state.hypotheses if h.status is HypothesisStatus.SUPPORTED), None
    )
    if winner is None:
        return None

    rejected = [
        f"{h.id}: {h.statement}"
        for h in state.hypotheses
        if h.status is HypothesisStatus.DISPROVEN
    ]
    evidence = _load_bearing_evidence(state, winner)

    lines = [
        "DIAGNOSTIC INVESTIGATION — supported root cause, with the evidence for it.",
        "",
        "1. ROOT CAUSE (supported):",
        f"   {winner.statement}",
        f"   confidence {winner.confidence:.2f}",
        "",
        "2. EXPLANATIONS ALREADY RULED OUT — do not revisit these:",
    ]
    lines += [f"   - {r}" for r in rejected] or ["   - (none)"]
    lines += ["", "3. EXACT EVIDENCE:"]
    lines += [f"   - {e}" for e in evidence]
    lines += [
        "",
        "4. SMALLEST JUSTIFIED FIX:",
        f"   {result.recommended_action}",
        "",
        "5. PRESERVE:",
        "   All implementation code, all valid evidence, every acceptance guard. Do not",
        "   rewrite history, do not hand-edit derived status, do not weaken any test.",
        "",
        "6. TARGETED VERIFICATION:",
        f"   {result.next_verification}",
        "",
        "7. BROADER GATES TO RERUN AFTERWARD:",
        "   the canonical suite, then the finalizer and clean-clone gate as the",
        "   repository's protocol requires.",
        "",
        "8. STOP CONDITIONS:",
        "   - if the fix does not change the failing signal, stop and report — the",
        "     diagnosis was wrong, not the fix incomplete",
        "   - if applying it would require a rewrite of history or a status hand-edit,",
        "     stop and say so",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Triggers
# --------------------------------------------------------------------------


def should_investigate(
    *,
    builder_report: str = "",
    audit: Any = None,
    protocol: Any = None,
    scenario_passed: bool | None = None,
    evaluator_confidence: float | None = None,
    prior_failures: list[str] | None = None,
    explicit: bool = False,
) -> tuple[bool, str]:
    """Decide whether the situation warrants open-ended diagnosis.

    Returns ``(should, reason)``. The reasons mirror the trigger list: a
    disagreement between sources, a blocker asserted without evidence, a repeated
    fix that changes nothing, a stage that reached BLOCKED without probing, low
    evaluator confidence, or an explicit request.
    """
    if explicit:
        return True, "founder invoked investigate"

    prior_failures = prior_failures or []

    # Builder and test results disagree.
    if audit is not None and getattr(audit, "contradictions", None):
        if getattr(audit, "correction_prompt", "") == "" or len(audit.contradictions) > 0:
            return True, "the completion auditor found contradictions between the claim and the repository"

    # The protocol resolver reached a blocking state it cannot fully explain, or
    # asserts an environment cause.
    if protocol is not None:
        status = getattr(getattr(protocol, "status", None), "value", "")
        if status in ("BLOCKED_ENVIRONMENT",):
            return True, "an environmental blocker is asserted; verify it with a direct probe"
        env_blockers = getattr(protocol, "environment_blockers", []) or []
        if env_blockers and not _has_direct_evidence(env_blockers):
            return True, "an environmental explanation is asserted without direct evidence"

    # A full suite fails while targeted tests pass — a discrepancy worth tracing.
    if scenario_passed is True and audit is not None:
        suite = None
        try:
            suite = audit.observed_state.receipt("suite")  # type: ignore[attr-defined]
        except Exception:
            suite = None
        if suite is not None and suite.exists and not suite.passed:
            return True, "a full suite fails while the targeted run passed"

    # Repeated fixes are not changing the failure.
    if len(prior_failures) >= 2 and len(set(prior_failures[-2:])) == 1:
        return True, "repeated corrections have not changed the failure"

    # The evaluator is unsure.
    if evaluator_confidence is not None and evaluator_confidence < 0.4:
        return True, "the evaluator's confidence is low"

    # A builder blocker that does not match execution — e.g. asserts a cause the
    # receipts do not show.
    if builder_report and _asserts_unverified_environment(builder_report):
        return True, "the builder asserts an environmental blocker; confirm it directly"

    return False, ""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _probe_observation_kind(probe: Probe):
    from .investigation_memory import ObservationKind

    return {
        "CLASSIFY_COMMITS": ObservationKind.GIT_STATE,
        "CHECK_RECEIPT": ObservationKind.FILE_CONTENT,
        "PROTOCOL_RESOLVE": ObservationKind.GIT_STATE,
        "HTTP_GET": ObservationKind.PRODUCT_BEHAVIOR,
        "SQL_READONLY": ObservationKind.PRODUCT_BEHAVIOR,
        "COMMAND": ObservationKind.COMMAND_RESULT,
        "PREDICATE": ObservationKind.COMMAND_RESULT,
    }.get(probe.kind.upper(), ObservationKind.COMMAND_RESULT)


def _load_bearing_evidence(state: InvestigationState, winner: Hypothesis | None) -> list[str]:
    if winner is not None and winner.supporting_evidence:
        return list(dict.fromkeys(winner.supporting_evidence))[:8]
    facts = [
        o.content
        for o in state.observations
        if o.reliability is Reliability.DIRECT and o.signals
    ]
    return list(dict.fromkeys(facts))[-8:]


def _partial_confidence(state: InvestigationState) -> float:
    leader = state.leading()
    return round(leader.confidence, 2) if leader is not None else 0.0


def _has_direct_evidence(env_blockers: list[Any]) -> bool:
    for b in env_blockers:
        evidence = getattr(b, "evidence", None) or []
        if any(str(e).strip() for e in evidence):
            return True
    return False


def _asserts_unverified_environment(report: str) -> bool:
    import re

    return bool(
        re.search(
            r"\b(sandbox|environment|permission|socket|bind|TLS|certificate|network)\b[^.\n]{0,60}"
            r"\b(block|blocks|blocked|prevent|prevents|denied|fails?)\b",
            report,
            re.I,
        )
    )
