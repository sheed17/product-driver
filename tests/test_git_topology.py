"""Commit-role classification, receipt binding and push state.

Roles come from the files a commit changed. The tests below deliberately use
misleading commit messages in places: a message is the cheapest thing in a
commit to get wrong, and must never decide a classification on its own.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from neyma_product_driver.git_topology import (
    CommitRole,
    GitTopologyAnalyzer,
    PathClass,
    expected_graph,
)
from neyma_product_driver.protocol_sources import discover_protocol

from protocol_fixtures import (
    CONTENT_PATHS,
    RECEIPT_PATHS,
    STATUS_PATHS,
    baseline_of,
    baseline_repo,
    content_plus_finalizer_metadata,
    content_plus_hand_edited_status,
    one_content_commit,
    two_content_commits,
)


def analyze(repo):
    protocol = discover_protocol(repo.root)
    return GitTopologyAnalyzer(repo.root, protocol).analyze()


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------


def test_baseline_comes_from_what_the_repository_records(tmp_path: Path) -> None:
    repo = one_content_commit(tmp_path / "neyma")
    topology = analyze(repo)

    assert topology.baseline_commit == baseline_of(repo)
    assert "BUILD-STATUS" in topology.baseline_source
    assert topology.baseline_confidence >= 0.9


def test_a_baseline_tag_is_used_when_no_value_is_recorded(tmp_path: Path) -> None:
    repo = baseline_repo(tmp_path / "neyma")
    baseline = repo.head()
    repo.write_build_status()  # clears the recorded pointer
    subprocess.run(["git", "tag", "p3-baseline", baseline], cwd=repo.root, check=True)
    repo.write("src/kernel.py", "def kernel():\n    return 9\n")
    repo.commit("content", *CONTENT_PATHS)

    topology = analyze(repo)
    assert topology.baseline_commit == baseline
    assert "tag p3-baseline" in topology.baseline_source


def test_a_supplied_baseline_that_does_not_resolve_is_refused(tmp_path: Path) -> None:
    """An unresolvable sha must never reach a generated command as if it were one."""
    repo = one_content_commit(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)
    topology = GitTopologyAnalyzer(repo.root, protocol).analyze(baseline="deadbeefdeadbeef")

    assert topology.baseline_commit == ""
    assert topology.baseline_confidence == 0.0
    assert any("does not resolve" in n for n in topology.notes)


def test_an_unrecorded_baseline_falls_back_and_says_so(tmp_path: Path) -> None:
    repo = baseline_repo(tmp_path / "neyma")
    repo.write_build_status()
    topology = analyze(repo)

    assert topology.baseline_commit
    assert "no baseline is recorded" in topology.baseline_source
    assert topology.baseline_confidence < 0.5


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------


def test_one_content_commit_is_classified_as_content(tmp_path: Path) -> None:
    topology = analyze(one_content_commit(tmp_path / "neyma"))

    assert len(topology.commits) == 1
    assert topology.commits[0].role is CommitRole.CONTENT
    assert topology.commits[0].confidence >= 0.8
    assert "src/kernel.py" in topology.commits[0].content_files


def test_a_second_content_commit_is_classified_as_remediation_content(tmp_path: Path) -> None:
    topology = analyze(two_content_commits(tmp_path / "neyma"))

    assert [c.role for c in topology.commits] == [
        CommitRole.CONTENT,
        CommitRole.REMEDIATION_CONTENT,
    ]
    assert len(topology.content_commits) == 2


def test_a_finalizer_written_status_commit_is_distinguished_from_a_hand_edit(tmp_path: Path) -> None:
    finalized = analyze(content_plus_finalizer_metadata(tmp_path / "a"))
    hand = analyze(content_plus_hand_edited_status(tmp_path / "b"))

    assert finalized.commits[-1].role is CommitRole.FINALIZER_GENERATED
    assert hand.commits[-1].role is CommitRole.METADATA_STATUS
    assert hand.hand_edited_status_commits
    assert not finalized.hand_edited_status_commits


def test_a_commit_message_cannot_create_a_classification(tmp_path: Path) -> None:
    """A status-shaped message on a content commit does not make it a status commit."""
    repo = baseline_repo(tmp_path / "neyma")
    repo.write("src/kernel.py", "def kernel():\n    return 5\n")
    repo.commit("P3: finalize status and record derived progress", *CONTENT_PATHS)

    topology = analyze(repo)
    assert topology.commits[0].role is CommitRole.CONTENT
    assert any("runtime/test/specification file" in e for e in topology.commits[0].evidence)


def test_a_receipt_only_commit_is_review_evidence(tmp_path: Path) -> None:
    repo = one_content_commit(tmp_path / "neyma")
    repo.write_suite_receipt()
    repo.commit("record the suite receipt", *RECEIPT_PATHS[:1])

    topology = analyze(repo)
    assert topology.commits[-1].role is CommitRole.REVIEW_EVIDENCE


def test_a_mixed_commit_records_that_it_carries_status_too(tmp_path: Path) -> None:
    repo = baseline_repo(tmp_path / "neyma")
    repo.write("src/kernel.py", "def kernel():\n    return 7\n")
    repo.write_current("# CURRENT\n\nchanged alongside the code\n")
    repo.commit("content and status together", *CONTENT_PATHS, *STATUS_PATHS)

    commit = analyze(repo).commits[0]
    assert commit.role is CommitRole.CONTENT
    assert commit.status_files
    assert any("also changes derived status" in e for e in commit.evidence)
    assert commit.confidence < 0.8


def test_path_classification_uses_the_repositorys_own_path_sets(tmp_path: Path) -> None:
    repo = one_content_commit(tmp_path / "neyma")
    analyzer = GitTopologyAnalyzer(repo.root, discover_protocol(repo.root))

    assert analyzer.classify_path("src/kernel.py") is PathClass.CONTENT
    assert analyzer.classify_path("docs/implementation/BUILD-STATUS.yaml") is PathClass.STATUS
    assert analyzer.classify_path("docs/implementation/CURRENT.md") is PathClass.STATUS
    assert analyzer.classify_path("docs/implementation/SUITE-RESULT.json") is PathClass.RECEIPT
    assert analyzer.classify_path("docs/implementation/phase-3-review.md") is PathClass.REVIEW


# --------------------------------------------------------------------------
# Push state
# --------------------------------------------------------------------------


def test_history_with_no_remote_is_reported_local_only(tmp_path: Path) -> None:
    topology = analyze(two_content_commits(tmp_path / "neyma"))

    assert not topology.is_shared
    assert len(topology.local_only_commits) == 2
    assert topology.push_state_determinable
    assert "local-only" in topology.push_state_detail
    assert all(c.pushed is False for c in topology.commits)


def test_pushed_history_is_detected_through_remote_tracking_refs(tmp_path: Path) -> None:
    repo = two_content_commits(tmp_path / "neyma")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo.root, check=True)
    subprocess.run(
        ["git", "push", "-q", "-u", "origin", "HEAD"], cwd=repo.root, capture_output=True, check=True
    )

    topology = analyze(repo)
    assert topology.is_shared
    assert len(topology.pushed_commits) == 2
    assert all(c.pushed for c in topology.commits)


# --------------------------------------------------------------------------
# Receipts
# --------------------------------------------------------------------------


def test_a_receipt_naming_the_content_commit_is_bound_to_a_known_state(tmp_path: Path) -> None:
    repo = content_plus_finalizer_metadata(tmp_path / "neyma")
    topology = analyze(repo)

    suite = next(r for r in topology.receipts if r.name == "SUITE-RESULT")
    assert suite.exists and suite.passed
    assert suite.matches_known_commit
    assert not suite.self_referential


def test_a_receipt_naming_an_unrelated_commit_is_bound_to_nothing(tmp_path: Path) -> None:
    repo = one_content_commit(tmp_path / "neyma")
    repo.write_suite_receipt(commit="0" * 40, tree="1" * 40)

    suite = next(r for r in analyze(repo).receipts if r.name == "SUITE-RESULT")
    assert not suite.matches_head
    assert not suite.matches_known_commit


def test_a_receipt_naming_the_commit_that_introduced_it_is_self_referential(tmp_path: Path) -> None:
    """A receipt cannot record a sha that did not exist when it was written."""
    repo = one_content_commit(tmp_path / "neyma")
    repo.write_suite_receipt(commit="0" * 40, tree="1" * 40)
    introduced_in = repo.commit("add the receipt", *RECEIPT_PATHS[:1])
    repo.write_suite_receipt(commit=introduced_in, tree=repo.tree_of(introduced_in))

    suite = next(r for r in analyze(repo).receipts if r.name == "SUITE-RESULT")
    assert suite.committed_in == introduced_in
    assert suite.self_referential


# --------------------------------------------------------------------------
# Graphs
# --------------------------------------------------------------------------


def test_the_expected_graph_is_rendered_from_the_repositorys_rule(tmp_path: Path) -> None:
    repo = one_content_commit(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)
    graph = expected_graph(protocol, "abc1234567890")

    assert "BASELINE" in graph
    assert "one content commit" in graph
    assert "finalizer-generated metadata/status commit" in graph
    assert "rule:" in graph


def test_no_expected_graph_is_asserted_when_the_repository_states_no_rule(tmp_path: Path) -> None:
    silent = "# CLAUDE.md\n\n## Authority\n\nThis file outranks all others.\n"
    repo = baseline_repo(tmp_path / "neyma", claude=silent, commit_protocol=None, guard=False)
    graph = expected_graph(discover_protocol(repo.root), "abc1234")

    assert "no expected graph can be asserted" in graph


def test_the_current_graph_shows_roles_and_push_state(tmp_path: Path) -> None:
    graph = analyze(two_content_commits(tmp_path / "neyma")).render_graph()

    assert "BASELINE" in graph
    assert "CONTENT" in graph
    assert "REMEDIATION_CONTENT" in graph
    assert "[local]" in graph


def test_analysis_never_writes_to_the_repository(tmp_path: Path) -> None:
    repo = two_content_commits(tmp_path / "neyma")
    before = repo._git("status", "--porcelain"), repo.head(), repo._git("branch", "--list")

    analyze(repo)
    analyze(repo)

    assert (repo._git("status", "--porcelain"), repo.head(), repo._git("branch", "--list")) == before
