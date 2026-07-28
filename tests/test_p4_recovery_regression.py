"""Regressions for the P4 recovery campaign.

Every test here corresponds to a defect that actually fired against the real
product repository, and each one names the behaviour that must never return.

The campaign's origin: a legal repository — a certified pair plus one fresh
content commit, with an implementation episode in the working tree — was read as
corrupt. The driver ranked a history rewrite first, offered to weaken the
commit-topology protocol second, and a builder then rebuilt P4 content on the
wrong baseline, moved the branch with `update-ref`, and left the episode
recoverable only because a preservation ref happened to exist.

Fixtures A–I below reproduce that whole chain.

No test in this file touches the real product repository: every one builds a
disposable repository in a temp directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from neyma_product_driver.command_guard import classify_command
from neyma_product_driver.git_topology import CommitRole, GitTopologyAnalyzer
from neyma_product_driver.journal_integrity import (
    JournalEvidenceMissing,
    log_absence_proves_nothing,
    require_run_evidence,
    verify_run_evidence,
)
from neyma_product_driver.ownership import (
    BUILDER_LOCK_NAME,
    FINALIZER_LOCK_NAME,
    LockHeld,
    RepoLock,
    builder_owns_worktree,
    finalizer_lock,
    finalizer_running,
)
from neyma_product_driver.protocol_resolver import ProtocolResolver, ProtocolStatus
from neyma_product_driver.protocol_sources import discover_protocol
from neyma_product_driver.remediation_planner import (
    ApprovalRecord,
    BaselineMismatch,
    ApprovalExpired,
    amendment_admissibility,
    assert_approved_baseline,
    assert_live_approval,
    certified_pair_commits,
    topology_fingerprint,
    working_state_fingerprint,
)
from neyma_product_driver.repo_state import RepositoryState, resolve_state
from neyma_product_driver.worktree_state import (
    capture_worktree_state,
    preserve_worktree,
    verify_worktree_restored,
)

from tests.protocol_fixtures import (
    baseline_repo,
    content_plus_finalizer_metadata,
    finalized_pair_with_content_baseline_pointer,
    producing_after_certified_pair,
    two_content_commits,
)


def _analyzer(repo_root: Path) -> GitTopologyAnalyzer:
    return GitTopologyAnalyzer(repo_root, discover_protocol(repo_root))


# ==========================================================================
# A. The legal P4 state: certified pair → next content → WIP worktree
# ==========================================================================


def test_A_legal_producing_state_is_consistent(tmp_path: Path) -> None:
    """PRODUCING, no blocker, no normalization, no protocol amendment.

    The exact shape of 3d231731b8b0 + f1e8e18 + 72512b9 with an EP-1 working
    tree. The driver used to call this corrupt and recommend rewriting it.
    """
    repo = producing_after_certified_pair(tmp_path / "neyma")
    resolution = ProtocolResolver(repo.root).resolve()

    assert resolution.topology is not None
    assert resolution.topology.state.state is RepositoryState.PRODUCING
    assert resolution.status is ProtocolStatus.CONSISTENT, resolution.render_report()
    assert resolution.violations == []
    assert resolution.options == []
    assert resolution.recommended_option is None


def test_A_legal_producing_state_emits_no_protocol_amendment(tmp_path: Path) -> None:
    """The APPROVE ... PROTOCOL AMENDMENT option must not exist for a legal state."""
    repo = producing_after_certified_pair(tmp_path / "neyma")
    resolution = ProtocolResolver(repo.root).resolve()

    phrases = " ".join(o.approval_phrase for o in resolution.options)
    assert "PROTOCOL AMENDMENT" not in phrases
    assert not any(o.weakens_protocol for o in resolution.options)
    assert "NORMALIZATION" not in (resolution.approval_prompt or "").upper()


def test_A_producing_receipts_naming_the_certified_baseline_are_fresh(tmp_path: Path) -> None:
    """PD-3: a receipt naming the certified pair is correct in PRODUCING, not stale."""
    repo = producing_after_certified_pair(tmp_path / "neyma")
    topology = _analyzer(repo.root).analyze()

    assert topology.receipts, "the fixture must present receipts"
    for binding in topology.receipts:
        assert binding.exists
        assert binding.fresh, f"{binding.path} judged stale: {binding.detail}"
        assert "certified baseline" in binding.fresh_reason


def test_A_working_tree_episode_is_present_and_untouched(tmp_path: Path) -> None:
    """The fixture's point is that real, uncommitted work is at risk."""
    repo = producing_after_certified_pair(tmp_path / "neyma")
    state = capture_worktree_state(repo.root)

    assert "src/governed_approval.py" in state.untracked
    assert "src/kernel.py" in state.dirty
    assert state.measured


# ---- the three legal states and the illegal historical shape -------------


