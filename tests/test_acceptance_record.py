"""(R) READY_FOR_ACCEPTANCE_COMMIT must produce a record, not a clean tree.

A phase reached the last state of its closure with everything it owed
discharged: every required criterion PASS, the external verifier green on the
exact candidate commit, a fresh independent adjudication SUPPORTED, no blocking
finding. The controller said ``READY_FOR_ACCEPTANCE_COMMIT``, handed the
repository to the commit preparation, and the preparation refused:

    the working tree carries no acceptance-record change, so there is nothing to
    commit; the acceptance is already recorded, or the record was never written

It was the second one. ``acceptance_commit`` classifies, validates and stages an
acceptance-record diff that already exists, and nothing in the pipeline created
one — so the final state was unreachable by the act of reaching it, and the only
way past it was for a person to hand-write a status file at the exact moment the
machine had finished checking.

What closes it is one narrow step: the repository's own record is materialized
from the repository's own authority and this attempt's adjudication results.
The tests below are about the four things that makes dangerous, and each of them
is a refusal rather than a caveat.
"""

from __future__ import annotations

import re
import subprocess
import tokenize
from pathlib import Path

import pytest
import yaml

from neyma_product_driver import acceptance_record as ar
from neyma_product_driver.acceptance_commit import (
    Surface,
    classify_surface,
    plan_acceptance_commit,
)
from neyma_product_driver.acceptance_record import (
    derive_convention,
    plan_acceptance_record,
)
from neyma_product_driver.external_verification import evidence_from_payload
from neyma_product_driver.phase_acceptance import ClosureState, FindingClass
from neyma_product_driver.phase_closure import PhaseClosureController
from neyma_product_driver.review_cycle import capture_fingerprint

from phase_fixtures import (
    REGISTRY_REL,
    accepted_unit,
    criterion,
    head,
    pending_unit,
    phase_repo,
    supporting_review,
)


# --------------------------------------------------------------------------
# The shape of a phase that has finished everything it owed
# --------------------------------------------------------------------------


def pending_criteria(count: int = 5) -> list[dict]:
    """A phase's criteria as they read BEFORE it is accepted: unscored.

    The registry is where the acceptance is recorded, so before the record is
    written every criterion reads PENDING with no evidence attached. That is the
    state the defect was found in, and the state these tests start from.
    """
    names = [
        "behaviour_landed",
        "guards_hold",
        "external_verification_green_on_the_accepted_tree",
        "independent_phase_review_by_a_non_builder",
        "carried_residuals_recorded_and_nonblocking",
    ]
    return [
        criterion(
            f"AC-{index + 1}",
            names[index] if index < len(names) else f"surface_{index + 1}_holds",
            result="PENDING",
            evidence="",
        )
        for index in range(count)
    ]


def registry_units(target: str = "P9", successor: str = "P10") -> list[dict]:
    """One unit the repository already accepted, and one waiting on this phase."""
    return [
        accepted_unit("P8", next_units=(target,)),
        pending_unit(successor, dependencies=(target,)),
    ]


def closed_phase(
    tmp_path: Path,
    *,
    criteria: list[dict] | None = None,
    extra_units: list[dict] | None = None,
    **kwargs,
) -> PhaseClosureController:
    """A phase taken to READY_FOR_ACCEPTANCE_COMMIT with nothing outstanding."""
    rows = pending_criteria() if criteria is None else criteria
    repo = phase_repo(
        tmp_path,
        criteria=rows,
        extra_units=registry_units() if extra_units is None else extra_units,
        **kwargs,
    )
    control = PhaseClosureController(repo, phase_id="P9", builder_session_ids=["builder-1"])
    control.preflight()
    control.record_external_evidence(
        evidence_from_payload(
            {"sha": head(repo), "status": "completed", "conclusion": "success"}
        )
    )
    control.ingest_review(
        supporting_review(
            [row["id"] for row in rows],
            reviewed_fingerprint=capture_fingerprint(repo).to_dict(),
        )
    )
    control.decide()
    assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
    return control


def dirty(repo: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "-uall"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line[3:].strip() for line in proc.stdout.splitlines() if line.strip()]


def registry(repo: Path) -> dict:
    return yaml.safe_load((repo / REGISTRY_REL).read_text(encoding="utf-8"))


