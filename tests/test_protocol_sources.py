"""Protocol discovery: the repository states the rules, the driver reads them.

Every test builds a synthetic repository. Nothing here reads the real Neyma
repository, and nothing here writes to any repository.
"""

from __future__ import annotations

from pathlib import Path

from neyma_product_driver.protocol_sources import (
    AuthorityLevel,
    RuleKind,
    discover_protocol,
)

from protocol_fixtures import (
    CLAUDE_MD,
    CONFLICTING_PROTOCOL_MD,
    baseline_repo,
    one_content_commit,
)


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------


def test_reads_the_repositorys_own_protocol_documents(tmp_path: Path) -> None:
    repo = baseline_repo(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)

    read = protocol.sources.all_paths()
    assert "CLAUDE.md" in read
    assert "docs/implementation/COMMIT-PROTOCOL.md" in read
    assert "scripts/finalize_status.py" in read
    assert "eval/test_status_reality.py" in read
    assert "docs/implementation/IMPLEMENTATION-REGISTRY.yaml" in read


def test_discovers_every_rule_kind_the_repository_states(tmp_path: Path) -> None:
    repo = baseline_repo(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)

    for kind in (
        RuleKind.COMMIT_TOPOLOGY,
        RuleKind.FINALIZER_OWNERSHIP,
        RuleKind.MANUAL_STATUS_PROHIBITED,
        RuleKind.CANONICAL_SUITE_GATE,
        RuleKind.STATUS_REALITY_GUARD,
        RuleKind.CLEAN_CLONE_GATE,
        RuleKind.RECEIPT_FRESHNESS,
        RuleKind.INDEPENDENT_REVIEW,
        RuleKind.HISTORY_REWRITE_APPROVAL,
    ):
        assert protocol.effective(kind) is not None, f"{kind.value} was not discovered"


def test_every_rule_cites_the_sentence_that_states_it(tmp_path: Path) -> None:
    repo = baseline_repo(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)

    rule = protocol.effective(RuleKind.COMMIT_TOPOLOGY)
    assert rule is not None
    assert rule.source_path == "CLAUDE.md"
    assert rule.source_lines_or_section.startswith("L")
    assert "content commit" in rule.description
    assert rule.cite().startswith("CLAUDE.md L")


def test_the_topology_limit_is_parsed_not_assumed(tmp_path: Path) -> None:
    """A repository that permits two content commits must be judged against two."""
    permissive = CLAUDE_MD.replace(
        "exactly one content commit, followed by one",
        "exactly two content commits, followed by one",
    )
    repo = baseline_repo(tmp_path / "neyma", claude=permissive)
    protocol = discover_protocol(repo.root)

    rule = protocol.effective(RuleKind.COMMIT_TOPOLOGY)
    assert rule.parameters["max_content_commits"] == 2


def test_a_rule_the_repository_does_not_state_is_not_discovered(tmp_path: Path) -> None:
    silent = "# CLAUDE.md\n\n## Authority\n\nThis file outranks all others.\n"
    repo = baseline_repo(
        tmp_path / "neyma", claude=silent, commit_protocol=None, guard=False, finalizer=False
    )
    protocol = discover_protocol(repo.root)

    assert protocol.effective(RuleKind.COMMIT_TOPOLOGY) is None
    assert protocol.effective(RuleKind.STATUS_REALITY_GUARD) is None


def test_wrapped_prose_is_read_as_one_rule(tmp_path: Path) -> None:
    """The rule spans two lines of a markdown paragraph; it is still one rule."""
    repo = baseline_repo(tmp_path / "neyma")
    rule = discover_protocol(repo.root).effective(RuleKind.COMMIT_TOPOLOGY)

    assert rule.parameters["max_content_commits"] == 1
    assert rule.parameters["requires_metadata_commit"] is True
    assert rule.parameters["metadata_commit_owner"] == "finalizer"


# --------------------------------------------------------------------------
# Authority
# --------------------------------------------------------------------------


def test_authority_ordering_is_read_from_the_repository(tmp_path: Path) -> None:
    repo = baseline_repo(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)

    assert protocol.authority_order[0] == "CLAUDE.md"
    assert "COMMIT-PROTOCOL.md" in protocol.authority_order
    assert protocol.effective(RuleKind.COMMIT_TOPOLOGY).authority_level is AuthorityLevel.CANONICAL


