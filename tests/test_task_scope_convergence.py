"""A risk belongs to the task, and its evidence belongs to the tree being judged.

Run 20260917-063502 built the U8.2 rule capability in one commit and, after a
resume, added one verification-only hardening commit. The resumed process
measured "what this run changed" from the HEAD it restarted on, so the judged
diff was one test file: no product module, no grounding, no reachability scan.
Three wordings of one ships-dark hypothesis each blocked acceptance as "names
too few concrete repository artifacts", while the task's own completion record
told the founder the next safe action was an independent review.

These tests hold the rule that replaced it:

* subject identity is the TASK's changed product modules, from the run's base;
* the measurement result is always the CURRENT tree's, never inherited;
* wordings of one hypothesis over the same resolved modules are one obligation;
* a review is not the next step while execution evidence is still open.

Synthetic repositories in a temporary directory; every session is faked.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from neyma_product_driver.changed_verification import (
    ChangedSurfaceVerification,
    verify_changed_surface,
)
from neyma_product_driver.cli import _changed_surface_verification, _pre_review_blockers
from neyma_product_driver.completion_auditor import (
    AuditDecision,
    CompletionAudit,
    hold_review_behind,
)
from neyma_product_driver.models import RunState, RunStatus
from neyma_product_driver.repo_verification import run_changed_files, task_base_commit
from neyma_product_driver.risk_grounding import (
    DARK,
    IMPORT_REACHABLE,
    ModuleReachability,
    ground,
    reachability_evidence,
)
from neyma_product_driver.scenario_gate import (
    BASIS_DRIVER_STRUCTURAL,
    GateStatus,
    evaluate_gate,
    risk_coverage,
)
from neyma_product_driver.scenario_plan import IdentifiedRisk
from neyma_product_driver.task_scope import ScopedCompletion, TaskResult, TaskScope

from test_risk_grounding import (
    GUARD_WITHOUT_CONTROL,
    LEDGER_DARK,
    SHIPS_DARK,
    SHIPS_DARK_REWORDED,
    _git,
    make_repo,
    risk,
    suite,
    unrelated_pass,
)

#: A third wording, as the resumed run generated it: names the file, a
#: milestone label and the package, none of which is a different subject.
SHIPS_DARK_CONCRETE = (
    "The U8.2 scheduler capability (scheduler.py / M12) is wired onto the live path — "
    "imported by a production module, a channel/adapter, a network or timer primitive, "
    "or reached from outside the pkg_core package — violating the ships-dark posture."
)


def harden(repo: Path) -> str:
    """A verification-only commit on top of the task's product commit."""
    path = repo / "tests" / "test_scheduler.py"
    path.write_text(path.read_text() + "\n\ndef test_hardening():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "harden: verification only")
    return _git(repo, "rev-parse", "HEAD")


def task_repo(tmp_path: Path, **kw) -> tuple[Path, str, str, str]:
    """(repo, task base, product commit, verification-only head).

    The only changed guard carries no positive control, so the repository's own
    guard cannot discharge anything and only the driver's scan can: the shape
    of the real run's third risk.
    """
    repo, base = make_repo(tmp_path, guard=GUARD_WITHOUT_CONTROL, **kw)
    product = _git(repo, "rev-parse", "HEAD")
    head = harden(repo)
    return repo, base, product, head


def task_record(repo: Path, base: str, head: str) -> ChangedSurfaceVerification:
    """The record the loop builds on ``head`` for a run whose base is ``base``."""
    return verify_changed_surface(
        repo, run_changed_files(repo, base), base_commit=base, commit=head
    )


def ships_dark_risks() -> list[IdentifiedRisk]:
    return [
        risk("R8", SHIPS_DARK),
        risk("RISK-R8-ships-dark", SHIPS_DARK_REWORDED),
        risk("R-SHIPS-DARK", SHIPS_DARK_CONCRETE),
    ]


# ==========================================================================
# 1 — the task's base survives a resume
# ==========================================================================


class TestTaskBase:
    def test_a_verification_only_commit_does_not_erase_the_task_product_modules(
        self, tmp_path
    ):
        repo, base, product, _head = task_repo(tmp_path)
        # From the resume point (the failure): the product module is gone.
        assert "pkg/scheduler.py" not in run_changed_files(repo, product)
        # From the task's own base: it is still what the task changed.
        assert "pkg/scheduler.py" in run_changed_files(repo, base)

    def test_a_recorded_base_is_kept(self, tmp_path):
        repo, base, _product, _head = task_repo(tmp_path)
        found, how = task_base_commit(repo, recorded=base, created_at="")
        assert found == base
        assert "recorded" in how

    def test_a_legacy_run_derives_its_base_from_its_own_creation_time(self, tmp_path):
        repo = tmp_path / "dated"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "test")

        def commit(message: str, when: str) -> str:
            (repo / f"{message}.txt").write_text(message)
            _git(repo, "add", "-A")
            env = dict(os.environ, GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when)
            subprocess.run(
                ["git", "commit", "-qm", message], cwd=repo, check=True, env=env
            )
            return _git(repo, "rev-parse", "HEAD")

        before = commit("baseline", "2026-09-16T08:00:00+00:00")
        commit("product", "2026-09-17T07:53:00+00:00")
        commit("hardening", "2026-09-18T03:56:00+00:00")

        found, how = task_base_commit(repo, created_at="2026-09-17T06:35:02+00:00")
        assert found == before
        assert "creation" in how

    def test_an_unknown_recorded_base_is_not_trusted(self, tmp_path):
        repo, _base, _product, _head = task_repo(tmp_path)
        found, _how = task_base_commit(repo, recorded="0" * 40)
        assert found == ""

    def test_the_base_is_a_persisted_field_of_the_run(self):
        state = RunState.model_validate({"run_id": "legacy"})
        assert state.task_base_commit == ""
        state.task_base_commit = "abc1234"
        assert RunState.model_validate(state.model_dump()).task_base_commit == "abc1234"


