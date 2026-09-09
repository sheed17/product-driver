"""The phase-acceptance vocabulary: freezing, blocking, evidence, residuals.

These are the rules that must not be re-derived at a call site, so they are
tested here rather than through the controller. Each test names the failure it
prevents, because every one of them is a way phase closure went wrong by hand.
"""

from __future__ import annotations

import pytest

from neyma_product_driver.phase_acceptance import (
    AcceptanceCriterion,
    AntiVacuity,
    CriterionEvidenceStatus,
    CriterionSet,
    EvidenceKind,
    EvidenceMap,
    EvidenceRef,
    FindingClass,
    PhaseFinding,
    RepairLayer,
    Residual,
    ResidualLedger,
    ResidualStatus,
    blocks_acceptance,
    classify_and_route,
    normalize_repo_path,
    repair_layer_for,
)


def criteria(*ids: str, required: bool = True) -> CriterionSet:
    return CriterionSet(
        phase_id="P9",
        criteria=[
            AcceptanceCriterion(
                criterion_id=cid,
                name=cid.lower(),
                required=required,
                requirement=f"the {cid} test passes",
            )
            for cid in ids
        ],
    )


class TestTheFreezeIsOverDemandsNotOutcomes:
    def test_scoring_a_criterion_does_not_break_the_freeze(self) -> None:
        """An adjudication is FOR scoring criteria.

        If scoring changed the fingerprint, the freeze would be broken by its
        own success and every adjudication would be compared against a set that
        no longer existed.
        """
        before = criteria("AC-1", "AC-2")
        after = before.model_copy(deep=True)
        after.criteria[0].result = "PASS"
        assert before.fingerprint() == after.fingerprint()

    def test_reordering_the_rows_is_not_a_criteria_change(self) -> None:
        before = criteria("AC-1", "AC-2")
        after = before.model_copy(deep=True)
        after.criteria.reverse()
        assert before.fingerprint() == after.fingerprint()

    def test_adding_a_criterion_moves_the_fingerprint(self) -> None:
        assert criteria("AC-1").fingerprint() != criteria("AC-1", "AC-2").fingerprint()

    def test_changing_what_a_criterion_demands_moves_the_fingerprint(self) -> None:
        before = criteria("AC-1")
        after = before.model_copy(deep=True)
        after.criteria[0].requirement = "something else entirely"
        assert before.fingerprint() != after.fingerprint()

    def test_making_an_optional_criterion_required_moves_the_fingerprint(self) -> None:
        assert criteria("AC-1").fingerprint() != criteria("AC-1", required=False).fingerprint()


class TestInstantiability:
    def test_a_criterion_naming_a_mechanism_is_addressable(self) -> None:
        c = AcceptanceCriterion(
            criterion_id="AC-1", requirement="the mutation battery catches every mutant"
        )
        assert c.machine_addressable
        assert c.ambiguity == ""

    def test_a_criterion_written_in_taste_is_not(self) -> None:
        c = AcceptanceCriterion(
            criterion_id="AC-9", requirement="the code is reasonably clean and maintainable"
        )
        assert not c.machine_addressable
        assert "only a person can settle" in c.ambiguity

    def test_a_criterion_that_demands_nothing_is_not(self) -> None:
        c = AcceptanceCriterion(criterion_id="AC-0")
        assert not c.machine_addressable
        assert "states no requirement" in c.ambiguity


