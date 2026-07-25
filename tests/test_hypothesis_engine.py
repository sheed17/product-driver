"""The domain-blind reasoning core: contradictions, belief update, convergence.

These tests use signals with invented names to prove the engine reasons over
structure, not over any known vocabulary.
"""

from __future__ import annotations

from neyma_product_driver.hypothesis_engine import HypothesisEngine
from neyma_product_driver.investigation_memory import (
    Hypothesis,
    HypothesisStatus,
    InvestigationState,
    Observation,
    Probe,
    ProbeResult,
    Reliability,
)


def engine() -> HypothesisEngine:
    return HypothesisEngine()


# --------------------------------------------------------------------------
# Contradiction detection
# --------------------------------------------------------------------------


def test_two_direct_sources_disagreeing_is_a_contradiction() -> None:
    obs = [
        Observation(source="probe-a", reliability=Reliability.DIRECT, signals={"flurb": "hot"}),
        Observation(source="probe-b", reliability=Reliability.DIRECT, signals={"flurb": "cold"}),
    ]
    contradictions = engine().detect_contradictions(obs)
    assert len(contradictions) == 1
    assert contradictions[0].signal_key == "flurb"


def test_a_measured_fact_contradicting_a_claim_is_flagged() -> None:
    obs = [
        Observation(source="builder", reliability=Reliability.REPORTED, signals={"blocker": "present"}),
        Observation(source="probe", reliability=Reliability.DIRECT, signals={"blocker": "absent"}),
    ]
    contradictions = engine().detect_contradictions(obs)
    assert contradictions
    assert "measured fact" in contradictions[0].significance


def test_two_prose_claims_disagreeing_is_not_worth_chasing() -> None:
    obs = [
        Observation(source="builder", reliability=Reliability.REPORTED, signals={"x": "1"}),
        Observation(source="reviewer", reliability=Reliability.REPORTED, signals={"x": "2"}),
    ]
    assert engine().detect_contradictions(obs) == []


def test_agreement_is_not_a_contradiction() -> None:
    obs = [
        Observation(source="a", reliability=Reliability.DIRECT, signals={"x": "1"}),
        Observation(source="b", reliability=Reliability.DIRECT, signals={"x": "1"}),
    ]
    assert engine().detect_contradictions(obs) == []


# --------------------------------------------------------------------------
# Belief update
# --------------------------------------------------------------------------


def test_a_matching_signal_supports_a_hypothesis() -> None:
    h = Hypothesis(id="H1", statement="x", predicted_observations=["zonk=high"], confidence=0.3)
    update = engine().incorporate({"zonk": "high"}, Reliability.DIRECT, [h], 1, source="probe")
    assert "H1" in update.supported
    assert h.confidence > 0.3
    assert h.supporting_evidence


def test_a_measured_signal_disagreeing_refutes_a_hypothesis() -> None:
    h = Hypothesis(id="H1", statement="x", predicted_observations=["zonk=high"], confidence=0.6)
    update = engine().incorporate({"zonk": "low"}, Reliability.DIRECT, [h], 1, source="probe")
    assert "H1" in update.contradicted
    assert h.status is HypothesisStatus.DISPROVEN
    assert h.opposing_evidence


def test_prose_can_weaken_but_never_refute() -> None:
    h = Hypothesis(id="H1", statement="x", predicted_observations=["zonk=high"], confidence=0.6)
    engine().incorporate({"zonk": "low"}, Reliability.REPORTED, [h], 1, source="builder")
    # A story disagreeing only casts doubt; it cannot kill a hypothesis.
    assert h.status is HypothesisStatus.WEAKENED
    assert h.status is not HypothesisStatus.DISPROVEN


def test_prose_cannot_promote_a_hypothesis_to_supported() -> None:
    h = Hypothesis(id="H1", statement="x", predicted_observations=["a=1", "b=2", "c=3"], confidence=0.6)
    # Even a pile of matching prose signals must not make it a fact.
    for _ in range(5):
        engine().incorporate({"a": "1", "b": "2", "c": "3"}, Reliability.REPORTED, [h], 1)
    assert h.status is not HypothesisStatus.SUPPORTED