# ==========================================================================
# 2 — stable subject, current-tree result
# ==========================================================================


class TestStableSubjectCurrentTreeEvidence:
    def test_the_risk_still_grounds_to_the_task_module_after_a_verification_commit(
        self, tmp_path
    ):
        repo, base, _product, head = task_repo(tmp_path)
        record = task_record(repo, base, head)
        assert record.task_modules and "pkg/scheduler.py" in record.task_modules
        assert ground(SHIPS_DARK, record.task_modules).capability_paths == ["pkg/scheduler.py"]

    def test_the_measurement_is_recomputed_on_the_new_tree_and_its_control_fires(
        self, tmp_path
    ):
        repo, base, product, head = task_repo(tmp_path)
        assert head != product
        record = task_record(repo, base, head)
        by_path = {m.path: m for m in record.module_reachability}
        scheduler = by_path["pkg/scheduler.py"]
        assert scheduler.tree == head == record.commit
        assert scheduler.status == DARK
        assert scheduler.control_importers, "the positive control must have fired"

    def test_the_risk_is_covered_by_the_driver_scan_on_the_new_tree(self, tmp_path):
        repo, base, _product, head = task_repo(tmp_path)
        record = task_record(repo, base, head)
        covered, gaps = risk_coverage([risk("R8", SHIPS_DARK)], suite(unrelated_pass()), changed_verification=record)
        assert gaps == []
        assert covered[0].basis == BASIS_DRIVER_STRUCTURAL
        assert covered[0].evidence_tree == head

    def test_measured_from_the_resume_point_the_same_risk_cannot_ground(self, tmp_path):
        """The regression itself, reproduced: the product commit as the base."""
        repo, _base, product, head = task_repo(tmp_path)
        record = task_record(repo, product, head)
        assert record.task_modules == []
        _covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(unrelated_pass()), changed_verification=record
        )
        assert len(gaps) == 1

    def test_a_record_without_task_modules_still_grounds_from_its_scan(self, tmp_path):
        """A record persisted before the field existed reads as it did."""
        repo, base, _product, head = task_repo(tmp_path)
        legacy = task_record(repo, base, head).model_dump(mode="json")
        legacy.pop("task_modules")
        record = ChangedSurfaceVerification.model_validate(legacy)
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(unrelated_pass()), changed_verification=record
        )
        assert gaps == [] and len(covered) == 1

    def test_a_persisted_pre_grounding_risk_is_grounded_from_the_task_context(
        self, tmp_path
    ):
        """Risks are persisted as prose; grounding happens at judgement time."""
        repo, base, _product, head = task_repo(tmp_path)
        persisted = IdentifiedRisk.model_validate(
            {
                "id": "R8",
                "description": SHIPS_DARK,
                "risk_category": "regression",
                "severity": "P1",
                "basis": "written before grounding existed",
            }
        )
        covered, gaps = risk_coverage(
            [persisted], suite(unrelated_pass()), changed_verification=task_record(repo, base, head)
        )
        assert gaps == [] and covered[0].risk_id == "R8"