def test_all_three_legal_states_are_modelled(tmp_path: Path) -> None:
    """PD-2 / guard 7: one state machine, and it is the repository's own."""
    producing = producing_after_certified_pair(tmp_path / "producing")
    assert resolve_state(_analyzer(producing.root)).state is RepositoryState.PRODUCING

    finalized = finalized_pair_with_content_baseline_pointer(tmp_path / "finalized")
    assert resolve_state(_analyzer(finalized.root)).state is RepositoryState.FINALIZED

    base = baseline_repo(tmp_path / "baseline")
    # BASELINE: the recorded content commit IS HEAD.
    base.write(
        "docs/implementation/CURRENT.md",
        f"# CURRENT\n# status-block: maintained by scripts/finalize_status.py\n"
        f"content_commit: {base.head()}\n# end-status-block\n",
    )
    base.commit("record baseline", "docs/implementation/CURRENT.md")
    base.write(
        "docs/implementation/CURRENT.md",
        f"# CURRENT\n# status-block: maintained by scripts/finalize_status.py\n"
        f"content_commit: {base.head()}\n# end-status-block\n",
    )
    assert resolve_state(_analyzer(base.root)).state is RepositoryState.BASELINE


def test_the_illegal_historical_shape_is_still_illegal(tmp_path: Path) -> None:
    """Two unfinalized content commits: the shape the convention forbids."""
    repo = two_content_commits(tmp_path / "neyma")
    repo.write(
        "docs/implementation/CURRENT.md",
        f"# CURRENT\n# status-block: maintained by scripts/finalize_status.py\n"
        f"content_commit: {repo._git('rev-parse', 'HEAD^^')}\n# end-status-block\n",
    )
    state = resolve_state(_analyzer(repo.root))
    assert state.state is RepositoryState.ILLEGAL
    assert not state.legal


# ---- baseline promotion (guard 8) ---------------------------------------


def test_baseline_promotes_content_commit_to_its_pure_metadata_child(tmp_path: Path) -> None:
    """857cdc1 → 180fdcc and 3d231731b8b0 → f1e8e18, structurally.

    The recorded baseline names the CONTENT half of a certified pair. The
    exclusion boundary must be the metadata half, or the certified commit falls
    inside the analyzed range and reads as a defect.
    """
    repo = producing_after_certified_pair(tmp_path / "neyma")
    certified_content = repo._git("rev-parse", "HEAD^^")
    metadata_child = repo._git("rev-parse", "HEAD^")

    analyzer = _analyzer(repo.root)
    assert analyzer.is_pure_finalizer_metadata(metadata_child)

    base, source, _confidence = analyzer.resolve_baseline()
    assert base == metadata_child, (
        f"baseline resolved to {base[:12]}, expected the metadata child {metadata_child[:12]}"
    )
    assert certified_content[:12] in source
    assert "certified pair" in source


def test_promotion_does_not_fire_when_the_child_is_content(tmp_path: Path) -> None:
    """Promotion is mechanical, not optimistic: only a PURE metadata child counts."""
    repo = two_content_commits(tmp_path / "neyma")
    analyzer = _analyzer(repo.root)
    head_parent = repo._git("rev-parse", "HEAD^")
    assert not analyzer.is_pure_finalizer_metadata(head_parent)


# ==========================================================================
# B. The invalid run-3 baseline choice
# ==========================================================================


def test_B_builder_selected_baseline_is_rejected_before_any_mutation() -> None:
    """PD-8: approved 3d231731b8b0, builder resolved 180fdcc → hard stop."""
    approved = "3d231731b8b0984b3decded34177907f8d3898d1"
    builder_chose = "180fdcc10fb3f5b2bed4d38447d7ca7ec41dab7a"

    record = ApprovalRecord(
        option_id="A",
        plan_hash="deadbeef",
        confirmation="APPROVE P4 LOCAL HISTORY NORMALIZATION",
        approval_phrase="APPROVE P4 LOCAL HISTORY NORMALIZATION",
        approved_baseline=approved,
    )

    with pytest.raises(BaselineMismatch) as exc:
        assert_approved_baseline(builder_chose, record)

    message = str(exc.value)
    assert "180fdcc" in message and "3d231731b8b0"[:12] in message
    assert "before any ref, index or working-tree mutation" in message

    # The approved baseline itself passes, in full or abbreviated form.
    assert_approved_baseline(approved, record)
    assert_approved_baseline(approved[:12], record)


def test_B_a_history_operation_without_an_approval_is_refused() -> None:
    """No history operation may execute without a live ApprovalRecord."""
    with pytest.raises(BaselineMismatch):
        assert_approved_baseline("3d231731b8b0", None)


# ==========================================================================
# C. Concurrent finalizers
# ==========================================================================


_SECOND_FINALIZER = textwrap.dedent(
    """
    import sys, json
    sys.path.insert(0, {driver!r})
    from neyma_product_driver.ownership import finalizer_lock, LockHeld

    receipt = {receipt!r}
    try:
        with finalizer_lock({repo!r}, target_commit="second"):
            # If we ever get here the mutual exclusion failed. Prove it by doing
            # exactly what a finalizer does first: destroy the receipt.
            import os
            os.remove(receipt)
            print("SECOND_FINALIZER_PROCEEDED")
            sys.exit(0)
    except LockHeld as exc:
        print("REFUSED")
        sys.exit(3)
    """
)