def test_a_disproven_hypothesis_is_left_alone() -> None:
    h = Hypothesis(id="H1", statement="x", predicted_observations=["z=1"], status=HypothesisStatus.DISPROVEN, confidence=0.15)
    update = engine().incorporate({"z": "1"}, Reliability.DIRECT, [h], 2)
    assert update.supported == []
    assert h.status is HypothesisStatus.DISPROVEN


def test_direct_support_promotes_over_the_threshold() -> None:
    h = Hypothesis(id="H1", statement="x", predicted_observations=["p=1", "q=1"], confidence=0.6)
    engine().incorporate({"p": "1", "q": "1"}, Reliability.DIRECT, [h], 1)
    assert h.status is HypothesisStatus.SUPPORTED
    assert h.confidence >= 0.7


# --------------------------------------------------------------------------
# Ranking, probe selection, convergence
# --------------------------------------------------------------------------


def test_ranking_puts_supported_first_then_by_confidence() -> None:
    hyps = [
        Hypothesis(id="H1", statement="a", status=HypothesisStatus.ACTIVE, confidence=0.5),
        Hypothesis(id="H2", statement="b", status=HypothesisStatus.SUPPORTED, confidence=0.8),
        Hypothesis(id="H3", statement="c", status=HypothesisStatus.ACTIVE, confidence=0.7),
    ]
    ranked = engine().rank(hyps)
    assert [h.id for h in ranked] == ["H2", "H3", "H1"]


def test_probe_selection_prefers_a_discriminating_probe() -> None:
    state = InvestigationState()
    state.hypotheses = [
        Hypothesis(id="H1", statement="a", predicted_observations=["kroon=1"]),
        Hypothesis(id="H2", statement="b", predicted_observations=["kroon=2"]),
    ]
    unrelated = Probe(id="u", question="unrelated", kind="COMMAND", command_or_action="echo x")
    discriminating = Probe(
        id="d", question="measure kroon", kind="COMMAND", command_or_action="cat kroon",
        interpretation_rules=[
            __import__("neyma_product_driver.investigation_memory", fromlist=["InterpretationRule"]).InterpretationRule(
                pattern=r"kroon=(\d+)", signal="kroon", value="$1"
            )
        ],
    )
    chosen = engine().select_probe(state, [unrelated, discriminating])
    assert chosen.id == "d"


def test_the_leaders_next_probe_is_honored_first() -> None:
    state = InvestigationState()
    state.hypotheses = [
        Hypothesis(id="H1", statement="a", status=HypothesisStatus.ACTIVE, confidence=0.6, next_probe="pref"),
    ]
    preferred = Probe(id="pref", question="q", kind="COMMAND", command_or_action="a")
    other = Probe(id="other", question="q", kind="COMMAND", command_or_action="b")
    assert engine().select_probe(state, [other, preferred]).id == "pref"


def test_an_already_run_probe_is_not_chosen_again() -> None:
    state = InvestigationState()
    state.hypotheses = [Hypothesis(id="H1", statement="a", predicted_observations=["x=1"])]
    ran = Probe(id="ran", question="q", kind="COMMAND", command_or_action="same")
    state.probes = [ran]
    again = Probe(id="again", question="q", kind="COMMAND", command_or_action="same")  # same fingerprint
    assert engine().select_probe(state, [again]) is None


def test_repeat_detection_flags_identical_probe_and_result() -> None:
    state = InvestigationState()
    probe = Probe(id="p", question="q", kind="COMMAND", command_or_action="pytest x")
    result = ProbeResult(probe_id="p", ran=True, signals={"exit_code": "1"})
    state.probes = [probe]
    state.probe_results = [result]

    again = Probe(id="p2", question="q", kind="COMMAND", command_or_action="pytest x")
    same_result = ProbeResult(probe_id="p2", ran=True, signals={"exit_code": "1"})
    assert engine().is_repeat(state, again, same_result)

    diff_result = ProbeResult(probe_id="p2", ran=True, signals={"exit_code": "0"})
    assert not engine().is_repeat(state, again, diff_result)