class TestBlockingIsNotSeverity:
    """(B), (C) and (O) from the closure contract, at the level they are decided."""

    def test_a_demonstrated_defect_against_a_required_criterion_blocks(self) -> None:
        found = PhaseFinding(
            finding_id="F1",
            classification=FindingClass.PRODUCT_DEFECT,
            severity="minor",
            criterion_ids=["AC-1"],
            mechanically_demonstrated=True,
        )
        blocking, reason = blocks_acceptance(found, criteria("AC-1"))
        assert blocking
        assert "AC-1" in reason

    def test_a_blocker_severity_opinion_naming_no_criterion_does_not_block(self) -> None:
        """The anti-infinite-review rule, stated as a test.

        A reviewer wanting a nicer guard is a good idea, and a good idea is not
        a reason a phase that meets its stated bar has not met it.
        """
        found = PhaseFinding(
            finding_id="F2",
            classification=FindingClass.NONBLOCKING_DEBT,
            severity="blocker",
            summary="a stronger regression scanner would be useful here",
        )
        blocking, reason = blocks_acceptance(found, criteria("AC-1"))
        assert not blocking
        assert "names no criterion" in reason

    def test_a_criterion_the_authority_does_not_contain_cannot_block(self) -> None:
        found = PhaseFinding(
            finding_id="F3",
            classification=FindingClass.PRODUCT_DEFECT,
            severity="blocker",
            criterion_ids=["AC-INVENTED"],
            mechanically_demonstrated=True,
        )
        blocking, reason = blocks_acceptance(found, criteria("AC-1"))
        assert not blocking
        assert "not in the frozen required set" in reason

    def test_an_assertion_without_a_demonstration_does_not_block(self) -> None:
        found = PhaseFinding(
            finding_id="F4",
            classification=FindingClass.PRODUCT_DEFECT,
            severity="blocker",
            criterion_ids=["AC-1"],
            mechanically_demonstrated=False,
        )
        blocking, reason = blocks_acceptance(found, criteria("AC-1"))
        assert not blocking
        assert "an argument is not a refutation" in reason

    def test_an_optional_criterion_is_not_a_required_one(self) -> None:
        found = PhaseFinding(
            finding_id="F5",
            classification=FindingClass.PRODUCT_DEFECT,
            criterion_ids=["AC-1"],
            mechanically_demonstrated=True,
        )
        blocking, _ = blocks_acceptance(found, criteria("AC-1", required=False))
        assert not blocking

    def test_a_verification_gap_cannot_falsify_a_criterion(self) -> None:
        found = PhaseFinding(
            finding_id="F6",
            classification=FindingClass.VERIFICATION_GAP,
            severity="blocker",
            criterion_ids=["AC-1"],
            mechanically_demonstrated=True,
        )
        blocking, reason = blocks_acceptance(found, criteria("AC-1"))
        assert not blocking
        assert "PRODUCT_VERIFICATION" in reason

    def test_a_runtime_safety_defect_blocks(self) -> None:
        found = PhaseFinding(
            finding_id="F7",
            classification=FindingClass.RUNTIME_SAFETY_DEFECT,
            severity="minor",
            criterion_ids=["AC-1"],
            mechanically_demonstrated=True,
        )
        assert blocks_acceptance(found, criteria("AC-1"))[0]


class TestStaleVerificationIsTheAuthoritysCall:
    def _finding(self) -> PhaseFinding:
        return PhaseFinding(
            finding_id="F8",
            classification=FindingClass.STALE_VERIFICATION,
            severity="blocker",
            criterion_ids=["AC-1"],
            mechanically_demonstrated=True,
        )

    def test_by_default_a_stale_probe_is_debt(self) -> None:
        blocking, reason = blocks_acceptance(self._finding(), criteria("AC-1"))
        assert not blocking
        assert "cannot false-green" in reason

    def test_an_authority_that_says_it_blocks_makes_it_block(self) -> None:
        blocking, _ = blocks_acceptance(
            self._finding(), criteria("AC-1"), stale_verification_blocks=True
        )
        assert blocking

    def test_even_then_it_must_name_a_required_criterion(self) -> None:
        found = self._finding()
        found.criterion_ids = []
        blocking, _ = blocks_acceptance(
            found, criteria("AC-1"), stale_verification_blocks=True
        )
        assert not blocking


