"""What a run was asked for, and whether it may say it did it.

Three defects observed on one real run, pinned here with synthetic ids so
nothing in this file depends on any particular product, phase or criterion
naming convention.

**Scope resolution read a criterion as a build unit.** The task said "build the
phase completely, according to its criteria CRIT-1 through CRIT-5". Two things
went wrong at once: "completely" was not recognised as completion intent
(``complet(?:e|ing|ion)`` refuses "completely", because the next character is a
word character), and ``CRIT-1`` — an acceptance criterion the repository
declares — was read as the nested unit the run had been asked to build. The run
then reported on a task nobody had set.

**Completion consistency.** A run whose task IS the phase reported its task
VERIFIED with nothing missing while the repository recorded most of that phase's
required criteria as unsatisfied, because every check in the auditor compared a
CLAIM against the repository, and a modest builder report makes no claim. The
question "is the declared task done?" was not asked anywhere.

**Resume.** ``--resume-run`` did not default to the task the run had recorded,
and an explicit ``--task`` silently replaced it — so a resume could narrow,
widen or swap the job the evidence was being gathered against.

Every repository here is built in a temporary directory. Nothing reads or
touches a real product repository.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest
import yaml

from neyma_product_driver.cli import _resolve_run_task, build_parser
from neyma_product_driver.completion_auditor import AuditDecision, CompletionAuditor
from neyma_product_driver.context import RepositoryContextLoader
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import RunState
from neyma_product_driver.task_scope import (
    ScopeLevel,
    TaskResult,
    resolve_task_scope,
    task_identity_differs,
)

# --------------------------------------------------------------------------
# A synthetic repository: one phase, nested units inside it, criteria beside it
# --------------------------------------------------------------------------

PHASE = "Q4"
#: Ids the repository declares as ACCEPTANCE CRITERIA — the phase's measuring
#: stick. Deliberately shaped exactly like a nested unit id, because that is the
#: whole difficulty: `Q4-CRIT-1` and `Q4-W2` are indistinguishable by shape.
CRITERIA = [f"{PHASE}-CRIT-{n}" for n in range(1, 6)]
#: An id the repository declares as a nested BUILD UNIT.
UNIT = "W2"


class SyntheticRepo:
    """A repository with one phase in progress, units inside it, criteria on it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.impl = root / "docs" / "implementation"
        self.impl.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
        (root / "CLAUDE.md").write_text(
            "# CLAUDE.md\n\nimplement -> targeted tests -> diff review -> commit -> push -> CI\n"
        )
        self.write_current(
            f"# CURRENT\n\n| Phase | Status |\n|---|---|\n| {PHASE} | IN PROGRESS |\n\n"
            f"The widget ({PHASE}-{UNIT}) is the unit being built. The rest remain.\n"
        )
        self.write_registry()
        (root / "src").mkdir()
        (root / "src" / "widget.py").write_text("# the unit under construction\n")
        self.commit_all("init")

    # -- pieces ----------------------------------------------------------

    def criteria(self, passing: int = 0, *, optional: list[str] | None = None) -> list[dict]:
        optional = optional or []
        rows = []
        for index, cid in enumerate(CRITERIA):
            row = {
                "id": cid,
                "criterion": f"criterion_{index + 1}",
                "weight": 20,
                "result": "PASS" if index < passing else "PENDING",
            }
            if cid in optional:
                row["required"] = False
            rows.append(row)
        return rows

    def write_registry(
        self,
        *,
        criteria: list[dict] | None = None,
        criterion_key: str = "acceptance_criteria",
    ) -> None:
        unit = {
            "unit_id": PHASE,
            "name": "the fourth quarter's machines",
            "status": "READY",
            "execution_state": "IN_PROGRESS",
            "objective": "the widget, the gauge and the brake",
        }
        unit[criterion_key] = criteria if criteria is not None else self.criteria()
        (self.impl / "IMPLEMENTATION-REGISTRY.yaml").write_text(
            yaml.safe_dump({"meta": {}, "units": [unit]}, sort_keys=False)
        )

    def write_current(self, text: str) -> None:
        (self.impl / "CURRENT.md").write_text(text)

    def commit_all(self, message: str = "work") -> None:
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=self.root, check=True)

    # -- convenience -----------------------------------------------------

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
def repo(tmp_path: Path) -> SyntheticRepo:
    return SyntheticRepo(tmp_path / "product")