def test_C_only_one_finalizer_proceeds_and_the_loser_changes_nothing(tmp_path: Path) -> None:
    """PD-10: exactly one owner; the second exits non-zero having mutated nothing."""
    repo = producing_after_certified_pair(tmp_path / "neyma")
    receipt = repo.root / "docs/implementation/SUITE-RESULT.json"
    before = receipt.read_bytes()

    driver_root = str(Path(__file__).resolve().parents[1])
    script = _SECOND_FINALIZER.format(
        driver=driver_root, repo=str(repo.root), receipt=str(receipt)
    )

    with finalizer_lock(repo.root, target_commit=repo.head(), run_id="first") as owner:
        assert owner.pid == os.getpid()
        assert owner.target_commit == repo.head()

        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
        )

    assert proc.returncode == 3, f"second finalizer did not refuse: {proc.stdout}{proc.stderr}"
    assert "REFUSED" in proc.stdout
    assert "SECOND_FINALIZER_PROCEEDED" not in proc.stdout
    assert receipt.exists(), "the losing finalizer deleted a receipt"
    assert receipt.read_bytes() == before


def test_C_the_lock_record_identifies_its_owner(tmp_path: Path) -> None:
    """The record must name pid, start time, repository, target and run/session."""
    repo = baseline_repo(tmp_path / "neyma")
    with finalizer_lock(
        repo.root, target_commit="abc123", run_id="run-7", session_id="sess-9"
    ) as record:
        payload = json.loads((repo.root / ".git" / FINALIZER_LOCK_NAME).read_text())
        assert payload["pid"] == os.getpid() == record.pid
        assert payload["started_at"] > 0
        assert payload["repository"] == str(repo.root)
        assert payload["target_commit"] == "abc123"
        assert payload["run_id"] == "run-7"
        assert payload["session_id"] == "sess-9"


def test_C_the_lock_is_released_on_every_exit_path(tmp_path: Path) -> None:
    """Including an exception: a crashed finalizer must not wedge the repository."""
    repo = baseline_repo(tmp_path / "neyma")

    with pytest.raises(RuntimeError):
        with finalizer_lock(repo.root):
            raise RuntimeError("finalizer blew up")

    assert finalizer_running(repo.root) is None
    with finalizer_lock(repo.root):
        pass


def test_C_a_reacquired_lock_is_not_stolen_from_a_live_owner(tmp_path: Path) -> None:
    repo = baseline_repo(tmp_path / "neyma")
    lock = RepoLock(repo.root, FINALIZER_LOCK_NAME, kind="finalizer")
    lock.acquire()
    try:
        other = RepoLock(repo.root, FINALIZER_LOCK_NAME, kind="finalizer")
        with pytest.raises(LockHeld):
            other.acquire()
    finally:
        lock.release()


# ==========================================================================
# D. Preservation with untracked files
# ==========================================================================


def test_D_preservation_captures_untracked_files_and_verifies_identical(tmp_path: Path) -> None:
    """Guard 9: before/after tree hashes identical, untracked files included."""
    repo = producing_after_certified_pair(tmp_path / "neyma")

    before = capture_worktree_state(repo.root)
    assert "src/governed_approval.py" in before.untracked

    preservation = preserve_worktree(repo.root, "refs/preserve/test-ep1")
    assert preservation.complete, preservation.errors
    assert preservation.tree == before.tree

    # The preserved tree must actually CONTAIN the untracked file — a
    # preservation that silently drops it proves nothing.
    listing = repo._git("ls-tree", "-r", "--name-only", preservation.tree)
    assert "src/governed_approval.py" in listing

    ok, detail, after = verify_worktree_restored(repo.root, preservation)
    assert ok, detail
    assert after.tree == before.tree


def test_D_a_lost_untracked_file_is_detected(tmp_path: Path) -> None:
    """The check has to actually fail when work is destroyed, or it proves nothing."""
    repo = producing_after_certified_pair(tmp_path / "neyma")
    preservation = preserve_worktree(repo.root, "refs/preserve/test-ep1")
    assert preservation.complete

    (repo.root / "src/governed_approval.py").unlink()

    ok, detail, _after = verify_worktree_restored(repo.root, preservation)
    assert not ok
    assert "src/governed_approval.py" in detail or "tree hash changed" in detail


def test_D_preservation_survives_a_ref_move_that_keeps_the_worktree(tmp_path: Path) -> None:
    """The Phase-1 recovery shape: move the branch ref, prove the tree is intact."""
    repo = producing_after_certified_pair(tmp_path / "neyma")
    preservation = preserve_worktree(repo.root, "refs/preserve/test-ep1")
    before = capture_worktree_state(repo.root)

    branch = repo._git("branch", "--show-current")
    target = repo._git("rev-parse", "HEAD^^")
    repo._git("update-ref", f"refs/heads/{branch}", target, repo.head())

    assert repo.head() == target
    ok, detail, after = verify_worktree_restored(repo.root, preservation)
    assert ok, detail
    assert after.tree == before.tree


# ==========================================================================
# E / F. Approval fingerprint: advisory vs topology
# ==========================================================================


def _record_for(topology, protocol, ref_transition=None) -> ApprovalRecord:
    return ApprovalRecord(
        option_id="A",
        plan_hash="hash",
        confirmation="APPROVE",
        approval_phrase="APPROVE",
        approved_baseline=topology.baseline_commit,
        topology_fingerprint=topology_fingerprint(topology, protocol, ref_transition),
    )


