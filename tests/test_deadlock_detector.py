"""Deadlock detection: a named causal cycle, not the word BLOCKED.

The negative tests matter most here. A detector that finds a cycle in every
stuck repository is not detecting anything.
"""

from __future__ import annotations

from pathlib import Path

from neyma_product_driver.deadlock_detector import (
    DeadlockDetector,
    GateObserver,
    GateState,
    find_cycles,
)
from neyma_product_driver.git_topology import GitTopologyAnalyzer
from neyma_product_driver.protocol_resolver import ProtocolResolver
from neyma_product_driver.protocol_sources import ViolationType, discover_protocol

from protocol_fixtures import (
    CLAUDE_MD,
    TLS_ERROR,
    baseline_repo,
    content_plus_finalizer_metadata,
    p3_deadlock_repo,
    two_content_commits,
)


def observe(repo, *, run_commands=(), topology_violated: bool | None = None):
    protocol = discover_protocol(repo.root)
    topology = GitTopologyAnalyzer(repo.root, protocol).analyze()
    violations = ProtocolResolver(repo.root).topology_violations(protocol, topology)
    if topology_violated is None:
        topology_violated = any(v.violation_type is ViolationType.HISTORY for v in violations)
    gates = GateObserver(repo.root, protocol, topology).observe(
        topology_violated=topology_violated, run_commands=run_commands
    )
    return protocol, topology, gates, violations


def detect(repo, **kw):
    protocol, topology, gates, violations = observe(repo, **kw)
    return DeadlockDetector(protocol).detect(topology, gates, violations)


# --------------------------------------------------------------------------
# The graph algorithm
# --------------------------------------------------------------------------


def test_find_cycles_finds_a_closed_loop() -> None:
    cycles = find_cycles([("a", "b"), ("b", "c"), ("c", "a")])
    assert len(cycles) == 1
    assert set(cycles[0]) == {"a", "b", "c"}


def test_find_cycles_ignores_an_open_chain() -> None:
    assert find_cycles([("a", "b"), ("b", "c"), ("c", "d")]) == []


def test_the_same_loop_is_reported_once_however_it_is_entered() -> None:
    cycles = find_cycles([("a", "b"), ("b", "a"), ("b", "c"), ("c", "b")])
    assert len(cycles) == 2


# --------------------------------------------------------------------------
# The finalization cycle
# --------------------------------------------------------------------------


def test_the_finalization_cycle_is_detected(tmp_path: Path) -> None:
    deadlocks = detect(p3_deadlock_repo(tmp_path / "neyma"))

    assert len(deadlocks) == 1
    cycle = deadlocks[0].cycle
    assert cycle[0] == "commit_topology"
    assert {"status_reality", "canonical_suite", "finalizer", "derived_status"} <= set(cycle)
    assert deadlocks[0].violation_type is ViolationType.HISTORY
    assert deadlocks[0].requires_human_approval


def test_every_edge_in_the_cycle_cites_a_rule_and_evidence(tmp_path: Path) -> None:
    deadlock = detect(p3_deadlock_repo(tmp_path / "neyma"))[0]

    assert len(deadlock.edges) == len(deadlock.cycle)
    assert all(e.evidence for e in deadlock.edges)
    assert sum(1 for e in deadlock.edges if e.rule_id) >= 3


def test_the_cycle_reads_as_a_causal_chain(tmp_path: Path) -> None:
    deadlock = detect(p3_deadlock_repo(tmp_path / "neyma"))[0]
    rendered = deadlock.render_cycle()

    assert rendered.startswith("commit topology")
    assert "status-reality guard [FAILING]" in rendered
    assert "canonical suite [FAILING]" in rendered
    assert "finalizer [NOT_RUN]" in rendered
    assert rendered.endswith("back to: commit topology since the authorized baseline [VIOLATED]")


def test_break_options_name_what_breaking_the_cycle_would_cost(tmp_path: Path) -> None:
    deadlock = detect(p3_deadlock_repo(tmp_path / "neyma"))[0]
    joined = " ".join(deadlock.break_options)

    assert "requires human approval" in joined
    assert "manual finalization is NOT available" in joined


# --------------------------------------------------------------------------
# No false deadlocks
# --------------------------------------------------------------------------


def test_a_valid_topology_produces_no_deadlock(tmp_path: Path) -> None:
    assert detect(content_plus_finalizer_metadata(tmp_path / "neyma")) == []


def test_no_deadlock_when_the_finalizer_is_free_to_repair_the_state(tmp_path: Path) -> None:
    """A green canonical suite means the finalizer can run: no cycle, just a violation."""
    repo = two_content_commits(tmp_path / "neyma")
    repo.write_suite_receipt(failed=0, exit_status=0)

    _protocol, _topology, gates, violations = observe(repo)
    assert gates.canonical_suite is GateState.PASSING
    assert violations, "the topology is still wrong"
    assert detect(repo) == []


def test_no_deadlock_when_the_repository_authorizes_manual_finalization(tmp_path: Path) -> None:
    """An authorized escape hatch means the loop is not closed."""
    authorizing = CLAUDE_MD.replace(
        "Manually editing derived status is forbidden.",
        "Manual finalization is explicitly authorized when the finalizer cannot run.",
    ).replace(
        "Derived status is owned exclusively by the finalizer.",
        "Derived status is normally written by the finalizer.",
    )
    repo = two_content_commits(tmp_path / "neyma", claude=authorizing)
    assert detect(repo) == []


def test_no_deadlock_from_rules_the_repository_does_not_state(tmp_path: Path) -> None:
    silent = "# CLAUDE.md\n\n## Authority\n\nThis file outranks all others.\n"
    repo = two_content_commits(
        tmp_path / "neyma", claude=silent, commit_protocol=None, guard=False, finalizer=False
    )
    assert detect(repo) == []