WHOLE_PHASE_TASK = (
    f"Build {PHASE} completely according to the repository's current authoritative "
    f"{PHASE} scope and its explicit {PHASE}-CRIT-1 through {PHASE}-CRIT-5 acceptance "
    "criteria. Implement the widget, the gauge and the brake; preserve every earlier "
    "guarantee. Do not open unrelated cleanup campaigns."
)

NESTED_TASK = (
    f"# Build {PHASE} / {UNIT} — the widget. Only that.\n\n"
    "Implement the canonical widget specification: one row, one machine, four "
    "states. Do not begin the gauge."
)

HONEST_SUBSET_REPORT = """\
## What a user can now do

The widget accepts a request and refuses a second one for the same key, so a
retry cannot produce two of them.

## What proves it

41 targeted tests pass; the mutation battery caught 6 of 6.

## What is knowingly incomplete

The gauge and the brake remain. The phase has not reached acceptance and no
criterion is scored.
"""


# --------------------------------------------------------------------------
# 1. Scope resolution: a criterion is not a build unit
# --------------------------------------------------------------------------


class TestCriterionIsNotAUnit:
    def test_a_whole_phase_task_that_enumerates_its_criteria_stays_phase_scope(
        self, repo: SyntheticRepo
    ) -> None:
        scope = repo.scope(WHOLE_PHASE_TASK)
        assert scope.level is ScopeLevel.PHASE
        assert scope.scope_id == PHASE
        assert scope.claims_phase_completion is True
        assert scope.phase_completion_requested is True
        assert not scope.is_nested

    def test_the_refusal_is_recorded_with_the_authority_behind_it(
        self, repo: SyntheticRepo
    ) -> None:
        derivation = " | ".join(repo.scope(WHOLE_PHASE_TASK).derivation)
        assert f"{PHASE}-CRIT-1 is not read as a build unit" in derivation
        assert "acceptance criterion" in derivation
        assert "IMPLEMENTATION-REGISTRY.yaml" in derivation

    def test_a_genuine_nested_unit_task_stays_nested(self, repo: SyntheticRepo) -> None:
        scope = repo.scope(NESTED_TASK)
        assert scope.level is ScopeLevel.TASK
        assert scope.scope_id == f"{PHASE}/{UNIT}"
        assert scope.is_nested
        assert scope.claims_phase_completion is False
        assert scope.parent_phase_execution_state == "IN_PROGRESS"

    def test_the_repository_decides_which_ids_are_criteria(self, tmp_path: Path) -> None:
        """Nothing here knows that `CRIT` means criterion.

        The same task, against a repository whose criteria are named in prose
        rather than by id, has no authority saying `Q4-CRIT-3` is a criterion —
        and with the criterion noun removed from the sentence there is nothing
        else saying it either. It is then read as the unit it looks like. That
        is the correct behaviour: the discrimination is the repository's, not a
        list of prefixes kept in this driver.
        """
        repo = SyntheticRepo(tmp_path / "prose")
        repo.write_registry(
            criteria=[
                {"criterion": "core_implementation", "weight": 60, "result": "PENDING"},
                {"criterion": "required_tests", "weight": 40, "result": "PENDING"},
            ]
        )
        repo.commit_all("criteria named in prose")
        scope = repo.scope(f"Build {PHASE}-CRIT-3, the third slice, and nothing else.")
        assert scope.scope_id == f"{PHASE}/CRIT-3"
        assert scope.is_nested

        # And with the repository declaring them as ids, the same sentence is
        # refused — same driver, same task, different authority.
        repo.write_registry()
        repo.commit_all("criteria named by id")
        assert repo.scope(f"Build {PHASE}-CRIT-3, the third slice, and nothing else.").level is (
            ScopeLevel.PHASE
        )

    def test_an_id_outside_the_declared_range_is_still_recognised_by_its_family(
        self, repo: SyntheticRepo
    ) -> None:
        # The repository declares CRIT-1..5. CRIT-9 is not declared and is
        # plainly the same kind of thing.
        scope = repo.scope(f"Start on {PHASE}-CRIT-9.")
        assert scope.level is ScopeLevel.PHASE

    def test_an_enumerated_set_of_units_is_not_one_nested_unit_either(
        self, repo: SyntheticRepo
    ) -> None:
        """A run builds one nested unit, or it is at the phase's bar.

        "Build W2 and W3" names two, and picking the first would silently halve
        the job. The strict reading — the phase — is the only safe one, and the
        derivation says which of the two reasons applied.
        """
        scope = repo.scope(f"Build {PHASE}/{UNIT} and {PHASE}/W3 together.")
        assert scope.level is ScopeLevel.PHASE
        assert any("enumerates it as one of a range" in d for d in scope.derivation)

    def test_the_task_can_say_so_without_a_registry_at_all(self, repo: SyntheticRepo) -> None:
        """The criterion noun in the task is enough on its own."""
        scope = resolve_task_scope(WHOLE_PHASE_TASK, repo.unit(), None)
        assert scope.level is ScopeLevel.PHASE
        assert any("criterion noun" in d for d in scope.derivation)

    def test_a_criterion_reference_never_becomes_the_repositorys_unit_id(
        self, repo: SyntheticRepo
    ) -> None:
        # The corroboration step reads the status document for the id the
        # repository gives a unit. It must not hand back a criterion id.
        repo.write_current(
            f"# CURRENT\n\nCRIT-1 — see {PHASE}-CRIT-1 in the registry. "
            f"The widget is {PHASE}-{UNIT}.\n"
        )
        repo.commit_all("status names both")
        assert repo.scope(WHOLE_PHASE_TASK).repository_unit_id == ""


