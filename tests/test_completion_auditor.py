"""The completion-claim auditor.

Every fixture builds a synthetic repository so the tests never depend on — or
touch — the real Neyma repository.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from neyma_product_driver.completion_auditor import (
    AuditDecision,
    ClaimType,
    CompletionAuditor,
    CriterionState,
    extract_claims,
    primary_claim,
)
from neyma_product_driver.context import RepositoryContextLoader
from neyma_product_driver.models import CommandResult

# --------------------------------------------------------------------------
# Repository builder
# --------------------------------------------------------------------------

FULL_CRITERIA = [
    ("accepted_scope_and_design", 6),
    ("required_tests", 8),
    ("core_implementation", 20),
    ("failure_handling", 8),
    ("concurrency_handling", 8),
    ("authorization_and_security", 10),
    ("migrations_and_persistence", 6),
    ("observability_and_operational_behavior", 6),
    ("mutation_or_hostile_cases", 8),
    ("full_test_suite", 5),
    ("canonical_finalizer", 3),
    ("clean_clone_execution", 3),
    ("independent_review", 5),
    ("final_adjudication", 4),
]


def criteria(results: dict[str, str] | None = None, default: str = "PENDING") -> list[dict]:
    results = results or {}
    return [
        {"criterion": name, "weight": w, "result": results.get(name, default)}
        for name, w in FULL_CRITERIA
    ]


def all_but_independent_pass() -> list[dict]:
    return criteria(
        {name: "PASS" for name, _ in FULL_CRITERIA if name not in ("independent_review", "final_adjudication")}
    )


class RepoBuilder:
    """Builds a synthetic Neyma-shaped repository."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.impl = root / "docs" / "implementation"
        self.impl.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
        (root / "CLAUDE.md").write_text("# CLAUDE.md\n## Authority\nThis file outranks all others.\n")
        (self.impl / "CURRENT.md").write_text("# CURRENT\n## Status\nWork in progress.\n")
        self.write_registry([self.unit("P3", "READY")])
        self.write_build_status()

    # -- pieces ----------------------------------------------------------

    @staticmethod
    def unit(uid: str, status: str, crits: list[dict] | None = None, **kw) -> dict:
        base = {
            "unit_id": uid,
            "name": f"{uid} unit",
            "status": status,
            "objective": f"objective of {uid}",
            "acceptance_contract": "docs/specifications/acceptance/platform-safety-acceptance.md",
            "acceptance_criteria": crits if crits is not None else criteria(),
        }
        base.update(kw)
        return base

    def write_registry(self, units: list[dict]) -> None:
        (self.impl / "IMPLEMENTATION-REGISTRY.yaml").write_text(
            yaml.safe_dump({"meta": {}, "units": units})
        )

    def write_build_status(
        self, percent: float = 0.0, content_commit: str = "", content_tree: str = "", **snapshot
    ) -> None:
        snap = {
            "finalizer_result": "NOT EXECUTED",
            "clean_clone_result": "NOT EXECUTED",
            "open_program_risks": ["R-07 OPEN - NOT CONTAINED. Only P4 closes it."],
        }
        snap.update(snapshot)
        (self.impl / "BUILD-STATUS.yaml").write_text(
            yaml.safe_dump(
                {
                    "derived": {
                        "current_phase_percent": percent,
                        "active_phase": "P3",
                        "content_commit": content_commit,
                        "content_tree": content_tree,
                    },
                    "snapshot": snap,
                }
            )
        )

    def write_suite_receipt(self, commit: str = "", tree: str = "", **kw) -> None:
        data = {
            "commit": commit or self.head_commit(),
            "tree": tree or self.head_tree(),
            "exit_status": 0,
            "passed": 1274,
            "failed": 0,
            "skipped": 0,
            "collected": 1274,
            "skipped_nodes": [],
            "command": ".venv/bin/python -m pytest -c pytest-canonical.ini -v",
        }
        data.update(kw)
        (self.impl / "SUITE-RESULT.json").write_text(json.dumps(data, indent=2))

    def write_gate_receipt(self, commit: str = "", tree: str = "", passed: bool = True, **kw) -> None:
        data = {
            "commit": commit or self.head_commit(),
            "tree": tree or self.head_tree(),
            "passed": passed,
            "gate": "clean_clone_gate",
            "steps": [],
        }
        data.update(kw)
        (self.impl / "GATE-RESULT.json").write_text(json.dumps(data, indent=2))

    def write_approved_skips(self, nodes: list[str]) -> None:
        (self.impl / "APPROVED-SKIPS.yaml").write_text(
            yaml.safe_dump({"expected_canonical_run_skips": nodes})
        )

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def commit_all(self, message: str = "work") -> None:
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=self.root, check=True)

    def head_commit(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True
        ).stdout.strip()

    def head_tree(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=self.root, capture_output=True, text=True
        ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> RepoBuilder:
    b = RepoBuilder(tmp_path / "neyma")
    b.write("src/kernel.py", "# implementation\n")
    b.commit_all("init")
    return b


def audit_of(repo: RepoBuilder, report: str, commands: list | None = None):
    return CompletionAuditor(repo.root).audit(report, run_commands=commands)


CLAIM_COMPLETE = "The work is finished. P3 is COMPLETE."


# --------------------------------------------------------------------------
# Claim extraction
# --------------------------------------------------------------------------


def test_extracts_a_phase_completion_claim() -> None:
    claims = extract_claims("All done — P3 is COMPLETE.")
    assert any(c.claim_type is ClaimType.PHASE_COMPLETE for c in claims)


def test_negated_completion_is_not_a_claim() -> None:
    for text in [
        "P3 is NOT complete.",
        "I cannot mark P3 COMPLETE.",
        "P3 is not yet complete; criteria remain pending.",
    ]:
        claims = extract_claims(text)
        assert not any(c.claim_type is ClaimType.PHASE_COMPLETE for c in claims), text


def test_extracts_receipt_and_review_claims() -> None:
    text = (
        "The full suite passed. The finalizer ran successfully. Clean-clone gate passed. "
        "Independent review complete. R-07 is contained. We are 100% complete."
    )
    types = {c.claim_type for c in extract_claims(text)}
    assert ClaimType.FULL_SUITE_PASSED in types
    assert ClaimType.FINALIZER_RAN in types
    assert ClaimType.CLEAN_CLONE_PASSED in types
    assert ClaimType.INDEPENDENT_REVIEW in types
    assert ClaimType.RISK_CONTAINED in types
    assert ClaimType.PROGRESS_PERCENT in types


def test_cited_evidence_paths_are_captured() -> None:
    claims = extract_claims("Done. See docs/implementation/phase-3-review.md for details. P3 is COMPLETE.")
    assert any("docs/implementation/phase-3-review.md" in c.claimed_evidence for c in claims)


def test_primary_claim_prefers_phase_completion() -> None:
    claims = extract_claims("All tests pass. P3 is COMPLETE.")
    assert primary_claim(claims).claim_type is ClaimType.PHASE_COMPLETE


def test_no_claims_from_an_empty_report() -> None:
    assert extract_claims("") == []


# --------------------------------------------------------------------------
# Weighted acceptance
# --------------------------------------------------------------------------


def test_progress_is_computed_from_criteria_not_from_prose(repo: RepoBuilder) -> None:
    audit = audit_of(repo, "We are 100% complete. P3 is COMPLETE.")
    assert audit.observed_state.progress.percent == 0.0
    assert any("exceeds what the criteria support" in c.what for c in audit.contradictions)


def test_partial_progress_is_weighted_correctly(repo: RepoBuilder) -> None:
    repo.write_registry([repo.unit("P3", "READY", criteria({"core_implementation": "PASS"}))])
    audit = audit_of(repo, "status update")
    # core_implementation is weight 20 of 100.
    assert audit.observed_state.progress.percent == pytest.approx(20.0)
    assert audit.observed_state.progress.earned_weight == 20


def test_ceiling_excludes_criteria_needing_a_fresh_session(repo: RepoBuilder) -> None:
    progress = audit_of(repo, "x").observed_state.progress
    # independent_review (5) + final_adjudication (4) = 9 of 100.
    assert progress.self_awardable_ceiling_percent == pytest.approx(91.0)


def test_criterion_state_semantics() -> None:
    assert CriterionState(criterion="x", result="PASS").passed
    assert not CriterionState(criterion="x", result="PENDING").passed
    assert CriterionState(criterion="independent_review").is_independent
    assert CriterionState(criterion="final_adjudication").is_independent
    assert not CriterionState(criterion="core_implementation").is_independent


# --------------------------------------------------------------------------
# Rejections the auditor must make
# --------------------------------------------------------------------------


def test_rejects_complete_with_pending_weighted_criteria(repo: RepoBuilder) -> None:
    audit = audit_of(repo, CLAIM_COMPLETE)
    assert audit.decision is AuditDecision.CONTRADICTED
    assert any("weighted criteria still pending" in c.what for c in audit.contradictions)


def test_rejects_a_cited_evidence_file_that_does_not_exist(repo: RepoBuilder) -> None:
    audit = audit_of(repo, "Reviewed in docs/implementation/phase-3-independent-review.md. Looks good.")
    assert any("does not exist" in c.what for c in audit.contradictions)
    assert audit.decision is AuditDecision.CONTRADICTED


def test_accepts_a_cited_evidence_file_that_does_exist(repo: RepoBuilder) -> None:
    repo.write("docs/implementation/notes.md", "notes")
    audit = audit_of(repo, "Progress recorded in docs/implementation/notes.md.")
    assert not any("does not exist" in c.what for c in audit.contradictions)


def test_rejects_an_implementers_own_review_as_independent(repo: RepoBuilder) -> None:
    repo.write("docs/implementation/phase-3-implementation-review.md", "# my own record\n")
    audit = audit_of(repo, "Independent review complete.")
    assert audit.decision is AuditDecision.CONTRADICTED
    assert any("not independent" in c.what for c in audit.contradictions)
    assert any("implementer's own record" in c.observed for c in audit.contradictions)


def test_rejects_an_untracked_review_as_independent(repo: RepoBuilder) -> None:
    """Produced in the implementing session's own working tree."""
    repo.write("docs/implementation/phase-3-review.md", "# review\nAll good.\n")
    audit = audit_of(repo, "Independent review complete.")
    assert any("untracked" in c.observed for c in audit.contradictions)


def test_rejects_independent_review_claim_with_no_artifact_at_all(repo: RepoBuilder) -> None:
    audit = audit_of(repo, "Independent review complete and adjudication done.")
    assert audit.decision is AuditDecision.CONTRADICTED
    assert any("no review artifact exists" in c.what or "registry still records" in c.what
               for c in audit.contradictions)


def test_rejects_finalizer_pass_without_a_receipt(repo: RepoBuilder) -> None:
    (repo.impl / "BUILD-STATUS.yaml").unlink()
    audit = audit_of(repo, "The finalizer ran successfully.")
    assert audit.decision is AuditDecision.CONTRADICTED
    assert any("finalizer PASS claimed with no receipt" in c.what for c in audit.contradictions)


def test_rejects_finalizer_pass_when_the_receipt_says_not_executed(repo: RepoBuilder) -> None:
    audit = audit_of(repo, "Status has been finalized.")
    assert any("finalizer" in c.what.lower() for c in audit.contradictions)


def test_rejects_clean_clone_pass_without_a_receipt(repo: RepoBuilder) -> None:
    audit = audit_of(repo, "Clean-clone gate passed.")
    assert audit.decision is AuditDecision.CONTRADICTED
    assert any("clean-clone gate PASS claimed with no receipt" in c.what for c in audit.contradictions)


def test_rejects_a_receipt_tied_to_the_wrong_commit(repo: RepoBuilder) -> None:
    repo.write_suite_receipt(commit="0" * 40, tree="1" * 40)
    repo.write_gate_receipt(commit="0" * 40, tree="1" * 40)
    audit = audit_of(repo, "The full suite passed and clean-clone gate passed.")
    assert audit.decision is AuditDecision.CONTRADICTED
    assert any("does not match the current tree" in c.what for c in audit.contradictions)
    assert any("different tree" in c.what for c in audit.contradictions)


def test_accepts_a_receipt_matching_the_current_tree(repo: RepoBuilder) -> None:
    repo.write_suite_receipt()
    repo.commit_all("add receipt")
    repo.write_suite_receipt()  # regenerate for the new head
    audit = audit_of(repo, "The full suite passed.")
    assert not any("does not match" in c.what for c in audit.contradictions)


def test_rejects_a_failing_receipt_presented_as_passing(repo: RepoBuilder) -> None:
    repo.write_suite_receipt(exit_status=1, failed=3)
    audit = audit_of(repo, "The full suite passed.")
    assert any("receipt says otherwise" in c.what for c in audit.contradictions)


def test_rejects_registry_ready_plus_narrative_complete(repo: RepoBuilder) -> None:
    (repo.impl / "CURRENT.md").write_text("# CURRENT\n\nPhase 3 status: P3 is COMPLETE and signed off.\n")
    audit = audit_of(repo, "status update")
    assert audit.decision is AuditDecision.CONTRADICTED
    assert any("declares P3 COMPLETE" in c.what for c in audit.contradictions)


def test_a_narrative_denial_is_not_a_completion_claim(repo: RepoBuilder) -> None:
    (repo.impl / "CURRENT.md").write_text(
        "# CURRENT\n\nP3 may not be recorded COMPLETE until adjudication.\n"
        "P4 requires P3 COMPLETE, and P3 is not.\n"
    )
    audit = audit_of(repo, "status update")
    assert not any("declares P3 COMPLETE" in c.what for c in audit.contradictions)


def test_a_quoted_claim_being_refuted_is_not_an_assertion(repo: RepoBuilder) -> None:
    (repo.impl / "CURRENT.md").write_text(
        '# CURRENT\n\n> An earlier session wrote that "P3 is COMPLETE and P4 is READY".\n'
        "That was false and has been retracted.\n"
    )
    audit = audit_of(repo, "status update")
    assert not any("declares P3 COMPLETE" in c.what for c in audit.contradictions)


def test_rejects_a_full_suite_claim_based_only_on_targeted_tests(repo: RepoBuilder) -> None:
    repo.write_suite_receipt()
    commands = [
        CommandResult(command=".venv/bin/python -m pytest eval/tests/test_phase3_witness.py -q", exit_code=0)
    ]
    audit = audit_of(repo, "The full suite passed.", commands=commands)
    assert any("rests only on targeted tests" in c.what for c in audit.contradictions)


def test_a_canonical_suite_command_is_not_flagged_as_targeted(repo: RepoBuilder) -> None:
    repo.write_suite_receipt()
    commands = [CommandResult(command=".venv/bin/python scripts/run_canonical_suite.py", exit_code=0)]
    audit = audit_of(repo, "The full suite passed.", commands=commands)
    assert not any("targeted tests" in c.what for c in audit.contradictions)


def test_rejects_a_dirty_tree_failure_disguised_as_an_ordinary_skip(repo: RepoBuilder) -> None:
    repo.write_approved_skips(["eval/tests/test_static.py::test_x"])
    repo.write_suite_receipt(
        skipped=19,
        skipped_nodes=[
            "eval/tests/test_action_callback.py::test_bind "
            "(PermissionError: [Errno 1] Operation not permitted binding a socket)"
        ],
    )
    audit = audit_of(repo, "The full suite passed with one expected skip.")
    assert any("environmental failure is recorded as an ordinary skip" in c.what
               for c in audit.contradictions)


def test_rejects_unapproved_skips(repo: RepoBuilder) -> None:
    repo.write_approved_skips(["eval/tests/test_static.py::test_x"])
    repo.write_suite_receipt(skipped=2, skipped_nodes=["eval/tests/test_other.py::test_y"])
    audit = audit_of(repo, "The full suite passed.")
    assert any("not approved skips" in c.what for c in audit.contradictions)


def test_rejects_phase_advancement_while_a_dependency_is_open(repo: RepoBuilder) -> None:
    repo.write_registry(
        [
            repo.unit("P3", "IN_PROGRESS"),
            repo.unit("P4", "READY", dependencies=["P3"]),
        ]
    )
    audit = audit_of(repo, "P4 is now the sole READY unit.")
    assert any("READY while its dependency P3 is not COMPLETE" in c.what
               for c in audit.contradictions)


def test_rejects_a_risk_claimed_contained_while_recorded_open(repo: RepoBuilder) -> None:
    audit = audit_of(repo, "R-07 is now contained.")
    assert audit.decision is AuditDecision.CONTRADICTED
    assert any("claimed contained while the repository records it open" in c.what
               for c in audit.contradictions)


def test_rejects_removal_of_a_required_architecture_limitation(repo: RepoBuilder) -> None:
    repo.write("ARCHITECTURE.md", "# Arch\n\nR-07 is OPEN - NOT CONTAINED.\nThe kernel ships dark.\n")
    repo.commit_all("record the limitation")
    repo.write("ARCHITECTURE.md", "# Arch\n\nThe kernel is in place.\n")
    audit = audit_of(repo, "Cleaned up the architecture document.")
    assert any("required limitation was removed" in c.what for c in audit.contradictions)


def test_rejects_a_fabricated_progress_percentage(repo: RepoBuilder) -> None:
    repo.write_build_status(percent=100.0)
    audit = audit_of(repo, "status update")
    assert any("more progress than the criteria support" in c.what for c in audit.contradictions)


def test_rejects_completion_claimed_on_an_uncommitted_tree(repo: RepoBuilder) -> None:
    repo.write("src/extra.py", "# uncommitted\n")
    audit = audit_of(repo, CLAIM_COMPLETE)
    assert any("uncommitted tree" in c.what for c in audit.contradictions)


# --------------------------------------------------------------------------
# Correct classifications
# --------------------------------------------------------------------------


def test_implementation_complete_but_review_pending(repo: RepoBuilder) -> None:
    """The state that must be neither failure nor completion."""
    repo.write_registry([repo.unit("P3", "READY", all_but_independent_pass())])
    repo.write_suite_receipt()
    repo.write_gate_receipt()
    repo.write_build_status(percent=91.0, finalizer_result="PASS", clean_clone_result="PASS")
    repo.commit_all("finalize")
    repo.write_suite_receipt()
    repo.write_gate_receipt()
    repo.write_build_status(
        percent=91.0, content_commit=repo.head_commit(), content_tree=repo.head_tree(),
        finalizer_result="PASS", clean_clone_result="PASS",
    )
    repo.commit_all("receipts")

    audit = audit_of(repo, "Implementation is complete. P3 is COMPLETE.")

    assert audit.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW
    assert audit.headline == "IMPLEMENTED — AWAITING INDEPENDENT REVIEW"
    assert audit.observed_state.progress.percent == pytest.approx(91.0)
    assert set(audit.observed_state.progress.independent_pending) == {
        "independent_review", "final_adjudication"
    }


def test_a_checkpoint_commit_is_not_finalization(repo: RepoBuilder) -> None:
    """Committed work with no finalizer receipt is not a finalized phase."""
    repo.write("src/kernel.py", "# more implementation\n")
    repo.commit_all("checkpoint: kernel work")
    audit = audit_of(repo, "Checkpoint committed. P3 is COMPLETE.")

    assert audit.decision is AuditDecision.CONTRADICTED
    finalizer = audit.observed_state.receipt("finalizer")
    assert finalizer is not None and not finalizer.passed
    assert audit.observed_state.dirty_file_count == 0  # committed...
    assert audit.observed_state.progress.percent == 0.0  # ...but not accepted


def test_all_criteria_verified_and_review_complete_is_verified(repo: RepoBuilder) -> None:
    repo.write_registry(
        [
            repo.unit("P3", "COMPLETE", criteria(default="PASS")),
            repo.unit("P4", "READY", criteria(), dependencies=["P3"]),
        ]
    )
    repo.write_build_status(percent=100.0, finalizer_result="PASS", clean_clone_result="PASS",
                            open_program_risks=[])  # content ids set after the final commit
    repo.write("docs/implementation/phase-3-independent-adjudication.md", "# independent\n")
    repo.commit_all("complete")
    repo.write_suite_receipt()
    repo.write_gate_receipt()
    repo.commit_all("receipts")
    repo.write_suite_receipt()
    repo.write_gate_receipt()
    repo.write_build_status(percent=100.0, content_commit=repo.head_commit(),
                            content_tree=repo.head_tree(), finalizer_result="PASS",
                            clean_clone_result="PASS", open_program_risks=[])
    repo.commit_all("receipts2")

    p3 = next(
        u for u in yaml.safe_load((repo.impl / "IMPLEMENTATION-REGISTRY.yaml").read_text())["units"]
        if u["unit_id"] == "P3"
    )
    from neyma_product_driver.context import ActiveUnit

    unit = ActiveUnit(
        unit_id="P3", name="P3 unit", status="COMPLETE",
        acceptance_criteria=p3["acceptance_criteria"],
    )
    audit = CompletionAuditor(repo.root).audit(
        "P3 is COMPLETE. The full suite passed and the clean-clone gate passed.", unit=unit
    )
    assert audit.decision is AuditDecision.VERIFIED, audit.contradictions
    assert audit.correction_prompt == ""


def test_an_honest_report_refusing_self_adjudication_is_not_contradicted(repo: RepoBuilder) -> None:
    report = (
        "I implemented the kernel. P3 is NOT complete: all 14 weighted criteria remain "
        "PENDING. I cannot mark independent_review or final_adjudication because they "
        "require a session other than this one. The finalizer has not run."
    )
    audit = audit_of(repo, report)
    assert audit.decision is not AuditDecision.CONTRADICTED
    assert audit.contradictions == []


def test_no_claims_means_nothing_to_contradict(repo: RepoBuilder) -> None:
    audit = audit_of(repo, "I refactored a helper function and added a docstring.")
    assert audit.decision is AuditDecision.VERIFIED
    assert audit.contradictions == []


# --------------------------------------------------------------------------
# The exact failure class this auditor exists for
# --------------------------------------------------------------------------


@pytest.fixture
def premature_completion_repo(tmp_path: Path) -> RepoBuilder:
    """Implementation exists; status claims COMPLETE; nothing supports it.

    - implementation exists
    - status documents claim the phase is COMPLETE
    - the registry still records the unit READY
    - every weighted criterion is still PENDING
    - the cited review artifact is absent
    - finalizer and clean-clone are claimed but never ran
    """
    b = RepoBuilder(tmp_path / "neyma")
    b.write("src/checkpoint.py", "# the implemented kernel\n")
    b.write("src/brake.py", "# brake\n")
    b.commit_all("implement the kernel")

    b.write(
        "docs/implementation/CURRENT.md",
        "# CURRENT\n\n## Status\nP3 is COMPLETE. The kernel is implemented, reviewed and\n"
        "adjudicated. Next: P4.\n",
    )
    b.write("README.md", "| Implementation Phase 3 | COMPLETE |\n")
    b.write_build_status(
        percent=100.0,
        finalizer_result="PASS",
        clean_clone_result="PASS",
        independent_review_status="COMPLETE",
    )
    # Registry unchanged: still READY, all criteria PENDING.
    return b


def test_the_premature_completion_failure_class_is_contradicted(
    premature_completion_repo: RepoBuilder,
) -> None:
    repo = premature_completion_repo
    report = (
        "P3 is COMPLETE. The full suite passed, the finalizer ran successfully, and the "
        "clean-clone gate passed. Independent review is documented in "
        "docs/implementation/phase-3-independent-review.md. We are 100% complete."
    )
    audit = audit_of(repo, report)

    assert audit.decision is AuditDecision.CONTRADICTED

    what = " | ".join(c.what for c in audit.contradictions)
    assert "registry says otherwise" in what
    assert "weighted criteria still pending" in what
    assert "does not exist" in what              # the cited review artifact
    assert "finalizer" in what.lower()
    assert "clean-clone" in what.lower()
    assert "declares P3 COMPLETE" in what        # narrative surface
    assert "more progress than the criteria support" in what

    # Progress is computed, not believed.
    assert audit.observed_state.progress.percent == 0.0
    assert audit.observed_state.implementation_present is True


def test_the_failure_class_correction_returns_to_the_supported_state(
    premature_completion_repo: RepoBuilder,
) -> None:
    audit = audit_of(
        premature_completion_repo,
        "P3 is COMPLETE. The finalizer ran and independent review is done.",
    )
    correction = audit.correction_prompt

    assert "HIGHEST EVIDENCE-SUPPORTED state" in correction
    assert "PRESERVE all implementation code" in correction
    # Never green-by-deletion.
    assert "Do NOT delete, weaken or disable any acceptance guard" in correction
    # Never destroy untracked implementation work.
    assert "git stash" in correction and "restore" in correction
    # Never self-adjudicate.
    assert "PENDING" in correction
    assert "0%" in correction


def test_the_failure_class_summary_block(premature_completion_repo: RepoBuilder) -> None:
    audit = audit_of(premature_completion_repo, "P3 is COMPLETE and 100% done.")
    block = audit.summary_block()

    assert "COMPLETION CLAIM: CONTRADICTED" in block
    assert "IMPLEMENTATION STATE: PRESENT" in block
    assert "VERIFIED PROGRESS: 0%" in block
    assert "ceiling without independent review: 91%" in block
    assert "NEXT SAFE ACTION:" in block


# --------------------------------------------------------------------------
# Safety of the auditor itself
# --------------------------------------------------------------------------


def test_the_auditor_never_modifies_the_repository(repo: RepoBuilder) -> None:
    before_head = repo.head_commit()
    before_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo.root, capture_output=True, text=True
    ).stdout

    audit_of(repo, "P3 is COMPLETE. Everything passed. 100% done.")

    assert repo.head_commit() == before_head
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo.root, capture_output=True, text=True
        ).stdout
        == before_status
    )


def test_audit_survives_a_repository_missing_every_receipt(tmp_path: Path) -> None:
    b = RepoBuilder(tmp_path / "bare")
    b.commit_all("init")
    for name in ("SUITE-RESULT.json", "GATE-RESULT.json", "BUILD-STATUS.yaml"):
        (b.impl / name).unlink(missing_ok=True)

    audit = audit_of(b, "P3 is COMPLETE.")
    assert audit.decision is AuditDecision.CONTRADICTED  # never VERIFIED by default


def test_audit_of_a_corrupt_receipt_does_not_crash(repo: RepoBuilder) -> None:
    (repo.impl / "SUITE-RESULT.json").write_text("{not json")
    audit = audit_of(repo, "The full suite passed.")
    assert audit.decision in (AuditDecision.CONTRADICTED, AuditDecision.UNPROVEN)


def test_active_unit_comes_from_the_repository(tmp_path: Path) -> None:
    b = RepoBuilder(tmp_path / "n")
    b.write_registry([b.unit("P9", "READY")])
    b.commit_all("init")
    unit = RepositoryContextLoader(b.root).resolve_active_unit()
    audit = CompletionAuditor(b.root).audit("status", unit=unit)
    assert audit.observed_state.active_unit_id == "P9"