def test_E_editing_the_working_tree_does_not_expire_a_topology_approval(tmp_path: Path) -> None:
    """PD-7: ordinary EP-1 edits update advisory state only."""
    repo = producing_after_certified_pair(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)
    topology = GitTopologyAnalyzer(repo.root, protocol).analyze()
    record = _record_for(topology, protocol)

    advisory_before = working_state_fingerprint(capture_worktree_state(repo.root))

    # A normal implementation edit: change a tracked file, add a new untracked one.
    repo.write("src/kernel.py", "def kernel():\n    return 5  # more EP-1\n")
    repo.write("src/another_ep1_file.py", "X = 1\n")

    protocol_after = discover_protocol(repo.root)
    topology_after = GitTopologyAnalyzer(repo.root, protocol_after).analyze()
    current = topology_fingerprint(topology_after, protocol_after)

    assert record.matches_topology(current), "an ordinary edit expired a topology approval"
    assert_live_approval(record, current, operation="consolidation")

    advisory_after = working_state_fingerprint(capture_worktree_state(repo.root))
    assert advisory_after != advisory_before, "advisory state must track the edit"
    assert advisory_after["worktree_tree"] != advisory_before["worktree_tree"]
    assert "src/another_ep1_file.py" in advisory_after["untracked_paths"]


def test_F_commit_graph_movement_expires_the_approval(tmp_path: Path) -> None:
    """A new commit changes what the approved operation would do."""
    repo = producing_after_certified_pair(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)
    topology = GitTopologyAnalyzer(repo.root, protocol).analyze()
    record = _record_for(topology, protocol)

    repo.write("src/kernel.py", "def kernel():\n    return 9\n")
    repo.commit("another content commit", "src/kernel.py")

    protocol_after = discover_protocol(repo.root)
    topology_after = GitTopologyAnalyzer(repo.root, protocol_after).analyze()
    current = topology_fingerprint(topology_after, protocol_after)

    assert not record.matches_topology(current)
    with pytest.raises(ApprovalExpired):
        assert_live_approval(record, current, operation="consolidation")


def test_F_a_different_ref_transition_expires_the_approval(tmp_path: Path) -> None:
    """The exact planned transition is part of what was approved."""
    repo = producing_after_certified_pair(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)
    topology = GitTopologyAnalyzer(repo.root, protocol).analyze()

    approved_transition = {"ref": "refs/heads/p4", "old": "aaa", "baseline": "bbb"}
    record = _record_for(topology, protocol, approved_transition)

    other = topology_fingerprint(
        topology, protocol, {"ref": "refs/heads/p4", "old": "ccc", "baseline": "bbb"}
    )
    assert not record.matches_topology(other)


def test_F_an_approval_is_single_use(tmp_path: Path) -> None:
    repo = producing_after_certified_pair(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)
    topology = GitTopologyAnalyzer(repo.root, protocol).analyze()
    record = _record_for(topology, protocol)
    current = topology_fingerprint(topology, protocol)

    assert_live_approval(record, current)
    record.consumed = True
    with pytest.raises(ApprovalExpired):
        assert_live_approval(record, current)


# ==========================================================================
# G. A FINALIZER_GENERATED commit offered for consolidation
# ==========================================================================


def test_G_a_certified_pair_commit_is_never_offered_for_consolidation(tmp_path: Path) -> None:
    """PD-4: squashing/orphaning a FINALIZER_GENERATED commit is not an option."""
    repo = content_plus_finalizer_metadata(tmp_path / "neyma")
    # Add a further content commit so the range holds BOTH a finalizer metadata
    # commit and content — the shape a consolidation would sweep together.
    repo.write("src/kernel.py", "def kernel():\n    return 7\n")
    repo.commit("more content", "src/kernel.py")

    protocol = discover_protocol(repo.root)
    topology = GitTopologyAnalyzer(repo.root, protocol).analyze(baseline=repo._git("rev-list", "--max-parents=0", "HEAD"))

    certified = certified_pair_commits(topology)
    assert certified, "the fixture must place a FINALIZER_GENERATED commit in range"
    assert all(c.role is CommitRole.FINALIZER_GENERATED for c in certified)

    resolution = ProtocolResolver(repo.root).resolve(
        baseline=repo._git("rev-list", "--max-parents=0", "HEAD")
    )
    for option in resolution.options:
        assert not option.rewrites_history, (
            f"option {option.option_id} rewrites history across a certified pair boundary"
        )


def test_G_rewriting_across_a_certified_pair_raises(tmp_path: Path) -> None:
    """The invariant is asserted, not merely hoped for."""
    from neyma_product_driver.remediation_planner import (
        RemediationOption,
        _assert_never_orphans_certified_pair,
    )

    repo = content_plus_finalizer_metadata(tmp_path / "neyma")
    repo.write("src/kernel.py", "def kernel():\n    return 7\n")
    repo.commit("more content", "src/kernel.py")
    protocol = discover_protocol(repo.root)
    topology = GitTopologyAnalyzer(repo.root, protocol).analyze(
        baseline=repo._git("rev-list", "--max-parents=0", "HEAD")
    )
    assert certified_pair_commits(topology)

    option = RemediationOption(option_id="X", title="squash everything", rewrites_history=True)
    with pytest.raises(AssertionError, match="certified pair"):
        _assert_never_orphans_certified_pair(option, topology)


