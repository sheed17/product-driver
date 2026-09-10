"""Failure injection: prove the closure guards are load-bearing.

Every test in the files beside this one asserts that some guard holds. None of
them, on its own, shows the guard is what makes it hold — a check that has been
deleted and an assertion that was never able to fail look identical from the
green side.

So each case here does the same three things:

    1. establish the CONTROL: with the guard in place, the safe thing happens;
    2. remove exactly one guard;
    3. show the unsafe thing now happens.

A mutant that survives step 3 means the guard it removed was decoration, and the
test that appeared to cover it was covering nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neyma_product_driver import acceptance_commit, external_verification, phase_acceptance
from neyma_product_driver import phase_closure as pc
from neyma_product_driver.acceptance_commit import (
    Surface,
    plan_acceptance_commit,
    prepare_acceptance_commit,
)
from neyma_product_driver.external_verification import (
    ExternalEvidence,
    ExternalRequirement,
    ExternalStatus,
    evidence_from_payload,
)
from neyma_product_driver.phase_acceptance import (
    AcceptanceCriterion,
    ClosureState,
    CriterionEvidenceStatus,
    CriterionSet,
    EvidenceKind,
    EvidenceMap,
    EvidenceRef,
    FindingClass,
    PhaseFinding,
    Residual,
    ResidualLedger,
    ResidualStatus,
    blocks_acceptance,
)
from neyma_product_driver.phase_closure import PhaseClosureController
from neyma_product_driver.review_cycle import capture_fingerprint

from phase_fixtures import head, phase_repo, supporting_review

ALL_CRITERIA = ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5"]

SHA = "e6b1753131f33016cf7beb77206b77eaf361b89e"


def one_criterion() -> CriterionSet:
    return CriterionSet(
        phase_id="P9",
        criteria=[
            AcceptanceCriterion(criterion_id="AC-1", name="a", required=True, requirement="a test")
        ],
    )


class TestTheFalsifyingClassSetIsLoadBearing:
    """Mutant: let any class of finding falsify a criterion."""

    def _suggestion(self) -> PhaseFinding:
        return PhaseFinding(
            finding_id="F",
            classification=FindingClass.NONBLOCKING_DEBT,
            severity="blocker",
            criterion_ids=["AC-1"],
            mechanically_demonstrated=True,
        )

    def test_control_a_suggestion_does_not_block(self) -> None:
        assert not blocks_acceptance(self._suggestion(), one_criterion())[0]

    def test_mutant_widening_the_set_lets_a_suggestion_reopen_the_phase(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            phase_acceptance, "FALSIFYING_CLASSES", frozenset(FindingClass), raising=True
        )
        assert blocks_acceptance(self._suggestion(), one_criterion())[0], (
            "the mutant escaped: widening FALSIFYING_CLASSES changed nothing, so the set "
            "is not what stops a suggestion from blocking"
        )


class TestTheDemonstrationRequirementIsLoadBearing:
    """Mutant: accept an assertion as a refutation."""

    def _opinion(self) -> PhaseFinding:
        return PhaseFinding(
            finding_id="F",
            classification=FindingClass.PRODUCT_DEFECT,
            severity="blocker",
            criterion_ids=["AC-1"],
            mechanically_demonstrated=False,
        )

    def test_control_an_undemonstrated_claim_does_not_block(self) -> None:
        assert not blocks_acceptance(self._opinion(), one_criterion())[0]

    def test_mutant_marking_it_demonstrated_blocks(self) -> None:
        found = self._opinion()
        found.mechanically_demonstrated = True
        assert blocks_acceptance(found, one_criterion())[0], (
            "the mutant escaped: mechanically_demonstrated is not consulted"
        )


class TestTheFrozenSetMembershipIsLoadBearing:
    """Mutant: let a criterion nobody asked for count."""

    def test_control_an_invented_criterion_cannot_block(self) -> None:
        found = PhaseFinding(
            finding_id="F",
            classification=FindingClass.PRODUCT_DEFECT,
            criterion_ids=["AC-INVENTED"],
            mechanically_demonstrated=True,
        )
        assert not blocks_acceptance(found, one_criterion())[0]

    def test_mutant_adding_it_to_the_authority_makes_it_block(self) -> None:
        widened = one_criterion()
        widened.criteria.append(
            AcceptanceCriterion(criterion_id="AC-INVENTED", required=True, requirement="a test")
        )
        found = PhaseFinding(
            finding_id="F",
            classification=FindingClass.PRODUCT_DEFECT,
            criterion_ids=["AC-INVENTED"],
            mechanically_demonstrated=True,
        )
        assert blocks_acceptance(found, widened)[0], (
            "the mutant escaped: membership of the frozen required set is not consulted"
        )


class TestTheCommitBindingIsLoadBearing:
    """Mutant: stop comparing the commit CI ran against."""

    def _wrong_tree(self) -> ExternalEvidence:
        return ExternalEvidence(sha="0" * 40, status=ExternalStatus.SUCCESS)

    def test_control_wrong_tree_evidence_is_refused(self) -> None:
        requirement = ExternalRequirement(required=True, expected_sha=SHA)
        assert not self._wrong_tree().satisfies(requirement)[0]

    def test_mutant_making_every_commit_equal_accepts_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(external_verification, "same_commit", lambda a, b: True)
        requirement = ExternalRequirement(required=True, expected_sha=SHA)
        assert self._wrong_tree().satisfies(requirement)[0], (
            "the mutant escaped: same_commit is not what binds evidence to a tree"
        )

    def test_mutant_escapes_into_the_controller_too(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = phase_repo(tmp_path)
        control = PhaseClosureController(repo, phase_id="P9")
        control.preflight()
        accepted, _ = control.record_external_evidence(
            evidence_from_payload({"sha": "1" * 40, "conclusion": "success"})
        )
        assert not accepted

        monkeypatch.setattr(external_verification, "same_commit", lambda a, b: True)
        other = PhaseClosureController(repo, phase_id="P9")
        other.preflight()
        accepted, _ = other.record_external_evidence(
            evidence_from_payload({"sha": "1" * 40, "conclusion": "success"})
        )
        assert accepted, "the mutant escaped: the controller's gate is not SHA-bound"


class TestTheIndependenceCheckIsLoadBearing:
    """Mutant: stop checking who wrote the adjudication."""

    def _self_review(self, repo: Path) -> object:
        control = PhaseClosureController(
            repo, phase_id="P9", builder_session_ids=["builder-1"]
        )
        control.preflight()
        control.record_external_evidence(
            evidence_from_payload({"sha": head(repo), "conclusion": "success"})
        )
        return control.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                reviewer_session_id="builder-1",
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            )
        )

    def test_control_the_builder_cannot_adjudicate_its_own_phase(
        self, tmp_path: Path
    ) -> None:
        assert not self._self_review(phase_repo(tmp_path)).independent

    def test_mutant_removing_the_check_lets_the_builder_pass_itself(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pc, "check_independence", lambda **kwargs: "")
        assert self._self_review(phase_repo(tmp_path)).independent, (
            "the mutant escaped: check_independence is not what enforces independence"
        )

    def test_mutant_reaches_the_closure_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = phase_repo(tmp_path)
        control = PhaseClosureController(
            repo, phase_id="P9", builder_session_ids=["builder-1"]
        )
        control.preflight()
        control.record_external_evidence(
            evidence_from_payload({"sha": head(repo), "conclusion": "success"})
        )
        control.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                reviewer_session_id="builder-1",
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            )
        )
        assert control.decide() is not ClosureState.READY_FOR_ACCEPTANCE_COMMIT

        monkeypatch.setattr(pc, "check_independence", lambda **kwargs: "")
        mutant = PhaseClosureController(
            repo, phase_id="P9", builder_session_ids=["builder-1"]
        )
        mutant.preflight()
        mutant.record_external_evidence(
            evidence_from_payload({"sha": head(repo), "conclusion": "success"})
        )
        mutant.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                reviewer_session_id="builder-1",
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            )
        )
        assert mutant.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT, (
            "the mutant escaped: a self-review does not actually reach the closure state"
        )


class TestTheFalsifiabilityRuleIsLoadBearing:
    """Mutant: let a citation count as a measurement."""

    def _citation_only(self) -> EvidenceMap:
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
        return evidence

    def test_control_a_citation_does_not_establish_a_criterion(self) -> None:
        assert self._citation_only().status_for("AC-1") is CriterionEvidenceStatus.VACUOUS

    def test_mutant_making_citations_falsifiable_establishes_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            phase_acceptance,
            "_FALSIFIABLE_KINDS",
            frozenset(EvidenceKind),
            raising=True,
        )
        assert self._citation_only().status_for("AC-1") is CriterionEvidenceStatus.ESTABLISHED, (
            "the mutant escaped: _FALSIFIABLE_KINDS is not what keeps a string from "
            "establishing a criterion"
        )

    def test_control_a_vacuous_battery_does_not_establish_a_criterion(self) -> None:
        evidence = EvidenceMap()
        evidence.add(
            EvidenceRef(
                criterion_id="AC-1",
                kind=EvidenceKind.MUTATION_BATTERY,
                locator="scripts/mutate.py",
                observed=True,
                established=True,
                anti_vacuity=phase_acceptance.AntiVacuity(
                    control_green=True,
                    mutant_expected_red=10,
                    mutant_observed_red=3,
                    escaped_mutants=7,
                    population_denominator=10,
                ),
            )
        )
        assert evidence.status_for("AC-1") is CriterionEvidenceStatus.VACUOUS

    def test_mutant_removing_the_anti_vacuity_numbers_establishes_it(self) -> None:
        evidence = EvidenceMap()
        evidence.add(
            EvidenceRef(
                criterion_id="AC-1",
                kind=EvidenceKind.MUTATION_BATTERY,
                locator="scripts/mutate.py",
                observed=True,
                established=True,
                anti_vacuity=None,
            )
        )
        assert evidence.status_for("AC-1") is CriterionEvidenceStatus.ESTABLISHED, (
            "the mutant escaped: anti-vacuity metadata changes nothing"
        )


class TestTheJudgementGuardIsLoadBearing:
    """Mutant: let a founder-decision residual be closed mechanically."""

    def _ledger(self) -> ResidualLedger:
        ledger = ResidualLedger()
        ledger.add(
            Residual(
                residual_id="D-1",
                closure_condition="the founder accepts the residual risk",
            )
        )
        return ledger

    def test_control_a_founder_residual_is_never_marked_closable(self) -> None:
        ledger = self._ledger()
        assert not ledger.mark_closable("D-1", "everything is green")
        assert ledger.residuals[0].status is ResidualStatus.REQUIRES_FOUNDER_DECISION

    def test_mutant_blinding_the_guard_closes_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import re

        monkeypatch.setattr(
            phase_acceptance, "_JUDGEMENT_CLOSURE", re.compile(r"(?!x)x"), raising=True
        )
        ledger = self._ledger()
        assert ledger.mark_closable("D-1", "everything is green"), (
            "the mutant escaped: _JUDGEMENT_CLOSURE is not what keeps a decision "
            "from being automated"
        )
        assert ledger.residuals[0].status is ResidualStatus.CLOSABLE_NOW


class TestTheWritableSurfaceSetIsLoadBearing:
    """Mutant: let the acceptance commit contain a runtime edit."""

    def _dirty(self, tmp_path: Path) -> Path:
        repo = phase_repo(tmp_path)
        (repo / "docs" / "implementation" / "CURRENT.md").write_text("P9\n", encoding="utf-8")
        (repo / "src" / "product.py").write_text("VALUE = 2\n", encoding="utf-8")
        return repo

    def test_control_a_runtime_edit_refuses_the_commit(self, tmp_path: Path) -> None:
        plan = plan_acceptance_commit(self._dirty(tmp_path), phase_id="P9")
        assert not plan.permitted

    def test_mutant_widening_the_writable_set_commits_runtime_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            acceptance_commit, "WRITABLE_SURFACES", frozenset(Surface), raising=True
        )
        repo = self._dirty(tmp_path)
        plan = prepare_acceptance_commit(
            repo, plan_acceptance_commit(repo, phase_id="P9"), allow_commit=True
        )
        assert plan.committed_sha, (
            "the mutant escaped: WRITABLE_SURFACES is not what keeps runtime code out "
            "of an acceptance commit"
        )


class TestTheFreezeExcludesOutcomes:
    """Mutant: fingerprint the result as well, and the freeze self-destructs."""

    def test_control_scoring_does_not_move_the_fingerprint(self) -> None:
        before = one_criterion()
        after = before.model_copy(deep=True)
        after.criteria[0].result = "PASS"
        assert before.fingerprint() == after.fingerprint()

    def test_mutant_including_the_result_breaks_the_freeze_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def with_result(self):  # type: ignore[no-untyped-def]
            return (
                self.criterion_id,
                self.name,
                bool(self.required),
                float(self.weight),
                " ".join((self.requirement or "").split()),
                self.result,
            )

        monkeypatch.setattr(AcceptanceCriterion, "identity", with_result, raising=True)
        before = one_criterion()
        after = before.model_copy(deep=True)
        after.criteria[0].result = "PASS"
        assert before.fingerprint() != after.fingerprint(), (
            "the mutant escaped: identity() is not what keeps scoring out of the freeze"
        )
