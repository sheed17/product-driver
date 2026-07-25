"""Remediation options, ranking, plan identity and the approval contract.

Nothing in this file executes a proposed plan against a real repository except
where a test explicitly runs one against its own throwaway fixture, to prove the
plan the driver proposes actually produces the graph and the tree it promises.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from neyma_product_driver.protocol_resolver import ProtocolResolver, approve_option
from neyma_product_driver.remediation_planner import (
    ApprovalRecord,
    ApprovalStore,
    NEVER_PROPOSE,
    ProtocolCompliance,
    RemediationOption,
    RiskLevel,
    rank_options,
    remediation_builder_prompt,
    validate_confirmation,
    verify_executed_plan,
)

from protocol_fixtures import p3_deadlock_repo, two_content_commits


@pytest.fixture
def resolution(tmp_path: Path):
    p3_deadlock_repo(tmp_path / "neyma")
    return ProtocolResolver(tmp_path / "neyma").resolve()


def option(resolution, option_id: str) -> RemediationOption:
    return next(o for o in resolution.options if o.option_id == option_id)


# --------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------


def test_every_materially_valid_option_is_offered(resolution) -> None:
    ids = {o.option_id for o in resolution.options}
    assert {"A", "B", "C", "D"} <= ids


def test_options_are_ranked_safest_first(resolution) -> None:
    ranks = [o.recommendation_rank for o in resolution.options]
    assert ranks == sorted(ranks)
    assert resolution.options[0].recommendation_rank == 1
    assert resolution.recommended_option.option_id == "A"


def test_the_recommended_option_preserves_both_reviewed_states(resolution) -> None:
    plan = resolution.recommended_option
    assert plan.evidence_preserved
    assert len(plan.archival_refs) == 2
    assert any("content" in ref for ref in plan.archival_refs)
    assert any("remediation" in ref for ref in plan.archival_refs)
    # The archival refs are created before anything moves.
    assert plan.operations[0].startswith("git branch ")
    assert "update-ref" in plan.operations[-2]


def test_the_plan_proves_tree_equivalence_before_any_ref_moves(resolution) -> None:
    plan = resolution.recommended_option
    verify_index = next(i for i, op in enumerate(plan.operations) if "git diff --stat" in op)
    move_index = next(i for i, op in enumerate(plan.operations) if "update-ref" in op)

    assert verify_index < move_index
    assert plan.expected_tree
    assert plan.expected_tree in " ".join(plan.operations)


def test_manual_finalization_is_disqualified_when_the_repository_forbids_it(resolution) -> None:
    manual = option(resolution, "D")
    assert manual.disqualified
    assert "does not authorize manual finalization" in manual.disqualification_reason
    assert manual.protocol_compliance.violates
    assert manual.recommendation_rank == len(resolution.options)
    assert resolution.recommended_option.option_id != "D"


def test_no_option_ever_proposes_destroying_evidence(resolution) -> None:
    for plan in resolution.options:
        blob = " ".join(plan.operations).lower()
        for forbidden in NEVER_PROPOSE:
            assert forbidden not in blob, f"{plan.option_id} proposes {forbidden}"


def test_history_rewriting_options_require_approval_and_name_their_operations(resolution) -> None:
    for option_id in ("A", "B"):
        plan = option(resolution, option_id)
        assert plan.rewrites_history
        assert plan.requires_human_approval
        assert plan.destructive_operations
    assert any("reset" in op for op in option(resolution, "B").destructive_operations)
    assert any("amend" in op for op in option(resolution, "B").destructive_operations)


def test_the_amend_option_is_withheld_when_it_would_absorb_a_later_commit(
    tmp_path: Path,
) -> None:
    """A soft reset re-commits the index, which would swallow anything after the content."""
    from protocol_fixtures import RECEIPT_PATHS

    repo = p3_deadlock_repo(tmp_path / "neyma")
    repo.commit("record the receipts", *RECEIPT_PATHS)  # a commit after the last content

    resolved = ProtocolResolver(repo.root).resolve()
    ids = {o.option_id for o in resolved.options}

    assert "A" in ids  # consolidation still works: it rebuilds from the content tree
    assert "B" not in ids
    assert resolved.recommended_option.option_id == "A"


def test_each_option_states_the_gates_to_rerun(resolution) -> None:
    plan = resolution.recommended_option
    joined = " ".join(plan.rerun_requirements)
    assert "canonical suite" in joined
    assert "finalizer" in joined
    assert "clean-clone" in joined
    assert "independent review" in joined


def test_shared_history_raises_severity_and_demands_remote_acknowledgement(tmp_path: Path) -> None:
    repo = two_content_commits(tmp_path / "neyma")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo.root, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "HEAD"], cwd=repo.root, check=True)

    resolved = ProtocolResolver(repo.root).resolve()
    plan = option(resolved, "A")

    assert plan.affects_remote_history
    assert plan.risk_level is RiskLevel.SEVERE
    assert "SHARED HISTORY" in plan.approval_phrase
    assert "REMOTE IMPACT" in plan.approval_phrase
    assert any("exists on a remote" in r for r in plan.risks)
    # And the recommendation moves away from rewriting history other clones
    # already have: it is no longer the safest option available.
    assert resolved.recommended_option.option_id != "A"
    assert not resolved.recommended_option.rewrites_history


def test_ranking_puts_a_disqualified_option_last_whatever_it_scores() -> None:
    safe = RemediationOption(
        option_id="X", title="safe", risk_level=RiskLevel.HIGH, evidence_preserved=True
    )
    tempting = RemediationOption(
        option_id="Y",
        title="tempting",
        risk_level=RiskLevel.LOW,
        evidence_preserved=True,
        disqualified=True,
        disqualification_reason="forbidden",
    )
    ordered = rank_options([tempting, safe])
    assert [o.option_id for o in ordered] == ["X", "Y"]


def test_ranking_prefers_a_compliant_option() -> None:
    compliant = RemediationOption(option_id="X", title="compliant")
    violating = RemediationOption(
        option_id="Y",
        title="violating",
        protocol_compliance=ProtocolCompliance(violates=["some-rule"]),
    )
    assert [o.option_id for o in rank_options([violating, compliant])] == ["X", "Y"]


# --------------------------------------------------------------------------
# Plan identity
# --------------------------------------------------------------------------


def test_the_same_repository_state_yields_the_same_plan_hash(tmp_path: Path) -> None:
    p3_deadlock_repo(tmp_path / "neyma")
    first = ProtocolResolver(tmp_path / "neyma").resolve()
    second = ProtocolResolver(tmp_path / "neyma").resolve()

    assert first.recommended_option.plan_hash == second.recommended_option.plan_hash
    assert first.plan_hash


def test_a_new_commit_expires_the_plan_hash(tmp_path: Path) -> None:
    repo = p3_deadlock_repo(tmp_path / "neyma")
    before = ProtocolResolver(repo.root).resolve().recommended_option.plan_hash

    repo.write("src/kernel.py", "def kernel():\n    return 99\n")
    repo.commit("more content", "src/kernel.py")
    after = ProtocolResolver(repo.root).resolve().recommended_option.plan_hash

    assert before != after


def test_a_changed_rule_expires_the_plan_hash(tmp_path: Path) -> None:
    repo = p3_deadlock_repo(tmp_path / "neyma")
    before = ProtocolResolver(repo.root).resolve().recommended_option.plan_hash

    repo.write("CLAUDE.md", (repo.root / "CLAUDE.md").read_text() + "\n## More\n\nX MUST be Y.\n")
    after = ProtocolResolver(repo.root).resolve().recommended_option.plan_hash

    assert before != after


def test_an_approval_only_matches_the_plan_it_approved(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    store.add(ApprovalRecord(option_id="A", plan_hash="abc123", confirmation="x", approval_phrase="x"))

    assert store.active("A", "abc123") is not None
    assert store.active("A", "different") is None
    assert store.active("B", "abc123") is None
    assert store.expired("different")


# --------------------------------------------------------------------------
# Confirmations
# --------------------------------------------------------------------------


def test_the_exact_phrase_is_an_approval(resolution) -> None:
    plan = resolution.recommended_option
    assert validate_confirmation(plan.approval_phrase, plan) == []
    assert validate_confirmation(f"  {plan.approval_phrase.lower()}  ", plan) == []


def test_a_vague_response_is_never_an_approval(resolution) -> None:
    plan = resolution.recommended_option
    for vague in ("go ahead with whatever", "sure", "yes", "do it", "approved", "up to you"):
        reasons = validate_confirmation(vague, plan)
        assert reasons, f"{vague!r} was accepted as an approval"
        assert any("exact required phrase" in r or "not an approval" in r for r in reasons)


def test_an_empty_confirmation_is_not_an_approval(resolution) -> None:
    assert validate_confirmation("", resolution.recommended_option)


def test_a_remote_impacting_plan_needs_the_remote_acknowledged() -> None:
    plan = RemediationOption(
        option_id="A",
        title="rewrite",
        affects_remote_history=True,
        approval_phrase="APPROVE P3 SHARED HISTORY NORMALIZATION INCLUDING REMOTE IMPACT",
    )
    assert validate_confirmation(plan.approval_phrase, plan) == []
    assert validate_confirmation("APPROVE P3 SHARED HISTORY NORMALIZATION", plan)


def test_approving_a_disqualified_option_is_refused(tmp_path: Path, resolution) -> None:
    record, reasons = approve_option(
        resolution=resolution,
        option_id="D",
        confirmation="APPROVE P3 MANUAL FINALIZATION",
        store=ApprovalStore(tmp_path),
        run_id="r1",
    )
    assert record is None
    assert any("disqualified" in r for r in reasons)


def test_approving_an_unknown_option_is_refused(tmp_path: Path, resolution) -> None:
    record, reasons = approve_option(
        resolution=resolution,
        option_id="Z",
        confirmation="anything",
        store=ApprovalStore(tmp_path),
    )
    assert record is None
    assert any("no option" in r for r in reasons)


def test_an_approval_records_the_option_and_the_plan_hash(tmp_path: Path, resolution) -> None:
    plan = resolution.recommended_option
    store = ApprovalStore(tmp_path)
    record, reasons = approve_option(
        resolution=resolution,
        option_id="A",
        confirmation=plan.approval_phrase,
        store=store,
        run_id="run-1",
    )

    assert reasons == []
    assert record.plan_hash == plan.plan_hash
    assert record.run_id == "run-1"
    assert store.active("A", plan.plan_hash) is not None
    assert store.path.exists()


# --------------------------------------------------------------------------
# The builder prompt
# --------------------------------------------------------------------------


def test_the_builder_prompt_binds_the_plan_it_was_approved_for(tmp_path: Path, resolution) -> None:
    plan = resolution.recommended_option
    record = ApprovalRecord(
        option_id="A",
        plan_hash=plan.plan_hash,
        confirmation=plan.approval_phrase,
        approval_phrase=plan.approval_phrase,
    )
    prompt = remediation_builder_prompt(
        option=plan, topology=resolution.topology, approval=record, unit_id="P3"
    )

    assert plan.plan_hash in prompt
    assert resolution.topology.head_commit in prompt
    assert resolution.topology.head_tree in prompt
    assert resolution.topology.baseline_commit in prompt
    for ref in plan.archival_refs:
        assert ref in prompt
    assert "EXPECTED GRAPH AFTER THE PLAN" in prompt
    assert "PROHIBITED, WITHOUT EXCEPTION" in prompt
    assert "VERIFICATION" in prompt
    assert "STOP CONDITIONS" in prompt
    assert plan.expected_tree in prompt


def test_the_builder_prompt_forbids_hand_written_status_and_pushing(resolution) -> None:
    plan = resolution.recommended_option
    record = ApprovalRecord(
        option_id="A", plan_hash=plan.plan_hash, confirmation="x", approval_phrase="x"
    )
    prompt = remediation_builder_prompt(option=plan, topology=resolution.topology, approval=record)

    assert "derived status file by hand" in prompt
    assert "pushing anything" in prompt
    assert "awards no criterion" in prompt


# --------------------------------------------------------------------------
# Executing the plan, and checking it was followed
# --------------------------------------------------------------------------


def test_the_recommended_plan_actually_produces_the_promised_tree(tmp_path: Path) -> None:
    """The plan is executed here, against this test's own throwaway fixture."""
    repo = p3_deadlock_repo(tmp_path / "neyma")
    resolver = ProtocolResolver(repo.root)
    plan = resolver.resolve().recommended_option

    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + "\n".join(plan.operations)],
        cwd=repo.root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    ok, deviations = resolver.verify_after_remediation(plan)
    assert ok, deviations

    after = resolver.resolve()
    assert len(after.topology.content_commits) == 1
    assert after.topology.head_tree == plan.expected_tree
    # Both reviewed states survive as archival refs.
    for ref in plan.archival_refs:
        assert repo._git("rev-parse", "--verify", ref)
    # And the topology deadlock is gone.
    assert after.deadlocks == []
    assert not [v for v in after.violations if v.violation_type.value == "HISTORY"]