class TestPhaseCompletionIntent:
    @pytest.mark.parametrize(
        "phrasing",
        [
            f"Build {PHASE} completely.",
            f"Finish {PHASE}.",
            f"Complete {PHASE} and take it to acceptance.",
            f"Implement {PHASE} in full.",
            f"Build the whole of {PHASE}.",
            f"Deliver {PHASE} fully.",
        ],
    )
    def test_explicit_completion_intent_is_read_as_phase_scope(
        self, repo: SyntheticRepo, phrasing: str
    ) -> None:
        scope = repo.scope(phrasing)
        assert scope.level is ScopeLevel.PHASE
        assert scope.phase_completion_requested is True

    def test_asking_for_the_phase_to_be_built_is_asking_for_the_phase(
        self, repo: SyntheticRepo
    ) -> None:
        scope = repo.scope(f"Build {PHASE}. Do not open unrelated work.")
        assert scope.level is ScopeLevel.PHASE
        assert scope.phase_completion_requested is True

    def test_building_a_unit_is_not_asking_for_the_phase(self, repo: SyntheticRepo) -> None:
        scope = repo.scope(NESTED_TASK)
        assert scope.phase_completion_requested is False

    def test_a_refusal_to_complete_the_phase_is_not_a_request_to(
        self, repo: SyntheticRepo
    ) -> None:
        scope = repo.scope(
            f"Build {PHASE} / {UNIT}. Do not complete {PHASE}, and do not score a criterion."
        )
        assert scope.is_nested
        assert scope.phase_completion_requested is False

    def test_a_unit_task_that_discusses_the_phase_at_length_stays_nested(
        self, repo: SyntheticRepo
    ) -> None:
        """The false positive that would make every real task file phase scope.

        A well-written unit task spends paragraphs saying what accepting the
        unit does NOT do — and every one of those paragraphs puts a completion
        word next to the phase id. If any of them counted, a run asked for one
        unit would be held to thirteen.
        """
        task = (
            f"# Build {PHASE} / {UNIT} — the widget. Only that.\n\n"
            f"- **No {PHASE} acceptance criterion is scored.** {PHASE} has not reached "
            "phase acceptance. **The next phase stays blocked.**\n\n"
            f"Accepting {UNIT} does **not** complete {PHASE}, does **not** score a "
            f"{PHASE} acceptance criterion, and unblocks nothing.\n"
        )
        scope = repo.scope(task)
        assert scope.scope_id == f"{PHASE}/{UNIT}"
        assert scope.is_nested
        assert scope.phase_completion_requested is False

    def test_a_title_that_names_the_unit_is_not_a_request_for_the_phase(
        self, repo: SyntheticRepo
    ) -> None:
        scope = repo.scope(f"# Build {PHASE} / {UNIT} — the widget. Only that.")
        assert scope.is_nested

    def test_completion_intent_outranks_a_unit_the_task_also_names(
        self, repo: SyntheticRepo
    ) -> None:
        scope = repo.scope(
            f"Finish {PHASE}. Start with {PHASE}/{UNIT}, then the gauge, then the brake."
        )
        assert scope.level is ScopeLevel.PHASE
        assert scope.phase_completion_requested is True


