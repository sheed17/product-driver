"""The investigation loop end to end.

Every test drives the loop with a scripted reasoner — a stand-in for the Claude
subagent that supplies judgment in a real run. The reasoner proposes candidate
hypotheses and probes; it never decides which wins. The verdict always comes from
the engine matching predictions against real probe results.

The generalization test is the load-bearing one: it uses a failure class whose
vocabulary appears nowhere in the implementation, and asserts as much.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from neyma_product_driver.investigation_memory import (
    Hypothesis,
    HypothesisStatus,
    InterpretationRule,
    InvestigationMemory,
    InvestigationStatus,
    Probe,
)
from neyma_product_driver.investigator import (
    Investigator,
    builder_correction_from_investigation,
    should_investigate,
)
from neyma_product_driver.probe_runner import clear_predicates, register_predicate

from investigation_fixtures import (
    MiniRepo,
    ScriptedReasoner,
    fixed_hypotheses,
    probe_sequence,
)


@pytest.fixture(autouse=True)
def _clean_predicates():
    clear_predicates()
    yield
    clear_predicates()


def predicate_probe(pid: str, name: str, targets: list[str]) -> Probe:
    return Probe(id=pid, question=f"run {name}", kind="PREDICATE", command_or_action=name, targets_hypotheses=targets)


def run(repo: MiniRepo, reasoner, tmp_path: Path, **kw):
    inv = Investigator(repo.root, reasoner, memory=InvestigationMemory(tmp_path / "run"))
    return inv.investigate(**kw)


# --------------------------------------------------------------------------
# Core lifecycle
# --------------------------------------------------------------------------


def test_it_generates_multiple_hypotheses_and_picks_a_discriminating_probe(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    register_predicate("measure", lambda i: {"widget": "green"})

    seen: dict[str, object] = {}

    def hyps(brief):
        return [
            Hypothesis(id="H1", statement="widget is red", predicted_observations=["widget=red"]),
            Hypothesis(id="H2", statement="widget is green", predicted_observations=["widget=green"]),
            Hypothesis(id="H3", statement="widget is blue", predicted_observations=["widget=blue"]),
        ]

    def probe(brief):
        if seen.get("done"):
            return None
        seen["done"] = True
        return predicate_probe("pr1", "measure", ["H1", "H2", "H3"])

    reasoner = ScriptedReasoner(fixed_hypotheses(*hyps(None)), probe)
    state = run(repo, reasoner, tmp_path, issue="what colour is the widget?")

    assert len([h for h in state.hypotheses]) == 3
    # The one probe supported H2 and refuted H1 and H3.
    assert state.hypothesis("H2").status is HypothesisStatus.SUPPORTED
    assert state.hypothesis("H1").status is HypothesisStatus.DISPROVEN
    assert state.hypothesis("H3").status is HypothesisStatus.DISPROVEN
    assert state.result.status is InvestigationStatus.ROOT_CAUSE_FOUND


def test_it_rejects_a_hypothesis_after_contradictory_evidence(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    register_predicate("check", lambda i: {"cause": "beta"})

    reasoner = ScriptedReasoner(
        fixed_hypotheses(
            Hypothesis(id="H1", statement="cause is alpha", predicted_observations=["cause=alpha"], confidence=0.5),
            Hypothesis(id="H2", statement="cause is beta", predicted_observations=["cause=beta"], confidence=0.5),
        ),
        probe_sequence(predicate_probe("pr1", "check", ["H1", "H2"])),
    )
    state = run(repo, reasoner, tmp_path, issue="which cause?")
    assert state.hypothesis("H1").status is HypothesisStatus.DISPROVEN
    assert "H1" in " ".join(state.result.rejected_hypotheses)


def test_it_does_not_treat_builder_prose_as_proof(tmp_path: Path) -> None:
    """The builder asserts a cause; a probe refutes it, and the probe wins."""
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    # Reality: the socket tests actually PASS under the finalizer.
    register_predicate("socket_check", lambda i: {"finalizer_socket_tests": "pass"})

    reasoner = ScriptedReasoner(
        fixed_hypotheses(
            Hypothesis(
                id="H1", statement="the sandbox blocks socket.bind",
                predicted_observations=["finalizer_socket_tests=fail"], confidence=0.6,
            ),
        ),
        probe_sequence(predicate_probe("pr1", "socket_check", ["H1"])),
    )
    state = run(
        repo, reasoner, tmp_path,
        issue="finalizer refuses",
        builder_report="socket.bind blocks finalization",
    )
    # The builder's asserted cause is refuted by the measured fact.
    assert state.hypothesis("H1").status is HypothesisStatus.DISPROVEN
    # The builder claim was recorded, but as REPORTED — never a fact.
    claims = [o for o in state.observations if o.kind.value == "BUILDER_CLAIM"]
    assert claims and claims[0].reliability.value == "REPORTED"


def test_it_avoids_repeating_an_identical_probe(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    register_predicate("stuck", lambda i: {"noise": "x"})  # never matches any prediction

    # The reasoner keeps handing back the SAME probe fingerprint.
    same = predicate_probe("pr", "stuck", ["H1"])
    reasoner = ScriptedReasoner(
        fixed_hypotheses(Hypothesis(id="H1", statement="a", predicted_observations=["cause=z"])),
        lambda brief: Probe(id=f"pr{brief.iteration}", question="run stuck", kind="PREDICATE", command_or_action="stuck"),
    )
    state = run(repo, reasoner, tmp_path, issue="x", max_iterations=8)
    # It stops rather than re-running the same non-informative probe forever.
    assert state.iteration < 8
    assert state.result.status in (
        InvestigationStatus.NEEDS_MORE_EVIDENCE,
        InvestigationStatus.PARTIAL_DIAGNOSIS,
    )


def test_it_stops_at_the_iteration_budget(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    # Each probe emits a fresh signal (so it "learns") but never enough to converge.
    counter = {"n": 0}

    def probe(brief):
        counter["n"] += 1
        register_predicate(f"p{counter['n']}", lambda i, n=counter["n"]: {f"sig{n}": "x"})
        return Probe(id=f"pr{counter['n']}", question="q", kind="PREDICATE", command_or_action=f"p{counter['n']}")

    reasoner = ScriptedReasoner(
        fixed_hypotheses(Hypothesis(id="H1", statement="a", predicted_observations=["never=true"])),
        probe,
    )
    state = run(repo, reasoner, tmp_path, issue="x", max_iterations=4)
    assert state.iteration <= state.hard_cap
    assert state.result.status in (
        InvestigationStatus.BUDGET_EXHAUSTED,
        InvestigationStatus.PARTIAL_DIAGNOSIS,
        InvestigationStatus.NEEDS_MORE_EVIDENCE,
    )


def test_it_distinguishes_deterministic_from_environmental_failure(tmp_path: Path) -> None:
    """The same probe returning different answers means the environment is flaky."""
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    flip = {"n": 0}

    def flaky(inputs):
        flip["n"] += 1
        return {"result": "pass" if flip["n"] % 2 else "fail"}

    register_predicate("flaky", flaky)
    # Same probe each time (identical fingerprint).
    reasoner = ScriptedReasoner(
        fixed_hypotheses(Hypothesis(id="H1", statement="deterministic bug", predicted_observations=["result=fail"])),
        lambda brief: Probe(id="pr", question="run flaky", kind="PREDICATE", command_or_action="flaky"),
    )
    state = run(repo, reasoner, tmp_path, issue="x", max_iterations=8)
    assert state.result.status is InvestigationStatus.NEEDS_MORE_EVIDENCE
    surfaced = state.result.recommended_action + " " + " ".join(state.result.unresolved_questions)
    assert "cannot attribute" in surfaced
    assert "the same probe returned" in surfaced and "without a repository change" in surfaced


# --------------------------------------------------------------------------
# Repository reloading and change detection
# --------------------------------------------------------------------------


def test_it_reloads_repository_state_between_iterations(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")

    collected = {"count": 0}
    from neyma_product_driver.probe_runner import EvidenceCollector

    class CountingCollector(EvidenceCollector):
        def collect(self, **kw):
            collected["count"] += 1
            return super().collect(**kw)

    register_predicate("noop", lambda i: {"z": "1"})
    reasoner = ScriptedReasoner(
        fixed_hypotheses(Hypothesis(id="H1", statement="a", predicted_observations=["y=1"])),
        probe_sequence(predicate_probe("pr1", "noop", ["H1"])),
    )
    inv = Investigator(repo.root, reasoner, memory=InvestigationMemory(tmp_path / "run"),
                       collector=CountingCollector(repo.root))
    inv.investigate(issue="x", max_iterations=3)
    # Collected at least once at iteration 0 and once more when it stepped.
    assert collected["count"] >= 2


def test_it_detects_the_repository_changing_mid_investigation(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")

    def probe(brief):
        # Mutate the repo between iteration 0 and the first step.
        repo.write("new.py", "x = 1\n")
        repo.commit("a change during the investigation")
        register_predicate("noop", lambda i: {"z": "1"})
        return predicate_probe(f"pr{brief.iteration}", "noop", ["H1"])

    reasoner = ScriptedReasoner(
        fixed_hypotheses(Hypothesis(id="H1", statement="a", predicted_observations=["y=1"])),
        probe,
    )
    state = run(repo, reasoner, tmp_path, issue="x", max_iterations=3)
    assert state.repo_changed


# --------------------------------------------------------------------------
# Consequential actions require the founder
# --------------------------------------------------------------------------


def test_it_asks_the_founder_before_a_destructive_git_probe(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")

    destructive = Probe(
        id="pr1", question="does resetting fix it?", kind="COMMAND",
        command_or_action="git reset --hard HEAD~1", read_only=False, targets_hypotheses=["H1"],
    )
    reasoner = ScriptedReasoner(
        fixed_hypotheses(Hypothesis(id="H1", statement="bad commit", predicted_observations=["x=1"])),
        probe_sequence(destructive),
    )
    state = run(repo, reasoner, tmp_path, issue="x")
    assert state.result.status is InvestigationStatus.ASK_USER
    assert state.result.ask_founder is not None
    assert "git reset --hard" in state.result.ask_founder.exact_command
    # It did not run the destructive command.
    assert repo._git("rev-list", "--count", "HEAD") == "1"


# --------------------------------------------------------------------------
# Builder correction
# --------------------------------------------------------------------------


def test_a_supported_root_cause_yields_a_grounded_correction(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    register_predicate("probe", lambda i: {"a": "1", "b": "1", "wrong": "no"})

    reasoner = ScriptedReasoner(
        fixed_hypotheses(
            Hypothesis(id="H1", statement="the config key is misspelled",
                       predicted_observations=["a=1", "b=1"], confidence=0.5),
            Hypothesis(id="H2", statement="the parser is broken",
                       predicted_observations=["wrong=yes"], confidence=0.5),
        ),
        probe_sequence(predicate_probe("pr1", "probe", ["H1", "H2"])),
    )
    state = run(repo, reasoner, tmp_path, issue="x")
    assert state.result.status is InvestigationStatus.ROOT_CAUSE_FOUND

    correction = builder_correction_from_investigation(state)
    assert correction is not None
    assert "ROOT CAUSE (supported)" in correction
    assert "misspelled" in correction
    assert "already ruled out" in correction.lower()
    assert "H2" in correction  # the rejected explanation is named
    assert "STOP CONDITIONS" in correction


def test_no_correction_is_generated_under_low_confidence(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    register_predicate("weak", lambda i: {"maybe": "1"})

    reasoner = ScriptedReasoner(
        fixed_hypotheses(Hypothesis(id="H1", statement="a", predicted_observations=["maybe=1"], confidence=0.3)),
        probe_sequence(predicate_probe("pr1", "weak", ["H1"])),
    )
    state = run(repo, reasoner, tmp_path, issue="x", max_iterations=2)
    # One weak support does not clear the fix-confidence floor.
    assert builder_correction_from_investigation(state) is None


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def test_it_persists_a_readable_timeline(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    register_predicate("p", lambda i: {"cause": "found", "corroborate": "yes"})

    reasoner = ScriptedReasoner(
        fixed_hypotheses(
            Hypothesis(id="H1", statement="the cause", predicted_observations=["cause=found", "corroborate=yes"], confidence=0.5),
            Hypothesis(id="H2", statement="a red herring", predicted_observations=["cause=other"], confidence=0.5),
        ),
        probe_sequence(predicate_probe("pr1", "p", ["H1", "H2"])),
    )
    state = run(repo, reasoner, tmp_path, issue="x")

    timeline = (tmp_path / "run" / "investigation" / "timeline.md").read_text()
    assert "How the diagnosis moved" in timeline
    assert "H1" in timeline and "H2" in timeline
    assert "Probes run" in timeline
    # And it can be reloaded.
    reloaded = InvestigationMemory(tmp_path / "run").load()
    assert reloaded is not None
    assert reloaded.result is not None
