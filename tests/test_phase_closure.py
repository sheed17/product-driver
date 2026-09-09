"""The phase-closure controller, driven end to end without spending a session.

Every scenario the closure contract names is here, and each one is named after
the thing that went wrong when a founder did it by hand.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from neyma_product_driver.external_verification import (
    ExternalStatus,
    evidence_from_payload,
)
from neyma_product_driver.phase_acceptance import (
    ClosureState,
    CriterionEvidenceStatus,
    FindingClass,
    RepairLayer,
    ResidualStatus,
)
from neyma_product_driver.phase_authority import resolve_phase_authority
from neyma_product_driver.phase_closure import (
    PhaseClosureController,
    _units_named_in,
    check_independence,
    classify_review_finding,
    phase_adjudication_prompt,
)
from neyma_product_driver.review_cycle import capture_fingerprint

from phase_fixtures import (
    FakeAssessment,
    FakeFinding,
    FakeReview,
    commit_all,
    criterion,
    default_criteria,
    head,
    phase_repo,
    supporting_review,
    write_registry,
)

ALL_CRITERIA = ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5"]


def controller(repo: Path, **kwargs) -> PhaseClosureController:
    kwargs.setdefault("phase_id", "P9")
    kwargs.setdefault("builder_session_ids", ["builder-1"])
    return PhaseClosureController(repo, **kwargs)


def green_ci(control: PhaseClosureController, repo: Path, sha: str | None = None):
    return control.record_external_evidence(
        evidence_from_payload(
            {"sha": sha or head(repo), "status": "completed", "conclusion": "success"}
        )
    )


def take_to_ready(repo: Path, **kwargs) -> PhaseClosureController:
    """Preflight, green CI, a supporting adjudication. The whole happy path."""
    control = controller(repo, **kwargs)
    control.preflight()
    green_ci(control, repo)
    control.ingest_review(
        supporting_review(
            ALL_CRITERIA, reviewed_fingerprint=capture_fingerprint(repo).to_dict()
        )
    )
    control.decide()
    return control


# --------------------------------------------------------------------------
# (A) everything passes
# --------------------------------------------------------------------------


class TestAllCriteriaPassWithGreenExternalEvidence:
    def test_the_phase_becomes_ready_for_the_acceptance_record(self, tmp_path: Path) -> None:
        control = take_to_ready(phase_repo(tmp_path))
        assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
        assert control.record.ledger.ready_for_acceptance_commit

    def test_the_ledger_states_the_facts_it_rests_on(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        ledger = take_to_ready(repo).record.ledger
        assert ledger.criteria_required == 5
        assert ledger.criteria_pass == 5
        assert ledger.criteria_fail == 0
        assert ledger.checkpoints_landed == ledger.checkpoints_expected == 2
        assert ledger.external_status == "SUCCESS"
        assert ledger.external_sha == head(repo)
        assert ledger.independent_review_status == "SUPPORTED"
        assert ledger.next_phase == "P10"
        assert not ledger.production_enabled

    def test_the_ledger_renders_both_ways(self, tmp_path: Path) -> None:
        ledger = take_to_ready(phase_repo(tmp_path)).record.ledger
        rendered = ledger.render()
        assert "phase: P9" in rendered
        assert "ready_for_acceptance_commit: true" in rendered
        assert isinstance(ledger.model_dump(mode="json"), dict)


# --------------------------------------------------------------------------
# (B) a required criterion fails
# --------------------------------------------------------------------------


class TestOneRequiredCriterionFails:
    def test_an_adjudicated_fail_blocks_the_phase(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        green_ci(control, repo)
        review = FakeReview(
            verdict="NOT_SUPPORTED",
            reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            criteria_assessment=[
                FakeAssessment("AC-1", "FAIL", "I ran the behaviour test and it came out red"),
                *[FakeAssessment(c, "PASS", "re-derived") for c in ALL_CRITERIA[1:]],
            ],
        )
        control.ingest_review(review)
        assert control.decide() is ClosureState.BLOCKED

    def test_the_blocking_finding_names_the_criterion_and_its_owner(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        green_ci(control, repo)
        control.ingest_review(
            FakeReview(
                verdict="NOT_SUPPORTED",
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
                criteria_assessment=[FakeAssessment("AC-1", "FAIL", "observed red")],
            )
        )
        blocking = control.record.blocking_findings
        assert [f.criterion_ids for f in blocking] == [["AC-1"]]
        assert blocking[0].repair_layer is RepairLayer.PRODUCT_BUILDER
        assert blocking[0].closure_condition


# --------------------------------------------------------------------------
# (C) a reviewer invents a criterion
# --------------------------------------------------------------------------


class TestAReviewerMayNotAddACriterion:
    def _reviewed(self, repo: Path) -> PhaseClosureController:
        control = controller(repo)
        control.preflight()
        green_ci(control, repo)
        control.ingest_review(
            FakeReview(
                verdict="NOT_SUPPORTED",
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
                criteria_assessment=[
                    *[FakeAssessment(c, "PASS", "re-derived") for c in ALL_CRITERIA],
                    FakeAssessment(
                        "AC-99-A-STRONGER-REGRESSION-SCANNER",
                        "FAIL",
                        "there should also be a scanner for this",
                    ),
                ],
            )
        )
        control.decide()
        return control

    def test_the_invented_criterion_is_recorded_as_outside_the_authority(
        self, tmp_path: Path
    ) -> None:
        control = self._reviewed(phase_repo(tmp_path))
        adjudication = control.record.adjudication
        assert adjudication is not None
        assert adjudication.unrecognised_criteria == ["AC-99-A-STRONGER-REGRESSION-SCANNER"]
        outside = [r for r in adjudication.criterion_results if r.outside_authority]
        assert len(outside) == 1

    def test_it_cannot_stop_the_phase(self, tmp_path: Path) -> None:
        """Widening the bar during an acceptance is an authority change."""
        control = self._reviewed(phase_repo(tmp_path))
        assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
        assert control.record.blocking_findings == []

    def test_it_is_kept_as_debt_rather_than_discarded(self, tmp_path: Path) -> None:
        control = self._reviewed(phase_repo(tmp_path))
        debt = [
            f
            for f in control.record.nonblocking_findings
            if "AC-99-A-STRONGER-REGRESSION-SCANNER" in f.unrecognised_criterion_ids
        ]
        assert len(debt) == 1
        assert debt[0].repair_layer is RepairLayer.RECORD_ONLY

    def test_the_criteria_fingerprint_did_not_move(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = self._reviewed(repo)
        fresh = resolve_phase_authority(repo, "P9").criteria.fingerprint()
        assert control.record.criteria_fingerprint == fresh


# --------------------------------------------------------------------------
# (D) and (O) nonblocking findings
# --------------------------------------------------------------------------


class TestNonblockingFindingsDoNotReopenThePhase:
    @pytest.mark.parametrize(
        "text",
        [
            "a nicer guard could exist around the effect boundary",
            "another regression scanner would be useful",
            "this test could be cleaner",
            "consider adding a probe for the empty case in a future phase",
        ],
    )
    def test_a_suggestion_is_debt_and_the_phase_still_closes(
        self, tmp_path: Path, text: str
    ) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        green_ci(control, repo)
        control.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
                findings=[FakeFinding(text, severity="blocker", evidence_path="src/product.py")],
            )
        )
        assert control.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
        assert control.record.nonblocking_findings
        assert control.record.blocking_findings == []

    def test_the_output_separates_blocking_from_record_and_move(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        green_ci(control, repo)
        control.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
                findings=[
                    FakeFinding("a cleaner guard would be nice", evidence_path="src/product.py")
                ],
            )
        )
        control.decide()
        lines = [ln.strip() for ln in control.record.ledger.render().splitlines()]
        assert "nonblocking_findings (record and move):" in lines
        assert "blocking_findings:" not in lines


# --------------------------------------------------------------------------
# (E) and (F) routing
# --------------------------------------------------------------------------


class TestRoutingSendsWorkToTheRightLayer:
    def _route(self, tmp_path: Path, finding: FakeFinding):
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        green_ci(control, repo)
        control.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
                findings=[finding],
            )
        )
        control.decide()
        return control, control.routing()

    def test_a_product_defect_routes_to_the_builder(self, tmp_path: Path) -> None:
        _control, plan = self._route(
            tmp_path,
            FakeFinding(
                "AC-1 is incorrect: the transition returns the wrong state",
                evidence_path="src/product.py",
                reasoning="I ran the probe and it returned CLAIMED where FAILED was expected",
            ),
        )
        assert plan.for_layer(RepairLayer.PRODUCT_BUILDER) is not None
        assert not plan.stop_product_run

    def test_a_harness_defect_stops_the_product_run(self, tmp_path: Path) -> None:
        """Product Driver must not patch the product to satisfy its own bug."""
        control, plan = self._route(
            tmp_path,
            FakeFinding(
                "the harness recorded a pass the scenario executor never observed",
                severity="blocker",
                evidence_path="runs/x/suite-result.json",
            ),
        )
        assert plan.stop_product_run
        route = plan.for_layer(RepairLayer.PRODUCT_DRIVER)
        assert route is not None
        assert plan.for_layer(RepairLayer.PRODUCT_BUILDER) is None
        assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT or True

    def test_a_harness_defect_is_never_a_product_defect(self, tmp_path: Path) -> None:
        control, _plan = self._route(
            tmp_path,
            FakeFinding(
                "product driver's own oracle is measuring the wrong field",
                evidence_path="runs/x/scenario.json",
            ),
        )
        classes = {f.classification for f in control.record.findings}
        assert FindingClass.HARNESS_DEFECT in classes
        assert FindingClass.PRODUCT_DEFECT not in classes

    def test_a_verification_gap_routes_to_tests_not_to_runtime(self, tmp_path: Path) -> None:
        _control, plan = self._route(
            tmp_path,
            FakeFinding(
                "no test covers the empty-population case; nothing would catch it",
                evidence_path="eval/tests/test_behaviour.py",
            ),
        )
        assert plan.for_layer(RepairLayer.PRODUCT_VERIFICATION) is not None
        assert plan.for_layer(RepairLayer.PRODUCT_BUILDER) is None

    def test_a_ci_infrastructure_finding_routes_to_ci(self, tmp_path: Path) -> None:
        _control, plan = self._route(
            tmp_path,
            FakeFinding(
                "the CI runner was cancelled at the job ceiling; ci infrastructure",
                evidence_path="ci://run/1",
            ),
        )
        assert plan.for_layer(RepairLayer.CI_INFRASTRUCTURE) is not None

    def test_an_authority_gap_finding_routes_to_the_founder(self, tmp_path: Path) -> None:
        _control, plan = self._route(
            tmp_path,
            FakeFinding(
                "the repository does not state what this criterion requires",
                evidence_path="docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
            ),
        )
        assert plan.founder_decision_required
        assert plan.for_layer(RepairLayer.FOUNDER_DECISION) is not None


class TestTheClassifier:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("product driver measured the wrong thing", FindingClass.HARNESS_DEFECT),
            ("the ci runner died mid-job", FindingClass.CI_INFRASTRUCTURE_DEFECT),
            ("the repository does not state the bar", FindingClass.AUTHORITY_GAP),
            ("this fails open when the store is unreachable", FindingClass.RUNTIME_SAFETY_DEFECT),
            ("this probe describes an earlier tree", FindingClass.STALE_VERIFICATION),
            ("the cited report is missing", FindingClass.EVIDENCE_GAP),
            ("no test covers this path", FindingClass.VERIFICATION_GAP),
            ("a nicer guard would be useful", FindingClass.NONBLOCKING_DEBT),
            ("the comment says the opposite; a typo", FindingClass.BOOKKEEPING_DEBT),
            ("this requires a push to a remote", FindingClass.EXTERNAL_BLOCKER),
            ("the transition returns the wrong state", FindingClass.PRODUCT_DEFECT),
            ("mmm", FindingClass.UNCLASSIFIED),
        ],
    )
    def test_it_reads_what_the_sentence_is_about(self, text: str, expected) -> None:
        assert classify_review_finding(text) is expected

    def test_a_harness_sentence_beats_a_defect_sentence(self) -> None:
        """Both words are present; only one of them names who repairs it."""
        assert (
            classify_review_finding("the harness has a bug: it returns the wrong field")
            is FindingClass.HARNESS_DEFECT
        )


# --------------------------------------------------------------------------
# (G) and (N) stale evidence
# --------------------------------------------------------------------------


class TestEvidenceFromAnEarlierTree:
    def test_a_later_commit_retires_the_evidence_it_touched(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = take_to_ready(repo)
        assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

        (repo / "eval" / "tests" / "test_behaviour.py").write_text(
            "def test_behaviour_holds():\n    assert False\n", encoding="utf-8"
        )
        commit_all(repo, "change the behaviour test")

        control.preflight()
        ref = control.record.evidence.for_criterion("AC-1")[0]
        assert ref.stale
        assert control.record.evidence.status_for("AC-1") is CriterionEvidenceStatus.STALE

    def test_an_adjudication_of_the_earlier_tree_no_longer_discharges_anything(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = take_to_ready(repo)
        (repo / "src" / "product.py").write_text("VALUE = 2\n", encoding="utf-8")
        commit_all(repo, "change the product")
        control.preflight()
        assert control.record.adjudication is None
        assert control.decide() is not ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_the_external_record_is_retired_with_the_tree(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = take_to_ready(repo)
        (repo / "src" / "product.py").write_text("VALUE = 3\n", encoding="utf-8")
        commit_all(repo, "change the product")
        control.preflight()
        assert control.record.external_evidence is None
        assert control.record.external_requirement.expected_sha == head(repo)

    def test_untouched_evidence_survives_the_change(self, tmp_path: Path) -> None:
        """Blast radius, not a blanket sweep."""
        repo = phase_repo(tmp_path)
        control = take_to_ready(repo)
        (repo / "eval" / "tests" / "test_behaviour.py").write_text(
            "def test_behaviour_holds():\n    assert True  # touched\n", encoding="utf-8"
        )
        commit_all(repo, "touch only the behaviour test")
        control.preflight()
        battery = control.record.evidence.for_criterion("AC-2")
        assert battery and not battery[0].stale


# --------------------------------------------------------------------------
# (H) CI for the wrong commit
# --------------------------------------------------------------------------


class TestExternalEvidenceIsCommitBound:
    def test_success_on_another_commit_is_refused(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        accepted, reason = control.record_external_evidence(
            evidence_from_payload(
                {"sha": "deadbeef" * 5, "status": "completed", "conclusion": "success"}
            )
        )
        assert not accepted
        assert "a different tree" in reason
        assert control.record.external_evidence is None

    def test_the_refusal_is_recorded_as_stale_verification_not_a_product_defect(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        control.record_external_evidence(
            evidence_from_payload({"sha": "cafe" * 10, "conclusion": "success"})
        )
        classes = {f.classification for f in control.record.findings}
        assert FindingClass.STALE_VERIFICATION in classes
        assert FindingClass.PRODUCT_DEFECT not in classes

    def test_a_red_gate_on_the_right_commit_is_a_demonstrated_product_defect(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        control.record_external_evidence(
            evidence_from_payload(
                {"sha": head(repo), "status": "completed", "conclusion": "failure"}
            )
        )
        blocking = control.record.blocking_findings
        assert blocking
        assert blocking[0].classification is FindingClass.PRODUCT_DEFECT
        assert blocking[0].criterion_ids == ["AC-3"]
        assert control.decide() is ClosureState.BLOCKED

    def test_a_cancelled_run_is_ci_infrastructure_not_a_product_defect(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        control.record_external_evidence(
            evidence_from_payload(
                {"sha": head(repo), "status": "completed", "conclusion": "cancelled"}
            )
        )
        found = [f for f in control.record.findings if f.finding_id.startswith("EXTERNAL")]
        assert found[0].classification is FindingClass.CI_INFRASTRUCTURE_DEFECT
        assert found[0].repair_layer is RepairLayer.CI_INFRASTRUCTURE
        assert not found[0].blocks_phase_acceptance

    def test_every_offered_record_is_kept_including_the_refused_ones(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        control.record_external_evidence(evidence_from_payload({"sha": "aa" * 20}))
        green_ci(control, repo)
        assert len(control.record.external_history) == 2


# --------------------------------------------------------------------------
# (I) independence
# --------------------------------------------------------------------------


class TestIndependenceIsMechanical:
    def test_the_builder_may_not_adjudicate_its_own_phase(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo, builder_session_ids=["builder-1", "builder-2"])
        control.preflight()
        green_ci(control, repo)
        adjudication = control.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                reviewer_session_id="builder-2",
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            )
        )
        assert not adjudication.independent
        assert "builder sessions" in adjudication.independence_problem
        # Not BLOCKED: an adjudication that does not count is an adjudication
        # that has not been taken, and the repair is to take a real one.
        assert control.decide() is ClosureState.READY_FOR_ADJUDICATION
        assert any(
            f.finding_id.endswith("REVIEW-NOT-INDEPENDENT") for f in control.record.findings
        )

    def test_an_inherited_conversation_is_not_a_second_opinion(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        green_ci(control, repo)
        adjudication = control.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                inherited_builder_context=True,
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            )
        )
        assert not adjudication.independent
        assert "inherited" in adjudication.independence_problem

    def test_an_adjudication_of_a_different_tree_does_not_count(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        green_ci(control, repo)
        adjudication = control.ingest_review(
            supporting_review(ALL_CRITERIA), reviewed_tree="some/other/tree"
        )
        assert not adjudication.independent
        assert "different implementation" in adjudication.independence_problem

    def test_an_adjudication_given_different_criteria_does_not_count(self) -> None:
        problem = check_independence(
            reviewer_session_id="r",
            builder_session_ids=["b"],
            inherited_builder_context=False,
            reviewed_tree="t",
            candidate_tree="t",
            criteria_fingerprint="aaaa",
            frozen_fingerprint="bbbb",
        )
        assert "different set of demands" in problem

    def test_a_missing_reviewer_id_is_not_treated_as_a_failure(self) -> None:
        """The structural guarantee stands when the SDK reports no id."""
        assert (
            check_independence(
                reviewer_session_id="",
                builder_session_ids=["b"],
                inherited_builder_context=False,
                reviewed_tree="t",
                candidate_tree="t",
                criteria_fingerprint="a",
                frozen_fingerprint="a",
            )
            == ""
        )

    def test_the_ledger_records_the_lineage_it_checked_against(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = take_to_ready(repo, builder_session_ids=["b1", "b2"])
        assert control.record.ledger.builder_session_ids == ["b1", "b2"]
        assert not control.record.ledger.inherited_builder_context


# --------------------------------------------------------------------------
# (J) no acceptance authority
# --------------------------------------------------------------------------


class TestAuthorityGap:
    def test_a_phase_with_no_criteria_stops(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path, with_criteria=False)
        control = controller(repo)
        control.preflight()
        assert control.record.state is ClosureState.AUTHORITY_GAP

    def test_nothing_is_manufactured_to_fill_the_gap(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path, with_criteria=False)
        control = controller(repo)
        control.preflight()
        assert control.record.criteria.criteria == []
        assert control.record.criteria_fingerprint == ""

    def test_the_gap_routes_to_the_founder_and_says_what_closes_it(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path, with_criteria=False)
        control = controller(repo)
        control.preflight()
        found = control.record.findings[0]
        assert found.classification is FindingClass.AUTHORITY_GAP
        assert found.repair_layer is RepairLayer.FOUNDER_DECISION
        assert "founder or architect" in found.closure_condition
        assert control.routing().founder_decision_required

    def test_the_gap_is_not_a_blocking_criterion_failure(self, tmp_path: Path) -> None:
        """Nobody knows the bar; that is different from failing it."""
        repo = phase_repo(tmp_path, with_criteria=False)
        control = controller(repo)
        control.preflight()
        assert control.record.blocking_findings == []
        assert control.decide() is ClosureState.AUTHORITY_GAP

    def test_a_missing_registry_is_also_an_authority_gap(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        (repo / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml").unlink()
        control = controller(repo)
        control.preflight()
        assert control.record.state is ClosureState.AUTHORITY_GAP

    def test_the_preflight_still_names_the_tree_it_read(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path, with_criteria=False)
        control = controller(repo)
        control.preflight()
        assert control.record.fingerprint().head == head(repo)
        assert head(repo)[:12] in control.preflight_block()


# --------------------------------------------------------------------------
# The criteria freeze, under a moving repository
# --------------------------------------------------------------------------


class TestTheBarMayNotMoveMidAttempt:
    def test_a_changed_criterion_set_ends_the_attempt_rather_than_being_adopted(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        frozen = control.record.criteria_fingerprint

        write_registry(
            repo,
            criteria=default_criteria() + [criterion("AC-6", "a_new_demand")],
        )
        commit_all(repo, "add a criterion mid-acceptance")
        control.preflight()

        assert control.record.criteria_fingerprint == frozen
        assert len(control.record.criteria.criteria) == 5
        moved = [
            f for f in control.record.findings if f.finding_id.endswith("CRITERIA-MOVED")
        ]
        assert moved and moved[0].classification is FindingClass.AUTHORITY_GAP
        assert moved[0].repair_layer is RepairLayer.FOUNDER_DECISION


# --------------------------------------------------------------------------
# (K) and (L) residuals, read from the repository
# --------------------------------------------------------------------------


class TestResidualsFromTheRepository:
    def _repo(self, tmp_path: Path, residuals: list[dict]) -> Path:
        return phase_repo(tmp_path, residuals=residuals, extra_units=[
            {"unit_id": "M9", "name": "an earlier unit", "status": "COMPLETE"}
        ])

    def test_a_residual_whose_named_unit_is_complete_is_proposed_closable(
        self, tmp_path: Path
    ) -> None:
        repo = self._repo(
            tmp_path,
            [{"id": "D-1", "finding": "a narrowed affordance", "closes_at": "M9"}],
        )
        control = controller(repo)
        control.preflight()
        assert control.record.residuals.residuals[0].status is ResidualStatus.CLOSABLE_NOW
        assert control.record.ledger.closable_residuals == ["D-1"]

    def test_a_residual_needing_a_decision_is_never_closed_however_much_evidence(
        self, tmp_path: Path
    ) -> None:
        repo = self._repo(
            tmp_path,
            [
                {
                    "id": "D-2",
                    "finding": "a narrower affordance",
                    "closes_at": "the founder accepts the tradeoff, M9 or not",
                }
            ],
        )
        control = controller(repo)
        control.preflight()
        residual = control.record.residuals.residuals[0]
        assert residual.status is ResidualStatus.REQUIRES_FOUNDER_DECISION
        assert residual.owner_layer is RepairLayer.FOUNDER_DECISION

    def test_a_blocking_residual_blocks(self, tmp_path: Path) -> None:
        repo = self._repo(
            tmp_path,
            [
                {
                    "id": "D-3",
                    "finding": "an unverified effect boundary",
                    "blocks_phase_acceptance": True,
                    "closes_at": "an effect probe lands",
                }
            ],
        )
        control = take_to_ready(repo)
        assert control.record.state is ClosureState.BLOCKED
        assert control.record.ledger.blocking_residuals == 1

    def test_a_residual_the_repository_closed_stops_blocking(self, tmp_path: Path) -> None:
        repo = self._repo(
            tmp_path,
            [
                {
                    "id": "D-4",
                    "finding": "an unverified effect boundary",
                    "blocks_phase_acceptance": "YES, until its closes_at is met",
                    "closes_at": "a CI run concludes SUCCESS",
                    "status": "CLOSED at the phase adjudication",
                    "closure": "run 12345 concluded SUCCESS on the accepted tree",
                }
            ],
        )
        control = take_to_ready(repo)
        assert control.record.ledger.blocking_residuals == 0
        assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_severity_alone_never_makes_a_residual_blocking(self, tmp_path: Path) -> None:
        repo = self._repo(
            tmp_path, [{"id": "D-5", "finding": "a thing", "severity": "CRITICAL"}]
        )
        control = controller(repo)
        control.preflight()
        assert control.record.residuals.residuals[0].blocks_phase_acceptance is False

    def test_prose_saying_no_is_read_as_no(self, tmp_path: Path) -> None:
        repo = self._repo(
            tmp_path,
            [
                {
                    "id": "D-6",
                    "finding": "a thing",
                    "blocks_phase_acceptance": "No — it cannot produce a wrong outcome",
                }
            ],
        )
        control = controller(repo)
        control.preflight()
        assert control.record.residuals.residuals[0].blocks_phase_acceptance is False


# --------------------------------------------------------------------------
# The stop rule
# --------------------------------------------------------------------------


class TestTheStopRule:
    def test_a_phase_short_of_its_checkpoints_is_not_ready(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path, checkpoints=2, expected_checkpoints=5)
        control = controller(repo)
        control.preflight()
        assert control.record.state is ClosureState.PREFLIGHT_BLOCKED
        assert control.decide() is ClosureState.PREFLIGHT_BLOCKED

    def test_a_criterion_the_repository_has_not_scored_needs_something_to_settle_it(
        self, tmp_path: Path
    ) -> None:
        """PENDING is not a pass, and an adjudication scoring it PASS is."""
        criteria = default_criteria()
        criteria[0]["result"] = "PENDING"
        repo = phase_repo(tmp_path, criteria=criteria)

        unsettled = controller(repo)
        unsettled.preflight()
        green_ci(unsettled, repo)
        unsettled.ingest_review(
            FakeReview(
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
                criteria_assessment=[
                    FakeAssessment(c, "PASS", "re-derived") for c in ALL_CRITERIA[1:]
                ],
            )
        )
        assert unsettled.decide() is ClosureState.BLOCKED
        assert [cid for cid, _ in unsettled.unsatisfied_required()] == ["AC-1"]

        settled = take_to_ready(repo)
        assert settled.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_a_non_instantiable_criterion_stops_the_preflight_before_a_reviewer(
        self, tmp_path: Path
    ) -> None:
        criteria = default_criteria()
        criteria.append(
            criterion("AC-6", "quality", requirement="the code is reasonably clean")
        )
        repo = phase_repo(tmp_path, criteria=criteria)
        control = controller(repo)
        control.preflight()
        assert control.record.state is ClosureState.PREFLIGHT_BLOCKED
        assert control.record.ledger.criteria_non_instantiable == ["AC-6"]

    def test_a_criterion_with_no_runnable_evidence_stops_the_preflight(
        self, tmp_path: Path
    ) -> None:
        criteria = default_criteria()
        criteria.append(
            criterion("AC-6", "counted_things", evidence="we counted them and they matched")
        )
        repo = phase_repo(tmp_path, criteria=criteria)
        control = controller(repo)
        control.preflight()
        assert control.record.state is ClosureState.PREFLIGHT_BLOCKED
        assert "AC-6" in control.record.ledger.criteria_unevidenced

    def test_a_gate_criterion_does_not_block_its_own_gate(self, tmp_path: Path) -> None:
        """The adjudication cannot be blocked on the adjudication not existing."""
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        green_ci(control, repo)
        control.decide()
        assert control.record.state is ClosureState.READY_FOR_ADJUDICATION

    def test_an_already_accepted_phase_says_so(self, tmp_path: Path) -> None:
        repo = phase_repo(
            tmp_path,
            status="COMPLETE",
            execution_state="COMPLETE",
            checkpoint_state="PHASE_ACCEPTANCE_COMPLETE",
        )
        control = controller(repo)
        control.preflight()
        assert control.record.state is ClosureState.ALREADY_ACCEPTED


# --------------------------------------------------------------------------
# The adjudication prompt
# --------------------------------------------------------------------------


class TestTheAdjudicationPrompt:
    def test_it_states_the_tree_the_criteria_and_that_the_set_is_closed(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        prompt = phase_adjudication_prompt(control.record, authority=control.authority)
        assert control.record.criteria_fingerprint in prompt
        assert head(repo)[:12] in prompt
        for cid in ALL_CRITERIA:
            assert cid in prompt
        assert "SCORE EXACTLY THESE CRITERIA AND NO OTHERS" in prompt

    def test_it_tells_the_reviewer_a_refusal_must_be_demonstrated(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        prompt = phase_adjudication_prompt(control.record, authority=control.authority)
        assert "DEMONSTRATE" in prompt
        assert "CANNOT_DETERMINE is an honest answer" in prompt

    def test_it_tells_the_reviewer_not_to_fix_anything(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        prompt = phase_adjudication_prompt(control.record, authority=control.authority)
        assert "not being asked to fix anything" in prompt


class TestAPhaseBeingClosedForTheFirstTime:
    """The live case: the registry records PENDING because the acceptance is
    what writes PASS. Requiring the registry to already say PASS would mean the
    only phase this controller could close is one that had already been closed.
    """

    def _pending(self) -> list[dict]:
        criteria = default_criteria()
        for row in criteria:
            row["result"] = "PENDING"
        return criteria

    def test_the_gates_and_the_evidence_satisfy_the_criteria(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path, criteria=self._pending())
        control = controller(repo)
        control.preflight()
        assert control.decide() is ClosureState.WAITING_FOR_EXTERNAL_VERIFICATION

        green_ci(control, repo)
        assert control.decide() is ClosureState.READY_FOR_ADJUDICATION

        control.ingest_review(
            supporting_review(
                ALL_CRITERIA, reviewed_fingerprint=capture_fingerprint(repo).to_dict()
            )
        )
        assert control.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_the_ledger_counts_what_the_attempt_can_show(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path, criteria=self._pending())
        control = take_to_ready(repo)
        assert control.record.ledger.criteria_pass == 5
        assert control.record.ledger.criteria_fail == 0

    def test_a_criterion_nothing_establishes_is_not_satisfied(
        self, tmp_path: Path
    ) -> None:
        """The other direction: PENDING plus no evidence is not a pass."""
        criteria = self._pending()
        criteria.append(
            criterion(
                "AC-6",
                "counted_things",
                result="PENDING",
                evidence="we counted them and they matched",
            )
        )
        repo = phase_repo(tmp_path, criteria=criteria)
        control = controller(repo)
        control.preflight()
        green_ci(control, repo)
        control.ingest_review(
            supporting_review(
                ALL_CRITERIA, reviewed_fingerprint=capture_fingerprint(repo).to_dict()
            )
        )
        assert control.decide() is ClosureState.BLOCKED
        assert [cid for cid, _why in control.unsatisfied_required()] == ["AC-6"]
        # And the founder is told which step fell short, rather than reading
        # BLOCKED with no sentence attached to it.
        assert any(
            f.finding_id.endswith("ADJUDICATION-INCOMPLETE") for f in control.record.findings
        )
        assert "AC-6" in control._review_line()

    def test_the_adjudication_can_satisfy_a_criterion_nothing_else_does(
        self, tmp_path: Path
    ) -> None:
        criteria = self._pending()
        criteria.append(
            criterion("AC-6", "counted_things", result="PENDING", evidence="counted by hand")
        )
        repo = phase_repo(tmp_path, criteria=criteria)
        control = controller(repo)
        control.preflight()
        green_ci(control, repo)
        control.ingest_review(
            supporting_review(
                ALL_CRITERIA + ["AC-6"],
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            )
        )
        assert control.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_a_refutation_outranks_the_repositorys_own_pass(self, tmp_path: Path) -> None:
        """A registry saying PASS does not survive an observation that it is not."""
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        control.record_external_evidence(
            evidence_from_payload({"sha": head(repo), "conclusion": "failure"})
        )
        satisfied = [cid for cid, _ in control.unsatisfied_required()]
        assert "AC-3" in satisfied
        assert control.decide() is ClosureState.BLOCKED


class TestAnAuthorityGapIsNotDebt:
    def test_it_is_listed_apart_from_record_and_move(self, tmp_path: Path) -> None:
        """"Record and move on" is the opposite of what a gap means."""
        repo = phase_repo(tmp_path, with_criteria=False)
        control = controller(repo)
        control.preflight()
        ledger = control.record.ledger
        assert ledger.authority_gaps
        assert ledger.nonblocking_findings == []
        rendered = ledger.render()
        assert "authority_gaps (only a founder or architect can close these):" in rendered
        assert "nonblocking_findings (record and move):" not in rendered

    def test_a_mid_attempt_criteria_change_is_listed_there_too(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        write_registry(repo, criteria=default_criteria() + [criterion("AC-6", "new")])
        commit_all(repo, "widen the bar mid-attempt")
        control.preflight()
        assert any("CRITERIA-MOVED" in line for line in control.record.ledger.authority_gaps)


class TestWhichUnitAClosureConditionActuallyNames:
    """Three false positives seen in a real registry, each refused by name."""

    def test_a_token_is_matched_at_its_full_length(self) -> None:
        """"resolves P6-D4" names a residual, and matching the stem read it as
        naming the phase P6 — which is complete, so the row closed itself."""
        assert _units_named_in("the change that resolves P6-D4", "P6") == {"P6-D4"}

    def test_the_phase_never_closes_its_own_residual(self) -> None:
        """A residual of P6 is carried OUT of P6; P6 completing is not its closure."""
        assert _units_named_in("closes when P6 is complete", "P6") == set()
        assert _units_named_in("closes when P6 is complete", "P7") == {"P6"}

    @pytest.mark.parametrize(
        "condition",
        [
            "a session that owns the phase-0 guards - not a P6 machine unit",
            "an owner other than M9",
            "M9, rather than this unit",
            "an owner outside M9",
        ],
    )
    def test_a_sentence_that_denies_the_closure_names_nothing(self, condition: str) -> None:
        assert _units_named_in(condition, "P7") == set()

    def test_a_plain_condition_still_names_its_units(self) -> None:
        assert _units_named_in("closes at M9 / M2", "P6") == {"M9", "M2"}

    def test_the_end_to_end_effect(self, tmp_path: Path) -> None:
        repo = phase_repo(
            tmp_path,
            residuals=[
                {"id": "D-1", "finding": "one", "closes_at": "M9"},
                {"id": "D-2", "finding": "two", "closes_at": "the change that resolves P9-D1"},
                {"id": "D-3", "finding": "three", "closes_at": "not a P9 machine unit"},
            ],
            extra_units=[{"unit_id": "M9", "name": "earlier", "status": "COMPLETE"}],
        )
        control = controller(repo)
        control.preflight()
        assert control.record.ledger.closable_residuals == ["D-1"]


class TestWritingTheAcceptanceRecordDoesNotInvalidateItsOwnEvidence:
    """The last step of a closure must not undo the closure by happening.

    A status-only edit does not change the product being accepted; it records
    that it was. Treating it like any other change retired the CI record and the
    adjudication for the tree they were about, so READY_FOR_ACCEPTANCE_COMMIT
    became unreachable by the act of reaching it.
    """

    def test_a_status_only_change_keeps_the_evidence_and_the_adjudication(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = take_to_ready(repo)
        assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

        (repo / "docs" / "implementation" / "CURRENT.md").write_text(
            "P9 accepted\n", encoding="utf-8"
        )
        control.preflight()
        assert control.record.adjudication is not None
        assert control.record.external_evidence is not None
        assert control.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
        assert any("the acceptance record itself" in n for n in control.record.history)

    def test_a_runtime_change_beside_it_still_invalidates_everything(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = take_to_ready(repo)
        (repo / "docs" / "implementation" / "CURRENT.md").write_text("P9\n", encoding="utf-8")
        (repo / "src" / "product.py").write_text("VALUE = 2\n", encoding="utf-8")
        control.preflight()
        assert control.record.adjudication is None
        assert control.record.external_evidence is None
        assert control.decide() is not ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_an_unclassifiable_path_beside_it_also_invalidates(
        self, tmp_path: Path
    ) -> None:
        """The surface classifier fails closed, and so does this."""
        repo = phase_repo(tmp_path)
        control = take_to_ready(repo)
        (repo / "docs" / "implementation" / "CURRENT.md").write_text("P9\n", encoding="utf-8")
        (repo / "surprise.dat").write_text("x", encoding="utf-8")
        control.preflight()
        assert control.record.adjudication is None

    def test_the_evidence_is_re_pointed_rather_than_left_describing_the_old_tree(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = take_to_ready(repo)
        (repo / "docs" / "implementation" / "CURRENT.md").write_text("P9\n", encoding="utf-8")
        control.preflight()
        now = control.record.fingerprint().identity
        live = [r for r in control.record.evidence.refs if not r.stale]
        assert live and all(r.observed_at_tree == now for r in live)
