"""Building a phase is not accepting it — and the builder cannot do the second.

Three jobs a run can be given about a phase, and they are different jobs:

    build one unit inside it        UNIT_IMPLEMENTATION
    build the whole phase           PHASE_IMPLEMENTATION
    accept / close / adjudicate it  PHASE_ACCEPTANCE

The defect pinned here: once "build the phase completely" was correctly read as
whole-phase work (and not as the nested unit ``P7/AC-1`` its criterion ids
suggested), it was then read as whole-phase ACCEPTANCE. The run was held to
every acceptance criterion already being scored PASS — while the repository
keeps them PENDING until its own phase closure, and an adjudicator outside the
build lineage, awards them. Nothing the building run could do would ever score
them, so a complete implementation could only end at MAX_ITERATIONS.

What a whole-phase build owes instead: the implementation every builder-owed
criterion describes, pointed at in the candidate tree. When that holds it is
IMPLEMENTATION VERIFIED / READY FOR PHASE CLOSURE — never "phase accepted" —
and the candidate is handed to the existing phase-closure controller, which
is what scores the criteria.

The second half of this file pins the shared independent-review vocabulary: a
phase whose only open obligation is a criterion named like
``independent_phase_review_by_a_non_builder`` is routed to the fresh
non-builder review it needs, not reported as generically UNPROVEN.

Every repository here is synthetic and built in a temporary directory, with
phase and criterion ids that are nobody's.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from neyma_product_driver.completion_auditor import (
    AuditDecision,
    CompletionAuditor,
    CriterionState,
)
from neyma_product_driver.context import RepositoryContextLoader
from neyma_product_driver.criterion_kinds import (
    CriterionKind,
    criterion_kind,
    is_independent_review_criterion,
)
from neyma_product_driver.external_verification import evidence_from_payload
from neyma_product_driver.phase_acceptance import ClosureState, CriterionEvidenceStatus
from neyma_product_driver.phase_authority import resolve_phase_authority
from neyma_product_driver.phase_closure import PhaseClosureController
from neyma_product_driver.review_cycle import (
    ReviewTrigger,
    _pending_independent_criteria,
    capture_fingerprint,
    resolve_review_requirement,
)
from neyma_product_driver.task_scope import (
    ScopeIntent,
    ScopeLevel,
    TaskResult,
    resolve_task_scope,
    scoped_completion,
)

PHASE = "P70"
NEXT = "P71"
IMPL_1, IMPL_2 = f"{PHASE}-AC-1", f"{PHASE}-AC-2"
CI_CRITERION = f"{PHASE}-AC-3"
REVIEW_CRITERION = f"{PHASE}-AC-4"
ALL = [IMPL_1, IMPL_2, CI_CRITERION, REVIEW_CRITERION]


def _criteria(results: dict[str, str] | None = None, *, review_name: str = "") -> list[dict]:
    results = results or {}
    rows = [
        (IMPL_1, "widget_refuses_a_duplicate_request", "the widget refuses a duplicate"),
        (IMPL_2, "gauge_guards_hold_under_mutation", "every gauge guard fails its mutant"),
        (CI_CRITERION, "ci_success_on_the_candidate_tree", "CI concludes SUCCESS on the tree"),
        (
            REVIEW_CRITERION,
            review_name or "independent_phase_review_by_a_non_builder",
            "a session outside the build lineage reviews the phase",
        ),
    ]
    return [
        {
            "id": cid,
            "criterion": name,
            "weight": 25,
            "required": True,
            "result": results.get(cid, "PENDING"),
            "requirement": requirement,
        }
        for cid, name, requirement in rows
    ]


class PhaseRepo:
    """One phase in progress, a later phase blocked behind it, bare criteria.

    The criteria carry no evidence field — the convention of a repository that
    lets its adjudication write what it found — so nothing points at any of
    them until the building run does.
    """

    def __init__(self, root: Path) -> None:
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
        (root / "eval" / "tests").mkdir(parents=True)
        (root / "eval" / "tests" / "test_widget.py").write_text(
            "def test_widget_refuses_duplicates():\n    assert True\n"
        )
        (root / "eval" / "tests" / "test_gauge.py").write_text(
            "def test_gauge_guard_holds():\n    assert True\n\n"
            "def test_not_implemented_refuses():\n    assert True\n"
        )
        (root / "scripts").mkdir()
        (root / "scripts" / "mutate_gauge.py").write_text("# the gauge battery\n")
        self.write_registry()
        self.commit("phase under construction")

    def write_registry(
        self,
        *,
        criteria: list[dict] | None = None,
        status: str = "READY",
        expected_checkpoints: int | None = None,
        checkpoints: int = 0,
    ) -> None:
        unit: dict[str, Any] = {
            "unit_id": PHASE,
            "name": "the widget and the gauge",
            "status": status,
            "execution_state": "IN_PROGRESS",
            "acceptance_criteria": criteria if criteria is not None else _criteria(),
            "landed_checkpoints": [
                {"id": f"{PHASE}-CP-{i + 1}", "checkpoint_state": "LANDED"}
                for i in range(checkpoints)
            ],
            "next_units_unlocked": [NEXT],
        }
        if expected_checkpoints is not None:
            unit["expected_checkpoints"] = expected_checkpoints
        blocked = {
            "unit_id": NEXT,
            "name": "what comes after",
            "status": "BLOCKED",
            "dependencies": [PHASE],
        }
        (self.impl / "IMPLEMENTATION-REGISTRY.yaml").write_text(
            yaml.safe_dump({"units": [unit, blocked]}, sort_keys=False)
        )

    def commit(self, message: str = "work") -> None:
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "commit", "-qm", message, "--allow-empty"], cwd=self.root, check=True
        )

    def registry_bytes(self) -> bytes:
        return (self.impl / "IMPLEMENTATION-REGISTRY.yaml").read_bytes()

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
def repo(tmp_path: Path) -> PhaseRepo:
    return PhaseRepo(tmp_path / "product")


BUILD_TASK = (
    f"Build {PHASE} completely according to the repository's current authoritative {PHASE} "
    f"scope and its explicit {IMPL_1} through {REVIEW_CRITERION} acceptance criteria. "
    "Implement the widget and the gauge. Do not open unrelated cleanup campaigns."
)
NESTED_TASK = f"# Build {PHASE} / W2 — the widget. Only that.\n\nDo not begin the gauge."
ACCEPT_TASK = f"Accept {PHASE}: take it through phase acceptance."

COMPLETE_BUILD_REPORT = f"""\
## What a user can now do

