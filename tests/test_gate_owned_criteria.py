"""A criterion a structural gate owns is settled by that gate, and by nothing else.

THE DEFECT THIS FILE EXISTS FOR, from the real P7 phase closure. A whole-phase
adjudication ran and came back everything a phase could ask for: a fresh session
outside the builder lineage, inheriting no builder conversation, reading the
exact candidate tree, given the exact frozen criteria, answering the response
contract, scoring sixteen of seventeen criteria PASS with no blocking product
finding. The seventeenth criterion was *independent phase review by a non
builder* — the criterion that adjudication's own existence satisfies — and the
reviewer, honestly, scored it CANNOT_DETERMINE, because whether the harness had
constructed it independently is not a fact any session can observe about itself.

Product Driver read that self-score as the answer. The criterion came out
observed-and-not-established, which reads as REFUTED; the phase persisted
BLOCKED; and the founder was handed ``ADJUDICATION-INCOMPLETE — the adjudication
could not determine AC-17`` over a review that had demonstrably happened. The
machine was asking the reviewer to certify the one thing the machine itself is
the only witness to, and a truthful answer refused the phase.

The rule these tests pin, stated once and without a product, a phase or a
criterion id in it:

    a criterion whose identifiers classify it as an INDEPENDENT REVIEW, an
    EXTERNAL VERIFICATION or a RESIDUAL LEDGER statement is settled by that
    gate. The gate's facts are mechanical. A reviewer may inspect such a
    criterion and say whatever it likes about it — recorded, reported, never
    rewritten — and its score is not the authority over the gate that owns it.

Two things that must NOT follow from it, and both are tested here as hard as the
rule itself:

* the independence checks are not weakened. A reviewer that is the builder, that
  inherited the builder's conversation, that read a different tree, that was
  given different demands, or that never answered the contract leaves the gate
  UNSATISFIED, exactly as before. Every one of those lives in
  :func:`check_independence` and :func:`check_adjudication_protocol`, and the
  gate is their conjunction, not a replacement for them.
* a mechanically valid review does not pass the product. The reviewer still owns
  every ordinary criterion, and its PASS, FAIL and CANNOT_DETERMINE on those
  decide them exactly as they did.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neyma_product_driver.criterion_kinds import CriterionKind, gate_kind
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.external_verification import evidence_from_payload
from neyma_product_driver.phase_acceptance import (
    ClosureState,
    CriterionEvidenceStatus,
    FindingClass,
)
from neyma_product_driver.phase_closure import (
    CLOSURE_FILE,
    PhaseClosureController,
    check_independence,
    gate_owned,
    phase_adjudication_prompt,
)
from neyma_product_driver.review_cycle import capture_fingerprint

from phase_fixtures import (
    FakeAssessment,
    FakeFinding,
    FakeReview,
    criterion,
    default_criteria,
    head,
    phase_repo,
)

#: The fixture phase's criteria. AC-1 and AC-2 are ordinary implementation
#: criteria; AC-3, AC-4 and AC-5 are owned by the external gate, the
#: independent-review gate and the residual ledger respectively.
ALL_CRITERIA = ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5"]
REVIEW_CRITERION = "AC-4"
EXTERNAL_CRITERION = "AC-3"
RESIDUAL_CRITERION = "AC-5"
#: An ordinary criterion the reviewer owns and nothing has run: the repository
#: records it PENDING and points at an artifact that IS in the tree, so its
#: evidence is UNOBSERVED rather than missing — the state an adjudication
#: exists to settle, and the one place a reviewer's score is decisive.
UNPROVEN = "AC-6"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def repo_with_unproven(tmp_path: Path) -> Path:
    return phase_repo(
        tmp_path,
        criteria=default_criteria()
        + [
            criterion(
                UNPROVEN,
                "unproven_behaviour",
                result="PENDING",
                evidence="eval/tests/test_behaviour.py::test_behaviour_holds would establish it",
            )
        ],
    )


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


def ready(repo: Path, **kwargs) -> PhaseClosureController:
    control = controller(repo, **kwargs)
    control.preflight()
    green_ci(control, repo)
    assert control.decide() is ClosureState.READY_FOR_ADJUDICATION
    return control


def review(repo: Path, verdicts: dict[str, str], **kwargs) -> FakeReview:
    """A structurally valid adjudication scoring exactly what it is given."""
    kwargs.setdefault("reviewed_fingerprint", capture_fingerprint(repo).to_dict())
    kwargs.setdefault(
        "verdict",
        "SUPPORTED" if all(v == "PASS" for v in verdicts.values()) else "NOT_SUPPORTED",
    )
    return FakeReview(
        criteria_assessment=[
            FakeAssessment(cid, v, "what the session saw") for cid, v in verdicts.items()
        ],
        **kwargs,
    )


def all_but(criteria: list[str], overrides: dict[str, str]) -> dict[str, str]:
    return {cid: overrides.get(cid, "PASS") for cid in criteria}


def status(control: PhaseClosureController, cid: str) -> CriterionEvidenceStatus:
    return control.record.evidence.status_for(cid)


def satisfied(control: PhaseClosureController, cid: str) -> tuple[bool, str]:
    return control._criterion_satisfied(control.record.criteria.get(cid))


def unsatisfied_ids(control: PhaseClosureController) -> list[str]:
    return [cid for cid, _why in control.unsatisfied_required()]


def incomplete_findings(control: PhaseClosureController) -> list:
    return [
        f
        for f in control.record.live_findings
        if f.finding_id.endswith("ADJUDICATION-INCOMPLETE")
    ]


# --------------------------------------------------------------------------
# (A) the classification itself
# --------------------------------------------------------------------------


class TestWhichCriteriaAGateOwns:
    def test_the_three_gate_kinds_are_recognised_by_their_identifiers(self) -> None:
        assert gate_kind("", "independent_phase_review_by_a_non_builder") is (
            CriterionKind.INDEPENDENT_REVIEW
        )
        assert gate_kind("", "ci_green_on_the_accepted_tree") is (
            CriterionKind.EXTERNAL_VERIFICATION
        )
        assert gate_kind("", "carried_residuals_recorded_and_nonblocking") is (
            CriterionKind.RESIDUAL_LEDGER
        )

    def test_an_implementation_criterion_is_owned_by_no_gate(self) -> None:
        assert gate_kind("AC-1", "behaviour_landed") is None
        # The near-miss the vocabulary is written to keep out: a screen that
        # shows independent totals is not a second opinion.
        assert gate_kind("AC-9", "reviewer_ui_shows_independent_totals") is None

    def test_a_criterion_set_reports_its_gates_and_their_owners(
        self, tmp_path: Path
    ) -> None:
        control = ready(repo_with_unproven(tmp_path))
        owners = gate_owned(control.record.criteria)
        assert owners == {
            EXTERNAL_CRITERION: CriterionKind.EXTERNAL_VERIFICATION,
            REVIEW_CRITERION: CriterionKind.INDEPENDENT_REVIEW,
            RESIDUAL_CRITERION: CriterionKind.RESIDUAL_LEDGER,
        }


# --------------------------------------------------------------------------
# (B) the independent-review gate, against every score a reviewer can give it
# --------------------------------------------------------------------------


class TestTheReviewerDoesNotScoreItsOwnIndependence:
    def _with_self_score(self, tmp_path: Path, score: str) -> PhaseClosureController:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(review(repo, all_but(ALL_CRITERIA, {REVIEW_CRITERION: score})))
        control.decide()
        return control

    @pytest.mark.parametrize("score", ["PASS", "CANNOT_DETERMINE", "FAIL"])
    def test_a_mechanically_valid_review_establishes_the_criterion(
        self, tmp_path: Path, score: str
    ) -> None:
        """The whole rule, in one parametrisation.

        The reviewer's own three possible answers about its own independence
        produce the same result, because none of them is the authority: what
        established the criterion is that the session was built outside the
        lineage, bound to this tree and these criteria, and answered.
        """
        control = self._with_self_score(tmp_path, score)
        assert control.record.adjudication.taken(control.record.criteria)[0]
        assert status(control, REVIEW_CRITERION) is CriterionEvidenceStatus.ESTABLISHED
        ok, why = satisfied(control, REVIEW_CRITERION)
        assert ok, why
        assert "independent-review gate" in why

    @pytest.mark.parametrize("score", ["PASS", "CANNOT_DETERMINE", "FAIL"])
    def test_the_reviewers_own_answer_is_kept_verbatim(
        self, tmp_path: Path, score: str
    ) -> None:
        """Append-only. Nothing rewrites the reviewer into agreement."""
        control = self._with_self_score(tmp_path, score)
        result = control.record.adjudication.result_for(REVIEW_CRITERION)
        assert result.verdict == score
        assert result.basis == "what the session saw"

    @pytest.mark.parametrize("score", ["PASS", "CANNOT_DETERMINE", "FAIL"])
    def test_the_record_shows_the_gate_and_the_self_score_separately(
        self, tmp_path: Path, score: str
    ) -> None:
        control = self._with_self_score(tmp_path, score)
        line = "\n".join(control.record.ledger.criteria_gate_settled)
        assert f"{REVIEW_CRITERION}: the independent-review gate ESTABLISHED it" in line
        assert f"the reviewer's own score was {score}" in line
        detail = "".join(
            ref.detail for ref in control.record.evidence.for_criterion(REVIEW_CRITERION)
        )
        assert score in detail
        assert "not the authority" in detail

    def test_cannot_determine_on_a_gate_criterion_does_not_report_an_incomplete_adjudication(
        self, tmp_path: Path
    ) -> None:
        """The misleading sentence the real run was blocked behind."""
        control = self._with_self_score(tmp_path, "CANNOT_DETERMINE")
        assert incomplete_findings(control) == []
        discharged, why = control.record.adjudication.discharges(control.record.criteria)
        assert discharged, why

    def test_a_reviewer_fail_on_a_gate_criterion_is_recorded_and_does_not_block(
        self, tmp_path: Path
    ) -> None:
        """A score is not a measurement. Dissent is kept; the gate still owns the fact."""
        control = self._with_self_score(tmp_path, "FAIL")
        dissent = [
            f for f in control.record.live_findings if f.finding_id.endswith("REVIEWER-DISSENT")
        ]
        assert [f.finding_id for f in dissent] == [f"{REVIEW_CRITERION}-REVIEWER-DISSENT"]
        assert not dissent[0].blocks_phase_acceptance
        assert "does not replace the gate" in dissent[0].summary
        assert control.record.blocking_findings == []
        assert not any(
            f.finding_id.endswith("ADJUDICATED-FAIL") for f in control.record.findings
        )
        assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_an_actual_independence_defect_still_comes_from_check_independence(
        self, tmp_path: Path
    ) -> None:
        """The other direction of the same test: a real defect is still fatal.

        A reviewer scoring itself FAIL changes nothing. A reviewer that IS the
        builder changes everything — and the difference is that the second is
        something this module measured.
        """
        repo = phase_repo(tmp_path)
        control = controller(repo, builder_session_ids=["builder-1", "builder-2"])
        control.preflight()
        green_ci(control, repo)
        control.ingest_review(
            review(
                repo,
                all_but(ALL_CRITERIA, {REVIEW_CRITERION: "PASS"}),
                reviewer_session_id="builder-2",
            )
        )
        assert status(control, REVIEW_CRITERION) is not CriterionEvidenceStatus.ESTABLISHED
        assert not satisfied(control, REVIEW_CRITERION)[0]
        assert control.decide() is ClosureState.READY_FOR_ADJUDICATION


# --------------------------------------------------------------------------
# (C) every way the gate may NOT be established
# --------------------------------------------------------------------------


class TestTheGateStaysUnsatisfiedWhenTheReviewWasNotOne:
    """Each of these used to be, and remains, a reason no review happened.

    Every case scores the review criterion PASS, so nothing here can be passing
    for the old reason. What refuses them is the mechanical check that names
    them, which is the half of the repair that must not be weakened.
    """

    def _gate_is_unsatisfied(self, control: PhaseClosureController) -> None:
        assert not control.record.adjudication.taken(control.record.criteria)[0]
        assert status(control, REVIEW_CRITERION) is not CriterionEvidenceStatus.ESTABLISHED
        assert not satisfied(control, REVIEW_CRITERION)[0]
        assert REVIEW_CRITERION in unsatisfied_ids(control)

    def test_the_reviewer_is_the_builder(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo, builder_session_ids=["builder-1", "builder-2"])
        control.preflight()
        green_ci(control, repo)
        control.ingest_review(
            review(repo, all_but(ALL_CRITERIA, {}), reviewer_session_id="builder-2")
        )
        control.decide()
        self._gate_is_unsatisfied(control)
        assert "builder sessions" in control.record.adjudication.independence_problem

    def test_the_reviewer_inherited_the_builders_conversation(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(
            review(repo, all_but(ALL_CRITERIA, {}), inherited_builder_context=True)
        )
        control.decide()
        self._gate_is_unsatisfied(control)
        assert "inherited" in control.record.adjudication.independence_problem

    def test_the_reviewer_read_a_different_tree(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(
            review(repo, all_but(ALL_CRITERIA, {})), reviewed_tree="some/other/tree"
        )
        control.decide()
        self._gate_is_unsatisfied(control)
        assert "different implementation" in control.record.adjudication.independence_problem

    def test_the_reviewer_was_given_a_different_criteria_fingerprint(
        self, tmp_path: Path
    ) -> None:
        """A review of a different set of demands is not a review of this phase.

        Built directly, because a live ingest always stamps the frozen
        fingerprint on the response: the shape being pinned is the one a record
        persisted under an earlier freeze comes back with.
        """
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(review(repo, all_but(ALL_CRITERIA, {})))
        stored = control.record.adjudication
        stored.criteria_fingerprint = "a-different-set-of-demands"
        stored.independence_problem = check_independence(
            reviewer_session_id=stored.reviewer_session_id,
            builder_session_ids=control.record.builder_session_ids,
            inherited_builder_context=stored.inherited_builder_context,
            reviewed_tree=stored.reviewed_tree,
            candidate_tree=control.record.fingerprint().identity,
            criteria_fingerprint=stored.criteria_fingerprint,
            frozen_fingerprint=control.record.criteria_fingerprint,
        )
        control._refresh_adjudication_evidence()
        control.decide()
        self._gate_is_unsatisfied(control)
        assert "different set of demands" in stored.independence_problem

    def test_the_response_did_not_cover_the_contract(self, tmp_path: Path) -> None:
        """A reply that omitted criteria is not an adjudication, whatever it scored."""
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(review(repo, {REVIEW_CRITERION: "PASS"}))
        control.decide()
        self._gate_is_unsatisfied(control)
        assert control._adjudication_outstanding()

    def test_no_adjudication_at_all_leaves_the_gate_unsatisfied(
        self, tmp_path: Path
    ) -> None:
        control = ready(phase_repo(tmp_path))
        assert control.record.adjudication is None
        assert status(control, REVIEW_CRITERION) is not CriterionEvidenceStatus.ESTABLISHED
        assert not satisfied(control, REVIEW_CRITERION)[0]

    def test_the_repository_may_not_self_certify_the_review_it_has_not_had(
        self, tmp_path: Path
    ) -> None:
        """A registry writing PASS beside its own review criterion establishes nothing.

        The gate is the authority, and the repository is not the gate. This is
        the one place the repair is STRICTER than what it replaced.
        """
        repo = phase_repo(
            tmp_path,
            criteria=[
                criterion("AC-1", "behaviour_landed", evidence="src/product.py"),
                criterion(
                    REVIEW_CRITERION,
                    "independent_phase_review_by_a_non_builder",
                    result="PASS",
                    evidence="we reviewed it ourselves",
                ),
            ],
        )
        control = controller(repo)
        control.preflight()
        assert control.record.criteria.get(REVIEW_CRITERION).passed
        assert not satisfied(control, REVIEW_CRITERION)[0]


# --------------------------------------------------------------------------
# (D) the other two gates keep their own authorities
# --------------------------------------------------------------------------


class TestTheOtherGatesAreNotTheReviewersEither:
    def test_the_external_criterion_follows_the_exact_tree_record_not_the_reviewer(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(
            review(repo, all_but(ALL_CRITERIA, {EXTERNAL_CRITERION: "FAIL"}))
        )
        control.decide()
        ok, why = satisfied(control, EXTERNAL_CRITERION)
        assert ok, why
        assert "external-verification gate" in why

    def test_a_reviewer_pass_cannot_stand_in_for_a_missing_external_record(
        self, tmp_path: Path
    ) -> None:
        """And the same rule in the direction that costs something."""
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        control.ingest_review(review(repo, all_but(ALL_CRITERIA, {})))
        control.decide()
        assert not satisfied(control, EXTERNAL_CRITERION)[0]
        assert control.record.state is ClosureState.WAITING_FOR_EXTERNAL_VERIFICATION

    def test_an_external_record_for_another_commit_does_not_establish_it(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = controller(repo)
        control.preflight()
        green_ci(control, repo, sha="0" * 40)
        control.ingest_review(review(repo, all_but(ALL_CRITERIA, {})))
        control.decide()
        assert not satisfied(control, EXTERNAL_CRITERION)[0]

    def test_the_residual_criterion_follows_the_ledger_not_the_reviewer(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(
            review(repo, all_but(ALL_CRITERIA, {RESIDUAL_CRITERION: "FAIL"}))
        )
        control.decide()
        ok, why = satisfied(control, RESIDUAL_CRITERION)
        assert ok, why
        assert "residual ledger" in why

    def test_a_blocking_residual_refuses_it_however_the_reviewer_scored_it(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(
            tmp_path,
            residuals=[
                {
                    "id": "R-1",
                    "finding": "a carried risk the repository says blocks",
                    "blocks_phase_acceptance": True,
                    "closure_condition": "a founder decides",
                }
            ],
        )
        control = controller(repo)
        control.preflight()
        green_ci(control, repo)
        control.ingest_review(review(repo, all_but(ALL_CRITERIA, {})))
        control.decide()
        assert not satisfied(control, RESIDUAL_CRITERION)[0]
        assert control.record.state is ClosureState.BLOCKED


# --------------------------------------------------------------------------
# (E) the reviewer keeps everything that was ever its to decide
# --------------------------------------------------------------------------


class TestTheReviewerStillOwnsTheOrdinaryCriteria:
    def test_a_valid_review_does_not_pass_a_criterion_it_could_not_determine(
        self, tmp_path: Path
    ) -> None:
        repo = repo_with_unproven(tmp_path)
        control = ready(repo)
        control.ingest_review(
            review(repo, all_but([*ALL_CRITERIA, UNPROVEN], {UNPROVEN: "CANNOT_DETERMINE"}))
        )
        assert control.decide() is ClosureState.BLOCKED
        assert not satisfied(control, UNPROVEN)[0]
        assert unsatisfied_ids(control) == [UNPROVEN]
        # And the honest sentence about it is raised, because this criterion IS
        # one the response owed.
        assert [f.finding_id for f in incomplete_findings(control)] == [
            "P9-ADJUDICATION-INCOMPLETE"
        ]
        assert UNPROVEN in incomplete_findings(control)[0].summary

    def test_a_valid_review_still_refuses_a_criterion_it_scored_fail(
        self, tmp_path: Path
    ) -> None:
        repo = repo_with_unproven(tmp_path)
        control = ready(repo)
        control.ingest_review(
            review(repo, all_but([*ALL_CRITERIA, UNPROVEN], {"AC-1": "FAIL"}))
        )
        assert control.decide() is ClosureState.BLOCKED
        assert not satisfied(control, "AC-1")[0]
        blocking = control.record.blocking_findings
        assert [f.finding_id for f in blocking] == ["AC-1-ADJUDICATED-FAIL"]
        assert blocking[0].classification is FindingClass.PRODUCT_DEFECT

    def test_a_demonstrated_finding_against_a_gate_criterion_still_blocks(
        self, tmp_path: Path
    ) -> None:
        """A gate is falsifiable like everything else — by a demonstration.

        The rule removes the reviewer's SCORE from the gate's authority. It does
        not make a gate-owned criterion unfalsifiable: something a reviewer ran
        and showed still refutes it.
        """
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(
            review(
                repo,
                all_but(ALL_CRITERIA, {RESIDUAL_CRITERION: "FAIL"}),
                findings=[
                    FakeFinding(
                        "the product does the wrong thing and "
                        f"{RESIDUAL_CRITERION} is false: I ran the ledger query on this "
                        "tree and it returned an open blocking row",
                        severity="blocker",
                        evidence_path="src/product.py",
                        reasoning="observed on this tree",
                    )
                ],
            )
        )
        assert control.decide() is ClosureState.BLOCKED
        assert not satisfied(control, RESIDUAL_CRITERION)[0]


# --------------------------------------------------------------------------
# (F) nothing here buys a reviewer
# --------------------------------------------------------------------------


class TestNoSecondReviewerIsEverPurchased:
    def _persisted(self, tmp_path: Path) -> tuple[Path, EvidenceStore]:
        repo = phase_repo(tmp_path)
        store = EvidenceStore(tmp_path / "runs", "20260914-000000")
        control = controller(repo, store=store)
        control.preflight()
        green_ci(control, repo)
        control.ingest_review(
            review(
                repo,
                all_but(ALL_CRITERIA, {REVIEW_CRITERION: "CANNOT_DETERMINE"}),
                reviewer_session_id="the-one-session",
            )
        )
        control.settle()
        return repo, store

    def test_a_valid_adjudication_is_reused_on_resume(self, tmp_path: Path) -> None:
        repo, store = self._persisted(tmp_path)
        reopened = controller(repo, store=store)
        assert reopened.load() is not None
        assert reopened.record.adjudication.reviewer_session_id == "the-one-session"
        assert not reopened._adjudication_outstanding()
        assert reopened.decide() is not ClosureState.READY_FOR_ADJUDICATION
        assert reopened.record.adjudication_history == []

    def test_the_stored_answer_survives_the_resume_byte_for_byte(
        self, tmp_path: Path
    ) -> None:
        repo, store = self._persisted(tmp_path)
        before = json.loads((store.run_dir / CLOSURE_FILE).read_text(encoding="utf-8"))[
            "adjudication"
        ]
        reopened = controller(repo, store=store)
        reopened.load()
        reopened.settle()
        after = json.loads((store.run_dir / CLOSURE_FILE).read_text(encoding="utf-8"))[
            "adjudication"
        ]
        assert after == before

    def test_a_record_written_under_the_old_rule_re_derives_without_a_reviewer(
        self, tmp_path: Path
    ) -> None:
        """The real run's shape: BLOCKED behind the reviewer's own self-score.

        Reopening must move the criterion to established, withdraw the sentence
        that said the adjudication fell short, keep the reviewer's answer
        exactly as it was, and launch nothing.
        """
        repo, store = self._persisted(tmp_path)
        path = store.run_dir / CLOSURE_FILE
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["decision_rule_version"] = "3"
        raw["state"] = "BLOCKED"
        for ref in raw["evidence"]["refs"]:
            if ref["criterion_id"] == REVIEW_CRITERION and ref["kind"] == "REVIEW":
                ref["observed"], ref["established"] = True, False
        raw["findings"].append(
            {
                "finding_id": "P9-ADJUDICATION-INCOMPLETE",
                "classification": "VERIFICATION_GAP",
                "severity": "major",
                "phase_id": "P9",
                "about_adjudication": "attempt 1 by the-one-session",
                "summary": (
                    "the independent adjudication did not settle the phase: the "
                    f"adjudication could not determine {REVIEW_CRITERION}"
                ),
            }
        )
        path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

        reopened = controller(repo, store=store)
        assert reopened.load() is not None

        assert status(reopened, REVIEW_CRITERION) is CriterionEvidenceStatus.ESTABLISHED
        assert not reopened._adjudication_outstanding()
        assert reopened.record.adjudication.reviewer_session_id == "the-one-session"
        assert (
            reopened.record.adjudication.result_for(REVIEW_CRITERION).verdict
            == "CANNOT_DETERMINE"
        )
        assert incomplete_findings(reopened) == []
        # Kept, not deleted: the statement is still in the record with the
        # reason it stopped applying.
        withdrawn = reopened.record.finding("P9-ADJUDICATION-INCOMPLETE")
        assert withdrawn is not None and withdrawn.withdrawn
        assert reopened.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT


# --------------------------------------------------------------------------
# (G) what the reviewer is told
# --------------------------------------------------------------------------


class TestThePromptSaysWhichCriteriaAreNotTheReviewersToAward:
    def test_the_gate_criteria_are_marked_and_explained(self, tmp_path: Path) -> None:
        control = ready(phase_repo(tmp_path))
        prompt = phase_adjudication_prompt(control.record, authority=control.authority)
        assert "SETTLED BY THE INDEPENDENT-REVIEW GATE" in prompt
        assert "SETTLED BY THE EXTERNAL-VERIFICATION GATE" in prompt
        assert "SETTLED BY THE RESIDUAL LEDGER" in prompt
        assert "NOT YOURS TO AWARD" in prompt

    def test_the_response_contract_is_unchanged(self, tmp_path: Path) -> None:
        """Still one entry per criterion. A gate-owned criterion is still scored.

        Relaxing the contract would make an omission ambiguous again, and the
        reviewer's recorded opinion of a gate is worth having.
        """
        control = ready(phase_repo(tmp_path))
        prompt = phase_adjudication_prompt(control.record, authority=control.authority)
        assert "criteria_assessment MUST contain exactly 5 entries" in prompt
        assert "Still score them" in prompt


# --------------------------------------------------------------------------
# (H) the acceptance record says which authority established what
# --------------------------------------------------------------------------


class TestTheAcceptanceRecordNamesTheAuthority:
    def test_a_gate_established_criterion_is_written_and_attributed(
        self, tmp_path: Path
    ) -> None:
        from phase_fixtures import REGISTRY_REL, accepted_unit

        import yaml

        # The review criterion's evidence field is left EMPTY: a row the
        # repository has already filled in is never overwritten, and what this
        # test is about is the sentence the record writes when it writes one.
        criteria = [
            dict(row, adjudication_evidence="")
            if row["id"] == REVIEW_CRITERION
            else row
            for row in default_criteria()
        ]
        repo = phase_repo(tmp_path, criteria=criteria, extra_units=[accepted_unit()])
        control = ready(repo)
        control.ingest_review(
            review(repo, all_but(ALL_CRITERIA, {REVIEW_CRITERION: "CANNOT_DETERMINE"}))
        )
        assert control.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

        plan = control.materialize_acceptance_record()
        assert not plan.refusal, plan.refusal
        assert plan.written

        registry = yaml.safe_load((repo / REGISTRY_REL).read_text(encoding="utf-8"))
        unit = next(u for u in registry["units"] if u["unit_id"] == "P9")
        row = next(
            r for r in unit["acceptance_criteria"] if r["id"] == REVIEW_CRITERION
        )
        assert str(row["result"]).upper() == "PASS"
        evidence = str(row["adjudication_evidence"])
        assert "the independent-review gate" in evidence
        assert "scored it CANNOT_DETERMINE" in evidence
        assert "not the authority over it" in evidence

    def test_a_gate_that_established_nothing_still_refuses_the_record(
        self, tmp_path: Path
    ) -> None:
        """The permission is exactly as wide as what the gate actually says."""
        from neyma_product_driver.acceptance_record import plan_acceptance_record
        from phase_fixtures import accepted_unit

        repo = phase_repo(tmp_path, extra_units=[accepted_unit()])
        control = ready(repo)
        control.ingest_review(review(repo, all_but(ALL_CRITERIA, {})))
        control.decide()
        plan = plan_acceptance_record(
            repo,
            phase_id="P9",
            criteria=control.record.criteria,
            adjudication=None,
            candidate=control.record.fingerprint(),
            gate_established={},
        )
        assert "does not score it PASS" in plan.refusal
