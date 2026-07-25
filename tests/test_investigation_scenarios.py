"""Scenario tests: the investigator diagnoses real, differently-shaped failures.

The generalization test is the important one. It builds a failure class whose
vocabulary appears nowhere in the driver's implementation, and asserts that
absence — so the test fails if the investigator ever comes to depend on matching
known keywords or fixture names.

Several scenarios reuse the protocol-layer fixtures (a stale receipt, the P3
two-content-commit deadlock) but reach the diagnosis through the generic loop —
observations, hypotheses, probes, revision — with no ``if P3`` branch anywhere.
"""

from __future__ import annotations

import sqlite3
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
from neyma_product_driver.investigator import Investigator, should_investigate
from neyma_product_driver.probe_runner import clear_predicates, register_predicate

from investigation_fixtures import MiniRepo, ScriptedReasoner, fixed_hypotheses, probe_sequence

# Reuse the protocol-layer repositories.
from protocol_fixtures import p3_deadlock_repo, one_content_commit


@pytest.fixture(autouse=True)
def _clean_predicates():
    clear_predicates()
    yield
    clear_predicates()


def investigate(repo_root: Path, reasoner, tmp_path: Path, **kw):
    inv = Investigator(repo_root, reasoner, memory=InvestigationMemory(tmp_path / "run"))
    return inv.investigate(**kw)


# --------------------------------------------------------------------------
# THE GENERALIZATION TEST
# --------------------------------------------------------------------------

# A failure class the implementation was never told about: a build manifest
# declares a "quorum" of replicas, but the seed script provisions fewer, so a
# leader election never completes and a readiness gate spins. None of these words
# — quorum, replica, election, provision — drive any code path in the driver.
NOVEL_TOKENS = ["quorum", "replica", "leader election", "provision"]


