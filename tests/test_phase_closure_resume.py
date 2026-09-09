"""(M) Resume: the attempt survives the process that started it.

Every state the controller can rest in is reached, persisted, reloaded into a
FRESH controller, and checked. What is being tested is not that a file round
trips — it is that no decision is reconstructed from prose: the frozen criteria,
the findings' classifications, which tree the evidence was about, and which
residual needs a person all come back as themselves.
"""

from __future__ import annotations

from pathlib import Path

from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.external_verification import evidence_from_payload
from neyma_product_driver.phase_acceptance import (
    ClosureState,
    CriterionEvidenceStatus,
    FindingClass,
    RepairLayer,
    ResidualStatus,
)
from neyma_product_driver.phase_closure import CLOSURE_FILE, PhaseClosureController
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


def store_for(tmp_path: Path) -> EvidenceStore:
    return EvidenceStore(tmp_path / "runs", "20260909-000000")


def fresh(repo: Path, store: EvidenceStore, **kwargs) -> PhaseClosureController:
    """A controller that knows nothing but what is on disk."""
    kwargs.setdefault("phase_id", "P9")
    control = PhaseClosureController(repo, store=store, **kwargs)
    assert control.load() is not None, "nothing was persisted to resume from"
    return control


class TestResumeAcrossThePreflight:
    def test_the_frozen_criteria_come_back_unchanged(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        first = PhaseClosureController(repo, store=store, phase_id="P9")
        first.preflight()
        frozen = first.record.criteria_fingerprint

        second = fresh(repo, store)
        assert second.record.criteria_fingerprint == frozen
        assert [c.criterion_id for c in second.record.criteria.criteria] == ALL_CRITERIA

    def test_a_criteria_change_after_the_freeze_does_not_move_the_resumed_bar(
        self, tmp_path: Path
    ) -> None:
        """The bar cannot move because a process restarted."""
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        first = PhaseClosureController(repo, store=store, phase_id="P9")
        first.preflight()
        frozen = first.record.criteria_fingerprint

        write_registry(repo, criteria=default_criteria() + [criterion("AC-6", "new_demand")])
        commit_all(repo, "widen the bar")

        second = fresh(repo, store)
        second.preflight()
        assert second.record.criteria_fingerprint == frozen
        assert len(second.record.criteria.criteria) == 5
        assert any(f.finding_id.endswith("CRITERIA-MOVED") for f in second.record.findings)

    def test_the_persisted_file_lives_at_the_run_root(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        PhaseClosureController(repo, store=store, phase_id="P9").preflight()
        assert (store.run_dir / CLOSURE_FILE).exists()

    def test_an_authority_gap_survives_the_resume(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path, with_criteria=False)
        store = store_for(tmp_path)
        PhaseClosureController(repo, store=store, phase_id="P9").preflight()
        second = fresh(repo, store)
        assert second.record.state is ClosureState.AUTHORITY_GAP
        assert second.record.findings[0].classification is FindingClass.AUTHORITY_GAP


class TestResumeWhileWaitingForExternalVerification:
    def test_the_same_acceptance_state_comes_back(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        first = PhaseClosureController(repo, store=store, phase_id="P9")
        first.preflight()
        assert first.record.state is ClosureState.WAITING_FOR_EXTERNAL_VERIFICATION

        second = fresh(repo, store)
        assert second.record.state is ClosureState.WAITING_FOR_EXTERNAL_VERIFICATION
        assert second.record.external_requirement.required
        assert second.record.external_requirement.expected_sha == head(repo)
        assert second.record.external_requirement.criterion_ids == ["AC-3"]

    def test_the_resumed_attempt_still_refuses_evidence_for_the_wrong_commit(
        self, tmp_path: Path
    ) -> None:
        """(H), after a restart. The SHA binding is not held in memory."""
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        PhaseClosureController(repo, store=store, phase_id="P9").preflight()

        second = fresh(repo, store)
        accepted, reason = second.record_external_evidence(
            evidence_from_payload({"sha": "ab" * 20, "conclusion": "success"})
        )
        assert not accepted
        assert "a different tree" in reason

    def test_the_resumed_attempt_accepts_evidence_for_the_right_commit(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        PhaseClosureController(repo, store=store, phase_id="P9").preflight()

        second = fresh(repo, store)
        accepted, _ = second.record_external_evidence(
            evidence_from_payload({"sha": head(repo), "conclusion": "success"})
        )
        assert accepted
        assert second.decide() is ClosureState.READY_FOR_ADJUDICATION

        third = fresh(repo, store)
        assert third.record.external_evidence is not None
        assert third.record.external_evidence.sha == head(repo)

    def test_the_waiting_block_says_what_to_bring_back(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        PhaseClosureController(repo, store=store, phase_id="P9").preflight()
        block = fresh(repo, store).record.external_requirement.waiting_block()
        assert head(repo) in block
        assert "sha" in block
        assert "phase external-evidence" in block


class TestResumeAcrossTheAdjudication:
    def _adjudicated(self, tmp_path: Path):
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        control = PhaseClosureController(
            repo, store=store, phase_id="P9", builder_session_ids=["builder-1"]
        )
        control.preflight()
        control.record_external_evidence(
            evidence_from_payload({"sha": head(repo), "conclusion": "success"})
        )
        control.ingest_review(
            supporting_review(
                ALL_CRITERIA, reviewed_fingerprint=capture_fingerprint(repo).to_dict()
            )
        )
        control.decide()
        return repo, store

    def test_the_adjudication_and_its_independence_proof_survive(
        self, tmp_path: Path
    ) -> None:
        repo, store = self._adjudicated(tmp_path)
        second = fresh(repo, store, builder_session_ids=["builder-1"])
        adjudication = second.record.adjudication
        assert adjudication is not None
        assert adjudication.independent
        assert adjudication.reviewer_session_id == "reviewer-1"
        assert adjudication.builder_session_ids == ["builder-1"]
        assert adjudication.criteria_fingerprint == second.record.criteria_fingerprint

    def test_the_resumed_state_is_still_ready(self, tmp_path: Path) -> None:
        repo, store = self._adjudicated(tmp_path)
        second = fresh(repo, store)
        second.preflight()
        assert second.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_the_builder_lineage_is_restored_when_the_caller_supplies_none(
        self, tmp_path: Path
    ) -> None:
        """Independence must not weaken because a resume forgot who built it."""
        repo, store = self._adjudicated(tmp_path)
        second = fresh(repo, store)
        assert second.builder_session_ids == ["builder-1"]


class TestResumeKeepsFindingClassifications:
    def _with_findings(self, tmp_path: Path):
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        control = PhaseClosureController(
            repo, store=store, phase_id="P9", builder_session_ids=["builder-1"]
        )
        control.preflight()
        control.record_external_evidence(
            evidence_from_payload({"sha": head(repo), "conclusion": "success"})
        )
        control.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
                findings=[
                    FakeFinding(
                        "a nicer guard would be useful here",
                        severity="blocker",
                        evidence_path="src/product.py",
                    ),
                    FakeFinding(
                        "product driver's own oracle read the wrong field",
                        severity="major",
                        evidence_path="runs/x/scenario.json",
                    ),
                ],
            )
        )
        control.decide()
        return repo, store

    def test_nonblocking_debt_does_not_become_blocking_after_a_resume(
        self, tmp_path: Path
    ) -> None:
        """The severity word is still 'blocker'. It still does not block."""
        repo, store = self._with_findings(tmp_path)
        second = fresh(repo, store)
        debt = [
            f for f in second.record.findings if f.classification is FindingClass.NONBLOCKING_DEBT
        ]
        assert debt and debt[0].severity == "blocker"
        assert not debt[0].blocks_phase_acceptance
        assert second.record.blocking_findings == []

    def test_the_repair_layer_survives_rather_than_being_re_derived(
        self, tmp_path: Path
    ) -> None:
        repo, store = self._with_findings(tmp_path)
        second = fresh(repo, store)
        harness = [
            f for f in second.record.findings if f.classification is FindingClass.HARNESS_DEFECT
        ]
        assert harness and harness[0].repair_layer is RepairLayer.PRODUCT_DRIVER
        assert second.routing().stop_product_run

    def test_the_state_is_unchanged_by_the_round_trip(self, tmp_path: Path) -> None:
        repo, store = self._with_findings(tmp_path)
        first_state = PhaseClosureController(repo, store=store, phase_id="P9")
        first_state.load()
        before = first_state.record.state
        second = fresh(repo, store)
        assert second.record.state is before


class TestResumeAfterTheTreeMoved:
    def test_stale_evidence_is_invalidated_on_the_resumed_preflight(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        control = PhaseClosureController(
            repo, store=store, phase_id="P9", builder_session_ids=["b"]
        )
        control.preflight()
        control.record_external_evidence(
            evidence_from_payload({"sha": head(repo), "conclusion": "success"})
        )
        control.ingest_review(
            supporting_review(
                ALL_CRITERIA, reviewed_fingerprint=capture_fingerprint(repo).to_dict()
            )
        )
        assert control.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

        (repo / "eval" / "tests" / "test_behaviour.py").write_text(
            "def test_behaviour_holds():\n    assert 1\n", encoding="utf-8"
        )
        commit_all(repo, "change the behaviour test")

        second = fresh(repo, store)
        second.preflight()
        assert second.record.evidence.status_for("AC-1") is CriterionEvidenceStatus.STALE
        assert second.record.external_evidence is None
        assert second.record.adjudication is None
        assert second.decide() is not ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_the_invalidation_is_recorded_rather_than_silent(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        first = PhaseClosureController(repo, store=store, phase_id="P9")
        first.preflight()
        (repo / "src" / "product.py").write_text("VALUE = 9\n", encoding="utf-8")
        commit_all(repo, "move the product")
        second = fresh(repo, store)
        second.preflight()
        assert any("no longer describe" in line for line in second.record.history)


class TestResumeKeepsResidualOwnership:
    def test_a_founder_decision_residual_stays_the_founders(self, tmp_path: Path) -> None:
        """(L), after a restart."""
        repo = phase_repo(
            tmp_path,
            residuals=[
                {
                    "id": "D-1",
                    "finding": "a narrowed affordance",
                    "closes_at": "the founder accepts it",
                },
                {"id": "D-2", "finding": "another", "closes_at": "M9"},
            ],
            extra_units=[{"unit_id": "M9", "name": "earlier", "status": "COMPLETE"}],
        )
        store = store_for(tmp_path)
        PhaseClosureController(repo, store=store, phase_id="P9").preflight()

        second = fresh(repo, store)
        by_id = {r.residual_id: r for r in second.record.residuals}
        assert by_id["D-1"].status is ResidualStatus.REQUIRES_FOUNDER_DECISION
        assert by_id["D-2"].status is ResidualStatus.CLOSABLE_NOW
        assert second.record.ledger.closable_residuals == ["D-2"]


class TestResumeIsPhaseScoped:
    def test_a_different_phase_starts_its_own_attempt(self, tmp_path: Path) -> None:
        """Resuming P9's attempt into P10 would carry P9's frozen criteria."""
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        PhaseClosureController(repo, store=store, phase_id="P9").preflight()

        from neyma_product_driver.cli import _phase_controller
        from neyma_product_driver.config import DriverConfig

        config = DriverConfig(neyma_repo=repo, runs_dir=store.runs_dir, task="t")
        other = _phase_controller(config, store, phase_id="P10")
        assert other.record.phase_id == "P10"
        assert other.record.criteria_fingerprint == ""

    def test_a_corrupt_record_is_not_resumed_from(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        (store.run_dir / CLOSURE_FILE).write_text("{not json", encoding="utf-8")
        control = PhaseClosureController(repo, store=store, phase_id="P9")
        assert control.load() is None
        control.preflight()
        assert control.record.criteria_fingerprint


class TestTheLedgerSurvivesAResumeWithoutAPreflight:
    """A resumed step that only decides must not report a phase that built nothing."""

    def _prepared(self, tmp_path: Path):
        repo = phase_repo(tmp_path)
        store = store_for(tmp_path)
        PhaseClosureController(repo, store=store, phase_id="P9").preflight()
        return repo, store

    def test_the_checkpoint_counts_come_from_the_persisted_snapshot(
        self, tmp_path: Path
    ) -> None:
        repo, store = self._prepared(tmp_path)
        second = fresh(repo, store)
        second.decide()
        assert second.record.ledger.checkpoints_landed == 2
        assert second.record.ledger.checkpoints_expected == 2

    def test_the_surrounding_facts_survive_too(self, tmp_path: Path) -> None:
        repo, store = self._prepared(tmp_path)
        second = fresh(repo, store)
        second.decide()
        assert second.record.ledger.next_phase == "P10"
        assert not second.record.ledger.production_enabled

    def test_supplying_ci_on_a_resumed_step_still_reports_the_scope(
        self, tmp_path: Path
    ) -> None:
        """This is the path ``phase external-evidence`` actually takes."""
        repo, store = self._prepared(tmp_path)
        second = fresh(repo, store)
        second.record_external_evidence(
            evidence_from_payload({"sha": head(repo), "conclusion": "success"})
        )
        second.decide()
        assert second.record.ledger.checkpoints_landed == 2
        assert second.record.state is ClosureState.READY_FOR_ADJUDICATION

    def test_an_already_accepted_phase_is_still_recognised_on_resume(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(
            tmp_path,
            status="COMPLETE",
            execution_state="COMPLETE",
            checkpoint_state="PHASE_ACCEPTANCE_COMPLETE",
        )
        store = store_for(tmp_path)
        PhaseClosureController(repo, store=store, phase_id="P9").preflight()
        second = fresh(repo, store)
        assert second.decide() is ClosureState.ALREADY_ACCEPTED

    def test_a_gate_criterion_is_not_reported_missing_after_a_resume(
        self, tmp_path: Path
    ) -> None:
        repo, store = self._prepared(tmp_path)
        second = fresh(repo, store)
        assert set(second.record.gate_criterion_ids) == {"AC-3", "AC-4", "AC-5"}
        assert "AC-4" not in second._unevidenced_required(exclude_gates=True)


class TestResidualsAreRereadRatherThanAccumulated:
    """The rows belong to the repository; only the marks belong to the attempt."""

    def _repo_with(self, tmp_path: Path, residuals: list[dict]) -> Path:
        return phase_repo(tmp_path, residuals=residuals)

    def test_a_residual_the_repository_closed_stops_being_carried(
        self, tmp_path: Path
    ) -> None:
        repo = self._repo_with(
            tmp_path,
            [{"id": "D-1", "finding": "a thing", "blocks_phase_acceptance": True}],
        )
        store = store_for(tmp_path)
        first = PhaseClosureController(repo, store=store, phase_id="P9")
        first.preflight()
        assert len(first.record.residuals.blocking) == 1

        write_registry(
            repo,
            criteria=default_criteria(),
            residuals=[
                {
                    "id": "D-1",
                    "finding": "a thing",
                    "blocks_phase_acceptance": True,
                    "status": "CLOSED at the phase adjudication",
                    "closure": "the probe landed",
                }
            ],
        )
        commit_all(repo, "close the residual")

        second = fresh(repo, store)
        second.preflight()
        assert second.record.residuals.blocking == []
        assert second.record.ledger.blocking_residuals == 0

    def test_a_new_residual_is_picked_up(self, tmp_path: Path) -> None:
        repo = self._repo_with(tmp_path, [{"id": "D-1", "finding": "one"}])
        store = store_for(tmp_path)
        PhaseClosureController(repo, store=store, phase_id="P9").preflight()

        write_registry(
            repo,
            criteria=default_criteria(),
            residuals=[
                {"id": "D-1", "finding": "one"},
                {"id": "D-2", "finding": "two"},
            ],
        )
        commit_all(repo, "record another residual")

        second = fresh(repo, store)
        second.preflight()
        assert {r.residual_id for r in second.record.residuals} == {"D-1", "D-2"}


class TestTheFreezeHoldsDemandsAndNotOutcomes:
    """The fingerprint excludes ``result``, so re-scoring must be picked up."""

    def test_a_criterion_scored_during_the_attempt_is_re_read(
        self, tmp_path: Path
    ) -> None:
        criteria = default_criteria()
        criteria[0]["result"] = "PENDING"
        repo = phase_repo(tmp_path, criteria=criteria)
        store = store_for(tmp_path)
        first = PhaseClosureController(repo, store=store, phase_id="P9")
        first.preflight()
        frozen = first.record.criteria_fingerprint
        assert first.record.criteria.get("AC-1").result == "PENDING"

        criteria[0]["result"] = "PASS"
        write_registry(repo, criteria=criteria)
        commit_all(repo, "score AC-1")

        second = fresh(repo, store)
        second.preflight()
        assert second.record.criteria_fingerprint == frozen
        assert second.record.criteria.get("AC-1").result == "PASS"
        assert any("re-scored AC-1" in line for line in second.record.history)

    def test_re_scoring_is_not_a_criteria_change(self, tmp_path: Path) -> None:
        criteria = default_criteria()
        criteria[0]["result"] = "PENDING"
        repo = phase_repo(tmp_path, criteria=criteria)
        store = store_for(tmp_path)
        PhaseClosureController(repo, store=store, phase_id="P9").preflight()

        criteria[0]["result"] = "PASS"
        write_registry(repo, criteria=criteria)
        commit_all(repo, "score AC-1")

        second = fresh(repo, store)
        second.preflight()
        assert not any(f.finding_id.endswith("CRITERIA-MOVED") for f in second.record.findings)