class TestRouting:
    @pytest.mark.parametrize(
        "classification,layer",
        [
            (FindingClass.PRODUCT_DEFECT, RepairLayer.PRODUCT_BUILDER),
            (FindingClass.RUNTIME_SAFETY_DEFECT, RepairLayer.PRODUCT_BUILDER),
            (FindingClass.VERIFICATION_GAP, RepairLayer.PRODUCT_VERIFICATION),
            (FindingClass.EVIDENCE_GAP, RepairLayer.PRODUCT_VERIFICATION),
            (FindingClass.HARNESS_DEFECT, RepairLayer.PRODUCT_DRIVER),
            (FindingClass.CI_INFRASTRUCTURE_DEFECT, RepairLayer.CI_INFRASTRUCTURE),
            (FindingClass.AUTHORITY_GAP, RepairLayer.FOUNDER_DECISION),
            (FindingClass.NONBLOCKING_DEBT, RepairLayer.RECORD_ONLY),
            (FindingClass.BOOKKEEPING_DEBT, RepairLayer.RECORD_ONLY),
            (FindingClass.EXTERNAL_BLOCKER, RepairLayer.EXTERNAL),
            (FindingClass.UNCLASSIFIED, RepairLayer.RECORD_ONLY),
        ],
    )
    def test_every_class_has_exactly_one_owner(self, classification, layer) -> None:
        assert repair_layer_for(classification) is layer

    def test_a_harness_defect_never_routes_to_the_product(self) -> None:
        """Patching the product to satisfy a broken measurement fixes nothing."""
        assert repair_layer_for(FindingClass.HARNESS_DEFECT) is not RepairLayer.PRODUCT_BUILDER

    def test_classify_moves_an_unrecognised_criterion_out_of_the_blocking_list(self) -> None:
        found = PhaseFinding(
            finding_id="F9",
            classification=FindingClass.PRODUCT_DEFECT,
            criterion_ids=["AC-1", "AC-REVIEWER-INVENTED"],
            mechanically_demonstrated=True,
        )
        classify_and_route(found, criteria("AC-1"))
        assert found.criterion_ids == ["AC-1"]
        assert found.unrecognised_criterion_ids == ["AC-REVIEWER-INVENTED"]
        assert found.blocks_phase_acceptance

    def test_every_classified_finding_gets_a_closure_condition(self) -> None:
        for classification in FindingClass:
            found = PhaseFinding(finding_id="F", classification=classification)
            classify_and_route(found, criteria("AC-1"))
            assert found.closure_condition


class TestEvidenceMustBeAbleToBeWrong:
    def test_a_citation_alone_does_not_establish_a_criterion(self) -> None:
        """(the anti-vacuity rule) A string existing is not a measurement."""
        evidence = EvidenceMap()
        evidence.add(
            EvidenceRef(
                criterion_id="AC-1",
                kind=EvidenceKind.AUTHORITY_CITATION,
                locator="docs/we-say-so.md",
                observed=True,
                established=True,
            )
        )
        assert evidence.status_for("AC-1") is CriterionEvidenceStatus.VACUOUS

    def test_a_passing_test_node_establishes_it(self) -> None:
        evidence = EvidenceMap()
        evidence.add(
            EvidenceRef(
                criterion_id="AC-1",
                kind=EvidenceKind.TEST_NODE,
                locator="tests/test_x.py::test_y",
                observed=True,
                established=True,
            )
        )
        assert evidence.status_for("AC-1") is CriterionEvidenceStatus.ESTABLISHED

    def test_a_vacuous_battery_establishes_nothing(self) -> None:
        evidence = EvidenceMap()
        evidence.add(
            EvidenceRef(
                criterion_id="AC-1",
                kind=EvidenceKind.MUTATION_BATTERY,
                locator="scripts/mutate.py",
                observed=True,
                established=True,
                anti_vacuity=AntiVacuity(
                    control_green=True,
                    mutant_expected_red=0,
                    mutant_observed_red=0,
                    population_denominator=0,
                ),
            )
        )
        assert evidence.status_for("AC-1") is CriterionEvidenceStatus.VACUOUS

    def test_a_battery_with_escapes_establishes_nothing(self) -> None:
        vac = AntiVacuity(
            control_green=True,
            mutant_expected_red=10,
            mutant_observed_red=8,
            escaped_mutants=2,
            population_denominator=10,
        )
        assert vac.vacuous

    def test_a_battery_whose_control_was_not_green_establishes_nothing(self) -> None:
        vac = AntiVacuity(
            control_green=False,
            mutant_expected_red=10,
            mutant_observed_red=10,
            population_denominator=10,
        )
        assert vac.vacuous

    def test_nothing_at_all_is_missing_not_vacuous(self) -> None:
        assert EvidenceMap().status_for("AC-1") is CriterionEvidenceStatus.MISSING

    def test_falsifiable_evidence_observed_not_to_hold_is_refuted(self) -> None:
        evidence = EvidenceMap()
        evidence.add(
            EvidenceRef(
                criterion_id="AC-1",
                kind=EvidenceKind.CI_JOB,
                locator="run-1",
                observed=True,
                established=False,
            )
        )
        assert evidence.status_for("AC-1") is CriterionEvidenceStatus.REFUTED