def test_generalization_diagnoses_a_failure_class_not_in_the_implementation(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.write("deploy/manifest.yaml", "quorum_required: 3\n")
    repo.write("deploy/seed.sh", "# provisions 2 replicas\nreplicas=2\n")
    repo.commit("init")

    # Real probes read the real fixture state and return real signals.
    def read_quorum(inputs):
        import yaml

        required = yaml.safe_load((repo.root / "deploy/manifest.yaml").read_text())["quorum_required"]
        return {"quorum_required": str(required)}

    def read_provisioned(inputs):
        text = (repo.root / "deploy/seed.sh").read_text()
        import re

        m = re.search(r"replicas=(\d+)", text)
        return {"replicas_provisioned": m.group(1) if m else "0"}

    register_predicate("read_quorum", read_quorum)
    register_predicate("read_provisioned", read_provisioned)

    # The reasoner offers three competing explanations. It does NOT know which is
    # true — the fixture state decides.
    hyps = fixed_hypotheses(
        Hypothesis(
            id="H1", statement="the readiness endpoint has a hardcoded false",
            predicted_observations=["endpoint_hardcoded=true"], confidence=0.34,
        ),
        Hypothesis(
            id="H2", statement="fewer replicas are provisioned than the quorum requires",
            predicted_observations=["quorum_unmet=true"], confidence=0.33,
        ),
        Hypothesis(
            id="H3", statement="the manifest file is missing entirely",
            predicted_observations=["manifest_exists=false"], confidence=0.33,
        ),
    )

    # Probe 1 measures the two numbers and computes whether quorum is unmet.
    def compute(inputs):
        req = int(read_quorum({})["quorum_required"])
        prov = int(read_provisioned({})["replicas_provisioned"])
        return {
            "quorum_required": str(req),
            "replicas_provisioned": str(prov),
            "quorum_unmet": str(prov < req).lower(),
            "manifest_exists": "true",
            "endpoint_hardcoded": "false",
        }

    register_predicate("compute_quorum", compute)
    probe = Probe(
        id="pr1", question="are enough replicas provisioned for the quorum?",
        kind="PREDICATE", command_or_action="compute_quorum", targets_hypotheses=["H1", "H2", "H3"],
    )

    reasoner = ScriptedReasoner(hyps, probe_sequence(probe))
    state = investigate(repo.root, reasoner, tmp_path, issue="readiness gate never passes")

    # The correct hypothesis wins, on evidence; the plausible rivals are refuted.
    assert state.result.status is InvestigationStatus.ROOT_CAUSE_FOUND
    assert state.hypothesis("H2").status is HypothesisStatus.SUPPORTED
    assert state.hypothesis("H1").status is HypothesisStatus.DISPROVEN
    assert state.hypothesis("H3").status is HypothesisStatus.DISPROVEN
    assert "quorum" in state.result.root_cause


def test_the_implementation_contains_none_of_the_novel_failure_vocabulary() -> None:
    """If the investigator matched keywords, these words would be in the code."""
    pkg = Path(__file__).resolve().parent.parent / "neyma_product_driver"
    sources = {
        "investigator.py",
        "hypothesis_engine.py",
        "probe_runner.py",
        "investigation_memory.py",
        "investigation_reasoner.py",
    }
    blob = "\n".join((pkg / name).read_text().lower() for name in sources)
    for token in NOVEL_TOKENS:
        assert token.lower() not in blob, (
            f"the investigator implementation mentions {token!r}; the generalization "
            "must come from the engine, not from a handler keyed on this failure class"
        )


# --------------------------------------------------------------------------
# THE P3 EVOLVING-DIAGNOSIS SEQUENCE
# --------------------------------------------------------------------------


def test_p3_socket_then_rebaseline_then_topology_evolution(tmp_path: Path) -> None:
    """The exact sequence from the brief, reached by evidence, with no if-P3 branch.

    H1 (sandbox blocks socket.bind) is DISPROVEN when socket cases pass under the
    finalizer. H2 (a guard fails alone) is SUPPORTED. Then, told H2's fix left
    status tests failing, H3 (two-content-commit topology) is SUPPORTED by the
    real commit graph — through the generic loop, not a special case.
    """
    repo = p3_deadlock_repo(tmp_path / "neyma")  # a real two-content-commit repo

    register_predicate(
        "socket_under_finalizer", lambda i: {"finalizer_socket_tests": "pass"}
    )
    register_predicate(
        "guard_alone", lambda i: {"guard_fails_alone": "true", "guard_seconds": "0.10", "guard_causes_refusal": "true"}
    )

    # Three hypotheses offered up front; the evidence sorts them.
    hyps = fixed_hypotheses(
        Hypothesis(
            id="H1", statement="the sandbox blocks socket.bind, refusing the finalizer",
            predicted_observations=["finalizer_socket_tests=fail"], confidence=0.5,
        ),
        Hypothesis(
            id="H2", statement="a rebaseline guard fails independently and refuses finalization",
            predicted_observations=["guard_fails_alone=true", "guard_causes_refusal=true"], confidence=0.4,
        ),
        Hypothesis(
            id="H3", statement="two content commits violate the finalizer-owned topology",
            predicted_observations=["topology_valid=false", "content_commit_count~=2"], confidence=0.3,
        ),
    )

    probes = probe_sequence(
        Probe(id="socket", question="do socket cases pass under the finalizer run?",
              kind="PREDICATE", command_or_action="socket_under_finalizer", targets_hypotheses=["H1"]),
        Probe(id="guard", question="does the guard fail alone and cause the refusal?",
              kind="PREDICATE", command_or_action="guard_alone", targets_hypotheses=["H2"]),
        # A built-in probe: classify commits against the finalizer-owned rule.
        Probe(id="topology", question="how many content commits since the baseline?",
              kind="CLASSIFY_COMMITS", targets_hypotheses=["H3"]),
    )

    reasoner = ScriptedReasoner(hyps, probes)
    state = investigate(
        repo.root, reasoner, tmp_path,
        issue="finalizer refuses", builder_report="socket.bind blocks finalization",
        max_iterations=8,
    )

    # H1 refuted by the socket probe (the builder's asserted cause did not hold).
    assert state.hypothesis("H1").status is HypothesisStatus.DISPROVEN
    # The commit-graph probe supported the topology hypothesis.
    assert state.hypothesis("H3").status in (HypothesisStatus.SUPPORTED, HypothesisStatus.ACTIVE)
    assert state.hypothesis("H3").supporting_evidence

    # The diagnosis moved more than once — recorded in the transitions.
    flips = [t for t in state.transitions if t.to_status is HypothesisStatus.DISPROVEN]
    assert flips  # at least the socket hypothesis died
    assert state.diagnosis_changes >= 1

    # And nowhere did a fixture name or unit id drive the logic.
    impl = (Path(__file__).resolve().parent.parent / "neyma_product_driver" / "investigator.py").read_text()
    assert '"P3"' not in impl and "'P3'" not in impl


# --------------------------------------------------------------------------
# STALE RECEIPT (reached generically, not via the auditor's handler)
# --------------------------------------------------------------------------


def test_diagnoses_a_stale_receipt_via_a_check_receipt_probe(tmp_path: Path) -> None:
    repo = one_content_commit(tmp_path / "neyma")
    repo.write_suite_receipt(commit="0" * 40, tree="1" * 40)  # a receipt for a tree that is not HEAD

    hyps = fixed_hypotheses(
        Hypothesis(id="H1", statement="the tests genuinely fail on the current tree",
                   predicted_observations=["suite_green=false"], confidence=0.4),
        Hypothesis(id="H2", statement="the suite receipt is stale — it names a different tree",
                   predicted_observations=["receipt_fresh=false", "receipt_exists=true"], confidence=0.4),
    )
    probe = Probe(id="rc", question="does the receipt match the current tree?",
                  kind="CHECK_RECEIPT", inputs={"path": "docs/implementation/SUITE-RESULT.json"},
                  targets_hypotheses=["H1", "H2"])

    reasoner = ScriptedReasoner(hyps, probe_sequence(probe))
    state = investigate(repo.root, reasoner, tmp_path, issue="suite result looks wrong")

    assert state.hypothesis("H2").status in (HypothesisStatus.SUPPORTED, HypothesisStatus.ACTIVE)
    assert state.hypothesis("H2").supporting_evidence
    assert any("receipt_fresh" in e for e in state.hypothesis("H2").supporting_evidence)


# --------------------------------------------------------------------------
# FULL-SUITE vs TARGETED-SUITE DISCREPANCY
# --------------------------------------------------------------------------


def test_diagnoses_a_full_versus_targeted_suite_discrepancy(tmp_path: Path) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    # Targeted run is green; the full suite has one deterministic failure elsewhere.
    register_predicate("targeted", lambda i: {"targeted_green": "true"})
    register_predicate(
        "full", lambda i: {"full_green": "false", "first_failure": "eval/test_unrelated.py::test_z"}
    )

    hyps = fixed_hypotheses(
        Hypothesis(id="H1", statement="the change under test is broken",
                   predicted_observations=["targeted_green=false"], confidence=0.4),
        Hypothesis(id="H2", statement="an unrelated deterministic test fails in the full suite",
                   predicted_observations=["targeted_green=true", "full_green=false"], confidence=0.4),
    )
    probes = probe_sequence(
        Probe(id="t", question="do the targeted tests pass?", kind="PREDICATE",
              command_or_action="targeted", targets_hypotheses=["H1", "H2"]),
        Probe(id="f", question="does the full suite pass?", kind="PREDICATE",
              command_or_action="full", targets_hypotheses=["H1", "H2"]),
    )
    reasoner = ScriptedReasoner(hyps, probes)
    state = investigate(repo.root, reasoner, tmp_path, issue="full suite red, targeted green")

    assert state.hypothesis("H1").status is HypothesisStatus.DISPROVEN
    assert state.hypothesis("H2").status is HypothesisStatus.SUPPORTED
    assert state.result.status is InvestigationStatus.ROOT_CAUSE_FOUND


# --------------------------------------------------------------------------
# PRODUCT UI / API / DATABASE MISMATCH
# --------------------------------------------------------------------------


def test_traces_a_product_mismatch_to_the_layer_where_it_diverges(tmp_path: Path) -> None:
    """Invoice says 'ready to pay' but a POD is missing. Which layer is wrong?"""
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")

    # A real read-only database: the backend obligation state is actually correct
    # (POD is required and missing), so the divergence is above the database.
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE obligations (load TEXT, pod_required INT, pod_present INT, payable INT)")
    con.execute("INSERT INTO obligations VALUES ('L1', 1, 0, 0)")  # required, missing, NOT payable
    con.commit()
    con.close()

    # The API layer, however, projects it as payable — the bug is the projection.
    register_predicate("api_says", lambda i: {"api_payable": "true"})
    register_predicate("ui_says", lambda i: {"ui_shows_ready": "true"})

    hyps = fixed_hypotheses(
        Hypothesis(id="H1", statement="the backend obligation state is wrong (marked payable in the db)",
                   predicted_observations=["db_payable=1"], confidence=0.25),
        Hypothesis(id="H2", statement="the API projection is stale/incorrect (db not payable, api payable)",
                   predicted_observations=["db_payable=0", "api_payable=true"], confidence=0.25),
        Hypothesis(id="H3", statement="the UI wording is wrong while the API is correct",
                   predicted_observations=["api_payable=false", "ui_shows_ready=true"], confidence=0.25),
        Hypothesis(id="H4", statement="the POD is simply not required under the active policy",
                   predicted_observations=["db_pod_required=0"], confidence=0.25),
    )

    # Trace the layers: db → api → ui.
    probes = probe_sequence(
        Probe(id="db", question="what does the database say?", kind="SQL_READONLY",
              command_or_action="SELECT payable FROM obligations WHERE load='L1'",
              inputs={"db": str(db)},
              interpretation_rules=[InterpretationRule(pattern=r"(\d+)", signal="db_payable", value="$1")],
              targets_hypotheses=["H1", "H2", "H4"]),
        Probe(id="dbpod", question="is a POD required in the database?", kind="SQL_READONLY",
              command_or_action="SELECT pod_required FROM obligations WHERE load='L1'",
              inputs={"db": str(db)},
              interpretation_rules=[InterpretationRule(pattern=r"(\d+)", signal="db_pod_required", value="$1")],
              targets_hypotheses=["H4"]),
        Probe(id="api", question="what does the API project?", kind="PREDICATE",
              command_or_action="api_says", targets_hypotheses=["H2", "H3"]),
    )
    reasoner = ScriptedReasoner(hyps, probes)
    state = investigate(repo.root, reasoner, tmp_path, issue="invoice shows ready to pay but POD is missing")

    # The database is correct (not payable, POD required); the API projection is
    # the layer that diverges.
    assert state.hypothesis("H1").status is HypothesisStatus.DISPROVEN
    assert state.hypothesis("H4").status is HypothesisStatus.DISPROVEN
    assert state.hypothesis("H2").status in (HypothesisStatus.SUPPORTED, HypothesisStatus.ACTIVE)
    assert state.hypothesis("H2").supporting_evidence


# --------------------------------------------------------------------------
# Triggers
# --------------------------------------------------------------------------


def test_triggers_fire_on_the_documented_conditions() -> None:
    assert should_investigate(explicit=True)[0]

    class FakeAudit:
        contradictions = ["something"]
        correction_prompt = ""

    assert should_investigate(audit=FakeAudit())[0]

    assert should_investigate(prior_failures=["same", "same"])[0]
    assert should_investigate(evaluator_confidence=0.2)[0]
    assert should_investigate(builder_report="the sandbox blocks socket.bind")[0]
    # Nothing wrong → no investigation.
    assert not should_investigate()[0]


def test_an_environment_blocker_without_evidence_triggers_investigation() -> None:
    class Blocker:
        evidence = []

    class FakeProtocol:
        environment_blockers = [Blocker()]
        status = type("S", (), {"value": "BLOCKED_ENVIRONMENT"})()

    should, reason = should_investigate(protocol=FakeProtocol())
    assert should