def unit(repo: Path, unit_id: str) -> dict:
    return next(u for u in registry(repo)["units"] if u["unit_id"] == unit_id)


# --------------------------------------------------------------------------
# 1. the whole point: the last state produces a real diff
# --------------------------------------------------------------------------


class TestAFinishedPhaseReachesARecord:
    def test_the_closure_creates_an_acceptance_record_diff(self, tmp_path: Path) -> None:
        control = closed_phase(tmp_path)
        assert dirty(control.repo) == [], "the candidate tree starts clean"

        plan = control.materialize_acceptance_record()

        assert plan.permitted, plan.refusal or plan.authority_gap
        assert plan.written
        assert dirty(control.repo) == [REGISTRY_REL]

    def test_seventeen_criteria_reach_the_same_record(self, tmp_path: Path) -> None:
        """Not five. The run this closes carried seventeen, all required."""
        rows = pending_criteria(17)
        control = closed_phase(tmp_path, criteria=rows)
        assert len(control.record.criteria.required) == 17

        control.materialize_acceptance_record()

        recorded = unit(control.repo, "P9")["acceptance_criteria"]
        assert [row["result"] for row in recorded] == ["PASS"] * 17

    def test_the_record_says_accepted_in_the_repositorys_own_words(
        self, tmp_path: Path
    ) -> None:
        control = closed_phase(tmp_path)
        control.materialize_acceptance_record()

        accepted = unit(control.repo, "P9")
        precedent = unit(control.repo, "P8")
        assert accepted["status"] == precedent["status"]
        assert accepted["execution_state"] == precedent["execution_state"]
        assert accepted["checkpoint_state"] == precedent["checkpoint_state"]

    def test_the_commit_preparation_then_has_something_to_validate(
        self, tmp_path: Path
    ) -> None:
        """The exact refusal this change exists to close."""
        control = closed_phase(tmp_path)
        before = plan_acceptance_commit(control.repo, phase_id="P9")
        assert "nothing to commit" in before.refusal

        control.materialize_acceptance_record()

        after = plan_acceptance_commit(control.repo, phase_id="P9")
        assert after.refusal == ""
        assert after.permitted
        assert [p.path for p in after.allowed_paths] == [REGISTRY_REL]

    def test_nothing_is_staged_and_nothing_is_committed(self, tmp_path: Path) -> None:
        control = closed_phase(tmp_path)
        before = head(control.repo)

        control.materialize_acceptance_record()

        assert head(control.repo) == before, "the driver does not commit"
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=control.repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert staged.strip() == "", "the driver does not stage either"


# --------------------------------------------------------------------------
# 2. only the acceptance record moves
# --------------------------------------------------------------------------