The widget refuses a duplicate request, and every gauge guard fails its mutant.

## Implementation, by criterion

- {IMPL_1}: IMPLEMENTED — eval/tests/test_widget.py::test_widget_refuses_duplicates
- {IMPL_2}: IMPLEMENTED — eval/tests/test_gauge.py::test_gauge_guard_holds
- {CI_CRITERION} and {REVIEW_CRITERION} are settled by phase closure; this session does not claim them.

## What proves it

41 targeted tests pass. Every result is left PENDING for phase closure to score.
"""

#: The widget is built; the gauge is not, and the report says so.
SUBSET_REPORT = f"""\
- {IMPL_1}: IMPLEMENTED — eval/tests/test_widget.py::test_widget_refuses_duplicates
- {IMPL_2}: NOT IMPLEMENTED yet — the gauge remains to be built.
"""

#: The same subset, and a report that simply never mentions the gauge.
SILENT_SUBSET_REPORT = f"""\
- {IMPL_1}: IMPLEMENTED — eval/tests/test_widget.py::test_widget_refuses_duplicates

41 targeted tests pass.
"""


# ==========================================================================
# 1. Scope: three intents, told apart
# ==========================================================================


class TestThreeIntentsAreToldApart:
    def test_a_nested_unit_stays_a_nested_unit(self, repo: PhaseRepo) -> None:
        scope = repo.scope(NESTED_TASK)
        assert scope.intent is ScopeIntent.UNIT_IMPLEMENTATION
        assert scope.level is ScopeLevel.TASK
        assert scope.is_nested
        assert scope.scope_id == f"{PHASE}/W2"
        assert not scope.phase_implementation_requested
        assert not scope.phase_acceptance_requested

    def test_building_the_whole_phase_is_implementation_not_acceptance(
        self, repo: PhaseRepo
    ) -> None:
        scope = repo.scope(BUILD_TASK)
        assert scope.level is ScopeLevel.PHASE
        assert scope.scope_id == PHASE
        assert scope.intent is ScopeIntent.PHASE_IMPLEMENTATION
        assert scope.phase_implementation_requested
        assert not scope.phase_acceptance_requested
        # The 818d386 repair stands: the criterion ids are not a nested unit.
        assert not scope.is_nested

    @pytest.mark.parametrize(
        "phrasing",
        [
            f"Build {PHASE} completely.",
            f"Finish {PHASE}.",
            f"Implement {PHASE} in full.",
            f"Build the whole of {PHASE}.",
            f"Build {PHASE}. Do not open unrelated work.",
            f"Build {PHASE} according to its acceptance criteria.",
            f"Build {PHASE} completely. Do not accept {PHASE}; phase closure is a separate step.",
        ],
    )
    def test_every_way_of_asking_for_the_work_is_implementation(
        self, repo: PhaseRepo, phrasing: str
    ) -> None:
        assert repo.scope(phrasing).intent is ScopeIntent.PHASE_IMPLEMENTATION

    @pytest.mark.parametrize(
        "phrasing",
        [
            f"Accept {PHASE}.",
            f"Close {PHASE}.",
            f"Adjudicate {PHASE}.",
            f"Sign off {PHASE}.",
            f"Take {PHASE} through phase acceptance.",
            f"Complete {PHASE} and take it to acceptance.",
            f"Build {PHASE} and take it through phase closure.",
        ],
    )
    def test_every_way_of_asking_for_acceptance_is_acceptance(
        self, repo: PhaseRepo, phrasing: str
    ) -> None:
        scope = repo.scope(phrasing)
        assert scope.intent is ScopeIntent.PHASE_ACCEPTANCE
        assert scope.phase_acceptance_requested

    def test_naming_the_acceptance_criteria_does_not_ask_for_acceptance(
        self, repo: PhaseRepo
    ) -> None:
        """The inference this design refuses: a build task references the bar it
        is built to, and that reference is not permission to award it."""
        scope = repo.scope(BUILD_TASK)
        assert "acceptance criteria" in BUILD_TASK
        assert scope.intent is not ScopeIntent.PHASE_ACCEPTANCE

    def test_the_builder_is_told_what_it_may_not_do(self, repo: PhaseRepo) -> None:
        rendered = repo.scope(BUILD_TASK).render()
        assert "IMPLEMENTATION" in rendered
        assert "does NOT accept" in rendered
        assert "does NOT score a criterion" in rendered
        assert "does NOT unblock the next phase" in rendered
        assert "account for EVERY required criterion by its id" in rendered


# ==========================================================================
# 2. A whole-phase build verifies without pre-awarded acceptance scores
# ==========================================================================


class TestAWholePhaseBuildVerifiesItsImplementation:
    def test_complete_implementation_is_verified_with_every_criterion_pending(
        self, repo: PhaseRepo
    ) -> None:
        audit = repo.audit(COMPLETE_BUILD_REPORT, BUILD_TASK)
        assert all(c["result"] == "PENDING" for c in repo.unit().acceptance_criteria)
        assert audit.decision is AuditDecision.VERIFIED, audit.summary_block()
        assert audit.missing_evidence == []
        assert "IMPLEMENTATION VERIFIED — READY FOR PHASE CLOSURE" in audit.headline

    def test_the_record_is_implementation_verified_and_nothing_more(
        self, repo: PhaseRepo
    ) -> None:
        completion = repo.audit(COMPLETE_BUILD_REPORT, BUILD_TASK).completion
        assert completion is not None
        assert completion.task_result is TaskResult.VERIFIED
        assert completion.implementation_verified
        assert completion.parent_phase_accepted is False
        assert "phase close" in completion.handoff
        assert f"{PHASE} is accepted or COMPLETE" in completion.does_not_imply
        assert "the next phase is unblocked" in completion.does_not_imply
        assert "IMPLEMENTATION VERIFIED / READY FOR PHASE CLOSURE" in completion.summary_block()

    def test_the_gate_criteria_are_left_to_phase_closure(self, repo: PhaseRepo) -> None:
        audit = repo.audit(COMPLETE_BUILD_REPORT, BUILD_TASK)
        assert audit.left_to_phase_closure == [CI_CRITERION, REVIEW_CRITERION]
        accounted = {a.criterion_id for a in audit.implementation_accounting}
        assert accounted == {IMPL_1, IMPL_2}

    def test_no_self_awarded_review_is_demanded_or_offered(self, repo: PhaseRepo) -> None:
        """The phase review is the adjudication phase closure takes. A focused
        review inside the building run is not it and cannot discharge it."""
        audit = repo.audit(COMPLETE_BUILD_REPORT, BUILD_TASK)
        assert audit.decision is not AuditDecision.REQUIRES_INDEPENDENT_REVIEW
        requirement = resolve_review_requirement(
            repo.root, repo.scope(BUILD_TASK), unit=repo.unit()
        )
        assert ReviewTrigger.PHASE_ACCEPTANCE_CRITERION not in requirement.triggers

    def test_evidence_the_repository_records_is_enough_on_its_own(
        self, repo: PhaseRepo
    ) -> None:
        rows = _criteria()
        rows[0]["evidence"] = "eval/tests/test_widget.py::test_widget_refuses_duplicates"
        rows[1]["evidence"] = "scripts/mutate_gauge.py caught 6/6, control green"
        repo.write_registry(criteria=rows)
        repo.commit("evidence recorded against the criteria")
        audit = repo.audit("The widget and the gauge are built.", BUILD_TASK)
        assert audit.decision is AuditDecision.VERIFIED, audit.missing_evidence
        assert {a.criterion_id: a.sources for a in audit.implementation_accounting} == {
            IMPL_1: ["repository record"],
            IMPL_2: ["repository record"],
        }

    def test_a_criterion_the_repository_already_scored_is_not_owed_again(
        self, repo: PhaseRepo
    ) -> None:
        repo.write_registry(criteria=_criteria({IMPL_2: "PASS"}))
        repo.commit("the gauge was scored earlier")
        audit = repo.audit(SILENT_SUBSET_REPORT, BUILD_TASK)
        assert audit.decision is AuditDecision.VERIFIED, audit.missing_evidence

    def test_nothing_is_invented_when_the_repository_states_no_criteria(
        self, repo: PhaseRepo
    ) -> None:
        repo.write_registry(criteria=[])
        repo.commit("no criteria")
        audit = repo.audit("The widget is built.", BUILD_TASK)
        assert not [m for m in audit.missing_evidence if "required for" in m]


# ==========================================================================
# 3. A build that is missing implementation cannot false-green
# ==========================================================================


class TestMissingImplementationCannotFalseGreen:
    def test_a_silent_subset_is_not_verified(self, repo: PhaseRepo) -> None:
        """Every scenario the subset owns can pass; the gauge is still unbuilt."""
        audit = repo.audit(SILENT_SUBSET_REPORT, BUILD_TASK)
        assert audit.decision is AuditDecision.UNPROVEN
        missing = " | ".join(audit.missing_evidence)
        assert IMPL_2 in missing
        assert IMPL_1 not in missing
        assert audit.completion is not None
        assert not audit.completion.implementation_verified
        assert audit.completion.handoff == ""

    def test_an_honest_not_implemented_is_outstanding(self, repo: PhaseRepo) -> None:
        audit = repo.audit(SUBSET_REPORT, BUILD_TASK)
        assert audit.decision is AuditDecision.UNPROVEN
        assert any(
            IMPL_2 in m and "not yet built" in m for m in audit.missing_evidence
        ), audit.missing_evidence

    def test_saying_not_implemented_outranks_citing_a_file(self, repo: PhaseRepo) -> None:
        report = (
            f"- {IMPL_1}: IMPLEMENTED — eval/tests/test_widget.py::test_widget_refuses_duplicates\n"
            f"- {IMPL_2}: NOT IMPLEMENTED yet — eval/tests/test_gauge.py holds a placeholder\n"
        )
        audit = repo.audit(report, BUILD_TASK)
        assert audit.decision is AuditDecision.UNPROVEN

    def test_a_pointer_at_nothing_is_not_evidence(self, repo: PhaseRepo) -> None:
        report = (
            f"- {IMPL_1}: IMPLEMENTED — eval/tests/test_widget.py::test_widget_refuses_duplicates\n"
            f"- {IMPL_2}: IMPLEMENTED — test_the_gauge_nobody_wrote\n"
        )
        audit = repo.audit(report, BUILD_TASK)
        assert audit.decision is AuditDecision.UNPROVEN
        assert any(IMPL_2 in m for m in audit.missing_evidence)

    def test_a_test_named_for_a_refusal_is_not_a_confession(self, repo: PhaseRepo) -> None:
        report = (
            f"- {IMPL_1}: IMPLEMENTED — eval/tests/test_widget.py::test_widget_refuses_duplicates\n"
            f"- {IMPL_2}: IMPLEMENTED — eval/tests/test_gauge.py::test_not_implemented_refuses\n"
        )
        assert repo.audit(report, BUILD_TASK).decision is AuditDecision.VERIFIED

    def test_a_criterion_is_not_evidenced_by_its_neighbours_line(
        self, repo: PhaseRepo
    ) -> None:
        """`AC-1` names AC-1; it does not also name AC-10, and a locator on
        AC-1's line is not AC-2's."""
        report = (
            f"- {IMPL_1}: IMPLEMENTED — eval/tests/test_widget.py::test_widget_refuses_duplicates\n"
            f"- {PHASE}-AC-10 does not exist here.\n"
        )
        audit = repo.audit(report, BUILD_TASK)
        assert any(IMPL_2 in m for m in audit.missing_evidence)

    def test_the_canonical_scope_must_have_landed(self, repo: PhaseRepo) -> None:
        repo.write_registry(expected_checkpoints=3, checkpoints=2)
        repo.commit("two of three checkpoints")
        audit = repo.audit(COMPLETE_BUILD_REPORT, BUILD_TASK)
        assert audit.decision is AuditDecision.UNPROVEN
        assert any("expects 3 landed checkpoint" in m for m in audit.missing_evidence)

    def test_the_correction_asks_for_the_work_not_for_scores(self, repo: PhaseRepo) -> None:
        correction = repo.audit(SILENT_SUBSET_REPORT, BUILD_TASK).correction_prompt
        assert "Keep building" in correction
        assert "the phase is not finished" in correction
        assert "Do NOT close the gap by scoring a criterion" in correction
        assert "honest rollback" not in correction
        assert "Settled by phase closure, not by you" in correction


# ==========================================================================
# 4. Completing the implementation cannot accept the parent phase
# ==========================================================================


class TestImplementationCannotAcceptThePhase:
    def test_the_guard_refuses_a_phase_acceptance_from_a_build(self, repo: PhaseRepo) -> None:
        scope = repo.scope(BUILD_TASK)
        completion = scoped_completion(scope, TaskResult.VERIFIED, phase_accepted=True)
        assert completion.parent_phase_accepted is False

    def test_even_a_registry_that_records_the_phase_complete_is_not_this_runs_acceptance(
        self, repo: PhaseRepo
    ) -> None:
        repo.write_registry(criteria=_criteria({c: "PASS" for c in ALL}), status="COMPLETE")
        repo.commit("somebody else accepted it")
        audit = repo.audit(COMPLETE_BUILD_REPORT, BUILD_TASK)
        assert audit.completion is not None
        assert audit.completion.parent_phase_accepted is False

    @pytest.mark.parametrize(
        ("claim", "what"),
        [
            (f"{PHASE} is COMPLETE.", "declared complete by the run that built it"),
            ("Every acceptance criterion is satisfied.", "scored by the run that built the phase"),
            (f"{NEXT} is now unblocked.", "declared unblocked by the run that built this one"),
            ("Final adjudication complete.", "adjudication claimed by the run that built"),
        ],
    )
    def test_the_building_run_may_not_claim_what_only_closure_establishes(
        self, repo: PhaseRepo, claim: str, what: str
    ) -> None:
        audit = repo.audit(COMPLETE_BUILD_REPORT + "\n" + claim + "\n", BUILD_TASK)
        assert audit.decision is AuditDecision.CONTRADICTED
        assert any(what in c.what for c in audit.contradictions), [
            c.what for c in audit.contradictions
        ]

    def test_the_audit_writes_nothing_to_the_registry(self, repo: PhaseRepo) -> None:
        before = repo.registry_bytes()
        repo.audit(COMPLETE_BUILD_REPORT, BUILD_TASK)
        assert repo.registry_bytes() == before


# ==========================================================================
# 5. "Accept / close the phase" invokes the full phase bar
# ==========================================================================


class TestAcceptanceInvokesTheFullBar:
    def test_a_complete_implementation_is_not_an_acceptance(self, repo: PhaseRepo) -> None:
        audit = repo.audit(COMPLETE_BUILD_REPORT, ACCEPT_TASK)
        assert audit.decision is AuditDecision.UNPROVEN
        missing = " | ".join(audit.missing_evidence)
        for cid in ALL:
            assert cid in missing, cid

    def test_acceptance_inherits_the_phase_review_criterion(self, repo: PhaseRepo) -> None:
        requirement = resolve_review_requirement(
            repo.root, repo.scope(ACCEPT_TASK), unit=repo.unit()
        )
        assert ReviewTrigger.PHASE_ACCEPTANCE_CRITERION in requirement.triggers

    def test_only_an_acceptance_task_may_record_the_phase_accepted(
        self, repo: PhaseRepo
    ) -> None:
        """The one route to `parent_phase_accepted`, so the guard that refuses
        it to a build is a guard and not a constant."""
        accept = scoped_completion(repo.scope(ACCEPT_TASK), TaskResult.VERIFIED, phase_accepted=True)
        build = scoped_completion(repo.scope(BUILD_TASK), TaskResult.VERIFIED, phase_accepted=True)
        nested = scoped_completion(repo.scope(NESTED_TASK), TaskResult.VERIFIED, phase_accepted=True)
        assert (accept.parent_phase_accepted, build.parent_phase_accepted) == (True, False)
        assert nested.parent_phase_accepted is False


# ==========================================================================
# 6. The hand-off lands in the existing phase-closure controller
# ==========================================================================


def _controller(repo: PhaseRepo, store: Any = None) -> PhaseClosureController:
    return PhaseClosureController(
        repo.root, store=store, phase_id=PHASE, builder_session_ids=["builder-1"]
    )


class TestTheHandoffIsTheExistingPhaseClosure:
    def test_without_the_handoff_closure_has_nothing_to_adjudicate(
        self, repo: PhaseRepo
    ) -> None:
        record = _controller(repo).preflight()
        assert record.state is ClosureState.PREFLIGHT_BLOCKED
        assert record.evidence.status_for(IMPL_1) is CriterionEvidenceStatus.MISSING

    def test_with_it_closure_proceeds_to_its_own_gates(self, repo: PhaseRepo) -> None:
        audit = repo.audit(COMPLETE_BUILD_REPORT, BUILD_TASK)
        record = _controller(repo).preflight(
            implementation_evidence=audit.implementation_evidence()
        )
        # CI is the next gate the phase's own criteria name.
        assert record.state is ClosureState.WAITING_FOR_EXTERNAL_VERIFICATION
        for cid in (IMPL_1, IMPL_2):
            assert record.evidence.status_for(cid) is CriterionEvidenceStatus.UNOBSERVED

    def test_pointers_are_never_observations(self, repo: PhaseRepo) -> None:
        audit = repo.audit(COMPLETE_BUILD_REPORT, BUILD_TASK)
        control = _controller(repo)
        control.preflight(implementation_evidence=audit.implementation_evidence())
        attached = [r for r in control.record.evidence.refs if r.criterion_id in (IMPL_1, IMPL_2)]
        assert attached
        assert not any(r.observed or r.established or r.counts for r in attached)
        assert control.record.ledger.criteria_pass == 0

    def test_a_gate_criterion_or_a_missing_file_is_never_attached(
        self, repo: PhaseRepo
    ) -> None:
        control = _controller(repo)
        control.preflight(
            implementation_evidence={
                REVIEW_CRITERION: ["eval/tests/test_widget.py::test_widget_refuses_duplicates"],
                CI_CRITERION: ["eval/tests/test_widget.py::test_widget_refuses_duplicates"],
                IMPL_1: ["eval/tests/test_nothing_here.py::test_ghost"],
            }
        )
        builder_refs = [
            r for r in control.record.evidence.refs if "pointed at by the run" in r.detail
        ]
        assert builder_refs == []

    def test_green_ci_then_waits_for_the_adjudication_not_for_the_builder(
        self, repo: PhaseRepo
    ) -> None:
        audit = repo.audit(COMPLETE_BUILD_REPORT, BUILD_TASK)
        control = _controller(repo)
        control.preflight(implementation_evidence=audit.implementation_evidence())
        accepted, why = control.record_external_evidence(
            evidence_from_payload(
                {"sha": control.record.external_requirement.expected_sha,
                 "status": "completed", "conclusion": "success"}
            )
        )
        assert accepted, why
        assert control.decide() is ClosureState.READY_FOR_ADJUDICATION
        # Implementation evidence alone never reaches the acceptance record.
        assert control.record.state is not ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_the_rest_of_the_path_is_the_adjudication_s(self, repo: PhaseRepo) -> None:
        """Closure, continued from the hand-off, reaches the acceptance record
        only through an independent adjudication that scores the criteria."""
        from phase_fixtures import supporting_review

        audit = repo.audit(COMPLETE_BUILD_REPORT, BUILD_TASK)
        control = _controller(repo)
        control.preflight(implementation_evidence=audit.implementation_evidence())
        control.record_external_evidence(
            evidence_from_payload(
                {"sha": control.record.external_requirement.expected_sha,
                 "status": "completed", "conclusion": "success"}
            )
        )
        control.ingest_review(
            supporting_review(ALL, reviewed_fingerprint=capture_fingerprint(repo.root).to_dict())
        )
        assert control.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_the_handoff_survives_into_a_resumed_closure(
        self, repo: PhaseRepo, tmp_path: Path
    ) -> None:
        """`phase close --run <the building run>` continues from its record."""
        from neyma_product_driver.evidence import EvidenceStore

        store = EvidenceStore(tmp_path / "runs", "build-run")
        audit = repo.audit(COMPLETE_BUILD_REPORT, BUILD_TASK)
        _controller(repo, store).preflight(implementation_evidence=audit.implementation_evidence())

        resumed = _controller(repo, store)
        assert resumed.load() is not None
        record = resumed.preflight()
        assert record.state is ClosureState.WAITING_FOR_EXTERNAL_VERIFICATION
        assert record.evidence.status_for(IMPL_2) is CriterionEvidenceStatus.UNOBSERVED


# ==========================================================================
# 7. The loop converges on the hand-off instead of spinning
# ==========================================================================


class _Builder:
    def __init__(self, reports: list[str]) -> None:
        self.session_id = "builder-1"
        self.reports = list(reports)
        self.prompts: list[str] = []

    async def send(self, prompt: str, timeout_s: int | None = None):
        self.prompts.append(prompt)
        text = self.reports.pop(0) if len(self.reports) > 1 else self.reports[0]

        class Turn:
            session_id = "builder-1"
            tool_uses: list[str] = []
            denied_requests: list[str] = []
            is_error = False
            error_detail = ""

        turn = Turn()
        turn.text = text  # type: ignore[attr-defined]
        return turn


class _Evaluator:
    session_id = "evaluator-1"

    async def evaluate(self, prompt: str, timeout_s: int | None = None):
        from neyma_product_driver.models import Decision, EvaluatorDecision

        return EvaluatorDecision(
            decision=Decision.ACCEPT,
            summary="the widget and the gauge behaved as specified",
            observed_behavior=["a duplicate request is refused"],
            confidence=0.9,
        )


async def _drive(repo: PhaseRepo, tmp_path: Path, reports: list[str], *, iterations: int):
    from neyma_product_driver.cli import run_control_loop
    from neyma_product_driver.config import DriverConfig
    from neyma_product_driver.context import load_founder_context
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.models import AssertionResult, RunState, ScenarioResult
    from neyma_product_driver.scenarios import Scenario

    driver_root = tmp_path / "driver"
    shutil.copytree(
        Path(__file__).resolve().parent.parent / "founder_context",
        driver_root / "founder_context",
    )
    config = DriverConfig(
        neyma_repo=repo.root, driver_root=driver_root, task=BUILD_TASK, max_iterations=iterations
    )
    assert config.runs_dir is not None
    store = EvidenceStore(config.runs_dir, "build-run")
    state = RunState(run_id=store.run_id, task=BUILD_TASK, max_iterations=iterations)

    def make_executor(_artifact_dir: Path):
        class Executor:
            service_logs: dict[str, str] = {}

            async def execute(self, scenario):
                return ScenarioResult(
                    scenario_name=scenario.name,
                    assertions=[
                        AssertionResult(kind="expect_visible", target="refused", passed=True)
                    ],
                )

        return Executor()

    builder = _Builder(reports)
    result = await run_control_loop(
        config=config,
        scenario=Scenario(name="p70-widget-and-gauge"),
        store=store,
        state=state,
        builder=builder,
        evaluator=_Evaluator(),
        make_executor=make_executor,
        emit=lambda _m: None,
        founder=load_founder_context(driver_root),
        repo_loader=RepositoryContextLoader(repo.root),
        auditor=CompletionAuditor(repo.root),
    )
    return result, store, builder


class TestTheLoopHandsOffInsteadOfSpinning:
    async def test_a_complete_build_ends_accepted_on_its_first_iteration(
        self, repo: PhaseRepo, tmp_path: Path
    ) -> None:
        from neyma_product_driver.models import RunStatus

        before = repo.registry_bytes()
        result, store, _builder = await _drive(
            repo, tmp_path, [COMPLETE_BUILD_REPORT], iterations=3
        )
        assert result.status is RunStatus.ACCEPTED
        assert len(result.state.iterations) == 1
        assert result.audit.completion.implementation_verified
        assert result.audit.completion.parent_phase_accepted is False
        # Handed to the existing controller, in this run's own record.
        assert result.phase_closure is not None
        assert result.phase_closure.record.state is ClosureState.WAITING_FOR_EXTERNAL_VERIFICATION
        persisted = json.loads((store.run_dir / "phase-closure.json").read_text())
        assert persisted["phase_id"] == PHASE
        # Nothing was scored, accepted or unblocked in the product repository.
        assert repo.registry_bytes() == before

    async def test_an_unfinished_build_is_corrected_then_hands_off(
        self, repo: PhaseRepo, tmp_path: Path
    ) -> None:
        from neyma_product_driver.models import Decision, RunStatus

        result, _store, builder = await _drive(
            repo, tmp_path, [SUBSET_REPORT, COMPLETE_BUILD_REPORT], iterations=3
        )
        assert result.status is RunStatus.ACCEPTED
        assert len(result.state.iterations) == 2
        first = result.state.iterations[0].decision
        assert first is not None and first.decision is Decision.FIX
        assert any(IMPL_2 in p for p in first.problems)
        # The correction asked for the work, never for a score.
        assert "Keep building" in builder.prompts[1]

    async def test_an_unfinished_build_never_false_greens(
        self, repo: PhaseRepo, tmp_path: Path
    ) -> None:
        from neyma_product_driver.models import RunStatus

        result, _store, _builder = await _drive(
            repo, tmp_path, [SILENT_SUBSET_REPORT], iterations=2
        )
        assert result.status is RunStatus.MAX_ITERATIONS
        assert not result.audit.completion.implementation_verified

    async def test_the_founder_is_told_it_is_ready_for_closure_not_accepted(
        self, repo: PhaseRepo, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        from neyma_product_driver.cli import _report_founder_summary
        from neyma_product_driver.config import DriverConfig

        result, store, _builder = await _drive(
            repo, tmp_path, [COMPLETE_BUILD_REPORT], iterations=2
        )
        config = DriverConfig(
            neyma_repo=repo.root, driver_root=tmp_path / "driver", task=BUILD_TASK
        )
        capsys.readouterr()
        _report_founder_summary(result, store, config)
        out = capsys.readouterr().out
        assert "IMPLEMENTATION VERIFIED — READY FOR PHASE CLOSURE" in out
        assert "READY TO SHIP" not in out
        assert f"phase close --run {store.run_id}" in out
        assert f"{PHASE} accepted by this run:      no" in out
        assert "push / merge" not in out


# ==========================================================================
# 8. One independent-review vocabulary, read by meaning
# ==========================================================================


REVIEW_NAMES = [
    "independent_review",
    "final_adjudication",
    "independent_phase_review",
    "independent_phase_review_by_a_non_builder",
    "phase_reviewed_by_a_session_outside_the_build",
    "non_builder_sign_off",
    "IndependentPhaseReview",
]
ORDINARY_NAMES = [
    "reviewer_ui_shows_independent_totals",
    "independent_tenant_isolation",
    "code_review_comments_rendered",
    "fresh_data_review_screen",
    "non_builder_edits_are_refused",
    "adjudication_evidence_is_recorded",
]


class TestOneReviewVocabulary:
    @pytest.mark.parametrize("name", REVIEW_NAMES)
    def test_semantic_review_criteria_are_recognised(self, name: str) -> None:
        assert is_independent_review_criterion("", name)
        assert criterion_kind("", name) is CriterionKind.INDEPENDENT_REVIEW

    @pytest.mark.parametrize("name", ORDINARY_NAMES)
    def test_incidental_words_do_not_make_a_review_criterion(self, name: str) -> None:
        assert not is_independent_review_criterion("", name)

    @pytest.mark.parametrize("name", REVIEW_NAMES + ORDINARY_NAMES)
    def test_all_three_consumers_agree(self, tmp_path: Path, name: str) -> None:
        repo = PhaseRepo(tmp_path / "product")
        repo.write_registry(criteria=_criteria(review_name=name))
        repo.commit("the review criterion named one way")
        expected = is_independent_review_criterion(REVIEW_CRITERION, name)

        auditor_says = CriterionState(
            criterion=name, criterion_id=REVIEW_CRITERION
        ).is_independent
        authority = resolve_phase_authority(repo.root, PHASE)
        authority_says = REVIEW_CRITERION in {
            c.criterion_id for c in authority.independent_review_criteria
        }
        review_cycle_says = name in _pending_independent_criteria(repo.unit())
        assert auditor_says == authority_says == review_cycle_says == expected

    def test_no_consumer_keeps_its_own_copy(self) -> None:
        root = Path(__file__).resolve().parents[1] / "neyma_product_driver"
        for module in ("completion_auditor.py", "review_cycle.py", "phase_authority.py"):
            source = (root / module).read_text(encoding="utf-8")
            assert "criterion_kinds" in source, module
            for stale in ('"independent_review", "final_adjudication"', '"non_builder"'):
                assert stale not in source, f"{module} still carries {stale}"

    def test_the_vocabulary_names_no_product(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "neyma_product_driver" / "criterion_kinds.py"
        ).read_text(encoding="utf-8")
        body = "\n".join(
            line for line in source.split('"""', 2)[-1].splitlines()
            if not line.strip().startswith("#")
        )
        for token in ("P7", "Neyma", "neyma", "AC-17"):
            assert token not in body