def test_a_divergent_consolidated_tree_is_rejected(tmp_path: Path) -> None:
    repo = p3_deadlock_repo(tmp_path / "neyma")
    resolver = ProtocolResolver(repo.root)
    plan = resolver.resolve().recommended_option

    # A builder that "helpfully" changes a file while consolidating.
    operations = "\n".join(op for op in plan.operations if "test " not in op and "diff --stat" not in op)
    subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + operations.replace(plan.expected_tree, "HEAD^{tree}")],
        cwd=repo.root,
        capture_output=True,
        text=True,
    )
    repo.write("src/kernel.py", "def kernel():\n    return 'changed'\n")
    repo.commit("sneak in a change", "src/kernel.py")

    ok, deviations = resolver.verify_after_remediation(plan)
    assert not ok
    assert any("tree does not match" in d for d in deviations)


def test_a_missing_archival_ref_is_a_deviation(tmp_path: Path) -> None:
    repo = p3_deadlock_repo(tmp_path / "neyma")
    resolver = ProtocolResolver(repo.root)
    plan = resolver.resolve().recommended_option

    # Consolidate without creating the archival refs first.
    subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + "\n".join(
            op for op in plan.operations if not op.startswith("git branch ")
        )],
        cwd=repo.root,
        capture_output=True,
        text=True,
    )

    ok, deviations = resolver.verify_after_remediation(plan)
    assert not ok
    assert any("archival ref" in d for d in deviations)


def test_verification_flags_a_graph_that_still_has_two_content_commits() -> None:
    plan = RemediationOption(option_id="A", title="x", rewrites_history=True)
    from neyma_product_driver.git_topology import CommitRole, GitCommitRole, GitTopology

    after = GitTopology(
        head_commit="a" * 40,
        commits=[
            GitCommitRole(commit_sha="b" * 40, role=CommitRole.CONTENT),
            GitCommitRole(commit_sha="c" * 40, role=CommitRole.REMEDIATION_CONTENT),
        ],
    )
    ok, deviations = verify_executed_plan(plan, after)
    assert not ok
    assert any("exactly one content commit" in d.what for d in deviations)