# ==========================================================================
# H. A legal recovery available alongside a protocol amendment
# ==========================================================================


def test_H_no_amendment_when_the_repository_is_already_legal(tmp_path: Path) -> None:
    """PD-6: nothing to amend, so nothing is offered."""
    repo = producing_after_certified_pair(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)
    topology = GitTopologyAnalyzer(repo.root, protocol).analyze()

    verdict = amendment_admissibility([], topology)
    assert not verdict.admissible
    assert not verdict.emit_disqualified
    assert "already in the legal PRODUCING state" in verdict.reason


def test_H_amendment_is_disqualified_when_a_clean_option_satisfies_the_rule(
    tmp_path: Path,
) -> None:
    """A non-destructive, evidence-preserving option beats weakening the rule."""
    from neyma_product_driver.remediation_planner import ProtocolCompliance, RemediationOption

    repo = two_content_commits(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)
    topology = GitTopologyAnalyzer(repo.root, protocol).analyze()

    clean = RemediationOption(
        option_id="A",
        title="consolidate, preserving everything",
        evidence_preserved=True,
        destructive_operations=[],
        affects_remote_history=False,
        protocol_compliance=ProtocolCompliance(satisfies=["commit-topology"]),
    )
    verdict = amendment_admissibility([clean], topology)
    assert not verdict.admissible
    assert verdict.emit_disqualified
    assert "A" in verdict.satisfying_option_ids
    assert "may not be weakened while the rule is satisfiable" in verdict.reason


def test_H_amendment_never_outranks_an_option_that_satisfies_the_rule(tmp_path: Path) -> None:
    """Even when both are on the table, weakening the protocol ranks below."""
    from neyma_product_driver.remediation_planner import (
        ProtocolCompliance,
        RemediationOption,
        RiskLevel,
        rank_options,
    )

    satisfying = RemediationOption(
        option_id="A",
        title="rewrite locally, preserving evidence",
        rewrites_history=True,
        risk_level=RiskLevel.MEDIUM,
        protocol_compliance=ProtocolCompliance(satisfies=["commit-topology"]),
    )
    amendment = RemediationOption(
        option_id="C",
        title="amend the protocol",
        rewrites_history=False,
        weakens_protocol=True,
        risk_level=RiskLevel.HIGH,
    )

    ordered = rank_options([amendment, satisfying])
    assert ordered[0].option_id == "A", [o.option_id for o in ordered]
    assert ordered[-1].option_id == "C"


def test_H_real_p4_topology_produces_no_amendment_option(tmp_path: Path) -> None:
    """End to end: the legal fixture yields no amendment anywhere in the report."""
    repo = producing_after_certified_pair(tmp_path / "neyma")
    resolution = ProtocolResolver(repo.root).resolve()
    report = resolution.render_report()

    assert "PROTOCOL AMENDMENT" not in report.upper()
    assert "REMEDIATION OPTIONS" not in report


# ==========================================================================
# I. Zero-byte journal evidence
# ==========================================================================


def _complete_run(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "journal.json").write_text(json.dumps({"run": "x"}), encoding="utf-8")
    (run_dir / "founder-summary.md").write_text("# Summary\n\nReal content.\n", encoding="utf-8")
    it = run_dir / "iteration-001"
    it.mkdir(parents=True, exist_ok=True)
    (it / "git-status.txt").write_text(" M src/kernel.py\n", encoding="utf-8")
    (it / "git-diff-stat.txt").write_text(" src/kernel.py | 2 +-\n", encoding="utf-8")
    (it / "commands.log").write_text("pytest eval/tests -q  (exit 0)\n", encoding="utf-8")
    (it / "record.json").write_text(json.dumps({"iteration": 1}), encoding="utf-8")