# ==========================================================================
# 3 — one obligation per semantic risk
# ==========================================================================


class TestDedup:
    def test_three_wordings_over_the_same_module_are_one_obligation(self, tmp_path):
        repo, base, _product, head = task_repo(tmp_path)
        covered, gaps = risk_coverage(
            ships_dark_risks(), suite(unrelated_pass()), changed_verification=task_record(repo, base, head)
        )
        assert gaps == []
        assert len(covered) == 1
        assert sorted(covered[0].duplicates) == ["R-SHIPS-DARK", "RISK-R8-ships-dark"]

    def test_uncovered_wordings_are_also_one_obligation(self, tmp_path):
        """Dedup is about identity, not about the verdict."""
        repo, base, _product, head = task_repo(tmp_path, reader_imported_by_app=True)
        _covered, gaps = risk_coverage(
            ships_dark_risks(), suite(unrelated_pass()), changed_verification=task_record(repo, base, head)
        )
        assert len(gaps) == 1
        assert len(gaps[0].duplicates) == 2

    def test_genuinely_different_subjects_stay_separate(self, tmp_path):
        repo, base, _product, head = task_repo(tmp_path)
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK), risk("R-LEDGER", LEDGER_DARK)],
            suite(unrelated_pass()),
            changed_verification=task_record(repo, base, head),
        )
        assert len(covered) + len(gaps) == 2
        assert all(not entry.duplicates for entry in [*covered, *gaps])

    def test_a_compound_name_is_not_its_shorter_neighbour(self):
        """``rule_admission.py`` is not ``rule.py`` because it contains 'rule'."""
        diff = ["src/pkg/rule.py", "src/pkg/rule_admission.py"]
        admission = ground(
            "rule_admission.py is imported by a production adapter, reaching the live path",
            diff,
        )
        rule = ground("The rule capability is wired into production through an import", diff)
        assert admission.capability_paths == ["src/pkg/rule_admission.py"]
        assert rule.capability_paths == ["src/pkg/rule.py"]
        assert admission.key("regression") != rule.key("regression")

    def test_a_package_or_label_in_one_wording_does_not_split_the_obligation(self):
        diff = ["src/pkg/rule.py", "src/pkg/rule_admission.py"]
        plain = ground("The rule capability is wired into production through an import", diff)
        concrete = ground(
            "The U8.2 rule capability (rule.py / M12) is imported by a production module or "
            "reached from outside the freight_recon package",
            diff,
        )
        assert plain.key("regression") == concrete.key("regression")


# ==========================================================================
# 4 — nothing weaker discharges it
# ==========================================================================