def test_convergence_needs_one_supported_and_no_active_rivals() -> None:
    state = InvestigationState()
    state.hypotheses = [
        Hypothesis(id="H1", statement="a", status=HypothesisStatus.SUPPORTED, confidence=0.85),
        Hypothesis(id="H2", statement="b", status=HypothesisStatus.DISPROVEN, confidence=0.1),
    ]
    assert engine().converged(state) is not None

    state.hypotheses[1].status = HypothesisStatus.ACTIVE  # a live rival remains
    assert engine().converged(state) is None


def test_convergence_needs_high_confidence() -> None:
    state = InvestigationState()
    state.hypotheses = [Hypothesis(id="H1", statement="a", status=HypothesisStatus.SUPPORTED, confidence=0.5)]
    assert engine().converged(state) is None


def test_elimination_never_rests_on_prose_alone() -> None:
    """A lone survivor supported only by a claim is not a diagnosis by elimination."""
    eng = engine()
    survivor = Hypothesis(id="H1", statement="the survivor", predicted_observations=["x=1"], confidence=0.3)
    refuted = Hypothesis(id="H2", statement="a rival", status=HypothesisStatus.DISPROVEN, confidence=0.1)
    state = InvestigationState()
    state.hypotheses = [survivor, refuted]

    # Support the survivor with PROSE only.
    eng.incorporate({"x": "1"}, Reliability.REPORTED, state.hypotheses, 1)
    assert survivor.supporting_evidence  # it has "evidence"...
    assert not survivor.has_direct_support  # ...but none of it is measured
    assert eng.converged(state) is None  # so no elimination win

    # A measured fact for the same prediction changes everything.
    eng.incorporate({"x": "1"}, Reliability.DIRECT, state.hypotheses, 2)
    assert survivor.has_direct_support
    assert eng.converged(state) is survivor


def test_elimination_needs_a_confidence_floor() -> None:
    eng = engine()
    survivor = Hypothesis(
        id="H1", statement="s", confidence=0.4, has_direct_support=True,
        supporting_evidence=["probe: x=1"],
    )
    refuted = Hypothesis(id="H2", statement="r", status=HypothesisStatus.DISPROVEN, confidence=0.1)
    state = InvestigationState()
    state.hypotheses = [survivor, refuted]
    assert eng.converged(state) is None  # 0.4 is a lead, not a conclusion
    survivor.confidence = 0.6
    assert eng.converged(state) is survivor


def test_a_flaky_probe_is_detected_as_environment_inconsistency() -> None:
    state = InvestigationState(repo_fingerprint="abc")
    probe = Probe(id="p", question="q", kind="COMMAND", command_or_action="pytest flaky")
    state.probes = [probe, probe]
    state.probe_results = [
        ProbeResult(probe_id="p", ran=True, signals={"exit_code": "0"}),
        ProbeResult(probe_id="p", ran=True, signals={"exit_code": "1"}),
    ]
    assert engine().environment_inconsistent(state)


def test_two_different_probes_sharing_a_signal_name_is_not_inconsistency() -> None:
    state = InvestigationState()
    p1 = Probe(id="p1", question="q", kind="COMMAND", command_or_action="cmd one")
    p2 = Probe(id="p2", question="q", kind="COMMAND", command_or_action="cmd two")
    state.probes = [p1, p2]
    state.probe_results = [
        ProbeResult(probe_id="p1", ran=True, signals={"exit_code": "0"}),
        ProbeResult(probe_id="p2", ran=True, signals={"exit_code": "1"}),
    ]
    assert not engine().environment_inconsistent(state)