class TestBlastRadius:
    """(N) A moved tree invalidates evidence — but only what it actually touched."""

    def _map(self) -> EvidenceMap:
        evidence = EvidenceMap()
        evidence.add(
            EvidenceRef(
                criterion_id="AC-1",
                kind=EvidenceKind.TEST_NODE,
                locator="tests/test_a.py::t",
                observed=True,
                established=True,
                observed_at_tree="old",
                depends_on_paths=["src/a.py"],
            )
        )
        evidence.add(
            EvidenceRef(
                criterion_id="AC-2",
                kind=EvidenceKind.TEST_NODE,
                locator="tests/test_b.py::t",
                observed=True,
                established=True,
                observed_at_tree="old",
                depends_on_paths=["src/b.py"],
            )
        )
        evidence.add(
            EvidenceRef(
                criterion_id="AC-3",
                kind=EvidenceKind.TEST_NODE,
                locator="tests/test_c.py::t",
                observed=True,
                established=True,
                observed_at_tree="old",
            )
        )
        return evidence

    def test_evidence_whose_declared_dependency_changed_is_retired(self) -> None:
        evidence = self._map()
        retired = evidence.invalidate_for_tree("new", ["src/a.py"])
        assert {r.criterion_id for r in retired} == {"AC-1", "AC-3"}
        assert evidence.status_for("AC-2") is CriterionEvidenceStatus.ESTABLISHED

    def test_evidence_that_declares_no_dependency_is_always_retired(self) -> None:
        """Nothing can vouch for it, so nothing does."""
        evidence = self._map()
        retired = evidence.invalidate_for_tree("new", ["src/unrelated.py"])
        assert [r.criterion_id for r in retired] == ["AC-3"]

    def test_a_retired_ref_is_stale_not_deleted(self) -> None:
        evidence = self._map()
        evidence.invalidate_for_tree("new", ["src/a.py"])
        ref = evidence.for_criterion("AC-1")[0]
        assert ref.stale and ref.superseded_by == "new"
        assert evidence.status_for("AC-1") is CriterionEvidenceStatus.STALE

    def test_evidence_already_about_the_current_tree_survives(self) -> None:
        evidence = self._map()
        evidence.refs[0].observed_at_tree = "new"
        retired = evidence.invalidate_for_tree("new", ["src/a.py"])
        assert "AC-1" not in {r.criterion_id for r in retired}


class TestResidualClosure:
    """(K) and (L): mechanical closure is proposed; judgement is never automated."""

    def test_a_mechanically_closable_residual_is_proposed_not_closed(self) -> None:
        ledger = ResidualLedger()
        ledger.add(
            Residual(residual_id="D-1", closure_condition="the M9 machine is complete")
        )
        assert ledger.mark_closable("D-1", "M9 is recorded complete")
        assert ledger.residuals[0].status is ResidualStatus.CLOSABLE_NOW
        assert ledger.residuals[0].status is not ResidualStatus.CLOSED

    def test_a_residual_needing_a_decision_is_never_marked_closable(self) -> None:
        ledger = ResidualLedger()
        ledger.add(
            Residual(
                residual_id="D-2",
                closure_condition="the founder accepts the risk of the narrower affordance",
            )
        )
        assert not ledger.mark_closable("D-2", "everything looks fine")
        assert ledger.residuals[0].status is ResidualStatus.REQUIRES_FOUNDER_DECISION

    @pytest.mark.parametrize(
        "condition",
        [
            "a founder decision",
            "the architect signs off",
            "an owner decides whether to keep it",
            "product decision required",
            "we approve the tradeoff",
        ],
    )
    def test_judgement_language_is_recognised(self, condition: str) -> None:
        residual = Residual(residual_id="D", closure_condition=condition)
        assert residual.needs_judgement

    def test_a_closed_residual_is_neither_blocking_nor_outstanding(self) -> None:
        ledger = ResidualLedger()
        ledger.add(
            Residual(
                residual_id="D-3",
                blocks_phase_acceptance=True,
                status=ResidualStatus.CLOSED,
            )
        )
        assert ledger.blocking == []
        assert ledger.nonblocking == []

    def test_an_unknown_residual_id_marks_nothing(self) -> None:
        ledger = ResidualLedger()
        ledger.add(Residual(residual_id="D-4"))
        assert not ledger.mark_closable("D-NOT-THERE", "evidence")
        assert ledger.residuals[0].status is ResidualStatus.OPEN


class TestPathNormalisation:
    def test_a_leading_dot_directory_survives(self) -> None:
        """``lstrip("./")`` strips a character SET, which ate ``.github/``."""
        assert normalize_repo_path(".github/workflows/ci.yml") == ".github/workflows/ci.yml"

    def test_a_relative_prefix_is_removed(self) -> None:
        assert normalize_repo_path("./src/a.py") == "src/a.py"
        assert normalize_repo_path(".//src/a.py") == "src/a.py"