class TestOnlyTheReviewRemainsRoutesToTheReview:
    """Acceptance intent, every other obligation satisfied, one review left."""

    def _only_review_left(self, repo: PhaseRepo, review_name: str) -> None:
        results = {c: "PASS" for c in (IMPL_1, IMPL_2, CI_CRITERION)}
        repo.write_registry(criteria=_criteria(results, review_name=review_name))
        repo.commit("everything but the review")

    def test_it_routes_to_the_fresh_non_builder_review(self, repo: PhaseRepo) -> None:
        self._only_review_left(repo, "independent_phase_review_by_a_non_builder")
        audit = repo.audit("The phase's work is done; the review is not mine.", ACCEPT_TASK)
        assert audit.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW, audit.summary_block()
        assert audit.observed_state.progress.independent_pending == [
            "independent_phase_review_by_a_non_builder"
        ]
        assert any(
            REVIEW_CRITERION in m and "session that did not build it" in m
            for m in audit.missing_evidence
        )
        correction = audit.correction_prompt
        assert "independent_phase_review_by_a_non_builder" in correction
        assert "fresh session that did not build" in correction

    def test_the_review_requirement_names_it(self, repo: PhaseRepo) -> None:
        self._only_review_left(repo, "independent_phase_review_by_a_non_builder")
        requirement = resolve_review_requirement(
            repo.root, repo.scope(ACCEPT_TASK), unit=repo.unit()
        )
        assert requirement.required
        assert ReviewTrigger.PHASE_ACCEPTANCE_CRITERION in requirement.triggers
        assert any("independent_phase_review_by_a_non_builder" in r for r in requirement.reasons)

    def test_an_ordinary_criterion_left_open_is_not_a_review(self, repo: PhaseRepo) -> None:
        self._only_review_left(repo, "reviewer_ui_shows_independent_totals")
        audit = repo.audit("The phase's work is done.", ACCEPT_TASK)
        assert audit.decision is AuditDecision.UNPROVEN
        assert audit.observed_state.progress.independent_pending == []
        requirement = resolve_review_requirement(
            repo.root, repo.scope(ACCEPT_TASK), unit=repo.unit()
        )
        assert ReviewTrigger.PHASE_ACCEPTANCE_CRITERION not in requirement.triggers

    def test_the_review_and_something_else_is_not_only_the_review(
        self, repo: PhaseRepo
    ) -> None:
        """Precise routing: the scarcest session is not spent on a phase with
        other work open."""
        results = {IMPL_1: "PASS", CI_CRITERION: "PASS"}
        repo.write_registry(criteria=_criteria(results))
        repo.commit("the gauge and the review are open")
        audit = repo.audit("Not finished.", ACCEPT_TASK)
        assert audit.decision is AuditDecision.UNPROVEN
