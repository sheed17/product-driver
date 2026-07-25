"""The domain-blind reasoning core.

This is where belief actually moves. It knows nothing about sockets, finalizers,
commit topologies or invoices. It knows only:

    - an observation carries signals (comparable facts)
    - a hypothesis predicts signals
    - a probe produces signals
    - when reality matches a prediction, the hypothesis gains support
    - when reality speaks to a prediction and disagrees, the hypothesis is refuted
    - prose (REPORTED evidence) can raise suspicion but never confirm

Because the mechanism is signals-and-predictions rather than keyword handlers,
the *same* engine resolves a failure class it has never seen, provided something
(a reasoner, a human, a subagent) proposes the hypotheses and the probes. The
engine's job is to be ruthless about which survive.

Nothing here touches the repository or the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .investigation_memory import (
    Contradiction,
    Hypothesis,
    HypothesisStatus,
    HypothesisTransition,
    InvestigationState,
    Observation,
    Probe,
    ProbeResult,
    Reliability,
    match_prediction,
)

# How much a single supporting DIRECT signal closes the gap to certainty.
_SUPPORT_GAIN = 0.35
# A DIRECT contradiction refutes outright; a REPORTED one only weakens.
_SUPPORT_THRESHOLD = 0.7
# Confidence at or above this, with every rival dead, means the diagnosis holds.
_ROOT_CAUSE_CONFIDENCE = 0.7
# Winning by elimination needs a real (measured) supporting fact and at least
# this much confidence — below it, a lone survivor is a lead, not a conclusion.
_ELIMINATION_CONFIDENCE = 0.5


@dataclass
class BeliefUpdate:
    """What one signal source did to the field of hypotheses."""

    supported: list[str] = field(default_factory=list)
    contradicted: list[str] = field(default_factory=list)
    transitions: list[HypothesisTransition] = field(default_factory=list)
    learned_signals: list[str] = field(default_factory=list)

    @property
    def learned_something(self) -> bool:
        return bool(self.supported or self.contradicted or self.learned_signals)


class HypothesisEngine:
    """Structural contradiction detection, belief update, ranking, probe choice."""

    # -- contradictions ----------------------------------------------------

    def detect_contradictions(self, observations: list[Observation]) -> list[Contradiction]:
        """Two observations that assign the same signal incompatible values.

        This is the generic version of "the builder says X but the run shows
        not-X": if two sources set ``finalizer_socket_tests`` to ``pass`` and
        ``fail``, that is a contradiction whatever the domain. A disagreement
        where at least one side is DIRECT is significant; two REPORTED claims
        disagreeing is merely noise between stories.
        """
        found: list[Contradiction] = []
        seen: set[tuple[str, str, str]] = set()

        for i, a in enumerate(observations):
            for b in observations[i + 1 :]:
                for key in set(a.signals) & set(b.signals):
                    va, vb = a.signals[key], b.signals[key]
                    if _norm(va) == _norm(vb):
                        continue
                    dedupe = (key, *sorted((_norm(va), _norm(vb))))
                    if dedupe in seen:
                        continue
                    # Two guesses disagreeing is not a contradiction worth chasing.
                    if a.reliability is Reliability.REPORTED and b.reliability is Reliability.REPORTED:
                        continue
                    seen.add(dedupe)
                    direct = Reliability.DIRECT in (a.reliability, b.reliability)
                    found.append(
                        Contradiction(
                            statement_a=f"{a.source}: {key}={va}",
                            statement_b=f"{b.source}: {key}={vb}",
                            evidence_a=a.evidence_path or a.source,
                            evidence_b=b.evidence_path or b.source,
                            significance=(
                                "a measured fact contradicts another source"
                                if direct
                                else "two derived values disagree"
                            ),
                            signal_key=key,
                        )
                    )
        return found

    # -- belief update -----------------------------------------------------

    def incorporate(
        self,
        signals: dict[str, str],
        reliability: Reliability,
        hypotheses: list[Hypothesis],
        iteration: int,
        source: str = "",
    ) -> BeliefUpdate:
        """Apply one bundle of observed signals to every live hypothesis.

        The rule is the same for a probe result and for a piece of collected
        evidence: match the signals against each hypothesis's predictions and
        move belief accordingly. A prediction that reality *disagrees* with —
        same signal, different value — refutes; a REPORTED source can only weaken.
        """
        update = BeliefUpdate(learned_signals=sorted(signals.keys()))

        for h in hypotheses:
            if not h.status.is_alive:
                continue
            verdicts = [match_prediction(p, signals) for p in h.predicted_observations]
            supports = verdicts.count("support")
            contradicts = verdicts.count("contradict")
            if supports == 0 and contradicts == 0:
                continue

            before_status, before_conf = h.status, h.confidence

            if contradicts:
                where = _first_matching(h.predicted_observations, signals, "contradict")
                h.opposing_evidence.append(f"{source}: {where}" if source else where)
                if reliability is Reliability.DIRECT:
                    # A measured fact refuted a prediction. The hypothesis is out.
                    h.status = HypothesisStatus.DISPROVEN
                    h.confidence = min(h.confidence, 0.15)
                    update.contradicted.append(h.id)
                else:
                    # Prose disagreed. Suspicion only — never a refutation.
                    h.status = HypothesisStatus.WEAKENED
                    h.confidence *= 0.6
                    update.contradicted.append(h.id)
            elif supports:
                where = _first_matching(h.predicted_observations, signals, "support")
                h.supporting_evidence.append(f"{source}: {where}" if source else where)
                gain = _SUPPORT_GAIN * reliability.weight * supports
                h.confidence += gain * (1.0 - h.confidence)
                update.supported.append(h.id)
                if reliability is Reliability.DIRECT:
                    h.has_direct_support = True
                # Only measured evidence can promote a hypothesis to SUPPORTED;
                # a plausible story that merely fails to be contradicted is not
                # a fact.
                if (
                    reliability is Reliability.DIRECT
                    and h.confidence >= _SUPPORT_THRESHOLD
                    and h.status is not HypothesisStatus.DISPROVEN
                ):
                    h.status = HypothesisStatus.SUPPORTED

            h.clamp()
            if (h.status, round(h.confidence, 3)) != (before_status, round(before_conf, 3)):
                update.transitions.append(
                    HypothesisTransition(
                        iteration=iteration,
                        hypothesis_id=h.id,
                        from_status=before_status,
                        to_status=h.status,
                        from_confidence=before_conf,
                        to_confidence=h.confidence,
                        reason=(
                            f"refuted by {source or 'evidence'}"
                            if h.id in update.contradicted
                            else f"supported by {source or 'evidence'}"
                        ),
                    )
                )
        return update

    # -- ranking and probe selection --------------------------------------

    def rank(self, hypotheses: list[Hypothesis]) -> list[Hypothesis]:
        return sorted(hypotheses, key=lambda h: (h.status.rank, h.confidence), reverse=True)

    def select_probe(
        self, state: InvestigationState, candidates: list[Probe]
    ) -> Probe | None:
        """Pick the probe that best separates the live hypotheses.

        A probe is discriminating when the signals it would produce appear in the
        predictions of more than one live hypothesis — running it must move at
        least one of them. The leading hypothesis's own ``next_probe`` is honored
        first, since it was chosen to test exactly that hypothesis.
        """
        alive = state.alive_hypotheses()
        if not alive:
            return candidates[0] if candidates else None

        already = {_pf(p) for p in state.probes}
        fresh = [p for p in candidates if _pf(p) not in already]

        leader = state.leading()
        if leader is not None and leader.next_probe:
            for p in fresh:
                if p.id == leader.next_probe or p.command_or_action == leader.next_probe:
                    return p

        def information(probe: Probe) -> int:
            # How many live hypotheses reference a signal this probe could set.
            probe_signals = {r.signal for r in probe.interpretation_rules}
            probe_signals |= _builtin_signals(probe)
            score = 0
            for h in alive:
                keys = {_pred_key(p) for p in h.predicted_observations}
                if probe_signals & keys or probe.id in (h.next_probe,):
                    score += 1
                if h.id in probe.targets_hypotheses:
                    score += 1
            return score

        ranked = sorted(fresh, key=information, reverse=True)
        if ranked and information(ranked[0]) > 0:
            return ranked[0]
        # Nothing discriminating and fresh remains.
        return ranked[0] if ranked else None

    # -- convergence -------------------------------------------------------

    def is_repeat(self, state: InvestigationState, probe: Probe, result: ProbeResult) -> bool:
        """True when this probe already ran with a materially identical result."""
        pf = _pf(probe)
        rf = result.result_fingerprint()
        for prior_probe, prior_result in zip(state.probes, state.probe_results):
            if _pf(prior_probe) == pf and prior_result.result_fingerprint() == rf:
                return True
        return False

    def environment_inconsistent(self, state: InvestigationState) -> str:
        """Detect the *same* probe returning different answers over time.

        When one identical, read-only probe produces conflicting signals without
        the repository changing, the environment is flaky and no failure can be
        reliably attributed. Two *different* probes sharing a signal name (both
        emit ``exit_code``) is not inconsistency, so grouping is by probe
        fingerprint, not by signal name.
        """
        if state.repo_changed:
            return ""
        by_probe: dict[str, list[dict[str, str]]] = {}
        for probe, result in zip(state.probes, state.probe_results):
            if result.ran and not result.refused:
                by_probe.setdefault(probe.fingerprint(), []).append(result.signals)
        for runs in by_probe.values():
            if len(runs) < 2:
                continue
            keys = set().union(*(set(r) for r in runs))
            for key in keys:
                values = {_norm(r[key]) for r in runs if key in r}
                if len(values) > 1 and key not in ("duration", "timed_out"):
                    return (
                        f"the same probe returned {key}={sorted(values)} on repeated runs "
                        "without a repository change"
                    )
        return ""

    def converged(self, state: InvestigationState) -> Hypothesis | None:
        """A single explanation now carries the load. Two ways to get there.

        1. One hypothesis is SUPPORTED at high confidence with no live rival.
        2. Every rival has been *directly refuted* and exactly one positively
           supported hypothesis remains — a root cause by elimination, which is
           legitimate when each rival died to a measured fact rather than to a
           low score. Elimination never rests on prose: the survivor must carry
           at least one supporting observation of its own.
        """
        alive = state.alive_hypotheses()
        supported = [h for h in state.hypotheses if h.status is HypothesisStatus.SUPPORTED]

        if len(supported) == 1 and supported[0].confidence >= _ROOT_CAUSE_CONFIDENCE:
            rivals = [h for h in alive if h.id != supported[0].id]
            if not rivals:
                return supported[0]

        if len(alive) == 1:
            survivor = alive[0]
            others = [h for h in state.hypotheses if h.id != survivor.id]
            all_refuted = bool(others) and all(
                h.status is HypothesisStatus.DISPROVEN for h in others
            )
            # Elimination is only a root cause when the survivor is itself backed
            # by a *measured* fact and clears a meaningful confidence bar. A
            # hypothesis left standing purely because prose favored it, or because
            # nothing happened to contradict it, is not a diagnosis.
            if (
                all_refuted
                and survivor.has_direct_support
                and survivor.confidence >= _ELIMINATION_CONFIDENCE
            ):
                return survivor
        return None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _norm(v: str) -> str:
    return str(v).strip().lower()


def _pf(probe: Probe) -> str:
    return probe.fingerprint()


def _pred_key(expression: str) -> str:
    import re

    m = re.match(r"^\s*([\w.\-/]+)\s*(?:!=|~=|=)", expression or "")
    return m.group(1) if m else ""


def _first_matching(predictions: list[str], signals: dict[str, str], verdict: str) -> str:
    for p in predictions:
        if match_prediction(p, signals) == verdict:
            key = _pred_key(p)
            return f"{p} (observed {key}={signals.get(key, '?')})"
    return "prediction " + (predictions[0] if predictions else "")


def _builtin_signals(probe: Probe) -> set[str]:
    """Signals a built-in probe kind is known to emit, for probe selection."""
    return {
        "CLASSIFY_COMMITS": {"content_commit_count", "commit_count", "topology_valid", "allowed_content_commits"},
        "CHECK_RECEIPT": {"receipt_exists", "receipt_fresh", "receipt_commit"},
        "PROTOCOL_RESOLVE": {"protocol_status", "deadlock_count", "violation_count"},
        "FILE_STAT": {"exists", "sha256", "size"},
        "SQL_READONLY": {"row_count", "value"},
        "HTTP_GET": {"http_status"},
        "GREP": {"match_count", "matched"},
        "COMMAND": {"exit_code"},
    }.get(probe.kind.upper(), set())