# --------------------------------------------------------------------------
# 2. Completion consistency: the declared task is what the run must answer for
# --------------------------------------------------------------------------


class TestPhaseTaskCompletionConsistency:
    @pytest.fixture
    def partial(self, repo: SyntheticRepo) -> SyntheticRepo:
        """Two of five required criteria scored; the subset's work is genuine."""
        repo.write_registry(criteria=repo.criteria(passing=2))
        repo.commit_all("two criteria scored")
        return repo

    def test_a_phase_task_with_criteria_outstanding_is_not_verified(
        self, partial: SyntheticRepo
    ) -> None:
        audit = partial.audit(HONEST_SUBSET_REPORT, WHOLE_PHASE_TASK)
        assert audit.decision is AuditDecision.UNPROVEN
        assert audit.completion is not None
        assert audit.completion.task_result is TaskResult.UNPROVEN
        assert audit.completion.task_outstanding

    def test_the_outstanding_portions_are_named_from_the_repository(
        self, partial: SyntheticRepo
    ) -> None:
        audit = partial.audit(HONEST_SUBSET_REPORT, WHOLE_PHASE_TASK)
        missing = " | ".join(audit.missing_evidence)
        for cid in CRITERIA[2:]:
            assert cid in missing
        # And the ones the repository records as scored are not reported owed.
        for cid in CRITERIA[:2]:
            assert cid not in missing

    def test_nothing_is_invented_when_the_repository_states_nothing(
        self, repo: SyntheticRepo
    ) -> None:
        repo.write_registry(criteria=[])
        repo.commit_all("no criteria declared")
        audit = repo.audit(HONEST_SUBSET_REPORT, WHOLE_PHASE_TASK)
        assert not [m for m in audit.missing_evidence if "required for" in m]

    def test_an_optional_criterion_does_not_hold_the_task_open(
        self, repo: SyntheticRepo
    ) -> None:
        repo.write_registry(
            criteria=repo.criteria(passing=4, optional=[CRITERIA[4]])
        )
        repo.commit_all("last criterion optional")
        audit = repo.audit(HONEST_SUBSET_REPORT, WHOLE_PHASE_TASK)
        assert not [m for m in audit.missing_evidence if CRITERIA[4] in m]

    def test_a_fully_scored_phase_leaves_nothing_outstanding(
        self, repo: SyntheticRepo
    ) -> None:
        repo.write_registry(criteria=repo.criteria(passing=5))
        repo.commit_all("all criteria scored")
        audit = repo.audit(HONEST_SUBSET_REPORT, WHOLE_PHASE_TASK)
        assert not [m for m in audit.missing_evidence if "required for" in m]

    def test_a_nested_task_is_not_held_to_the_phases_criteria(
        self, partial: SyntheticRepo
    ) -> None:
        """The over-correction this guard must not become.

        A run asked for one unit inside the phase is judged on that unit. Three
        pending phase criteria describe units it was told not to build.
        """
        audit = partial.audit(HONEST_SUBSET_REPORT, NESTED_TASK)
        assert not [m for m in audit.missing_evidence if "required for" in m]
        assert audit.decision is not AuditDecision.UNPROVEN

    def test_a_terse_task_is_not_held_to_a_phase_nobody_asked_for(
        self, partial: SyntheticRepo
    ) -> None:
        """`claims_phase_completion` is a strict default about CLAIMS.

        It says the phase's bar governs what this run may assert. It does not
        say the founder asked for the phase, and reading it that way would make
        every terse task unfinishable.
        """
        scope = partial.scope("Fix the flaky test and tidy the helper.")
        assert scope.claims_phase_completion is True
        assert scope.phase_completion_requested is False
        audit = CompletionAuditor(partial.root).audit(
            HONEST_SUBSET_REPORT, unit=partial.unit(), scope=scope
        )
        assert not [m for m in audit.missing_evidence if "required for" in m]

    def test_an_unfinished_phase_is_not_described_to_the_builder_as_dishonesty(
        self, partial: SyntheticRepo
    ) -> None:
        """The correction has to be the right instruction, not just a refusal.

        "Restore every status document to the highest evidence-supported state"
        is the answer to a builder that overclaimed. A builder that has honestly
        built two of five criteria has not overclaimed, and telling it to roll
        back status is both wrong and an accusation.
        """
        correction = partial.audit(HONEST_SUBSET_REPORT, WHOLE_PHASE_TASK).correction_prompt
        assert "honest rollback" not in correction
        assert "the phase is not finished" in correction
        assert "Keep building" in correction
        assert "scoring a criterion" in correction

    def test_the_false_green_this_exists_to_prevent(self, partial: SyntheticRepo) -> None:
        """The whole defect, in one assertion.

        A phase-completion task, a subset implemented, everything the subset
        owns passing, an honest builder report that claims nothing — and the run
        may not report the task VERIFIED with nothing missing.
        """
        audit = partial.audit(HONEST_SUBSET_REPORT, WHOLE_PHASE_TASK)
        verified_and_empty = (
            audit.decision is AuditDecision.VERIFIED and not audit.missing_evidence
        )
        assert not verified_and_empty, audit.summary_block()


