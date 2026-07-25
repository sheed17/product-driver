"""End-to-end protocol resolution, including the P3 failure fixture.

The resolver answers: what is blocked, which rule, what evidence, what kind of
defect, is it circular, what may be done about it, which option is safest, does
it need approval, what runs next.
"""

from __future__ import annotations

from pathlib import Path

from neyma_product_driver.protocol_resolver import (
    CauseCategory,
    ProtocolResolver,
    ProtocolStatus,
)
from neyma_product_driver.protocol_sources import ViolationType

from protocol_fixtures import (
    CLAUDE_MD,
    CONFLICTING_PROTOCOL_MD,
    TLS_ERROR,
    baseline_repo,
    content_plus_finalizer_metadata,
    content_plus_hand_edited_status,
    one_content_commit,
    p3_deadlock_repo,
    two_content_commits,
)


def resolve(repo, **kw):
    return ProtocolResolver(repo.root).resolve(**kw)


# --------------------------------------------------------------------------
# Consistent states
# --------------------------------------------------------------------------


def test_the_permitted_topology_is_consistent(tmp_path: Path) -> None:
    resolution = resolve(content_plus_finalizer_metadata(tmp_path / "neyma"))

    assert resolution.status is ProtocolStatus.CONSISTENT
    assert resolution.violations == []
    assert resolution.deadlocks == []
    assert resolution.options == []
    assert not resolution.blocks_independent_review
    assert resolution.next_safe_action.startswith("proceed")


def test_work_in_progress_before_finalization_is_not_a_violation(tmp_path: Path) -> None:
    """One content commit and no metadata commit yet is 'not finalized', not 'wrong'."""
    resolution = resolve(one_content_commit(tmp_path / "neyma"))

    assert resolution.status is ProtocolStatus.CONSISTENT
    assert resolution.violations == []


def test_a_repository_permitting_two_content_commits_is_judged_against_two(tmp_path: Path) -> None:
    """The rule is read from the repository, never assumed."""
    permissive = CLAUDE_MD.replace(
        "exactly one content commit, followed by one",
        "exactly two content commits, followed by one",
    )
    resolution = resolve(two_content_commits(tmp_path / "neyma", claude=permissive))

    assert resolution.status is ProtocolStatus.CONSISTENT
    assert not [v for v in resolution.violations if v.violation_type is ViolationType.HISTORY]


# --------------------------------------------------------------------------
# Violations
# --------------------------------------------------------------------------


def test_two_content_commits_violate_the_stated_topology(tmp_path: Path) -> None:
    """A green suite removes the deadlock. The topology is still wrong."""
    repo = two_content_commits(tmp_path / "neyma")
    repo.write_suite_receipt(failed=0, exit_status=0)
    resolution = resolve(repo)

    assert resolution.deadlocks == []
    # Correcting it still means rewriting history, which is never the driver's
    # to do unasked.
    assert resolution.status is ProtocolStatus.REQUIRES_APPROVAL
    violation = resolution.violations[0]
    assert violation.violation_type is ViolationType.HISTORY
    assert "2 content commit" in violation.observed_state
    assert violation.rule_id.startswith("commit-topology:CLAUDE.md")
    assert "CLAUDE.md" in violation.rule_citation
    assert violation.evidence_paths


def test_a_hand_edited_status_commit_violates_finalizer_ownership(tmp_path: Path) -> None:
    repo = content_plus_hand_edited_status(tmp_path / "neyma")
    resolution = resolve(repo)

    violations = [v for v in resolution.violations if "hand-edited" in v.detail]
    assert violations
    assert violations[0].expected_state == "derived status is written only by the finalizer"
    assert violations[0].violation_type is ViolationType.HISTORY


def test_a_stale_receipt_is_an_evidence_defect_not_a_history_defect(tmp_path: Path) -> None:
    repo = one_content_commit(tmp_path / "neyma")
    repo.write_suite_receipt(commit="0" * 40, tree="1" * 40)
    resolution = resolve(repo)

    stale = [v for v in resolution.violations if v.detail == "stale receipt"]
    assert stale
    assert stale[0].violation_type is ViolationType.EVIDENCE
    assert "not HEAD" in stale[0].observed_state
    assert resolution.cause("PRIMARY").category is CauseCategory.EVIDENCE
    # Nothing here needs a history rewrite, so nothing here needs approval.
    assert resolution.status is ProtocolStatus.VIOLATION
    assert resolution.options == []