# --------------------------------------------------------------------------
# Other cycles
# --------------------------------------------------------------------------


def test_receipt_freshness_against_a_moving_head_is_a_cycle(tmp_path: Path) -> None:
    """The receipt must name the final HEAD, and committing it moves HEAD."""
    demanding = CLAUDE_MD.replace(
        "A receipt MUST name the commit and tree it validated;",
        "A receipt MUST name the final HEAD and the final tree;",
    )
    repo = baseline_repo(tmp_path / "neyma", claude=demanding)
    repo.write("src/kernel.py", "def kernel():\n    return 4\n")
    content = repo.commit("content", "src/kernel.py")
    repo.write_suite_receipt(commit=content, tree=repo.tree_of(content))
    repo.commit("record the receipt", "docs/implementation/SUITE-RESULT.json")

    deadlocks = detect(repo)
    assert any("receipt_freshness" in d.cycle for d in deadlocks)
    receipt_deadlock = next(d for d in deadlocks if "receipt_freshness" in d.cycle)
    assert "receipt-freshness deadlock" in receipt_deadlock.root_cause
    assert receipt_deadlock.violation_type is ViolationType.EVIDENCE


def test_a_guard_validating_its_own_moving_baseline_is_a_cycle(tmp_path: Path) -> None:
    repo = two_content_commits(tmp_path / "neyma")
    repo.write(
        "eval/test_status_reality.py",
        '"""The status-reality guard."""\n\n\n'
        "def test_baseline_matches() -> None:\n"
        "    # The guard validates the authorized baseline recorded in BUILD-STATUS.\n"
        "    assert recorded_baseline() == observed_baseline()\n",
    )
    repo.commit("guard reads the baseline", "eval/test_status_reality.py")

    assert any("baseline_value" in d.cycle for d in detect(repo))


# --------------------------------------------------------------------------
# Gate observation
# --------------------------------------------------------------------------


def test_a_tls_failure_is_an_environment_blocker_not_a_gate_failure(tmp_path: Path) -> None:
    _p, _t, gates, _v = observe(p3_deadlock_repo(tmp_path / "neyma"))

    assert gates.clean_clone is GateState.BLOCKED
    assert gates.environment_blockers
    blocker = gates.environment_blockers[0]
    assert blocker.classification is ViolationType.ENVIRONMENT
    assert "TLS/certificate" in blocker.description
    assert blocker.blocks == "clean_clone"


def test_a_tls_failure_in_this_runs_output_is_also_seen(tmp_path: Path) -> None:
    class FakeCommand:
        command = "pip install -r requirements.txt"
        stdout = ""
        stderr = TLS_ERROR

    repo = two_content_commits(tmp_path / "neyma")
    _p, _t, gates, _v = observe(repo, run_commands=[FakeCommand()])

    assert gates.clean_clone is GateState.BLOCKED
    assert gates.environment_blockers


def test_a_failing_status_reality_node_is_read_from_the_suite_receipt(tmp_path: Path) -> None:
    _p, _t, gates, _v = observe(p3_deadlock_repo(tmp_path / "neyma"))

    assert gates.status_reality is GateState.FAILING
    assert not gates.status_reality_predicted
    assert "failing node" in gates.status_reality_detail
    assert gates.guard_in_canonical_suite


def test_a_predicted_guard_failure_is_labelled_as_a_prediction(tmp_path: Path) -> None:
    repo = two_content_commits(tmp_path / "neyma")  # no suite receipt at all
    _p, _t, gates, _v = observe(repo)

    assert gates.status_reality is GateState.FAILING
    assert gates.status_reality_predicted
    assert "not an executed result" in gates.status_reality_detail


def test_a_refusing_finalizer_is_read_as_blocked_not_failing(tmp_path: Path) -> None:
    repo = two_content_commits(tmp_path / "neyma")
    repo.write_build_status(finalizer_result="REFUSED - canonical suite is red")
    _p, _t, gates, _v = observe(repo)

    assert gates.finalizer is GateState.BLOCKED


def test_an_unreadable_receipt_is_not_a_pass_and_does_not_crash(tmp_path: Path) -> None:
    """A receipt is written by another program and may say anything at all."""
    repo = two_content_commits(tmp_path / "neyma")
    (repo.impl / "SUITE-RESULT.json").write_text(
        '{"commit": "abc", "exit_status": null, "failed": "2 of 30", "passed": []}'
    )

    _p, _t, gates, _v = observe(repo)
    assert gates.canonical_suite is GateState.FAILING


def test_a_certificate_error_elsewhere_does_not_un_run_a_passing_gate(tmp_path: Path) -> None:
    class FakeCommand:
        command = "curl https://api.internal/health"
        stdout = "checked certificate verify failed handling in the retry path"
        stderr = ""

    repo = two_content_commits(tmp_path / "neyma")
    repo.write_gate_receipt(passed=True)

    _p, _t, gates, _v = observe(repo, run_commands=[FakeCommand()])
    assert gates.clean_clone is GateState.PASSING
    assert not gates.environment_blockers


def test_a_finalizer_result_for_another_tree_does_not_count_as_run(tmp_path: Path) -> None:
    repo = two_content_commits(tmp_path / "neyma")
    repo.write_build_status(
        content_commit="0" * 40, finalizer_result="EXECUTED - generated_by: finalize_status.py"
    )
    _p, _t, gates, _v = observe(repo)

    assert gates.finalizer is GateState.NOT_RUN
    assert "does not validate this work" in gates.finalizer_detail