def test_I_a_complete_run_journal_is_admissible(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _complete_run(run_dir)
    result = verify_run_evidence(run_dir)
    assert result.ok, result.render()


@pytest.mark.parametrize(
    "artifact",
    [
        "iteration-001/git-status.txt",
        "iteration-001/git-diff-stat.txt",
        "iteration-001/record.json",
        "journal.json",
        "founder-summary.md",
    ],
)
def test_I_zero_byte_evidence_fails_closed(tmp_path: Path, artifact: str) -> None:
    """PD-12: an empty artifact records a capture that did not happen."""
    run_dir = tmp_path / "run"
    _complete_run(run_dir)
    (run_dir / artifact).write_text("", encoding="utf-8")

    result = verify_run_evidence(run_dir)
    assert not result.ok
    assert any("zero bytes" in f for f in result.failures), result.failures

    with pytest.raises(JournalEvidenceMissing):
        require_run_evidence(run_dir)


@pytest.mark.parametrize(
    "artifact",
    ["iteration-001/git-status.txt", "iteration-001/git-diff-stat.txt", "journal.json"],
)
def test_I_missing_evidence_fails_closed(tmp_path: Path, artifact: str) -> None:
    run_dir = tmp_path / "run"
    _complete_run(run_dir)
    (run_dir / artifact).unlink()

    result = verify_run_evidence(run_dir)
    assert not result.ok
    assert any("missing" in f for f in result.failures), result.failures


def test_I_a_clean_tree_still_produces_real_evidence(tmp_path: Path) -> None:
    """The rule must not fire on a legitimately clean tree.

    The writers record an explicit marker, so zero bytes always means the
    capture failed rather than the state being empty.
    """
    from neyma_product_driver.journal_integrity import empty_marker

    run_dir = tmp_path / "run"
    _complete_run(run_dir)
    (run_dir / "iteration-001/git-status.txt").write_text(
        empty_marker("git status --porcelain"), encoding="utf-8"
    )
    assert verify_run_evidence(run_dir).ok


# ==========================================================================
# PD-11. A missing log is not a dead process
# ==========================================================================


def test_PD11_a_missing_log_never_implies_death(tmp_path: Path) -> None:
    explanation = log_absence_proves_nothing(tmp_path / "finalizer.log", pid=4242)
    assert "proves nothing" in explanation
    assert "nohup" in explanation
    assert "lock is authoritative" in explanation


def test_PD11_liveness_comes_from_the_lock_not_from_artifacts(tmp_path: Path) -> None:
    """Even with no log anywhere, a held lock reports the finalizer as running."""
    repo = baseline_repo(tmp_path / "neyma")
    assert finalizer_running(repo.root) is None

    lock = RepoLock(repo.root, FINALIZER_LOCK_NAME, kind="finalizer", run_id="r1")
    lock.acquire()
    try:
        # Same process holds it, so `held_by_other` reports None for the owner;
        # a separate process is what the concurrency test covers. What matters
        # here is that no log file exists and nothing infers death from that.
        assert not (tmp_path / "finalizer.log").exists()
        assert lock.read_record() is not None
        assert lock.read_record().run_id == "r1"
    finally:
        lock.release()

    # After a clean release the lock file may remain, but it is not held.
    assert finalizer_running(repo.root) is None


# ==========================================================================
# PD-9. Builder worktree ownership
# ==========================================================================


@pytest.mark.parametrize(
    "command",
    [
        "git update-ref refs/heads/p4 abc123",
        "git branch -f p4 abc123",
        "git branch -D p4",
        "git symbolic-ref HEAD refs/heads/other",
        "git reset --hard HEAD~1",
        "git reset HEAD~1",
        "git checkout other-branch",
        "git switch other-branch",
        "git stash",
        "git clean -fd",
        "git restore .",
        "git restore --source=HEAD~1 src/kernel.py",
        "git checkout -- .",
        "git worktree remove /tmp/wt",
        "NEW=$(git commit-tree $TREE -p $BASE -m x) && git update-ref refs/heads/p4 $NEW",
    ],
)
def test_PD9_ref_and_worktree_operations_denied_while_a_builder_owns(command: str) -> None:
    reason = classify_command(command, builder_owns_worktree=True)
    assert reason is not None, command
    assert "builder owns this product worktree" in reason


@pytest.mark.parametrize(
    "command",
    [
        "pytest eval/tests -q",
        "git status --porcelain",
        "git log --oneline -5",
        "git add -A && git commit -m 'EP-1 work'",
        "git diff --stat",
        # Reverting ONE file the builder itself is editing. Denying this while
        # the Write tool can rewrite the same file byte for byte would be
        # theatre, not a guard.
        "git restore src/kernel.py",
        "git checkout -- src/kernel.py",
    ],
)
def test_PD9_ordinary_builder_work_is_not_blocked(command: str) -> None:
    assert classify_command(command, builder_owns_worktree=True) is None


def test_PD9_soft_reset_still_needs_a_preservation_backed_authorization() -> None:
    """Ownership does not weaken the amendment path — it defers to it.

    `git reset --soft` remains denied without an authorization, by the stricter
    layer that demands proof the commits are unpushed and preserved. With that
    authorization it proceeds, because an approved amendment is exactly the
    "exact ref transition through a live approval" case.
    """
    command = "git reset --soft HEAD~2"

    denied = classify_command(command, builder_owns_worktree=True)
    assert denied is not None
    assert "preservation-backed authorization" in denied

    allowed = classify_command(command, builder_owns_worktree=True, allow_amend=True)
    assert allowed is None

    # Authorizing an amendment unlocks nothing else.
    assert classify_command(
        "git update-ref refs/heads/p4 abc", builder_owns_worktree=True, allow_amend=True
    ) is not None


def test_PD9_ownership_scoping_does_not_leak_when_no_builder_owns() -> None:
    """Outside a builder-owned worktree these are ordinary commands."""
    assert classify_command("git update-ref refs/heads/p4 abc", builder_owns_worktree=False) is None
    assert classify_command("git checkout main", builder_owns_worktree=False) is None


def test_PD9_ownership_is_reported_from_the_lock(tmp_path: Path) -> None:
    repo = baseline_repo(tmp_path / "neyma")
    assert builder_owns_worktree(repo.root) is None

    lock = RepoLock(repo.root, BUILDER_LOCK_NAME, kind="builder-worktree")
    lock.acquire()
    try:
        assert (repo.root / ".git" / BUILDER_LOCK_NAME).exists()
    finally:
        lock.release()


# ==========================================================================
# Guard 10. No finalization while product work is hidden
# ==========================================================================


def test_guard10_finalizer_refuses_when_the_preserved_tree_is_not_materialized(
    tmp_path: Path,
) -> None:
    from neyma_product_driver.preservation import (
        HiddenWorkNotFinalizable,
        assert_preserved_tree_materialized,
    )

    repo = producing_after_certified_pair(tmp_path / "neyma")
    preservation = preserve_worktree(repo.root, "refs/preserve/ep1")
    assert preservation.complete

    # Materialized: finalizing is allowed.
    assert_preserved_tree_materialized(repo.root, preservation)

    # Now hide the episode, exactly as a stash or a reset would.
    (repo.root / "src/governed_approval.py").unlink()
    with pytest.raises(HiddenWorkNotFinalizable) as exc:
        assert_preserved_tree_materialized(repo.root, preservation)
    assert "hidden or stashed product tree may not be finalized" in str(exc.value)


def test_guard10_an_incomplete_review_artifact_does_not_unlock_it(tmp_path: Path) -> None:
    from neyma_product_driver.preservation import (
        HiddenWorkNotFinalizable,
        assert_preserved_tree_materialized,
    )

    repo = producing_after_certified_pair(tmp_path / "neyma")
    preservation = preserve_worktree(repo.root, "refs/preserve/ep1")
    (repo.root / "src/governed_approval.py").unlink()

    for artifact in (
        {"approved": True},
        {"approved": True, "reviewed_commit": "abc", "reviewed_tree": "def"},
        {"approved": False, "reviewed_commit": "abc", "reviewed_tree": "def",
         "restoration_procedure": "git checkout ref"},
    ):
        with pytest.raises(HiddenWorkNotFinalizable):
            assert_preserved_tree_materialized(repo.root, preservation, review_artifact=artifact)


def test_guard10_a_complete_review_artifact_proving_the_tree_unlocks_it(tmp_path: Path) -> None:
    from neyma_product_driver.preservation import assert_preserved_tree_materialized

    repo = producing_after_certified_pair(tmp_path / "neyma")
    preservation = preserve_worktree(repo.root, "refs/preserve/ep1")
    (repo.root / "src/governed_approval.py").unlink()

    current = capture_worktree_state(repo.root)
    artifact = {
        "approved": True,
        "reviewed_commit": repo.head(),
        "reviewed_tree": current.tree,
        "restoration_procedure": f"git read-tree {preservation.tree} && git checkout-index -af",
    }
    assert_preserved_tree_materialized(repo.root, preservation, review_artifact=artifact)


# ==========================================================================
# Guard 9. Preservation verification is mandatory
# ==========================================================================


def test_guard9_an_unverified_rewrite_is_a_failed_rewrite(tmp_path: Path) -> None:
    from neyma_product_driver.preservation import (
        AmendmentAuthorization,
        PreservationNotVerified,
        capture_identity,
    )

    repo = producing_after_certified_pair(tmp_path / "neyma")
    auth = AmendmentAuthorization(repo=str(repo.root), before=capture_identity(repo.root))

    assert auth.verified is None
    assert not auth.restoration_proven
    with pytest.raises(PreservationNotVerified, match="never called"):
        auth.assert_restoration_proven()


def test_guard9_verification_without_untracked_capture_fails(tmp_path: Path) -> None:
    """A preservation that never captured untracked files cannot prove anything."""
    from neyma_product_driver.preservation import (
        AmendmentAuthorization,
        PreservationNotVerified,
        capture_identity,
    )

    repo = producing_after_certified_pair(tmp_path / "neyma")
    auth = AmendmentAuthorization(repo=str(repo.root), before=capture_identity(repo.root))
    auth.worktree = None

    auth.verify_result()
    assert auth.worktree_verified is False
    assert "untracked product files" in auth.worktree_verification_detail
    assert not auth.restoration_proven
    with pytest.raises(PreservationNotVerified):
        auth.assert_restoration_proven()


def test_guard9_a_real_preservation_verifies(tmp_path: Path) -> None:
    from neyma_product_driver.preservation import AmendmentAuthorization, capture_identity

    repo = producing_after_certified_pair(tmp_path / "neyma")
    auth = AmendmentAuthorization(repo=str(repo.root), before=capture_identity(repo.root))
    auth.worktree = preserve_worktree(repo.root, "refs/preserve/amend")

    assert auth.verify_result() is True
    assert auth.restoration_proven
    auth.assert_restoration_proven()


# ==========================================================================
# Live wiring: the guards must be enforced, not merely implemented
# ==========================================================================


def test_live_builder_session_owns_its_worktree_and_denies_ref_moves(tmp_path: Path) -> None:
    """A BuilderSession constructs its guard with ownership held."""
    from neyma_product_driver.builder import BuilderSession
    from neyma_product_driver.config import BuilderConfig

    repo = producing_after_certified_pair(tmp_path / "neyma")
    session = BuilderSession(repo.root, BuilderConfig())

    assert session.own_worktree
    assert session.guard.builder_owns_worktree

    denied = session.guard.classify("Bash", {"command": "git update-ref refs/heads/p4 abc"})
    assert denied.denied
    assert "builder owns this product worktree" in (denied.reason or "")

    allowed = session.guard.classify("Bash", {"command": "pytest eval/tests -q"})
    assert not allowed.denied


def test_live_guard_refuses_a_second_finalizer_launch(tmp_path: Path) -> None:
    """PD-12/guard 12: the lock is authoritative at the launch point."""
    from neyma_product_driver.command_guard import CommandGuard

    repo = producing_after_certified_pair(tmp_path / "neyma")
    guard = CommandGuard(cwd=repo.root)

    launch = {"command": ".venv/bin/python scripts/finalize_status.py --phase P4"}

    # No owner: launching is fine.
    assert guard.check_finalizer_launch(launch["command"]) is None

    # A live owner in another process must block the launch. The in-process
    # lock is invisible to `held_by_other`, so hold it from a child.
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys, time
                sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
                from neyma_product_driver.ownership import finalizer_lock
                with finalizer_lock({str(repo.root)!r}, target_commit="held"):
                    print("HELD", flush=True)
                    time.sleep(30)
                """
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "HELD"

        reason = guard.check_finalizer_launch(launch["command"])
        assert reason is not None
        assert "already owns this repository" in reason
        assert "never infer that it died from a missing log" in reason

        decision = guard.classify("Bash", launch)
        assert decision.denied
        assert decision.layer == "finalizer-lock"
    finally:
        holder.kill()
        holder.wait(timeout=30)

    # Once the owner exits, the lock is reclaimable with no timeout heuristic.
    assert guard.check_finalizer_launch(launch["command"]) is None


def test_PD9_ownership_does_not_scan_script_prose(tmp_path: Path) -> None:
    """A docstring is not a command.

    The P4 mutation battery documents its own safety by naming the commands it
    refuses to use. Scanning that prose for ownership violations blocked a
    required gate on the strength of a sentence promising the opposite — and a
    docstring is not a `#` comment, so no comment skip catches it.

    Hard-blocked commands written into a script are still caught: that is what
    the script layer is for.
    """
    from neyma_product_driver.command_guard import CommandGuard
    from neyma_product_driver.paths import ApprovedRoot, ApprovedRoots

    repo = producing_after_certified_pair(tmp_path / "neyma")
    scripts = repo.root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)

    safe = scripts / "mutate_boundary.py"
    safe.write_text(
        '"""Safe in-memory mutation battery.\n\n'
        "  * original bytes are held IN MEMORY - never `git checkout/restore/stash/clean`\n"
        "  * restoration is verified byte-for-byte\n"
        '"""\n'
        "print('mutating in memory')\n",
        encoding="utf-8",
    )

    roots = ApprovedRoots([ApprovedRoot("repo", repo.root, "the product repository")])
    guard = CommandGuard(roots=roots, cwd=repo.root, builder_owns_worktree=True)

    decision = guard.classify("Bash", {"command": f"python {safe}"})
    assert not decision.denied, decision.reason

    # A genuinely hard-blocked command inside a script is still caught.
    nasty = scripts / "sneaky.sh"
    nasty.write_text("#!/bin/sh\ngit push --force origin main\n", encoding="utf-8")
    blocked = guard.classify("Bash", {"command": f"sh {nasty}"})
    assert blocked.denied
    assert "force push" in (blocked.reason or "").lower()