def test_a_status_commit_before_the_content_commit_is_a_violation(tmp_path: Path) -> None:
    repo = baseline_repo(tmp_path / "neyma")
    repo.write_current("# CURRENT\n\nstatus written first\n")
    repo.commit("status before content", "docs/implementation/CURRENT.md")
    repo.write("src/kernel.py", "def kernel():\n    return 3\n")
    repo.commit("content after status", "src/kernel.py")

    observed = [v.observed_state for v in resolve(repo).violations]
    assert any("precedes the content commit" in o for o in observed)


# --------------------------------------------------------------------------
# Authority
# --------------------------------------------------------------------------


def test_contradictory_protocol_returns_blocked_authority(tmp_path: Path) -> None:
    repo = two_content_commits(tmp_path / "neyma", extra_protocol=CONFLICTING_PROTOCOL_MD)
    resolution = resolve(repo)

    assert resolution.status is ProtocolStatus.BLOCKED_AUTHORITY
    assert resolution.conflicts
    assert resolution.cause("PRIMARY").category is CauseCategory.PROTOCOL
    assert "cannot both hold" in resolution.cause("PRIMARY").summary
    assert "no standing to choose" in resolution.next_safe_action
    assert resolution.blocks_independent_review


def test_authority_conflict_outranks_every_other_finding(tmp_path: Path) -> None:
    """Even with a deadlock present, an unresolvable authority conflict comes first."""
    repo = p3_deadlock_repo(tmp_path / "neyma")
    repo.write("docs/implementation/PROGRESS-PROTOCOL.md", CONFLICTING_PROTOCOL_MD)
    resolution = resolve(repo)

    assert resolution.status is ProtocolStatus.BLOCKED_AUTHORITY
    assert resolution.deadlocks  # still detected and reported, just not decisive


def test_an_undeterminable_baseline_blocks_on_authority(tmp_path: Path) -> None:
    repo = one_content_commit(tmp_path / "neyma")
    resolution = ProtocolResolver(repo.root).resolve(baseline="")
    assert resolution.status in (ProtocolStatus.CONSISTENT, ProtocolStatus.BLOCKED_AUTHORITY)


# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------


def test_a_clean_clone_tls_failure_is_environmental_and_never_a_pass(tmp_path: Path) -> None:
    repo = one_content_commit(tmp_path / "neyma")
    repo.write_gate_receipt(passed=False, error=TLS_ERROR)
    resolution = resolve(repo)

    assert resolution.status is ProtocolStatus.BLOCKED_ENVIRONMENT
    assert resolution.environment_blockers
    environmental = [v for v in resolution.violations if v.violation_type is ViolationType.ENVIRONMENT]
    assert environmental
    assert environmental[0].severity == "environmental"
    assert "not a product failure" in environmental[0].detail
    assert "do not record its result as PASS" in resolution.next_safe_action


# --------------------------------------------------------------------------
# The P3 fixture
# --------------------------------------------------------------------------


def test_the_p3_fixture_requires_approval(tmp_path: Path) -> None:
    resolution = resolve(p3_deadlock_repo(tmp_path / "neyma"))
    assert resolution.status is ProtocolStatus.REQUIRES_APPROVAL
    assert resolution.requires_human_approval


def test_the_p3_fixtures_primary_cause_is_the_commit_history_deadlock(tmp_path: Path) -> None:
    resolution = resolve(p3_deadlock_repo(tmp_path / "neyma"))
    primary = resolution.cause("PRIMARY")

    assert primary.category is CauseCategory.HISTORY
    assert "commit-history finalization deadlock" in primary.summary


def test_the_p3_fixtures_secondary_blocker_is_the_environment(tmp_path: Path) -> None:
    resolution = resolve(p3_deadlock_repo(tmp_path / "neyma"))
    secondary = resolution.cause("SECONDARY")

    assert secondary.category is CauseCategory.ENVIRONMENT
    assert "clean-clone" in secondary.summary
    assert "TLS" in secondary.summary


def test_the_p3_fixture_defers_independent_review_rather_than_demanding_it_now(tmp_path: Path) -> None:
    resolution = resolve(p3_deadlock_repo(tmp_path / "neyma"))
    later = [c for c in resolution.root_causes if c.priority == "REQUIRED_LATER"]

    assert any(c.category is CauseCategory.INDEPENDENT_REVIEW for c in later)
    assert any(c.category is CauseCategory.HUMAN_APPROVAL for c in later)
    assert resolution.blocks_independent_review