class TestNothingWeakerDischarges:
    def test_a_live_import_chain_on_the_current_tree_keeps_it_blocking(self, tmp_path):
        repo, base, _product, head = task_repo(tmp_path, reader_imported_by_app=True)
        record = task_record(repo, base, head)
        scheduler = {m.path: m for m in record.module_reachability}["pkg/scheduler.py"]
        assert scheduler.status == IMPORT_REACHABLE
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(unrelated_pass()), changed_verification=record
        )
        assert covered == [] and len(gaps) == 1

    def test_a_guard_blind_to_one_import_form_cannot_outvote_the_driver_scan(
        self, tmp_path
    ):
        """The replay's mutant A: the repository guard checks ``from pkg.scheduler
        import ...`` and the live chain uses ``from pkg import scheduler``. The
        guard passes with its own positive control; the driver's resolver sees the
        chain. The risk stays blocking, stated as a disagreement."""
        from test_risk_grounding import GUARD_WITH_CONTROL, verification

        repo, base = make_repo(tmp_path, guard=GUARD_WITH_CONTROL)
        app = repo / "pkg" / "app.py"
        app.write_text(app.read_text() + "\nfrom pkg import scheduler as _s  # wired in\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "wire the capability into the entry point")
        # The guard ran and passed on this tree: its scan is blind to this form.
        record = verification(repo, base, passed=True)
        scheduler = {m.path: m for m in record.module_reachability}["pkg/scheduler.py"]
        assert scheduler.status == IMPORT_REACHABLE
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(unrelated_pass()), changed_verification=record
        )
        assert covered == [] and len(gaps) == 1
        assert gaps[0].contradiction and "import-reachable" in gaps[0].contradiction

    def test_the_same_guard_still_discharges_when_the_scan_agrees(self, tmp_path):
        from test_risk_grounding import GUARD_WITH_CONTROL, verification

        repo, base = make_repo(tmp_path, guard=GUARD_WITH_CONTROL)
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)],
            suite(unrelated_pass()),
            changed_verification=verification(repo, base, passed=True),
        )
        assert gaps == [] and len(covered) == 1

    def test_no_positive_control_keeps_it_blocking(self, tmp_path):
        repo, base, _product, head = task_repo(tmp_path)
        # Break the control: nothing in verification imports the capability.
        for name in ("test_scheduler.py",):
            (repo / "tests" / name).write_text("def test_nothing():\n    assert True\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "the guard no longer imports the capability")
        head = _git(repo, "rev-parse", "HEAD")
        record = task_record(repo, base, head)
        scheduler = {m.path: m for m in record.module_reachability}["pkg/scheduler.py"]
        assert scheduler.status != DARK
        _covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(unrelated_pass()), changed_verification=record
        )
        assert len(gaps) == 1

    def test_a_dark_record_without_a_control_is_never_evidence(self):
        grounding = ground(SHIPS_DARK, ["pkg/scheduler.py"])
        forged = ModuleReachability(path="pkg/scheduler.py", status=DARK, tree="t1")
        discharged, _why = reachability_evidence(grounding, [forged], tree="t1")
        assert discharged is False

    def test_an_unrelated_measurement_cannot_discharge_it(self, tmp_path):
        repo, base, _product, head = task_repo(tmp_path)
        record = task_record(repo, base, head)
        # Only the scheduler's DARK record survives; the ledger risk must not use it.
        record.module_reachability = [
            m for m in record.module_reachability if m.path == "pkg/scheduler.py"
        ]
        _covered, gaps = risk_coverage(
            [risk("R-LEDGER", LEDGER_DARK)], suite(unrelated_pass()), changed_verification=record
        )
        assert len(gaps) == 1
        assert "pkg/ledger.py" in gaps[0].reason

    def test_an_old_tree_dark_is_never_inherited(self, tmp_path):
        repo, base, product, head = task_repo(tmp_path)
        old = task_record(repo, base, product)
        assert {m.path: m for m in old.module_reachability}["pkg/scheduler.py"].status == DARK
        # Judged on the new tree, carrying only the old tree's scan.
        stale = old.model_copy(update={"commit": head})
        covered, gaps = risk_coverage(
            [risk("R8", SHIPS_DARK)], suite(unrelated_pass()), changed_verification=stale
        )
        assert covered == [] and len(gaps) == 1
        assert head[:12] in gaps[0].reason

    def test_an_empty_diff_does_not_return_another_trees_record(self, tmp_path):
        repo, base, product, head = task_repo(tmp_path)
        held = task_record(repo, base, product)
        config = SimpleNamespace(neyma_repo=repo)
        obligation = {"value": held, "commit": product, "files": "x"}
        value = _changed_surface_verification(
            config=config,
            base_commit=head,  # base == HEAD, clean tree: nothing changed
            head_commit=head,
            obligation=obligation,
            emit=lambda _m: None,
        )
        assert value is None