class TestOnlyTheAcceptanceRecordMayMove:
    def test_every_changed_path_classifies_as_the_acceptance_record(
        self, tmp_path: Path
    ) -> None:
        control = closed_phase(tmp_path)
        control.materialize_acceptance_record()

        changed = dirty(control.repo)
        assert changed
        assert all(classify_surface(p) is Surface.ACCEPTANCE_RECORD for p in changed), changed

    @pytest.mark.parametrize(
        "path, surface",
        [
            ("src/product.py", Surface.RUNTIME),
            ("eval/tests/test_behaviour.py", Surface.TEST),
            ("migrations/0007_add_column.sql", Surface.MIGRATION),
            (".github/workflows/ci.yml", Surface.CI),
            ("docs/specifications/acceptance/registry.md", Surface.SPECIFICATION),
        ],
    )
    def test_a_change_outside_the_record_refuses_the_whole_write(
        self, tmp_path: Path, path: str, surface: Surface
    ) -> None:
        """All-or-nothing, and BEFORE the write rather than after.

        A status edit laid on top of an unverified runtime change produces a
        commit whose message says the phase is accepted over code nothing
        checked. The refusal names the file and its surface so the founder knows
        what to do with it.
        """
        control = closed_phase(tmp_path)
        target = control.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("touched by something outside the acceptance\n", encoding="utf-8")
        registry_before = (control.repo / REGISTRY_REL).read_text(encoding="utf-8")

        plan = control.materialize_acceptance_record()

        assert not plan.permitted
        assert not plan.written
        assert path in plan.refusal and surface.value in plan.refusal
        assert (control.repo / REGISTRY_REL).read_text(encoding="utf-8") == registry_before

    def test_a_registry_outside_the_record_surface_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The classifier decides, not the fact that it is called a registry."""
        control = closed_phase(tmp_path)
        plan = plan_acceptance_record(
            control.repo,
            phase_id="P9",
            criteria=control.record.criteria,
            adjudication=control.record.adjudication,
            candidate=control.record.fingerprint(),
            registry_paths=["src/registry.yaml"],
        )
        assert "RUNTIME" in plan.refusal or "no readable unit registry" in plan.authority_gap


# --------------------------------------------------------------------------
# 3. the record does not retire the evidence it records
# --------------------------------------------------------------------------


class TestTheRecordDoesNotInvalidateTheEvidence:
    def test_the_external_record_and_the_adjudication_both_survive(
        self, tmp_path: Path
    ) -> None:
        control = closed_phase(tmp_path)
        sha = control.record.external_evidence.sha
        reviewer = control.record.adjudication.reviewer_session_id

        control.materialize_acceptance_record()

        assert control.record.external_evidence is not None
        assert control.record.external_evidence.sha == sha
        assert control.record.adjudication is not None
        assert control.record.adjudication.reviewer_session_id == reviewer

    def test_no_evidence_is_retired_as_stale(self, tmp_path: Path) -> None:
        control = closed_phase(tmp_path)
        control.materialize_acceptance_record()

        stale = [
            f
            for f in control.record.findings
            if f.classification is FindingClass.STALE_VERIFICATION
        ]
        assert stale == [], [f.brief() for f in stale]

    def test_the_evidence_still_points_at_the_accepted_product(
        self, tmp_path: Path
    ) -> None:
        """The tree moved, and the evidence moved with it rather than dying.

        The commit and the tree are untouched by a status edit — only the
        working-tree digest moves — so the measurements still describe the same
        product, and the record says so in its own history.
        """
        control = closed_phase(tmp_path)
        before = control.record.fingerprint()

        control.materialize_acceptance_record()

        after = control.record.fingerprint()
        assert after.head == before.head and after.tree == before.tree
        assert after.identity != before.identity
        assert all(
            ref.observed_at_tree != before.identity
            for ref in control.record.evidence.refs
            if not ref.stale
        )
        assert any("acceptance record itself" in line for line in control.record.history)

    def test_the_phase_is_still_ready_afterwards(self, tmp_path: Path) -> None:
        control = closed_phase(tmp_path)
        control.materialize_acceptance_record()
        assert control.decide() is ClosureState.READY_FOR_ACCEPTANCE_COMMIT

    def test_the_evidence_names_the_accepted_commit_and_tree(
        self, tmp_path: Path
    ) -> None:
        control = closed_phase(tmp_path)
        candidate = control.record.fingerprint()

        control.materialize_acceptance_record()

        for row in unit(control.repo, "P9")["acceptance_criteria"]:
            written = row["adjudication_evidence"]
            assert candidate.head[:12] in written
            assert candidate.tree[:12] in written


# --------------------------------------------------------------------------
# 4. the next unit moves only where the repository says it does
# --------------------------------------------------------------------------


class TestTheNextUnitAdvancesOnlyOnTheRepositorysAuthority:
    def test_it_advances_where_the_repository_states_the_transition(
        self, tmp_path: Path
    ) -> None:
        control = closed_phase(tmp_path)
        assert unit(control.repo, "P10")["status"] == "BLOCKED"

        plan = control.materialize_acceptance_record()

        assert plan.next_phase_advanced == "P10"
        assert unit(control.repo, "P10")["status"] == unit(control.repo, "P9")["status"] or True
        assert unit(control.repo, "P10")["status"] == "READY"

    def test_it_takes_the_token_the_repository_itself_used(
        self, tmp_path: Path
    ) -> None:
        """SELECTED, not READY, when SELECTED is what this repository writes."""
        units = [
            accepted_unit("P8", next_units=("P9",)),
            pending_unit("P10", dependencies=("P9",)),
        ]
        control = closed_phase(tmp_path, extra_units=units, status="SELECTED")

        control.materialize_acceptance_record()

        assert unit(control.repo, "P10")["status"] == "SELECTED"

    def test_a_repository_that_states_no_transition_advances_nothing(
        self, tmp_path: Path
    ) -> None:
        units = [
            accepted_unit("P8", next_units=()),
            pending_unit("P10", dependencies=("P9",)),
        ]
        control = closed_phase(tmp_path, extra_units=units)

        plan = control.materialize_acceptance_record()

        assert plan.written, "the phase itself is still recorded as accepted"
        assert plan.next_phase_advanced == ""
        assert unit(control.repo, "P10")["status"] == "BLOCKED"
        assert any("founder decision" in note for note in plan.notes)

    def test_a_successor_with_an_outstanding_blocker_does_not_advance(
        self, tmp_path: Path
    ) -> None:
        units = [
            accepted_unit("P8", next_units=("P9",)),
            pending_unit("P10", dependencies=("P9",), blockers=("a decision nobody has taken",)),
        ]
        control = closed_phase(tmp_path, extra_units=units)

        plan = control.materialize_acceptance_record()

        assert plan.next_phase_advanced == ""
        assert unit(control.repo, "P10")["status"] == "BLOCKED"
        assert any("blocker" in note for note in plan.notes)

    def test_a_successor_still_waiting_on_something_else_does_not_advance(
        self, tmp_path: Path
    ) -> None:
        units = [
            accepted_unit("P8", next_units=("P9",)),
            pending_unit("P10", dependencies=("P9", "P11")),
            pending_unit("P11", dependencies=()),
        ]
        control = closed_phase(tmp_path, extra_units=units)

        plan = control.materialize_acceptance_record()

        assert plan.next_phase_advanced == ""
        assert unit(control.repo, "P10")["status"] == "BLOCKED"
        assert any("P11" in note for note in plan.notes)


# --------------------------------------------------------------------------
# 5. a repository that has not said how it records an acceptance
# --------------------------------------------------------------------------


class TestMissingAuthorityStopsRatherThanInvents:
    def test_no_accepted_unit_anywhere_is_an_authority_gap(
        self, tmp_path: Path
    ) -> None:
        control = closed_phase(
            tmp_path, extra_units=[pending_unit("P10", dependencies=("P9",))]
        )
        before = (control.repo / REGISTRY_REL).read_text(encoding="utf-8")

        plan = control.materialize_acceptance_record()

        assert plan.authority_gap
        assert not plan.written
        assert (control.repo / REGISTRY_REL).read_text(encoding="utf-8") == before
        assert control.record.state is ClosureState.AUTHORITY_GAP

    def test_the_gap_is_reported_as_a_founder_decision(self, tmp_path: Path) -> None:
        control = closed_phase(
            tmp_path, extra_units=[pending_unit("P10", dependencies=("P9",))]
        )
        control.materialize_acceptance_record()

        gaps = control.record.authority_gaps
        assert gaps, "an unstated acceptance format is an authority gap, not a refusal"
        assert any("founder or architect" in g.closure_condition for g in gaps)

    def test_accepted_units_that_disagree_are_a_gap_rather_than_a_vote(
        self, tmp_path: Path
    ) -> None:
        units = [
            accepted_unit("P8", next_units=("P9",)),
            accepted_unit("P7X", next_units=(), checkpoint_state="ACCEPTED"),
            pending_unit("P10", dependencies=("P9",)),
        ]
        control = closed_phase(tmp_path, extra_units=units)

        plan = control.materialize_acceptance_record()

        assert "disagree" in plan.authority_gap
        assert not plan.written

    def test_an_unscored_precedent_cannot_say_what_passing_looks_like(
        self, tmp_path: Path
    ) -> None:
        units = [
            accepted_unit("P8", next_units=("P9",), with_result=False),
            pending_unit("P10", dependencies=("P9",)),
        ]
        control = closed_phase(tmp_path, extra_units=units)

        plan = control.materialize_acceptance_record()

        assert plan.authority_gap
        assert not plan.written

    def test_a_record_somebody_else_wrote_is_still_validated(
        self, tmp_path: Path
    ) -> None:
        """A gap stops Product Driver from WRITING, not the phase from closing.

        Where the repository has not stated how an acceptance is recorded and a
        founder or a builder session already wrote one, the record exists. The
        phase does not stop: the change is handed to the classifier, which is
        the half of this that was never in doubt.
        """
        control = closed_phase(
            tmp_path, extra_units=[pending_unit("P10", dependencies=("P9",))]
        )
        (control.repo / "docs" / "implementation" / "CURRENT.md").write_text(
            "P9 accepted\n", encoding="utf-8"
        )

        plan = control.materialize_acceptance_record()

        assert plan.authority_gap
        assert plan.existing_record == ["docs/implementation/CURRENT.md"]
        assert control.record.state is ClosureState.READY_FOR_ACCEPTANCE_COMMIT
        assert plan_acceptance_commit(control.repo, phase_id="P9").permitted

    def test_two_pass_tokens_among_accepted_units_is_a_gap(
        self, tmp_path: Path
    ) -> None:
        """With no predecessor to read, every accepted unit has to agree."""
        units = [
            accepted_unit("P8", next_units=()),
            accepted_unit("P7X", next_units=(), result="SATISFIED"),
            pending_unit("P10", dependencies=("P9",)),
        ]
        control = closed_phase(tmp_path, extra_units=units)

        plan = control.materialize_acceptance_record()

        assert "means PASS" in plan.authority_gap
        assert not plan.written

    def test_an_older_convention_does_not_outvote_the_unit_this_one_follows(
        self, tmp_path: Path
    ) -> None:
        """A long-lived repository carries records written under earlier rules.

        Its own graph says which one is current: the accepted unit whose "what
        this unlocks" names the phase being accepted. Reading the majority
        instead would write the convention the repository has moved on from,
        and there are always more old records than new ones.
        """
        units = [
            accepted_unit("P8", next_units=("P9",), result="PASS",
                          evidence_key="adjudication_evidence"),
            accepted_unit("P7X", next_units=(), result="OK", evidence_key="evidence"),
            accepted_unit("P6X", next_units=(), result="OK", evidence_key="evidence"),
            pending_unit("P10", dependencies=("P9",)),
        ]
        control = closed_phase(tmp_path, extra_units=units)

        plan = control.materialize_acceptance_record()

        assert plan.written, plan.authority_gap or plan.refusal
        rows = unit(control.repo, "P9")["acceptance_criteria"]
        assert {row["result"] for row in rows} == {"PASS"}
        assert all("adjudication_evidence" in row for row in rows)
        assert all("evidence" not in row for row in rows)


# --------------------------------------------------------------------------
# 6. running it again changes nothing
# --------------------------------------------------------------------------


class TestRunningTheClosureAgainIsIdempotent:
    def test_the_second_write_is_a_no_op(self, tmp_path: Path) -> None:
        control = closed_phase(tmp_path)
        control.materialize_acceptance_record()
        after_first = (control.repo / REGISTRY_REL).read_text(encoding="utf-8")

        again = control.materialize_acceptance_record()

        assert again.already_recorded
        assert not again.edits
        assert (control.repo / REGISTRY_REL).read_text(encoding="utf-8") == after_first

    def test_the_record_is_not_duplicated_or_corrupted(self, tmp_path: Path) -> None:
        control = closed_phase(tmp_path)
        control.materialize_acceptance_record()
        control.materialize_acceptance_record()
        control.materialize_acceptance_record()

        rows = unit(control.repo, "P9")["acceptance_criteria"]
        assert len(rows) == 5
        assert [row["id"] for row in rows] == [f"AC-{i}" for i in range(1, 6)]
        assert all(
            str(row["adjudication_evidence"]).count("Adjudicated") == 1 for row in rows
        )
        assert len(registry(control.repo)["units"]) == 3

    def test_a_fresh_closure_over_the_written_record_reports_already_accepted(
        self, tmp_path: Path
    ) -> None:
        """The repository now says so, so a second attempt has nothing to do."""
        control = closed_phase(tmp_path)
        control.materialize_acceptance_record()

        second = PhaseClosureController(
            control.repo, phase_id="P9", builder_session_ids=["builder-1"]
        )
        second.preflight()
        assert second.decide() is ClosureState.ALREADY_ACCEPTED


# --------------------------------------------------------------------------
# 7. the record may only say what was adjudicated
# --------------------------------------------------------------------------


class TestTheRecordSaysOnlyWhatWasEstablished:
    def test_it_writes_no_criterion_the_repository_did_not_declare(
        self, tmp_path: Path
    ) -> None:
        control = closed_phase(tmp_path)
        declared = [row["id"] for row in unit(control.repo, "P9")["acceptance_criteria"]]

        control.materialize_acceptance_record()

        assert [
            row["id"] for row in unit(control.repo, "P9")["acceptance_criteria"]
        ] == declared

    def test_an_unadjudicated_required_criterion_refuses_the_write(
        self, tmp_path: Path
    ) -> None:
        control = closed_phase(tmp_path)
        control.record.adjudication.criterion_results = [
            r for r in control.record.adjudication.criterion_results if r.criterion_id != "AC-3"
        ]
        before = (control.repo / REGISTRY_REL).read_text(encoding="utf-8")

        plan = plan_acceptance_record(
            control.repo,
            phase_id="P9",
            criteria=control.record.criteria,
            adjudication=control.record.adjudication,
            candidate=control.record.fingerprint(),
        )

        assert "AC-3" in plan.refusal
        assert not plan.permitted
        assert (control.repo / REGISTRY_REL).read_text(encoding="utf-8") == before

    def test_it_is_refused_before_the_phase_is_ready(self, tmp_path: Path) -> None:
        repo = phase_repo(
            tmp_path, criteria=pending_criteria(), extra_units=registry_units()
        )
        control = PhaseClosureController(repo, phase_id="P9", builder_session_ids=["b"])
        control.preflight()

        plan = control.materialize_acceptance_record()

        assert not plan.permitted
        assert "READY_FOR_ACCEPTANCE_COMMIT" in plan.refusal
        assert dirty(repo) == []


# --------------------------------------------------------------------------
# 8. the rule is about repositories, not about any one product
# --------------------------------------------------------------------------

#: Tokens from the run that found this. A writer that had to know them would be
#: a patch over one repository rather than a rule about acceptance records.
_INCIDENT_TOKENS = re.compile(
    r"(?i)(?:neyma|freight|ops_control|\bP7\b|P7-AC-\d+|20260911-\d+|a9050b69|"
    r"IMPLEMENTATION-REGISTRY|PHASE_ACCEPTANCE_COMPLETE)"
)


def _code_only(path: Path) -> str:
    """The module with its comments and docstrings removed."""
    kept: list[str] = []
    previous = tokenize.INDENT
    with path.open(encoding="utf-8") as handle:
        for token in tokenize.generate_tokens(handle.readline):
            if token.type == tokenize.COMMENT:
                continue
            if token.type == tokenize.STRING and previous in (
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.NEWLINE,
                tokenize.NL,
                tokenize.ENCODING,
            ):
                continue
            previous = token.type
            kept.append(token.string)
    return "\n".join(kept)


class TestTheWriterNamesNoProduct:
    def test_the_module_encodes_no_product_identifier(self) -> None:
        found = _INCIDENT_TOKENS.findall(_code_only(Path(ar.__file__)))
        assert found == [], f"product-specific tokens in the acceptance-record writer: {found}"

    def test_the_guard_would_notice_one(self, tmp_path: Path) -> None:
        """Anti-vacuity: a guard that strips too much passes by seeing nothing."""
        planted = tmp_path / "planted.py"
        planted.write_text("STATE = 'PHASE_ACCEPTANCE_COMPLETE'\n", encoding="utf-8")
        assert _INCIDENT_TOKENS.findall(_code_only(planted))

    def test_every_written_value_comes_from_the_repository(
        self, tmp_path: Path
    ) -> None:
        """A repository with its own vocabulary gets its own vocabulary back."""
        units = [
            accepted_unit(
                "P8",
                next_units=("P9",),
                status="ACCEPTED",
                execution_state="FINISHED",
                checkpoint_state="SIGNED_OFF",
                result="SATISFIED",
            ),
            pending_unit("P10", dependencies=("P9",)),
        ]
        control = closed_phase(tmp_path, extra_units=units)

        control.materialize_acceptance_record()

        accepted = unit(control.repo, "P9")
        assert accepted["status"] == "ACCEPTED"
        assert accepted["execution_state"] == "FINISHED"
        assert accepted["checkpoint_state"] == "SIGNED_OFF"
        assert {row["result"] for row in accepted["acceptance_criteria"]} == {"SATISFIED"}

    def test_an_unrecognisable_accepted_state_is_a_gap_rather_than_a_guess(
        self, tmp_path: Path
    ) -> None:
        """A repository whose "accepted" this driver cannot read gets no record.

        ``already_accepted`` reads one small vocabulary across the whole driver,
        and a repository outside it is one whose acceptance state Product Driver
        cannot recognise anywhere — not merely here. Deciding that ``DONE`` must
        be what this repository means by accepted is exactly the inference the
        closure policy forbids, so the answer is the founder's.
        """
        units = [
            accepted_unit("P8", next_units=("P9",), status="DONE",
                          execution_state="DONE", checkpoint_state="DONE"),
            pending_unit("P10", dependencies=("P9",)),
        ]
        control = closed_phase(tmp_path, extra_units=units)

        plan = control.materialize_acceptance_record()

        assert plan.authority_gap
        assert not plan.written
        assert unit(control.repo, "P9")["status"] == "READY"

    def test_the_convention_is_read_rather_than_assumed(self, tmp_path: Path) -> None:
        repo = phase_repo(
            tmp_path,
            criteria=pending_criteria(),
            extra_units=[
                accepted_unit("P8", next_units=("P9",), result="OK"),
                pending_unit("P10", dependencies=("P9",)),
            ],
        )
        units = registry(repo)["units"]
        convention = derive_convention(units, units[0])

        assert convention.problem == ""
        assert convention.pass_token == "OK"
        assert convention.state_values["status"] == "COMPLETE"
        assert convention.successor_token == "READY"
        assert convention.derivation, "it says where each value came from"


# --------------------------------------------------------------------------
# 9. the file is edited, never regenerated
# --------------------------------------------------------------------------


class TestTheRegistryIsEditedRatherThanRewritten:
    def test_a_comment_beside_an_untouched_field_survives(self, tmp_path: Path) -> None:
        """A registry is prose as much as data. Re-serializing deletes all of it."""
        control = closed_phase(tmp_path)
        path = control.repo / REGISTRY_REL
        text = path.read_text(encoding="utf-8")
        marked = text.replace(
            "- unit_id: P9\n",
            "# ### THE REASON THIS UNIT READS THE WAY IT DOES, KEPT DELIBERATELY.\n- unit_id: P9\n",
            1,
        )
        path.write_text(marked, encoding="utf-8")
        subprocess.run(["git", "commit", "-qam", "a comment"], cwd=control.repo, check=True)

        control.materialize_acceptance_record()

        after = path.read_text(encoding="utf-8")
        assert "KEPT DELIBERATELY" in after

    def test_untouched_lines_are_untouched(self, tmp_path: Path) -> None:
        control = closed_phase(tmp_path)
        before = (control.repo / REGISTRY_REL).read_text(encoding="utf-8").splitlines()

        control.materialize_acceptance_record()

        diff = subprocess.run(
            ["git", "diff", "--numstat", "--", REGISTRY_REL],
            cwd=control.repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        added, removed = int(diff[0]), int(diff[1])
        assert removed == 14, (
            "three unit fields, five results, five empty evidence rows, one successor"
        )
        assert added <= removed + 12, "folded evidence wraps; nothing else grows"
        assert len(before) > removed

    def test_an_edit_that_would_change_anything_else_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The last guard: the rendered file is parsed and compared before it lands.

        If the line editor ever moved something this module did not intend, the
        comparison catches it and the whole write is abandoned — a registry is
        not a file to be half-right about.
        """
        control = closed_phase(tmp_path)
        original = ar._folded

        def sabotage(indent: int, key: str, text: str) -> list[str]:
            return original(indent, key, text) + [" " * indent + "smuggled: true"]

        monkeypatch.setattr(ar, "_folded", sabotage)
        before = (control.repo / REGISTRY_REL).read_text(encoding="utf-8")

        plan = control.materialize_acceptance_record()

        assert not plan.permitted
        assert "did not come out as intended" in plan.refusal
        assert (control.repo / REGISTRY_REL).read_text(encoding="utf-8") == before
