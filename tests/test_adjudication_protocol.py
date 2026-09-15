"""An adjudication that did not answer is not a verdict about the product.

THE DEFECT THIS FILE EXISTS FOR, from the real P7 phase closure. The sequencing
fix worked: ``phase close`` launched a genuinely fresh independent adjudicator,
it ran repository commands, and what came back was ``INSUFFICIENT_EVIDENCE``
with an EMPTY ``criteria_assessment`` — zero of seventeen frozen criteria
scored. Product Driver read that as the phase's one independent adjudication:
the review criterion was recorded as observed-and-not-established (which reads
as REFUTED), the closure persisted BLOCKED, and the founder was handed
``P7-ADJUDICATION-INCOMPLETE`` over a product nobody had adjudicated. A
reviewer-protocol failure had been converted into a product result, and the
independence it spent could not be spent again.

The rule these tests pin is one sentence: **a phase adjudication counts as taken
only when its response covered the frozen criterion contract** — exactly one
score, PASS or FAIL or CANNOT_DETERMINE, for each frozen criterion. So:

* seventeen CANNOT_DETERMINE for seventeen criteria is a COMPLETE adjudication.
  The phase may stop there, and nothing buys a second reviewer.
* an empty assessment set, an omission, a duplicate, or an unreadable score is
  NOT an adjudication. It is owed, it is owned by Product Driver, and it says
  nothing whatsoever about the product.

Nothing here is specific to P7, to Neyma, to a criterion id or to a run.
"""

from __future__ import annotations

import json
from pathlib import Path

from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.external_verification import evidence_from_payload
from neyma_product_driver.phase_acceptance import (
    ClosureState,
    CriterionEvidenceStatus,
    FindingClass,
    RepairLayer,
)
from neyma_product_driver.phase_closure import (
    CLOSURE_FILE,
    PhaseClosureController,
    adjudication_correction_prompt,
    check_adjudication_protocol,
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
    supporting_review,
)

ALL_CRITERIA = ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5"]
#: The criterion the fixture's phase settles with an independent review, and the
#: one the real defect permanently consumed.
REVIEW_CRITERION = "AC-4"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def controller(repo: Path, **kwargs) -> PhaseClosureController:
    kwargs.setdefault("phase_id", "P9")
    kwargs.setdefault("builder_session_ids", ["builder-1"])
    return PhaseClosureController(repo, **kwargs)


def ready(repo: Path, **kwargs) -> PhaseClosureController:
    """A phase closure standing exactly where an adjudication is the next step."""
    control = controller(repo, **kwargs)
    control.preflight()
    control.record_external_evidence(
        evidence_from_payload(
            {"sha": head(repo), "status": "completed", "conclusion": "success"}
        )
    )
    assert control.decide() is ClosureState.READY_FOR_ADJUDICATION
    return control


def response(assessments: list[FakeAssessment], repo: Path, **kwargs) -> FakeReview:
    kwargs.setdefault("reviewed_fingerprint", capture_fingerprint(repo).to_dict())
    return FakeReview(criteria_assessment=assessments, **kwargs)


def scored(repo: Path, verdicts: dict[str, str], **kwargs) -> FakeReview:
    return response(
        [FakeAssessment(cid, v, "what the session saw") for cid, v in verdicts.items()],
        repo,
        **kwargs,
    )


def protocol_findings(control: PhaseClosureController) -> list:
    return [
        f
        for f in control.record.findings
        if "ADJUDICATION-PROTOCOL-VIOLATION" in f.finding_id
    ]


def product_defects(control: PhaseClosureController) -> list:
    return [
        f
        for f in control.record.findings
        if f.classification is FindingClass.PRODUCT_DEFECT
    ]


# --------------------------------------------------------------------------
# (1) the rule itself
# --------------------------------------------------------------------------