# --------------------------------------------------------------------------
# 3. Resume: a saved run already knows its own task
# --------------------------------------------------------------------------


def args(**kw) -> argparse.Namespace:
    base = {"task": "", "override_task": False}
    base.update(kw)
    return argparse.Namespace(**base)


class _Config:
    task = "the config's default task"


class TestResumeTaskSemantics:
    def test_a_resume_defaults_to_the_runs_own_task(self) -> None:
        state = RunState(run_id="r1", task=WHOLE_PHASE_TASK)
        task, problem = _resolve_run_task(args(), _Config(), state)
        assert problem == ""
        assert task == WHOLE_PHASE_TASK

    def test_a_resume_with_a_materially_different_task_is_refused(self) -> None:
        state = RunState(run_id="r1", task=WHOLE_PHASE_TASK)
        task, problem = _resolve_run_task(args(task=NESTED_TASK), _Config(), state)
        assert task == ""
        assert "may not silently change" in problem
        assert "--override-task" in problem

    def test_the_same_task_typed_differently_is_the_same_task(self) -> None:
        state = RunState(run_id="r1", task=WHOLE_PHASE_TASK)
        retyped = "  " + WHOLE_PHASE_TASK.replace(" ", "  ").upper() + "\n"
        task, problem = _resolve_run_task(args(task=retyped), _Config(), state)
        assert problem == ""
        assert task == WHOLE_PHASE_TASK

    def test_an_override_is_deliberate_and_recorded(self, tmp_path: Path) -> None:
        store = EvidenceStore(tmp_path / "runs", "r1")
        state = RunState(run_id="r1", task=WHOLE_PHASE_TASK)
        task, problem = _resolve_run_task(
            args(task=NESTED_TASK, override_task=True), _Config(), state, store
        )
        assert problem == ""
        assert task == NESTED_TASK
        recorded = (store.run_dir / "task-override.json").read_text()
        assert "previous_task" in recorded
        assert WHOLE_PHASE_TASK[:40] in recorded

    def test_an_override_outside_a_resume_is_refused(self) -> None:
        task, problem = _resolve_run_task(args(task="x", override_task=True), _Config(), None)
        assert task == ""
        assert "only means something on a resume" in problem

    def test_a_fresh_run_still_takes_its_task_from_the_command_line(self) -> None:
        task, problem = _resolve_run_task(args(task=NESTED_TASK), _Config(), None)
        assert problem == ""
        assert task == NESTED_TASK

    def test_a_fresh_run_falls_back_to_the_configured_task(self) -> None:
        task, problem = _resolve_run_task(args(), _Config(), None)
        assert problem == ""
        assert task == "the config's default task"

    def test_the_flag_exists_on_the_run_command(self) -> None:
        parsed = build_parser().parse_args(
            ["run", "--resume-run", "r1", "--task", "x", "--override-task"]
        )
        assert parsed.override_task is True
        assert build_parser().parse_args(["run", "--resume-run", "r1"]).override_task is False

    def test_task_identity_is_compared_on_substance(self) -> None:
        assert not task_identity_differs("Build Q4 completely.", " build  q4 completely. ")
        assert task_identity_differs("Build Q4 completely.", "Build Q4/W2 only.")


