"""A run whose product passes must be able to reach its review boundary.

Run 20260918-223447 (Neyma U8.4) judged the same tree for eleven iterations:
12/12 scenarios passed, no observed failure, and the run learned nothing after
the third. Three defects did that together, and these tests hold the repairs:

A. **changed-verification starvation.** ``max_changed_guards`` selected the
   first four of eleven changed verification files on every pass, so on an
   unchanged tree the same four were run forever and the other seven stayed
   "not operated" for the life of the run. The bound is per PASS now: the next
   pass takes what is still owed, same-tree results carry, a resume keeps them,
   and another tree's results never do.
B. **changed probes.** A changed probe or battery that collects no test was
   never run at all. It is run now through an exact invocation a human declared
   — a scenario step or a CI line — and otherwise stays an explicit gap. No
   argument is ever composed.
C. **a risk too thin to check.** A risk whose words name one artifact can never
   be checked against a guard, so a builder asked for a guard could add exactly
   the right one and change nothing. A gap routed BY RISK IDENTITY binds the
   tests the answering change moves to those risk keys only; the driver runs
   them by node id on the judged tree and requires their control.
D. **sequencing.** While either obligation is open a review is owed and not
   next; once both close, it is.

Synthetic repositories in a temporary directory. No Neyma name is load-bearing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from neyma_product_driver.changed_verification import (
    ChangedSurfaceVerification,
    DeclaredInvocation,
    choose_invocation,
    declared_invocations_from_ci,
    declared_invocations_from_scenarios,
    verify_changed_surface,
)
from neyma_product_driver.cli import (
    _changed_surface_verification,
    _pre_review_blockers,
    _verification_gap_decision,
)
from neyma_product_driver.completion_auditor import (
    AuditDecision,
    CompletionAudit,
    hold_review_behind,
)
from neyma_product_driver.models import Decision, EvaluatorDecision
from neyma_product_driver.repo_verification import run_changed_files
from neyma_product_driver.scenario_gate import (
    BASIS_BOUND_GAP,
    GateStatus,
    evaluate_gate,
)
from neyma_product_driver.scenario_plan import IdentifiedRisk, Priority, RiskCategory
from neyma_product_driver.scenario_suite import SuiteResult
from neyma_product_driver.scenarios import CommandSpec, Scenario
from neyma_product_driver.task_scope import ScopedCompletion, TaskResult, TaskScope
from neyma_product_driver.verification_obligations import (
    answer,
    bound_evidence,
    execute,
    group_by_obligation,
    obligation_group,
    route,
)
from neyma_product_driver.worktree_state import worktree_tree_hash

from test_risk_grounding import generated, suite


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _tree(repo: Path) -> str:
    tree, error = worktree_tree_hash(repo)
    assert tree, error
    return tree


PRODUCT = '''\
VERIFIED = "VERIFIED"
UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
PERMITTED = frozenset({VERIFIED})
LEDGER = []


def compensate(original_state):
    """Only a VERIFIED original effect may be compensated."""
    if original_state not in PERMITTED:
        return "refused"
    LEDGER.append("compensating-effect")
    return "created"
'''


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "guards").mkdir()
    (repo / "scripts").mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "compensation.py").write_text(PRODUCT)
    (repo / "guards" / "conftest.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
    )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")
    return repo, _commit(repo, "base")


def _pass_file(i: int) -> str:
    return f"def test_holds_{i}():\n    assert {i} == {i}\n"


# ==========================================================================
# A — bounded passes, not a lifetime cap
# ==========================================================================


def _seven(repo: Path) -> list[str]:
    for i in range(7):
        (repo / "guards" / f"test_g{i}.py").write_text(_pass_file(i))
    _commit(repo, "seven changed guards")
    return [f"guards/test_g{i}.py" for i in range(7)]


class TestBoundedPasses:
    def test_seven_changed_files_converge_over_two_passes_of_four(self, tmp_path):
        repo, base = make_repo(tmp_path)
        paths = _seven(repo)
        head = _git(repo, "rev-parse", "HEAD")
        diff = run_changed_files(repo, base)

        first = verify_changed_surface(repo, diff, base_commit=base, commit=head, max_changed=4)
        assert len(first.passes) == 1 and len(first.passes[0]) == 4
        assert first.passes[0] == paths[:4]
        assert [g.path for g in first.pending] == paths[4:]
        assert first.blocks_claim
        status = first.path_status()
        assert [status[p] for p in paths] == ["OBSERVED"] * 4 + ["PENDING"] * 3

        second = verify_changed_surface(
            repo, diff, base_commit=base, commit=head, max_changed=4, prior=first
        )
        # Only what was still owed ran; nothing already passed was re-run or lost.
        assert second.passes == [paths[:4], paths[4:]]
        assert sorted(second.executed_paths) == sorted(paths)
        assert all(r.passed for r in second.results)
        assert not second.pending and not second.unobserved_changed_paths
        assert not second.blocks_claim
        assert set(second.path_status().values()) == {"OBSERVED"}

    def test_the_progress_survives_a_resume(self, tmp_path):
        repo, base = make_repo(tmp_path)
        paths = _seven(repo)
        head = _git(repo, "rev-parse", "HEAD")
        diff = run_changed_files(repo, base)
        first = verify_changed_surface(repo, diff, base_commit=base, commit=head, max_changed=4)

        # What a restart reads back: the persisted JSON, nothing in memory.
        restored = ChangedSurfaceVerification.model_validate(first.model_dump(mode="json"))
        assert restored.tree == first.tree and restored.diff_fingerprint

        config = SimpleNamespace(
            neyma_repo=repo,
            scenarios_dir=None,
            changed_verification=SimpleNamespace(
                max_changed_guards=4, max_related_guards=2, timeout_s=300
            ),
        )
        obligation = {
            "value": restored,
            "commit": restored.commit,
            "files": restored.diff_fingerprint,
            "tree": restored.tree,
        }
        persisted: list[ChangedSurfaceVerification] = []
        value = _changed_surface_verification(
            config=config,
            base_commit=base,
            head_commit=head,
            obligation=obligation,
            emit=lambda _m: None,
            persist=persisted.append,
        )
        assert value.passes == [paths[:4], paths[4:]]
        assert not value.blocks_claim
        assert len(persisted) == 1  # one more pass, persisted as it finished

    def test_the_loop_drives_every_pass_in_one_step_and_persists_each(self, tmp_path):
        repo, base = make_repo(tmp_path)
        paths = _seven(repo)
        head = _git(repo, "rev-parse", "HEAD")
        config = SimpleNamespace(
            neyma_repo=repo,
            scenarios_dir=None,
            changed_verification=SimpleNamespace(
                max_changed_guards=4, max_related_guards=2, timeout_s=300
            ),
        )
        persisted: list[ChangedSurfaceVerification] = []
        value = _changed_surface_verification(
            config=config,
            base_commit=base,
            head_commit=head,
            obligation={"value": None, "commit": "", "files": "", "tree": ""},
            emit=lambda _m: None,
            persist=lambda r: persisted.append(r.model_copy(deep=True)),
        )
        assert [len(p) for p in value.passes] == [4, 3]
        assert len(persisted) == 2 and len(persisted[0].results) == 4
        assert sorted(value.executed_paths) == sorted(paths)

    def test_another_trees_results_are_never_carried(self, tmp_path):
        repo, base = make_repo(tmp_path)
        _seven(repo)
        head = _git(repo, "rev-parse", "HEAD")
        diff = run_changed_files(repo, base)
        first = verify_changed_surface(repo, diff, base_commit=base, commit=head, max_changed=4)

        # An uncommitted edit: same commit, same changed-file list, different tree.
        (repo / "guards" / "test_g0.py").write_text("def test_holds_0():\n    assert False\n")
        second = verify_changed_surface(
            repo, diff, base_commit=base, commit=head, max_changed=4, prior=first
        )
        assert second.tree != first.tree
        assert second.passes == [second.passes[-1]], "nothing inherited from the other tree"
        assert "guards/test_g0.py" in [r.target.path for r in second.product_failures]


# ==========================================================================
# B — a changed probe runs only through an exact declared invocation
# ==========================================================================

PROBE = "import sys\nprint('0 wrong')\nsys.exit(0)\n"


def _probe_repo(tmp_path: Path) -> tuple[Path, str, str, list[str]]:
    repo, base = make_repo(tmp_path)
    (repo / "scripts" / "probe_thing.py").write_text(PROBE)
    head = _commit(repo, "a changed probe")
    return repo, base, head, run_changed_files(repo, base)


def _scenario(*commands: tuple[str, int]) -> Scenario:
    return Scenario(
        name="declares",
        commands=[
            CommandSpec(name=f"c{i}", run=run, expect_exit_code=code)
            for i, (run, code) in enumerate(commands)
        ],
    )


class TestDeclaredInvocations:
    def test_an_exact_declared_invocation_is_operated_verbatim(self, tmp_path):
        repo, base, head, diff = _probe_repo(tmp_path)
        declared_run = f"{sys.executable} scripts/probe_thing.py --all"
        declared = declared_invocations_from_scenarios(
            [_scenario((f"{sys.executable} scripts/probe_thing.py --list-cases", 0), (declared_run, 0))]
        )
        # --all was declared with the larger allowance in real files; here the
        # tie-break is order, so make the whole-run one explicit.
        declared[1].timeout_s = 1800
        record = verify_changed_surface(
            repo, diff, base_commit=base, commit=head, declared=declared
        )
        assert record.path_status() == {"scripts/probe_thing.py": "OBSERVED"}
        assert [r.target.command for r in record.results] == [declared_run]
        assert record.results[0].passed and not record.blocks_claim

    def test_a_ci_line_is_a_declaration_and_only_its_interpreter_is_substituted(self, tmp_path):
        repo, base = make_repo(tmp_path)
        (repo / "scripts" / "probe_thing.py").write_text(PROBE)
        workflows = repo / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text(
            "jobs:\n  probe:\n    steps:\n      - run: |\n"
            "          python scripts/probe_thing.py --case a\n"
            "          python scripts/probe_thing.py | tee log.txt\n"
        )
        head = _commit(repo, "probe and its CI step")
        declared = declared_invocations_from_ci(repo, sys.executable)
        # The piped line is not a single program's exit status and is not read.
        assert [d.command for d in declared] == [
            f"{sys.executable} scripts/probe_thing.py --case a"
        ]
        record = verify_changed_surface(
            repo, run_changed_files(repo, base), base_commit=base, commit=head, declared=declared
        )
        assert record.results[0].target.command == declared[0].command
        assert not record.blocks_claim

    def test_no_declaration_is_an_explicit_blocking_gap(self, tmp_path):
        repo, base, head, diff = _probe_repo(tmp_path)
        record = verify_changed_surface(repo, diff, base_commit=base, commit=head)
        assert record.results == []
        assert record.path_status() == {"scripts/probe_thing.py": "UNEXECUTABLE"}
        assert record.blocks_claim
        assert any("no exact invocation" in r for r in record.gap_reasons)
        assert "no declared invocation" in record.headline()

    def test_an_invented_invocation_is_impossible(self, tmp_path):
        repo, base, head, diff = _probe_repo(tmp_path)
        declared = declared_invocations_from_scenarios(
            [
                _scenario(
                    # a refusal control: proves input is closed, not that checks hold
                    (f"{sys.executable} scripts/probe_thing.py --inject bogus", 2),
                    # another program entirely
                    (f"{sys.executable} scripts/probe_other.py", 0),
                    # shell syntax: the status read back is not the script's
                    (f"{sys.executable} scripts/probe_thing.py; echo ok", 0),
                    (f"{sys.executable} -m scripts.probe_thing", 0),
                )
            ]
        )
        assert choose_invocation("scripts/probe_thing.py", declared) is None
        record = verify_changed_surface(
            repo, diff, base_commit=base, commit=head, declared=declared
        )
        assert record.results == [] and record.blocks_claim
        # And whatever does run is always a string a human wrote.
        allowed = DeclaredInvocation(
            command=f"{sys.executable} scripts/probe_thing.py", script="scripts/probe_thing.py"
        )
        ran = verify_changed_surface(
            repo, diff, base_commit=base, commit=head, declared=[*declared, allowed]
        )
        assert [r.target.command for r in ran.results] == [allowed.command]


# ==========================================================================
# C — a verification gap routed by identity, answered by a bound guard
# ==========================================================================

W1 = (
    "UNKNOWN_OUTCOME could be treated as a VERIFIED original effect, inventing a "
    "compensating call/effect from an ambiguous external outcome."
)
W2 = (
    "An UNKNOWN_OUTCOME original effect could be treated as a VERIFIED effect and invent a "
    "compensating call/effect from an ambiguous external outcome; the changed wiring must "
    "refuse with zero compensating calls and zero grant/effect."
)
W3 = (
    "An UNKNOWN_OUTCOME original effect could be read as a VERIFIED effect, so the wiring "
    "invents a compensating call/grant/effect instead of refusing with zero compensating effect."
)
#: Same category, a different subject: a genuinely different risk.
OTHER = (
    "A compensating effect could reuse the ORIGINAL_EFFECT commit_key and collide with the "
    "original on retry."
)


def _risk(rid: str, text: str, category=RiskCategory.AMBIGUOUS_EXTERNAL_EFFECT) -> IdentifiedRisk:
    return IdentifiedRisk(id=rid, description=text, risk_category=category, severity=Priority.P0)


def _wordings() -> list[IdentifiedRisk]:
    return [_risk("R4", W1), _risk("R4-w2", W2), _risk("R4-unknown", W3)]


def _suite() -> SuiteResult:
    return suite(generated("S-other", RiskCategory.IDEMPOTENCY))


GUARD = '''\
from pkg import compensation as c


def test_unknown_outcome_creates_no_compensation():
    c.LEDGER.clear()
    assert c.compensate(c.UNKNOWN_OUTCOME) == "refused"
    assert c.LEDGER == []
'''

CONTROL = '''

def test_the_control_catches_a_compensation_from_unknown_outcome(monkeypatch):
    import pytest

    monkeypatch.setattr(c, "PERMITTED", frozenset({c.VERIFIED, c.UNKNOWN_OUTCOME}))
    with pytest.raises(AssertionError):
        test_unknown_outcome_creates_no_compensation()
'''

#: Named like a control, invokes nothing: no discrimination at all.
HOLLOW_CONTROL = '''

def test_the_control_catches_nothing_in_particular():
    assert True
'''


class Routed:
    """A repository with the three wordings routed as one obligation at T0."""

    def __init__(self, tmp_path: Path) -> None:
        self.repo, self.base = make_repo(tmp_path)
        self.register = [*_wordings(), _risk("R7", OTHER)]
        self.routed_tree = _tree(self.repo)
        self.obligations: list = []
        self.obligation = route(
            self.obligations,
            obligation_group(self.register[0], self.register),
            iteration=3,
            tree=self.routed_tree,
        )

    def answer_with(self, text: str, name: str = "guards/test_unknown_outcome_guard.py") -> str:
        (self.repo / name).write_text(text)
        tree = _tree(self.repo)
        answer(self.obligations, self.repo, tree)
        return tree

    def operate(self, tree: str):
        return execute(
            self.obligation, self.repo, tree=tree, runner=f"{sys.executable} -m pytest", timeout_s=300
        )

    def gate(self, tree: str, suite: SuiteResult | None = None):
        return evaluate_gate(
            suite or _suite(),
            risks=self.register,
            obligations=self.obligations,
            judged_tree=tree,
            repo=self.repo,
        )


class TestRoutedByIdentity:
    def test_a_risk_naming_no_file_is_routed_once_by_its_exact_keys(self, tmp_path):
        repo, _base = make_repo(tmp_path)
        register = [*_wordings(), _risk("R7", OTHER)]
        planner = SimpleNamespace(plan=SimpleNamespace(risks=register))
        routed = _verification_gap_decision(
            planner=planner,
            wave=None,
            gaps=[register[0]],
            verdict=None,
            accepted=EvaluatorDecision(decision=Decision.ACCEPT, summary="ok"),
            scenario=Scenario(name="s"),
            budget_spent=True,
        )
        assert routed is not None
        decision, group = routed
        assert decision.decision is Decision.FIX
        assert [r.id for r in group] == ["R4", "R4-w2", "R4-unknown"]
        for r in group:
            assert r.key in decision.correction_prompt
        assert register[3].key not in decision.correction_prompt

    def test_three_wordings_are_one_obligation_and_a_different_risk_is_not(self):
        groups = group_by_obligation([*_wordings(), _risk("R7", OTHER)])
        assert [[r.id for r in g] for g in groups] == [["R4", "R4-w2", "R4-unknown"], ["R7"]]
        # The same subjects under a different category is a different obligation.
        crossed = group_by_obligation(
            [_risk("R4", W1), _risk("RX", W1, category=RiskCategory.RETRY_SAFETY)]
        )
        assert len(crossed) == 2

    def test_before_any_answer_the_wordings_block_as_one_entry(self, tmp_path):
        routed = Routed(tmp_path)
        verdict = routed.gate(routed.routed_tree)
        assert verdict.status is GateStatus.NOT_VERIFIED
        r4 = [r for r in verdict.uncovered_risks if r.obligation]
        assert len(r4) == 1 and sorted(r4[0].duplicates) == ["R4-unknown", "R4-w2"]
        assert "no change has answered it yet" in r4[0].reason

    def test_a_bound_guard_with_its_control_executed_on_the_judged_tree_discharges(
        self, tmp_path
    ):
        routed = Routed(tmp_path)
        tree = routed.answer_with(GUARD + CONTROL)
        execution = routed.operate(tree)
        assert execution is not None and len(execution.passed) == 2 and not execution.failed

        verdict = routed.gate(tree)
        covered = [r for r in verdict.covered_risks if r.basis == BASIS_BOUND_GAP]
        assert len(covered) == 1
        entry = covered[0]
        # Auditable end to end: keys -> guard -> exact command -> tree -> control.
        assert sorted(entry.duplicates) == ["R4-unknown", "R4-w2"]
        assert entry.measurement == (
            "guards/test_unknown_outcome_guard.py::test_unknown_outcome_creates_no_compensation"
        )
        assert entry.command == execution.command
        assert entry.evidence_tree == tree
        assert entry.discrimination == [
            "guards/test_unknown_outcome_guard.py::"
            "test_the_control_catches_a_compensation_from_unknown_outcome"
        ]
        # ONLY the named obligation. The different risk is still open.
        assert [r.risk_id for r in verdict.uncovered_risks] == ["R7"]

    def test_an_unrelated_green_guard_that_predates_the_routing_does_not(self, tmp_path):
        routed = Routed(tmp_path)
        # The very same assertions, but already in the tree when the gap was routed.
        repo = routed.repo
        (repo / "guards" / "test_already_here.py").write_text(GUARD + CONTROL)
        routed.routed_tree = _tree(repo)
        routed.obligations.clear()
        routed.obligation = route(
            routed.obligations,
            obligation_group(routed.register[0], routed.register),
            iteration=3,
            tree=routed.routed_tree,
        )
        tree = routed.answer_with("def test_unrelated():\n    assert 1\n", "guards/test_unrelated.py")
        routed.operate(tree)
        verdict = routed.gate(tree)
        assert not [r for r in verdict.covered_risks if r.basis == BASIS_BOUND_GAP]
        entry = next(r for r in verdict.uncovered_risks if r.obligation)
        assert "no discrimination" in entry.reason or "not yet discriminating" in entry.reason

    def test_a_guard_changed_after_the_answer_is_not_bound(self, tmp_path):
        routed = Routed(tmp_path)
        routed.answer_with("def test_unrelated():\n    assert 1\n", "guards/test_unrelated.py")
        # A later change, answering nothing that was routed.
        later = routed.answer_with(GUARD + CONTROL)
        routed.operate(later)
        assert list(routed.obligation.bound_tests()) == ["guards/test_unrelated.py"]
        assert routed.gate(later).status is GateStatus.NOT_VERIFIED

    def test_a_guard_without_discrimination_does_not(self, tmp_path):
        routed = Routed(tmp_path)
        tree = routed.answer_with(GUARD)
        routed.operate(tree)
        entry = next(r for r in routed.gate(tree).uncovered_risks if r.obligation)
        assert "not yet discriminating" in entry.reason

    def test_a_control_that_never_invokes_the_guard_does_not(self, tmp_path):
        routed = Routed(tmp_path)
        tree = routed.answer_with(GUARD + HOLLOW_CONTROL)
        routed.operate(tree)
        assert routed.gate(tree).status is GateStatus.NOT_VERIFIED

    def test_evidence_from_an_earlier_tree_does_not(self, tmp_path):
        routed = Routed(tmp_path)
        answered = routed.answer_with(GUARD + CONTROL)
        routed.operate(answered)
        assert any(r.basis == BASIS_BOUND_GAP for r in routed.gate(answered).covered_risks)
        # The tree moves on; the execution stays where it was taken.
        (routed.repo / "pkg" / "unrelated.py").write_text("X = 1\n")
        moved = _tree(routed.repo)
        verdict = routed.gate(moved)
        entry = next(r for r in verdict.uncovered_risks if r.obligation)
        assert "not evidence about this one" in entry.reason
        assert not [r for r in verdict.covered_risks if r.basis == BASIS_BOUND_GAP]

    def test_a_red_bound_guard_refutes_and_blocks(self, tmp_path):
        routed = Routed(tmp_path)
        routed.answer_with(GUARD + CONTROL)
        # The mutant: UNKNOWN_OUTCOME may now create a compensation.
        (routed.repo / "pkg" / "compensation.py").write_text(
            PRODUCT.replace("frozenset({VERIFIED})", "frozenset({VERIFIED, UNKNOWN_OUTCOME})")
        )
        mutant = _tree(routed.repo)
        execution = routed.operate(mutant)
        assert execution.failed
        verdict = routed.gate(mutant)
        assert verdict.status is GateStatus.NOT_VERIFIED
        entry = next(r for r in verdict.uncovered_risks if r.obligation)
        assert "REFUSED" in entry.reason

        # And a scenario that passed carrying the category cannot outvote it.
        carrying = suite(
            generated("S-other", RiskCategory.IDEMPOTENCY),
            generated("gen-unknown", RiskCategory.AMBIGUOUS_EXTERNAL_EFFECT),
        )
        clash = routed.gate(mutant, carrying)
        entry = next(r for r in clash.uncovered_risks if r.obligation)
        assert entry.contradiction

    def test_the_bound_answer_is_measured_from_the_routed_tree_even_uncommitted(self, tmp_path):
        routed = Routed(tmp_path)
        tree = routed.answer_with(GUARD + CONTROL)
        assert _git(routed.repo, "status", "--porcelain")  # never committed
        bound = routed.obligation.bound_tests()
        assert bound == {
            "guards/test_unknown_outcome_guard.py": [
                "test_unknown_outcome_creates_no_compensation",
                "test_the_control_catches_a_compensation_from_unknown_outcome",
            ]
        }
        verdict = bound_evidence(routed.obligation, routed.repo, judged_tree=tree)
        assert not verdict.discharges and "has not been executed" in verdict.reason


# ==========================================================================
# D — the review is next only once both obligations are closed
# ==========================================================================


def _audit() -> CompletionAudit:
    completion = ScopedCompletion(
        scope=TaskScope(scope_id="P9/U9.1", label="unit"),
        task_result=TaskResult.AWAITING_INDEPENDENT_REVIEW,
    )
    return CompletionAudit(decision=AuditDecision.REQUIRES_INDEPENDENT_REVIEW, completion=completion)


class TestSequencing:
    def test_an_open_changed_verification_obligation_holds_the_review(self, tmp_path):
        repo, base = make_repo(tmp_path)
        _seven(repo)
        head = _git(repo, "rev-parse", "HEAD")
        partial = verify_changed_surface(
            repo, run_changed_files(repo, base), base_commit=base, commit=head, max_changed=4
        )
        gate = evaluate_gate(_suite())
        assert gate.status is GateStatus.VERIFIED
        blockers = _pre_review_blockers(gate, partial)
        assert blockers and all(b.startswith("changed verification:") for b in blockers)
        held = hold_review_behind(_audit(), blockers)
        assert held.completion.task_result is TaskResult.UNPROVEN
        assert not held.next_safe_action().startswith("have a session")

    def test_an_open_bound_obligation_holds_the_review(self, tmp_path):
        routed = Routed(tmp_path)
        tree = routed.answer_with(GUARD)
        routed.operate(tree)
        blockers = _pre_review_blockers(routed.gate(tree), None)
        assert blockers and "uncovered risk" in blockers[0]

    def test_once_both_close_the_review_is_next(self, tmp_path):
        routed = Routed(tmp_path)
        tree = routed.answer_with(GUARD + CONTROL)
        routed.operate(tree)
        head = _commit(routed.repo, "the bound guard")
        changed = verify_changed_surface(
            routed.repo,
            run_changed_files(routed.repo, routed.base),
            base_commit=routed.base,
            commit=head,
        )
        assert not changed.blocks_claim
        register = _wordings()  # the obligation's own risks, and nothing else open
        gate = evaluate_gate(
            _suite(),
            risks=register,
            obligations=routed.obligations,
            judged_tree=tree,
            repo=routed.repo,
        )
        assert gate.status is GateStatus.VERIFIED
        assert _pre_review_blockers(gate, changed) == []
        audit = _audit()
        assert hold_review_behind(audit, []) is audit
        assert audit.completion.task_result is TaskResult.AWAITING_INDEPENDENT_REVIEW