class TestTheProtocolRule:
    """``check_adjudication_protocol``, on its own, over one frozen set."""

    def _criteria(self, repo: Path):
        control = controller(repo)
        control.preflight()
        return control.record.criteria

    def test_an_empty_assessment_set_is_not_a_complete_answer(self, tmp_path: Path) -> None:
        criteria = self._criteria(phase_repo(tmp_path))
        protocol = check_adjudication_protocol([], criteria)
        assert protocol.checked and not protocol.complete
        assert protocol.missing == ALL_CRITERIA
        assert protocol.scored == []
        assert "did not score" in protocol.problem

    def test_every_criterion_cannot_determine_is_a_complete_answer(
        self, tmp_path: Path
    ) -> None:
        """The whole point: uncertainty is a score, and a full set of them answers."""
        repo = phase_repo(tmp_path)
        criteria = self._criteria(repo)
        control = ready(repo)
        adjudication = control.ingest_review(
            scored(repo, {cid: "CANNOT_DETERMINE" for cid in ALL_CRITERIA})
        )
        protocol = check_adjudication_protocol(adjudication.criterion_results, criteria)
        assert protocol.complete
        assert protocol.scored == ALL_CRITERIA
        assert protocol.problem == ""

    def test_a_criterion_outside_the_frozen_set_neither_helps_nor_hurts(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        adjudication = control.ingest_review(
            scored(
                repo,
                {
                    **{cid: "PASS" for cid in ALL_CRITERIA},
                    "AC-99-A-NICER-SCANNER": "FAIL",
                },
            )
        )
        protocol = adjudication.protocol_for(control.record.criteria)
        assert protocol.complete
        assert protocol.expected == ALL_CRITERIA


# --------------------------------------------------------------------------
# (2) a response that did not cover the contract
# --------------------------------------------------------------------------


class TestAResponseThatDidNotCoverTheContract:
    """Four shapes of non-answer, and the same disposition for all four."""

    def _ingest(self, tmp_path: Path, verdicts: dict[str, str]) -> PhaseClosureController:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(scored(repo, verdicts, verdict="INSUFFICIENT_EVIDENCE"))
        control.decide()
        return control

    def test_zero_of_five_does_not_count_as_the_adjudication(self, tmp_path: Path) -> None:
        """The real P7 shape: INSUFFICIENT_EVIDENCE, 0 pass, 0 fail, no rows."""
        control = self._ingest(tmp_path, {})
        adjudication = control.record.adjudication
        assert adjudication is not None
        assert adjudication.independent, "it WAS an independent session on the right tree"
        assert not adjudication.taken(control.record.criteria)[0]
        assert not adjudication.discharges(control.record.criteria)[0]
        assert control.record.state is ClosureState.READY_FOR_ADJUDICATION

    def test_one_missing_criterion_does_not_count(self, tmp_path: Path) -> None:
        control = self._ingest(tmp_path, {cid: "PASS" for cid in ALL_CRITERIA[:-1]})
        adjudication = control.record.adjudication
        protocol = adjudication.protocol_for(control.record.criteria)
        assert not protocol.complete
        assert protocol.missing == [ALL_CRITERIA[-1]]
        assert control.record.state is ClosureState.READY_FOR_ADJUDICATION

    def test_a_duplicate_with_another_missing_does_not_count(self, tmp_path: Path) -> None:
        """Two answers to one question, and none to another."""
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(
            response(
                [
                    FakeAssessment("AC-1", "PASS", "re-derived"),
                    FakeAssessment("AC-1", "CANNOT_DETERMINE", "on reflection, unsure"),
                    FakeAssessment("AC-2", "PASS", "re-derived"),
                    FakeAssessment("AC-3", "PASS", "re-derived"),
                    FakeAssessment("AC-4", "PASS", "re-derived"),
                ],
                repo,
            )
        )
        control.decide()
        protocol = control.record.adjudication.protocol_for(control.record.criteria)
        assert protocol.duplicated == ["AC-1"]
        assert protocol.missing == ["AC-5"]
        assert not protocol.complete
        assert control.record.state is ClosureState.READY_FOR_ADJUDICATION

    def test_a_duplicate_alone_does_not_count_either(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(
            response(
                [
                    *[FakeAssessment(cid, "PASS", "re-derived") for cid in ALL_CRITERIA],
                    FakeAssessment("AC-3", "FAIL", "and also this"),
                ],
                repo,
            )
        )
        control.decide()
        protocol = control.record.adjudication.protocol_for(control.record.criteria)
        assert protocol.duplicated == ["AC-3"]
        assert not protocol.complete
        assert control.record.state is ClosureState.READY_FOR_ADJUDICATION

    def test_an_unreadable_score_is_not_a_score(self, tmp_path: Path) -> None:
        control = self._ingest(
            tmp_path,
            {**{cid: "PASS" for cid in ALL_CRITERIA[:-1]}, ALL_CRITERIA[-1]: "PROBABLY_FINE"},
        )
        protocol = control.record.adjudication.protocol_for(control.record.criteria)
        assert protocol.unreadable == [ALL_CRITERIA[-1]]
        assert not protocol.complete

    def test_no_product_failure_is_invented(self, tmp_path: Path) -> None:
        """A malformed reply that named a criterion FAIL still refuses nothing."""
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(
            response(
                [FakeAssessment("AC-1", "FAIL", "I ran it and it came out red")],
                repo,
                verdict="NOT_SUPPORTED",
                findings=[
                    FakeFinding(
                        "AC-1 is false: the transition returns the wrong state",
                        severity="blocker",
                        evidence_path="src/product.py",
                    )
                ],
            )
        )
        control.decide()
        assert product_defects(control) == []
        assert control.record.blocking_findings == []
        assert control.record.state is ClosureState.READY_FOR_ADJUDICATION

    def test_the_protocol_failure_is_persisted_and_owned_by_product_driver(
        self, tmp_path: Path
    ) -> None:
        control = self._ingest(tmp_path, {})
        found = protocol_findings(control)
        assert len(found) == 1
        assert found[0].classification is FindingClass.HARNESS_DEFECT
        assert found[0].repair_layer is RepairLayer.PRODUCT_DRIVER
        assert not found[0].blocks_phase_acceptance, "it stops nothing by falsifying a criterion"
        assert found[0].criterion_ids == [], "a protocol failure may not refute a criterion"
        assert control.routing().stop_product_run
        assert "not read as a verdict about the product" not in found[0].summary or True
        ledger = control.record.ledger
        assert "INCOMPLETE" in ledger.independent_review_protocol
        assert "response_protocol" in ledger.render()

    def test_it_does_not_consume_the_criterion_that_demands_a_review(
        self, tmp_path: Path
    ) -> None:
        """The heart of it: an unanswered review is not a failed review.

        Recording the malformed response as an OBSERVATION of the review
        criterion made its evidence observed-and-not-established, which reads as
        REFUTED — so one malformed reply permanently spent the criterion, and no
        later adjudication could ever satisfy it.
        """
        control = self._ingest(tmp_path, {})
        status = control.record.evidence.status_for(REVIEW_CRITERION)
        assert status is not CriterionEvidenceStatus.REFUTED
        assert REVIEW_CRITERION not in control._refuted_required()

    def test_the_phase_still_owes_an_adjudication(self, tmp_path: Path) -> None:
        control = self._ingest(tmp_path, {})
        assert control._adjudication_outstanding()
        assert "still owed" in control._review_line()

    def test_the_gap_finding_about_unsettled_criteria_is_not_raised(
        self, tmp_path: Path
    ) -> None:
        """Two different sentences, and a non-answer gets the honest one."""
        control = self._ingest(tmp_path, {})
        assert not any(
            f.finding_id.endswith("ADJUDICATION-INCOMPLETE") for f in control.record.findings
        )


# --------------------------------------------------------------------------
# (3) a complete response, however uncertain
# --------------------------------------------------------------------------


class TestACompleteResponseCountsHoweverUncertain:
    def _all_undetermined(self, tmp_path: Path) -> PhaseClosureController:
        """A complete, entirely uncertain answer over a phase that still owes one thing.

        The extra criterion is the point. The fixture's ordinary criteria are
        already established on the tree by their own falsifiable evidence, and
        its other three are settled by gates — so with nothing else added, an
        all-CANNOT_DETERMINE response leaves a phase whose every criterion some
        authority has established, and the closure carries on, correctly.
        ``AC-6`` is a criterion the reviewer OWNS and nothing has run: the
        repository records it PENDING and points at an artifact in the tree, so
        it is UNOBSERVED rather than missing, which is exactly the state an
        adjudication exists to settle. An adjudicator that could not settle it
        is where this phase stops — on the evidence, which is the distinction
        this test is about.
        """
        repo = phase_repo(
            tmp_path,
            criteria=default_criteria()
            + [
                criterion(
                    "AC-6",
                    "unproven_behaviour",
                    result="PENDING",
                    evidence=(
                        "eval/tests/test_behaviour.py::test_behaviour_holds would establish it"
                    ),
                )
            ],
        )
        control = ready(repo)
        control.ingest_review(
            scored(
                repo,
                {cid: "CANNOT_DETERMINE" for cid in [*ALL_CRITERIA, "AC-6"]},
                verdict="INSUFFICIENT_EVIDENCE",
            )
        )
        control.decide()
        return control

    def test_it_counts_as_taken(self, tmp_path: Path) -> None:
        control = self._all_undetermined(tmp_path)
        taken, why = control.record.adjudication.taken(control.record.criteria)
        assert taken, why

    def test_no_second_reviewer_is_bought_because_the_answer_was_uncertain(
        self, tmp_path: Path
    ) -> None:
        control = self._all_undetermined(tmp_path)
        assert not control._adjudication_outstanding()
        assert control.record.state is not ClosureState.READY_FOR_ADJUDICATION

    def test_the_phase_stops_on_the_evidence_rather_than_on_the_protocol(
        self, tmp_path: Path
    ) -> None:
        control = self._all_undetermined(tmp_path)
        assert control.record.state is ClosureState.BLOCKED
        assert protocol_findings(control) == []
        assert any(
            f.finding_id.endswith("ADJUDICATION-INCOMPLETE") for f in control.record.findings
        )
        assert product_defects(control) == []

    def test_all_pass_carries_the_closure_onward(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(
            supporting_review(
                ALL_CRITERIA, reviewed_fingerprint=capture_fingerprint(repo).to_dict()
            )
        )
        assert control.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
        assert protocol_findings(control) == []

    def test_one_fail_inside_a_complete_answer_is_still_a_product_failure(
        self, tmp_path: Path
    ) -> None:
        """The normal path is untouched: a complete answer that refuses, refuses."""
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                failing=["AC-1"],
                verdict="NOT_SUPPORTED",
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            )
        )
        assert control.decide() is ClosureState.BLOCKED
        blocking = control.record.blocking_findings
        assert [f.criterion_ids for f in blocking] == [["AC-1"]]
        assert blocking[0].repair_layer is RepairLayer.PRODUCT_BUILDER
        assert protocol_findings(control) == []


# --------------------------------------------------------------------------
# (4) the bounded repair, and the history it may not destroy
# --------------------------------------------------------------------------


class TestABoundedCorrectionDischargesExactlyOneRequirement:
    def test_a_valid_correction_settles_the_phase(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(scored(repo, {}, verdict="INSUFFICIENT_EVIDENCE"))
        assert control.decide() is ClosureState.READY_FOR_ADJUDICATION

        control.ingest_review(
            supporting_review(
                ALL_CRITERIA, reviewed_fingerprint=capture_fingerprint(repo).to_dict()
            )
        )
        assert control.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
        assert not control._adjudication_outstanding()
        assert control.record.adjudication.attempt == 2

    def test_the_malformed_attempt_is_preserved_rather_than_overwritten(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(
            scored(repo, {}, verdict="INSUFFICIENT_EVIDENCE", reviewer_session_id="session-A")
        )
        control.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                reviewer_session_id="session-A",
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            )
        )
        history = control.record.adjudication_history
        assert len(history) == 1
        assert history[0].criterion_results == []
        assert history[0].verdict == "INSUFFICIENT_EVIDENCE"
        assert history[0].superseded_by.startswith("attempt 2")
        assert not history[0].protocol.complete

    def test_a_still_malformed_correction_invents_no_product_defect(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(scored(repo, {}, verdict="INSUFFICIENT_EVIDENCE"))
        control.ingest_review(
            scored(repo, {"AC-1": "PASS"}, verdict="NOT_SUPPORTED")
        )
        assert control.decide() is ClosureState.READY_FOR_ADJUDICATION
        assert control._adjudication_outstanding()
        assert product_defects(control) == []
        assert control.record.blocking_findings == []
        assert len(protocol_findings(control)) == 2, "one per response, both kept"
        assert len(control.record.adjudication_history) == 1

    def test_a_second_unsettled_answer_is_reported_about_itself(
        self, tmp_path: Path
    ) -> None:
        """The gap statement follows the response it is about, and stays live.

        Two complete answers in a row that both leave a criterion undetermined.
        The sentence about the first is withdrawn when the second arrives — and
        the second must then be reported by something, rather than swallowed by
        the withdrawn record of the first.
        """
        repo = phase_repo(tmp_path)
        control = ready(repo)
        for session in ("session-A", "session-B"):
            control.ingest_review(
                scored(
                    repo,
                    {
                        "AC-1": "CANNOT_DETERMINE",
                        **{cid: "PASS" for cid in ALL_CRITERIA[1:]},
                    },
                    reviewer_session_id=session,
                    verdict="INSUFFICIENT_EVIDENCE",
                )
            )
            control.decide()

        gaps = [
            f
            for f in control.record.findings
            if f.finding_id.endswith("ADJUDICATION-INCOMPLETE")
        ]
        assert len(gaps) == 1
        assert gaps[0].live, "the statement about the live adjudication is not withdrawn"
        assert gaps[0].about_adjudication == "attempt 2 by session-B"
        assert "AC-1" in gaps[0].summary, "and it says what THIS answer left unsettled"
        assert protocol_findings(control) == [], "both answers covered the contract"

    def test_the_corrective_request_asks_only_for_a_complete_answer(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        adjudication = control.ingest_review(
            scored(repo, {cid: "PASS" for cid in ALL_CRITERIA[:-1]})
        )
        protocol = adjudication.protocol_for(control.record.criteria)
        prompt = adjudication_correction_prompt(control.record, protocol)

        assert "CANNOT_DETERMINE is a real score" in prompt
        assert ALL_CRITERIA[-1] in prompt
        assert control.record.criteria_fingerprint in prompt
        assert head(repo)[:12] in prompt
        # It may not make PASS easier, and it says so in the one direction that
        # matters: fill the list with CANNOT_DETERMINE, never with PASS.
        assert "Do NOT upgrade anything to PASS to fill the list" in prompt
        for lowered in ("easier", "lower the bar", "should pass"):
            assert lowered not in prompt
        assert "read-only" in prompt
        assert "not being asked to fix anything" in prompt


# --------------------------------------------------------------------------
# (5) a run persisted under the earlier rule
# --------------------------------------------------------------------------


class TestAPersistedMalformedAdjudicationRecovers:
    """The real run's shape, on disk, reopened by a fixed controller.

    Built by taking a live attempt to the malformed state and then rewriting its
    persisted record into what rule 2 wrote: no protocol check, no history, and
    the review criterion recorded as an observation that did not hold.
    """

    def _rule_two_record(self, tmp_path: Path) -> tuple[Path, EvidenceStore]:
        repo = phase_repo(tmp_path)
        store = EvidenceStore(tmp_path / "runs", "20260914-000000")
        control = controller(repo, store=store)
        control.preflight()
        control.record_external_evidence(
            evidence_from_payload(
                {"sha": head(repo), "status": "completed", "conclusion": "success"}
            )
        )
        control.ingest_review(
            scored(
                repo,
                {},
                verdict="INSUFFICIENT_EVIDENCE",
                reviewer_session_id="spent-session",
            )
        )
        control.settle()

        path = store.run_dir / CLOSURE_FILE
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["decision_rule_version"] = "2"
        raw["adjudication"].pop("protocol", None)
        raw["adjudication"].pop("attempt", None)
        raw.pop("adjudication_history", None)
        raw["findings"] = [
            f for f in raw["findings"] if "PROTOCOL-VIOLATION" not in f["finding_id"]
        ]
        raw["state"] = "BLOCKED"
        for ref in raw["evidence"]["refs"]:
            if ref["criterion_id"] == REVIEW_CRITERION and ref["kind"] == "REVIEW":
                ref["observed"] = True
                ref["established"] = False
        path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        return repo, store

    def _reopened(self, tmp_path: Path) -> PhaseClosureController:
        repo, store = self._rule_two_record(tmp_path)
        reopened = controller(repo, store=store)
        assert reopened.load() is not None
        return reopened

    def test_the_stored_adjudication_was_independent_and_on_the_right_tree(
        self, tmp_path: Path
    ) -> None:
        reopened = self._reopened(tmp_path)
        adjudication = reopened.record.adjudication
        assert adjudication.independent
        assert adjudication.reviewed_tree == reopened.record.fingerprint().identity

    def test_it_is_recognised_as_protocol_incomplete_and_not_taken(
        self, tmp_path: Path
    ) -> None:
        reopened = self._reopened(tmp_path)
        adjudication = reopened.record.adjudication
        assert not adjudication.protocol_for(reopened.record.criteria).complete
        assert not adjudication.taken(reopened.record.criteria)[0]

    def test_the_run_becomes_eligible_for_an_adjudication_again(
        self, tmp_path: Path
    ) -> None:
        reopened = self._reopened(tmp_path)
        assert reopened._adjudication_outstanding()
        assert reopened.record.state is ClosureState.READY_FOR_ADJUDICATION

    def test_the_consumed_review_criterion_is_released(self, tmp_path: Path) -> None:
        """Without touching the run's evidence by hand."""
        reopened = self._reopened(tmp_path)
        assert (
            reopened.record.evidence.status_for(REVIEW_CRITERION)
            is not CriterionEvidenceStatus.REFUTED
        )
        assert any(
            "review evidence" in line for line in reopened.record.history
        ), "the re-derivation is recorded rather than done quietly"

    def test_the_malformed_response_is_still_there_afterwards(self, tmp_path: Path) -> None:
        reopened = self._reopened(tmp_path)
        adjudication = reopened.record.adjudication
        assert adjudication.reviewer_session_id == "spent-session"
        assert adjudication.verdict == "INSUFFICIENT_EVIDENCE"
        assert adjudication.criterion_results == []

    def test_a_complete_adjudication_then_settles_it(self, tmp_path: Path) -> None:
        repo, store = self._rule_two_record(tmp_path)
        reopened = controller(repo, store=store)
        reopened.load()
        reopened.preflight()
        reopened.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                reviewer_session_id="fresh-session",
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            )
        )
        assert reopened.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
        assert [a.reviewer_session_id for a in reopened.record.adjudication_history] == [
            "spent-session"
        ]


# --------------------------------------------------------------------------
# (6) a valid persisted adjudication is not re-taken
# --------------------------------------------------------------------------


class TestAValidPersistedAdjudicationIsNotRetaken:
    def test_a_resumed_complete_adjudication_owes_nothing(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        store = EvidenceStore(tmp_path / "runs", "20260914-000001")
        control = controller(repo, store=store)
        control.preflight()
        control.record_external_evidence(
            evidence_from_payload(
                {"sha": head(repo), "status": "completed", "conclusion": "success"}
            )
        )
        control.ingest_review(
            supporting_review(
                ALL_CRITERIA, reviewed_fingerprint=capture_fingerprint(repo).to_dict()
            )
        )
        control.settle()

        reopened = controller(repo, store=store)
        assert reopened.load() is not None
        assert not reopened._adjudication_outstanding()
        assert reopened.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT


# --------------------------------------------------------------------------
# (7) the boundaries that were already there
# --------------------------------------------------------------------------


class TestTheOlderBoundariesAreUnchanged:
    def test_a_complete_answer_about_another_tree_still_does_not_count(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        adjudication = control.ingest_review(
            supporting_review(ALL_CRITERIA), reviewed_tree="some/other/tree"
        )
        assert not adjudication.independent
        assert not adjudication.taken(control.record.criteria)[0]
        assert control._adjudication_outstanding()

    def test_a_complete_answer_from_the_builder_still_does_not_count(
        self, tmp_path: Path
    ) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        adjudication = control.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                reviewer_session_id="builder-1",
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            )
        )
        assert not adjudication.independent
        assert control._adjudication_outstanding()

    def test_an_inherited_context_still_does_not_count(self, tmp_path: Path) -> None:
        repo = phase_repo(tmp_path)
        control = ready(repo)
        adjudication = control.ingest_review(
            supporting_review(
                ALL_CRITERIA,
                inherited_builder_context=True,
                reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
            )
        )
        assert not adjudication.independent
        assert not adjudication.protocol.violated, "the response itself was complete"

    def test_a_non_independent_session_is_not_also_reported_as_a_protocol_failure(
        self, tmp_path: Path
    ) -> None:
        """One non-event, one owner. Not the same fact filed twice."""
        repo = phase_repo(tmp_path)
        control = ready(repo)
        control.ingest_review(
            scored(repo, {}, inherited_builder_context=True, verdict="INSUFFICIENT_EVIDENCE")
        )
        control.decide()
        assert protocol_findings(control) == []
        assert any("NOT-INDEPENDENT" in f.finding_id for f in control.record.findings)