def test_implementation_files_carry_implementation_authority(tmp_path: Path) -> None:
    repo = baseline_repo(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)

    guard_rules = [
        r for r in protocol.of_kind(RuleKind.STATUS_REALITY_GUARD) if r.source_path.endswith(".py")
    ]
    assert guard_rules
    assert all(r.authority_level is AuthorityLevel.IMPLEMENTATION for r in guard_rules)


def test_finalizer_owned_paths_come_from_the_finalizer_itself(tmp_path: Path) -> None:
    repo = baseline_repo(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)

    assert "docs/implementation/BUILD-STATUS.yaml" in protocol.finalizer_owned_paths
    assert "docs/implementation/CURRENT.md" in protocol.finalizer_owned_paths


# --------------------------------------------------------------------------
# Conflicting protocol
# --------------------------------------------------------------------------


def test_contradictory_topology_rules_are_reported_as_a_conflict(tmp_path: Path) -> None:
    repo = baseline_repo(tmp_path / "neyma", extra_protocol=CONFLICTING_PROTOCOL_MD)
    protocol = discover_protocol(repo.root)

    assert protocol.conflicts
    conflict = protocol.conflicts[0]
    assert conflict.kind is RuleKind.COMMIT_TOPOLOGY
    assert "two different limits" in conflict.description
    assert len(conflict.sources) >= 2


def test_a_conflict_the_repository_has_not_ranked_is_unresolvable(tmp_path: Path) -> None:
    """The driver's guess about which document matters more is not authority."""
    repo = baseline_repo(tmp_path / "neyma", extra_protocol=CONFLICTING_PROTOCOL_MD)
    protocol = discover_protocol(repo.root)

    assert protocol.unresolved_conflicts
    assert not protocol.conflicts[0].resolved_by_authority


def test_a_conflict_the_repository_ranked_is_resolved_by_its_own_ordering(tmp_path: Path) -> None:
    ranked = CLAUDE_MD.replace(
        "4. docs/implementation/BUILD-STATUS.yaml",
        "4. docs/implementation/BUILD-STATUS.yaml\n5. docs/implementation/PROGRESS-PROTOCOL.md",
    )
    repo = baseline_repo(tmp_path / "neyma", claude=ranked, extra_protocol=CONFLICTING_PROTOCOL_MD)
    protocol = discover_protocol(repo.root)

    assert protocol.conflicts
    assert protocol.conflicts[0].resolved_by_authority
    assert not protocol.unresolved_conflicts
    # The winner is the document the repository ranked first.
    assert protocol.rule(protocol.conflicts[0].winning_rule_id).source_path == "CLAUDE.md"


def test_manual_finalization_authorization_conflicts_with_the_prohibition(tmp_path: Path) -> None:
    authorizing = CLAUDE_MD + (
        "\n## Exception\n\nManual finalization is explicitly authorized when the finalizer "
        "cannot run.\n"
    )
    repo = baseline_repo(tmp_path / "neyma", claude=authorizing)
    protocol = discover_protocol(repo.root)

    kinds = {c.kind for c in protocol.conflicts}
    assert RuleKind.MANUAL_FINALIZATION_ALLOWED in kinds or RuleKind.FINALIZER_OWNERSHIP in kinds


def test_every_rule_id_identifies_exactly_one_rule(tmp_path: Path) -> None:
    """A violation citing an ambiguous id names a sentence the reader cannot find."""
    repo = baseline_repo(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)

    rule_ids = [r.rule_id for r in protocol.rules]
    assert len(rule_ids) == len(set(rule_ids))
    for rule_id in rule_ids:
        assert protocol.rule(rule_id) is not None


def test_a_file_matching_two_categories_is_read_once(tmp_path: Path) -> None:
    """COMMIT-PROTOCOL.md matches both the commit and the protocol globs."""
    repo = baseline_repo(tmp_path / "neyma")
    protocol = discover_protocol(repo.root)

    appearances = [
        category
        for category, paths in protocol.sources.by_category.items()
        if "docs/implementation/COMMIT-PROTOCOL.md" in paths
    ]
    assert len(appearances) == 1


def test_discovery_never_writes_to_the_repository(tmp_path: Path) -> None:
    repo = one_content_commit(tmp_path / "neyma")
    before = repo._git("status", "--porcelain"), repo.head()

    discover_protocol(repo.root)
    discover_protocol(repo.root)

    assert (repo._git("status", "--porcelain"), repo.head()) == before