# ==========================================================================
# 5 — the review follows the evidence it needs
# ==========================================================================


def _audit(result: TaskResult = TaskResult.AWAITING_INDEPENDENT_REVIEW) -> CompletionAudit:
    scope = TaskScope(scope_id="P8/U8.2", parent_phase_id="P8")
    return CompletionAudit(
        decision=AuditDecision.REQUIRES_INDEPENDENT_REVIEW,
        scope=scope,
        completion=ScopedCompletion(
            task_scope="P8/U8.2",
            task_result=result,
            task_outstanding=["an independent review of P8/U8.2 by a session that did not build it"],
        ),
        headline="P8/U8.2 IMPLEMENTED — AWAITING INDEPENDENT REVIEW",
    )


class TestReviewSequencing:
    def test_an_unverified_gate_is_a_pre_review_blocker(self):
        gate = evaluate_gate(suite(unrelated_pass()), risks=[risk("R8", SHIPS_DARK)])
        assert gate.status is GateStatus.NOT_VERIFIED
        blockers = _pre_review_blockers(gate, None)
        assert blockers and "uncovered risk" in blockers[0]

    def test_a_verified_gate_is_not(self):
        gate = evaluate_gate(suite(unrelated_pass()))
        assert gate.status is GateStatus.VERIFIED
        assert _pre_review_blockers(gate, None) == []

    def test_with_blockers_review_is_owed_but_not_next(self):
        held = hold_review_behind(_audit(), ["scenario gate: uncovered risk R8"])
        assert held.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW
        assert held.completion is not None
        assert held.completion.task_result is TaskResult.UNPROVEN
        assert held.completion.task_outstanding[0] == "scenario gate: uncovered risk R8"
        assert any("independent review" in o for o in held.completion.task_outstanding)
        action = held.next_safe_action()
        assert action.startswith("establish the pre-review acceptance evidence")
        assert "AWAITING" not in held.headline

    def test_without_blockers_review_is_next(self):
        audit = _audit()
        assert hold_review_behind(audit, []) is audit
        assert "independent" in audit.next_safe_action()
        assert audit.completion.task_result is TaskResult.AWAITING_INDEPENDENT_REVIEW


class TestReviewSequencingInTheLoop:
    """The real control loop, with a repository that owes a review."""

    async def test_a_blocked_gate_launches_no_review_and_does_not_advertise_one(
        self, tmp_path
    ):
        from test_integrated_review import FakeReviewer, PhaseRepo, drive, supported

        repo = PhaseRepo(tmp_path / "neyma")
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(
            repo, tmp_path, reviewer=reviewer, passing=False, max_iterations=1
        )
        assert reviewer.launches == 0
        assert result.status is not RunStatus.ACCEPTED
        assert result.audit is not None
        assert result.audit.pre_review_blockers
        completion = result.audit.completion
        assert completion is not None
        assert completion.task_result is not TaskResult.AWAITING_INDEPENDENT_REVIEW
        assert not result.audit.next_safe_action().startswith("have a session")

    async def test_a_verified_gate_takes_the_review_and_a_supported_one_closes_the_task(
        self, tmp_path
    ):
        from test_integrated_review import FakeReviewer, PhaseRepo, drive, supported

        repo = PhaseRepo(tmp_path / "neyma")
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(repo, tmp_path, reviewer=reviewer)
        assert reviewer.launches == 1
        assert result.status is RunStatus.ACCEPTED
        assert result.audit is not None and not result.audit.pre_review_blockers
        assert result.audit.completion.task_result is TaskResult.VERIFIED

    async def test_a_refusing_review_goes_back_to_the_builder(self, tmp_path):
        from test_integrated_review import (
            FakeBuilder,
            FakeReviewer,
            PhaseRepo,
            drive,
            refusing,
            supported,
        )

        repo = PhaseRepo(tmp_path / "neyma")
        builder = FakeBuilder(repo.root)
        reviewer = FakeReviewer([refusing(), supported()])
        result, _store = await drive(repo, tmp_path, builder=builder, reviewer=reviewer)
        assert reviewer.launches == 2
        assert len(builder.prompts) >= 2
        assert "claim CAS" in builder.prompts[1]