# --------------------------------------------------------------------------
# 4. The founder summary says the same thing the audit says
# --------------------------------------------------------------------------


class _Gate:
    """A deterministic acceptance gate that verified every scenario it ran."""

    blocks_acceptance = False
    unverified: list[str] = []
    uncovered_risks: list[str] = []
    generation_problems: list[str] = []
    required_passed = 3
    required_total = 3

    def headline(self) -> str:
        return "3/3 required scenario(s) passed with resolvable evidence"


def _loop_result(repo: SyntheticRepo, audit):
    from neyma_product_driver.cli import LoopResult
    from neyma_product_driver.models import RunStatus

    return LoopResult(
        status=RunStatus.ACCEPTED,
        state=RunState(run_id="r1", task=WHOLE_PHASE_TASK),
        audit=audit,
        gate=_Gate(),
    )


class TestTheSummaryAgreesWithTheAudit:
    """Scope, audit, gate and summary must be talking about the same task.

    The gate answers "did the scenarios this run wrote pass". That is not the
    same question as "is the job the founder asked for finished", and a summary
    that derives shippability from the first alone prints READY TO SHIP over an
    audit that says required portions of the task are unbuilt.
    """

    def summary(self, repo: SyntheticRepo, report: str, task: str, capsys) -> str:
        from neyma_product_driver.cli import _report_founder_summary
        from neyma_product_driver.config import DriverConfig
        from neyma_product_driver.evidence import EvidenceStore

        audit = repo.audit(report, task)
        # A driver root beside the synthetic repository, so the run store this
        # writes lands in the test's own temporary directory and never in the
        # real `runs/` of whatever checkout the suite runs from.
        config = DriverConfig(
            neyma_repo=repo.root, driver_root=repo.root.parent / "driver", task=task
        )
        assert config.runs_dir is not None
        store = EvidenceStore(config.runs_dir, "summary-run")
        _report_founder_summary(_loop_result(repo, audit), store, config)
        return capsys.readouterr().out

    def test_a_phase_task_with_criteria_outstanding_is_not_ready_to_ship(
        self, repo: SyntheticRepo, capsys: pytest.CaptureFixture
    ) -> None:
        repo.write_registry(criteria=repo.criteria(passing=2))
        repo.commit_all("two of five scored")
        out = self.summary(repo, HONEST_SUBSET_REPORT, WHOLE_PHASE_TASK, capsys)
        # A whole-phase BUILD is headed by where it stands against phase
        # closure, not by shipping — and an unfinished one is not ready for it.
        assert "NOT READY FOR PHASE CLOSURE" in out
        assert "IMPLEMENTATION VERIFIED — READY" not in out
        assert "task completion:" in out
        assert CRITERIA[4] in out
        assert "the task this run declared is not finished" in out.lower()

    def test_the_same_run_still_reports_its_gate_honestly(
        self, repo: SyntheticRepo, capsys: pytest.CaptureFixture
    ) -> None:
        # The refusal must not erase what WAS established. Coverage is still
        # reported; what changes is the claim about the job.
        repo.write_registry(criteria=repo.criteria(passing=2))
        repo.commit_all("two of five scored")
        out = self.summary(repo, HONEST_SUBSET_REPORT, WHOLE_PHASE_TASK, capsys)
        assert "3/3 required scenario(s) passed" in out

    def test_a_nested_task_with_nothing_outstanding_still_reaches_ready_to_ship(
        self, repo: SyntheticRepo, capsys: pytest.CaptureFixture
    ) -> None:
        out = self.summary(repo, HONEST_SUBSET_REPORT, NESTED_TASK, capsys)
        assert "NOT READY TO SHIP" not in out
        assert "READY TO SHIP" in out


class TestTheJournalCannotClaimWhatTheAuditDenies:
    def test_verification_is_not_established_while_the_task_owes_something(self) -> None:
        from neyma_product_driver.run_journal import RunJournal

        journal = RunJournal(run_id="r1", task=WHOLE_PHASE_TASK, repo="/tmp/x")
        journal.run_status = "ACCEPTED"
        journal.gate_status = "VERIFIED"
        assert journal.verification_established is True
        journal.task_result = "UNPROVEN"
        journal.task_outstanding = [f"{CRITERIA[3]} is required and is recorded PENDING"]
        assert journal.verification_established is False
        assert CRITERIA[3] in "\n".join(journal.personal_summary_lines())
