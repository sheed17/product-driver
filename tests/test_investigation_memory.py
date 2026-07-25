"""Investigation records: the taxonomy, prediction matching, and persistence."""

from __future__ import annotations

from pathlib import Path

from neyma_product_driver.investigation_memory import (
    EpistemicStatus,
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
    match_prediction,
    render_timeline,
)


# --------------------------------------------------------------------------
# The epistemic taxonomy
# --------------------------------------------------------------------------


def test_direct_evidence_is_a_fact_reported_evidence_is_not() -> None:
    direct = Observation(source="git", reliability=Reliability.DIRECT, content="HEAD abc")
    reported = Observation(source="builder", reliability=Reliability.REPORTED, content="it works")

    assert direct.epistemic is EpistemicStatus.FACT
    assert reported.epistemic is EpistemicStatus.INFERENCE
    assert Reliability.DIRECT.weight > Reliability.REPORTED.weight


def test_a_hypothesis_is_never_a_fact() -> None:
    h = Hypothesis(id="H1", statement="x", status=HypothesisStatus.SUPPORTED, confidence=0.9)
    # Even fully supported, it is an inference — never promoted to FACT.
    assert h.epistemic is EpistemicStatus.INFERENCE
    assert Hypothesis(id="H2", statement="y").epistemic is EpistemicStatus.HYPOTHESIS
    assert (
        Hypothesis(id="H3", statement="z", status=HypothesisStatus.DISPROVEN).epistemic
        is EpistemicStatus.DISPROVEN
    )


def test_status_ranking_orders_supported_over_disproven() -> None:
    assert HypothesisStatus.SUPPORTED.rank > HypothesisStatus.ACTIVE.rank
    assert HypothesisStatus.ACTIVE.rank > HypothesisStatus.DISPROVEN.rank
    assert HypothesisStatus.SUPPORTED.is_alive
    assert not HypothesisStatus.DISPROVEN.is_alive


# --------------------------------------------------------------------------
# Prediction matching — the domain-blind mechanism
# --------------------------------------------------------------------------


def test_prediction_equality_supports_and_contradicts() -> None:
    assert match_prediction("socket_tests=pass", {"socket_tests": "pass"}) == "support"
    assert match_prediction("socket_tests=pass", {"socket_tests": "fail"}) == "contradict"
    assert match_prediction("socket_tests=pass", {"other": "x"}) is None


def test_prediction_inequality_and_regex() -> None:
    assert match_prediction("count!=0", {"count": "2"}) == "support"
    assert match_prediction("count!=0", {"count": "0"}) == "contradict"
    assert match_prediction("trace~=socket", {"trace": "at socket.bind()"}) == "support"
    assert match_prediction("trace~=socket", {"trace": "at db.query()"}) == "contradict"


def test_prediction_is_case_insensitive() -> None:
    assert match_prediction("valid=TRUE", {"valid": "true"}) == "support"


def test_a_probe_fingerprint_is_stable_for_the_same_action() -> None:
    a = Probe(id="p1", question="q", kind="COMMAND", command_or_action="pytest x", inputs={"n": 1})
    b = Probe(id="p2", question="other", kind="COMMAND", command_or_action="pytest x", inputs={"n": 1})
    c = Probe(id="p3", question="q", kind="COMMAND", command_or_action="pytest y", inputs={"n": 1})
    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() != c.fingerprint()


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def _state() -> InvestigationState:
    state = InvestigationState(run_id="r1", issue="finalizer refuses", max_iterations=8)
    state.observations = [
        Observation(id="O1", source="git", reliability=Reliability.DIRECT, content="HEAD abc123"),
        Observation(
            id="O2", source="builder", reliability=Reliability.REPORTED, content="socket blocks it"
        ),
    ]
    state.hypotheses = [
        Hypothesis(id="H1", statement="socket denied", status=HypothesisStatus.DISPROVEN, confidence=0.15),
        Hypothesis(id="H2", statement="topology invalid", status=HypothesisStatus.SUPPORTED, confidence=0.9),
    ]
    state.probes = [Probe(id="pr1", question="classify commits", kind="CLASSIFY_COMMITS")]
    state.probe_results = [ProbeResult(probe_id="pr1", ran=True, signals={"content_commit_count": "2"})]
    return state


def test_investigation_persists_every_record_family(tmp_path: Path) -> None:
    memory = InvestigationMemory(tmp_path / "run")
    memory.save(_state())

    inv = tmp_path / "run" / "investigation"
    for name in ("state.json", "observations.json", "contradictions.json", "hypotheses.json", "probes.json", "timeline.md"):
        assert (inv / name).exists(), name


def test_investigation_state_reloads_from_disk(tmp_path: Path) -> None:
    memory = InvestigationMemory(tmp_path / "run")
    memory.save(_state())

    loaded = memory.load()
    assert loaded is not None
    assert loaded.run_id == "r1"
    assert loaded.hypothesis("H2").status is HypothesisStatus.SUPPORTED
    assert loaded.leading().id == "H2"


def test_the_timeline_shows_how_the_diagnosis_moved(tmp_path: Path) -> None:
    state = _state()
    from neyma_product_driver.investigation_memory import HypothesisTransition

    state.transitions = [
        HypothesisTransition(
            iteration=1, hypothesis_id="H1",
            from_status=HypothesisStatus.ACTIVE, to_status=HypothesisStatus.DISPROVEN,
            from_confidence=0.5, to_confidence=0.15, reason="refuted by probe pr1",
        )
    ]
    timeline = render_timeline(state)

    assert "How the diagnosis moved" in timeline
    assert "H1 ACTIVE→DISPROVEN" in timeline
    assert "DISPROVEN" in timeline and "SUPPORTED" in timeline


def test_secrets_are_redacted_on_persistence(tmp_path: Path) -> None:
    state = _state()
    state.observations.append(
        Observation(
            id="O9", source="env", reliability=Reliability.DIRECT,
            content="API_KEY=sk-ant-abcdefgh12345678 leaked into a log",
        )
    )
    memory = InvestigationMemory(tmp_path / "run")
    memory.save(state)

    text = (tmp_path / "run" / "investigation" / "observations.json").read_text()
    assert "sk-ant-abcdefgh12345678" not in text
    assert "REDACTED" in text


def test_the_leading_hypothesis_is_the_highest_alive_one() -> None:
    state = InvestigationState()
    state.hypotheses = [
        Hypothesis(id="H1", statement="a", status=HypothesisStatus.DISPROVEN, confidence=0.9),
        Hypothesis(id="H2", statement="b", status=HypothesisStatus.ACTIVE, confidence=0.4),
        Hypothesis(id="H3", statement="c", status=HypothesisStatus.SUPPORTED, confidence=0.8),
    ]
    assert state.leading().id == "H3"  # supported outranks active, disproven is skipped


def test_the_result_summary_labels_rejected_hypotheses() -> None:
    result = InvestigationResult(
        status=InvestigationStatus.ROOT_CAUSE_FOUND,
        root_cause="two content commits",
        confidence=0.94,
        rejected_hypotheses=["H1: sandbox socket denial"],
        evidence=["content_commit_count=2"],
    )
    block = result.summary_block()
    assert "ROOT CAUSE FOUND" in block
    assert "0.94" in block
    assert "do not repeat" in block
    assert "H1: sandbox socket denial" in block