def test_I_the_run_loop_actually_writes_its_journal(tmp_path: Path) -> None:
    """PD-12: run-journal evidence is mandatory, so a run must produce it.

    The control loop recorded per-iteration evidence but never constructed a
    RunJournal, so journal.json and FOUNDER-SUMMARY.md were never written by any
    run — the mandatory acceptance evidence did not exist. Requiring it while
    nothing produced it would have failed every run closed; the fix is to write
    it, not to stop asking for it.
    """
    from neyma_product_driver.cli import _write_run_journal
    from neyma_product_driver.config import DriverConfig
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.models import RunState
    from neyma_product_driver.run_journal import JOURNAL_FILE, SUMMARY_FILE

    repo = producing_after_certified_pair(tmp_path / "neyma")
    store = EvidenceStore(tmp_path / "runs", "20260728-000000")
    store.run_dir.mkdir(parents=True, exist_ok=True)

    config = DriverConfig(neyma_repo=repo.root, runs_dir=tmp_path / "runs")
    _write_run_journal(store, RunState(run_id=store.run_id), config)

    journal = store.run_dir / JOURNAL_FILE
    summary = store.run_dir / SUMMARY_FILE
    assert journal.is_file() and journal.stat().st_size > 0
    assert summary.is_file() and summary.stat().st_size > 0

    # And the run-level half of the integrity check now passes.
    result = verify_run_evidence(store.run_dir)
    assert not any("journal.json" in f for f in result.failures), result.failures
    assert not any(SUMMARY_FILE in f for f in result.failures), result.failures


def test_I_iteration_dir_naming_matches_the_evidence_store(tmp_path: Path) -> None:
    """A complete run must not be failed over a padding difference."""
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.journal_integrity import iteration_dir

    store = EvidenceStore(tmp_path / "runs", "20260728-000001")
    written = store.iteration_dir(1)
    assert iteration_dir(store.run_dir, 1) == written, (
        f"integrity check looks in {iteration_dir(store.run_dir, 1).name}, "
        f"store writes {written.name}"
    )
