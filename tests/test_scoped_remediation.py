"""A bounded remediation is not a phase acceptance.

The defect pinned here came from a real run. Its task said "Close ONLY the
concrete G4 verification gaps", "This is a G4 REMEDIATION task ... not a
phase-acceptance run" and "Do not mark P8 COMPLETE" — and was resolved as
PHASE_ACCEPTANCE, because a later sentence told the builder to "Preserve every
accepted P0-P8 invariant". Two things went wrong at once:

1. ``accepted`` there is an adjective describing invariants that already hold,
   and ``P8`` is the far end of the range ``P0-P8``. Neither is a request to
   accept P8.
2. Even without that match, a task naming no unit fell back to the whole phase,
   so a bounded repair was still held to the phase's own acceptance bar.

The fix is a fourth intent, SCOPED_REMEDIATION: a bounded task inside the
phase, granted only when the task names what it repairs AND explicitly refuses
the phase's acceptance. Any positive request for the phase still wins, and a
vague task keeps the strict reading.

The run's exact task text is kept as a fixture, byte for byte.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from neyma_product_driver.completion_auditor import AuditDecision, CompletionAuditor
from neyma_product_driver.context import RepositoryContextLoader
from neyma_product_driver.task_scope import (
    ScopeIntent,
    ScopeLevel,
    TaskResult,
    resolve_task_scope,
    scoped_completion,
)

PHASE = "P8"
CRITERIA = [f"{PHASE}-AC-{i}" for i in range(1, 18)]
EXACT_TASK = (Path(__file__).parent / "fixtures" / "task-20260924-073928.txt").read_text()


class Repo:
    """One phase READY with seventeen PENDING criteria, and the phase after it."""

    def __init__(self, root: Path, *, status: str = "READY") -> None:
        self.root = root
        self.impl = root / "docs" / "implementation"
        self.impl.mkdir(parents=True)
        for args in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@e.com"],
            ["git", "config", "user.name", "t"],
        ):
            subprocess.run(args, cwd=root, check=True)
        (root / "CLAUDE.md").write_text("# CLAUDE.md\n\nimplement -> test -> commit\n")
        self.write_registry(status)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "phase"], cwd=root, check=True)

    def write_registry(self, status: str) -> None:
        unit = {
            "unit_id": PHASE,
            "name": "the phase under remediation",
            "status": status,
            "execution_state": "NOT_STARTED",
            "acceptance_criteria": [
                {
                    "id": cid,
                    "criterion": f"criterion_{i}",
                    "weight": 5,
                    "required": True,
                    "result": "PENDING",
                }
                for i, cid in enumerate(CRITERIA, 1)
            ],
            "next_units_unlocked": ["P9"],
        }
        blocked = {"unit_id": "P9", "name": "next", "status": "BLOCKED", "dependencies": [PHASE]}
        (self.impl / "IMPLEMENTATION-REGISTRY.yaml").write_text(
            yaml.safe_dump({"units": [unit, blocked]}, sort_keys=False)
        )

    def unit(self):
        return RepositoryContextLoader(self.root).resolve_active_unit_optional()

    def scope(self, task: str):
        return resolve_task_scope(task, self.unit(), self.root)

    def audit(self, report: str, task: str):
        unit = self.unit()
        return CompletionAuditor(self.root).audit(
            report, unit=unit, scope=resolve_task_scope(task, unit, self.root)
        )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "product")


# ==========================================================================
# 1. The exact task from run 20260924-073928
# ==========================================================================


class TestTheExactRunTask:
    def test_the_fixture_is_the_run_s_task(self) -> None:
        assert EXACT_TASK.startswith("Close ONLY the concrete G4 verification gaps")
        assert "Preserve every accepted P0-P8 invariant" in EXACT_TASK
        assert "Do not mark P8 COMPLETE" in EXACT_TASK

    def test_it_is_a_bounded_remediation_inside_p8(self, repo: Repo) -> None:
        scope = repo.scope(EXACT_TASK)
        assert scope.parent_phase_id == PHASE
        assert scope.intent is ScopeIntent.SCOPED_REMEDIATION
        assert scope.intent is not ScopeIntent.PHASE_ACCEPTANCE
        assert scope.intent is not ScopeIntent.PHASE_IMPLEMENTATION
        assert scope.level is ScopeLevel.TASK
        assert scope.scope_id == f"{PHASE}/REMEDIATION"
        assert scope.claims_phase_completion is False
        assert scope.phase_completion_requested is False
        assert scope.phase_acceptance_requested is False
        assert scope.may_record_phase_acceptance is False
        assert scope.requires_phase_acceptance is False
        assert scope.is_nested

    def test_the_remediation_it_names_is_recorded_as_the_bar(self, repo: Repo) -> None:
        scope = repo.scope(EXACT_TASK)
        assert scope.remediation_targets == ["Close ONLY the concrete G4 verification gaps"]
        assert any("Do not mark P8 COMPLETE" in d for d in scope.derivation)
        rendered = scope.render()
        assert "BOUNDED REMEDIATION" in rendered
        assert "G4 verification gaps" in rendered
        assert f"does NOT accept {PHASE}" in rendered

    def test_completion_can_never_mark_p8_accepted(self, repo: Repo) -> None:
        completion = scoped_completion(
            repo.scope(EXACT_TASK), TaskResult.VERIFIED, phase_accepted=True
        )
        assert completion.parent_phase_accepted is False
        assert f"{PHASE} is accepted or COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply

    def test_not_even_when_the_registry_records_p8_complete(self, tmp_path: Path) -> None:
        repo = Repo(tmp_path / "complete", status="COMPLETE")
        audit = repo.audit("The G4 gaps are closed; every oracle passes.", EXACT_TASK)
        assert audit.completion is not None
        assert audit.completion.parent_phase_accepted is False

    def test_the_seventeen_criteria_are_not_this_run_s_bar(self, repo: Repo) -> None:
        audit = repo.audit("The G4 gaps are closed; every oracle passes.", EXACT_TASK)
        assert not any(cid in m for m in audit.missing_evidence for cid in CRITERIA)
        assert audit.decision is not AuditDecision.CONTRADICTED

    @pytest.mark.parametrize(
        ("claim", "what"),
        [
            (f"The gaps are closed, and with them {PHASE} is COMPLETE.", "phase completion claimed"),
            ("The gaps are closed. P9 is now unblocked.", "unblocked"),
        ],
    )
    def test_it_cannot_claim_the_phase_or_what_follows_it(
        self, repo: Repo, claim: str, what: str
    ) -> None:
        audit = repo.audit(claim, EXACT_TASK)
        assert audit.decision is AuditDecision.CONTRADICTED
        assert any(what in c.what for c in audit.contradictions)

    def test_it_takes_no_phase_closure_preflight(self, repo: Repo) -> None:
        # `run` preflights phase closure only for a scope that requires the
        # phase's acceptance. A bounded remediation materializes none.
        assert not repo.scope(EXACT_TASK).requires_phase_acceptance


# ==========================================================================
# 2. Acceptance: positive requests are recognized, descriptions are not
# ==========================================================================


class TestAcceptanceIsARequestNotADescription:
    @pytest.mark.parametrize(
        "phrasing",
        [
            f"Accept {PHASE}.",
            f"Close {PHASE}.",
            f"Perform {PHASE} phase acceptance.",
            f"Take {PHASE} through phase acceptance.",
            f"Adjudicate {PHASE}.",
            f"Declare {PHASE} accepted.",
        ],
    )
    def test_a_request_to_accept_is_acceptance(self, repo: Repo, phrasing: str) -> None:
        scope = repo.scope(phrasing)
        assert scope.intent is ScopeIntent.PHASE_ACCEPTANCE
        assert scope.may_record_phase_acceptance

    @pytest.mark.parametrize(
        "phrasing",
        [
            "Preserve every accepted P0-P8 invariant.",
            f"Preserve accepted {PHASE} behavior.",
            f"Do not accept {PHASE}.",
            f"This is not a {PHASE} phase-acceptance run.",
            f"Do not mark {PHASE} COMPLETE.",
            f"{PHASE} acceptance criteria remain frozen.",
            "Previously accepted U8.1-U8.6 work must remain unchanged.",
            f"Work from the frozen {PHASE} phase-acceptance run.",
            f"Read the frozen {PHASE} phase acceptance review.",
        ],
    )
    def test_a_description_is_not_acceptance(self, repo: Repo, phrasing: str) -> None:
        scope = repo.scope(phrasing)
        assert scope.intent is not ScopeIntent.PHASE_ACCEPTANCE
        assert not scope.phase_acceptance_requested
        assert not scope.may_record_phase_acceptance

    @pytest.mark.parametrize(
        "phrasing",
        [
            "Preserve every accepted P0-P8 invariant.",
            "Close out the P0-P8 audit trail.",
            "Accept P0 through P8 as history.",
        ],
    )
    def test_a_range_endpoint_is_not_the_object_of_the_verb(
        self, repo: Repo, phrasing: str
    ) -> None:
        assert repo.scope(phrasing).intent is not ScopeIntent.PHASE_ACCEPTANCE

    def test_a_denial_cannot_hide_a_later_request(self, repo: Repo) -> None:
        task = (
            f"Close the G4 verification gaps. Do not accept {PHASE} yet. "
            f"Then accept {PHASE}."
        )
        assert repo.scope(task).intent is ScopeIntent.PHASE_ACCEPTANCE


# ==========================================================================
# 3. The other intents are unchanged, and the strict fallback has no loophole
# ==========================================================================


class TestTheStrictReadingSurvives:
    @pytest.mark.parametrize(
        "phrasing", [f"Complete {PHASE}.", f"Finish {PHASE}.", f"Build {PHASE} completely."]
    )
    def test_a_whole_phase_build_is_still_implementation(
        self, repo: Repo, phrasing: str
    ) -> None:
        assert repo.scope(phrasing).intent is ScopeIntent.PHASE_IMPLEMENTATION

    def test_vague_phase_work_keeps_the_phase_fallback(self, repo: Repo) -> None:
        scope = repo.scope(f"Work on {PHASE}.")
        assert scope.intent is ScopeIntent.UNSPECIFIED
        assert scope.level is ScopeLevel.PHASE
        assert scope.claims_phase_completion is True

    @pytest.mark.parametrize(
        "phrasing",
        [
            # A denial with no bounded target bounds nothing.
            f"Work on {PHASE}. This is not a phase-acceptance run.",
            f"Improve {PHASE}. Do not mark {PHASE} COMPLETE.",
            # A bounded target with no denial is not granted a bounded scope.
            f"Close the G4 verification gaps in {PHASE}.",
            # Sequence is not refusal: acceptance "later" is still acceptance's bar.
            f"Fix the G4 gaps before {PHASE} phase acceptance.",
        ],
    )
    def test_a_half_signal_does_not_escape_the_phase_bar(
        self, repo: Repo, phrasing: str
    ) -> None:
        scope = repo.scope(phrasing)
        assert scope.intent is not ScopeIntent.SCOPED_REMEDIATION
        assert scope.claims_phase_completion is True
        assert not scope.is_nested

    def test_a_whole_phase_build_cannot_become_a_remediation(self, repo: Repo) -> None:
        task = (
            f"Build {PHASE} completely. Fix the open gaps. Do not mark {PHASE} COMPLETE; "
            "this is not a phase-acceptance run."
        )
        assert repo.scope(task).intent is ScopeIntent.PHASE_IMPLEMENTATION

    def test_an_acceptance_request_cannot_become_a_remediation(self, repo: Repo) -> None:
        task = f"Fix AC-RACE-017. Do not mark {PHASE} COMPLETE until it is fixed. Accept {PHASE}."
        assert repo.scope(task).intent is ScopeIntent.PHASE_ACCEPTANCE

    def test_a_named_unit_is_still_a_unit(self, repo: Repo) -> None:
        task = f"Fix the gaps in {PHASE} / M3. Do not mark {PHASE} COMPLETE."
        scope = repo.scope(task)
        assert scope.intent is ScopeIntent.UNIT_IMPLEMENTATION
        assert scope.scope_id == f"{PHASE}/M3"

    @pytest.mark.parametrize(
        "phrasing",
        [
            f"Fix AC-RACE-017 and nothing else. Do not accept {PHASE}.",
            f"Remediate the review's findings. This is not a {PHASE} phase-acceptance run.",
        ],
    )
    def test_a_named_repair_with_a_refusal_is_a_remediation(
        self, repo: Repo, phrasing: str
    ) -> None:
        scope = repo.scope(phrasing)
        assert scope.intent is ScopeIntent.SCOPED_REMEDIATION
        assert scope.claims_phase_completion is False