def test_the_p3_fixture_keeps_unrelated_blockers_separate(tmp_path: Path) -> None:
    """A TLS outage and a commit-history deadlock are two findings, not one."""
    resolution = resolve(p3_deadlock_repo(tmp_path / "neyma"))
    categories = [c.category for c in resolution.root_causes]

    assert CauseCategory.HISTORY in categories
    assert CauseCategory.ENVIRONMENT in categories
    assert len(set(categories)) == len(categories)


def test_the_p3_fixture_recommends_preserving_both_states(tmp_path: Path) -> None:
    resolution = resolve(p3_deadlock_repo(tmp_path / "neyma"))
    plan = resolution.recommended_option

    assert plan.option_id == "A"
    assert plan.evidence_preserved
    assert len(plan.archival_refs) == 2
    assert plan.expected_tree
    assert plan.approval_phrase == "APPROVE P3 LOCAL HISTORY NORMALIZATION"


def test_the_p3_fixture_does_not_call_the_implementation_incomplete(tmp_path: Path) -> None:
    """PENDING criteria are not the resolver's finding, and it must not imply they are."""
    resolution = resolve(p3_deadlock_repo(tmp_path / "neyma"))
    report = resolution.render_report().lower()

    assert "incomplete" not in report
    assert "unresolved finding" not in report
    for violation in resolution.violations:
        assert violation.violation_type is not ViolationType.CODE


def test_the_p3_fixture_never_proposes_hand_editing_status_or_retrying(tmp_path: Path) -> None:
    resolution = resolve(p3_deadlock_repo(tmp_path / "neyma"))
    plan = resolution.recommended_option

    assert "finalize" not in " ".join(plan.operations).lower()
    assert not any("status" in op.lower() and "write" in op.lower() for op in plan.operations)
    manual = next(o for o in resolution.options if o.option_id == "D")
    assert manual.disqualified


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def test_the_report_answers_the_questions_it_exists_to_answer(tmp_path: Path) -> None:
    resolution = resolve(p3_deadlock_repo(tmp_path / "neyma"))
    report = resolution.render_report(run_id="20260722-000000")

    assert "REPOSITORY PROTOCOL: REQUIRES_APPROVAL" in report
    assert "PRIMARY ROOT CAUSE:" in report
    assert "CURRENT GRAPH:" in report
    assert "EXPECTED GRAPH:" in report
    assert "WHY THE FINALIZER CANNOT PROCEED:" in report
    assert "SECONDARY BLOCKER:" in report
    assert "REQUIRED AFTER REPAIR:" in report
    assert "RECOMMENDED OPTION:" in report
    assert "APPROVAL REQUIRED:" in report
    assert "APPROVE P3 LOCAL HISTORY NORMALIZATION" in report
    assert "python -m neyma_product_driver approve --run 20260722-000000" in report


def test_the_approval_prompt_shows_everything_a_decision_needs(tmp_path: Path) -> None:
    resolution = resolve(p3_deadlock_repo(tmp_path / "neyma"))
    prompt = resolution.approval_prompt

    for heading in (
        "CURRENT GRAPH",
        "EXPECTED GRAPH",
        "ROOT CAUSE",
        "PROPOSED OPERATIONS",
        "EVIDENCE PRESERVED",
        "RISKS",
        "REMOTE IMPACT",
        "COMMANDS TO BE EXECUTED",
        "GATES TO RERUN",
        "PLAN HASH",
    ):
        assert heading in prompt, heading
    assert "A general agreement is not an approval" in prompt


def test_no_approval_prompt_when_nothing_needs_approving(tmp_path: Path) -> None:
    resolution = resolve(content_plus_finalizer_metadata(tmp_path / "neyma"))
    assert resolution.approval_prompt == ""


def test_the_resolution_records_which_sources_it_read(tmp_path: Path) -> None:
    resolution = resolve(p3_deadlock_repo(tmp_path / "neyma"))

    assert "CLAUDE.md" in resolution.sources_read
    assert resolution.rules_consulted
    assert resolution.unit_id == "P3"


def test_diagnosis_is_read_only_and_needs_no_approval(tmp_path: Path) -> None:
    repo = p3_deadlock_repo(tmp_path / "neyma")
    before = (repo._git("status", "--porcelain"), repo.head(), repo._git("branch", "--list"))

    resolve(repo)
    resolve(repo)

    assert (repo._git("status", "--porcelain"), repo.head(), repo._git("branch", "--list")) == before
