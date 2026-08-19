"""Product safety versus engineering ceremony, proved end to end.

The driver was pulled back into manual orchestration by repository-process
rules that outranked demonstrated product behaviour. These tests pin the new
operating philosophy in both directions: ordinary product work now completes
without a founder in the loop, and every boundary that protects a customer, a
credential, a remote or a piece of history is exactly where it was.

All Claude sessions are faked. Nothing here consumes real Claude usage.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from neyma_product_driver.cli import run_control_loop
from neyma_product_driver.completion_auditor import AuditDecision, CompletionAuditor
from neyma_product_driver.config import DriverConfig, ReviewPolicyConfig
from neyma_product_driver.context import ContextResolutionError, RepositoryContextLoader
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import (
    AssertionResult,
    Decision,
    EvaluatorDecision,
    RunState,
    RunStatus,
    ScenarioResult,
)
from neyma_product_driver.policy import (
    ChangeRisk,
    assess_change_risk,
    protocol_warrants_investigation,
    requires_founder_authority,
)
from neyma_product_driver.reviewer import IndependentReview, ReviewFinding
from neyma_product_driver.scenarios import Scenario


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


@dataclass
class FakeTurn:
    text: str = "I made the change.\n\nRUNNABLE CHECKPOINT: run `make demo`."
    session_id: str | None = "builder-session-1"
    tool_uses: list[str] = field(default_factory=list)
    denied_requests: list[str] = field(default_factory=list)
    is_error: bool = False
    error_detail: str = ""


class FakeBuilder:
    def __init__(self, text: str | None = None) -> None:
        self.session_id = "builder-session-1"
        self.prompts: list[str] = []
        self.turn = FakeTurn(text=text) if text is not None else FakeTurn()

    async def send(self, prompt: str, timeout_s: int | None = None) -> FakeTurn:
        self.prompts.append(prompt)
        return self.turn


class FakeEvaluator:
    def __init__(self, decisions: list[EvaluatorDecision]) -> None:
        self.session_id = "evaluator-session-1"
        self.decisions = list(decisions)
        self.calls = 0

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision:
        self.calls += 1
        if self.decisions:
            return self.decisions.pop(0)
        return accept()


class FakeExecutor:
    def __init__(self, artifact_dir: Path, passing: bool = True) -> None:
        self.artifact_dir = artifact_dir
        self.service_logs: dict[str, str] = {}
        self.passing = passing

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        return ScenarioResult(
            scenario_name=scenario.name,
            assertions=[
                AssertionResult(kind="expect_visible", target="the thing", passed=self.passing)
            ],
        )


class FakeReviewerSession:
    """A stand-in for the fresh read-only reviewer session."""

    def __init__(self, reviews: list[IndependentReview]) -> None:
        self.reviews = list(reviews)
        self.prompts: list[str] = []
        self.launches = 0

    def __call__(self) -> "FakeReviewerSession":
        self.launches += 1
        return self

    async def __aenter__(self) -> "FakeReviewerSession":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def review(self, prompt: str) -> IndependentReview:
        self.prompts.append(prompt)
        return self.reviews.pop(0) if self.reviews else supported_review()


def accept(**kw) -> EvaluatorDecision:
    return EvaluatorDecision(
        decision=Decision.ACCEPT, summary="the product behaved", observed_behavior=["saw it"], **kw
    )


def supported_review() -> IndependentReview:
    return IndependentReview(
        verdict="SUPPORTED", summary="the evidence supports the change", confidence=0.9
    )


def refusing_review() -> IndependentReview:
    return IndependentReview(
        verdict="NOT_SUPPORTED",
        summary="the tenant filter is applied after the query, not inside it",
        confidence=0.85,
        findings=[
            ReviewFinding(
                finding="rows for other tenants are fetched before being filtered in Python",
                severity="blocker",
                evidence_path="app/repo/loads.py:88",
                reasoning="the SQL has no tenant predicate; isolation depends on caller discipline",
            )
        ],
    )


@pytest.fixture
def loop_bits(driver_config: DriverConfig):
    assert driver_config.runs_dir is not None
    store = EvidenceStore(driver_config.runs_dir, "20260819-000000")
    state = RunState(
        run_id=store.run_id,
        task="show the load list",
        max_iterations=driver_config.max_iterations,
    )
    scenario = Scenario(name="load-list")
    return driver_config, store, state, scenario, lambda d: FakeExecutor(d)


async def drive(loop_bits, **kw):
    config, store, state, scenario, make_executor = loop_bits
    return await run_control_loop(
        config=config,
        scenario=scenario,
        store=store,
        state=state,
        make_executor=make_executor,
        emit=lambda _m: None,
        **kw,
    )


# --------------------------------------------------------------------------
# 1. Ordinary product work completes without a manual reviewer authorization
# --------------------------------------------------------------------------


class TestOrdinaryWorkNeedsNoFounder:
    async def test_an_ordinary_change_is_accepted_with_no_reviewer_launched(self, loop_bits):
        """The whole point: a small change, a passing scenario, and it is done.

        Before, an ACCEPT could still land on NEEDS_INDEPENDENT_REVIEW and wait
        for the founder to run ``review --run <id>`` by hand. An ordinary edit is
        not an occasion for a second opinion.
        """
        reviewer = FakeReviewerSession([])
        result = await drive(
            loop_bits,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            reviewer_factory=reviewer,
        )
        assert result.status is RunStatus.ACCEPTED
        assert reviewer.launches == 0
        assert result.risk.level is ChangeRisk.ORDINARY

    async def test_a_repository_with_no_unit_registry_still_builds(self, loop_bits, fake_repo):
        """A target repo that has retired its registry is not an outage.

        This used to terminate the run before the builder was asked to do
        anything: the driver required ``IMPLEMENTATION-REGISTRY.yaml`` with
        exactly one READY unit, so simplifying the target repository broke the
        driver that was supposed to build in it.
        """
        loader = RepositoryContextLoader(fake_repo)
        with pytest.raises(ContextResolutionError):
            loader.resolve_active_unit()

        builder = FakeBuilder()
        result = await drive(
            loop_bits,
            builder=builder,
            evaluator=FakeEvaluator([accept()]),
            repo_loader=loader,
        )
        assert result.status is RunStatus.ACCEPTED
        assert builder.prompts, "the builder was never asked to do the work"
        assert "declares no active work unit" in builder.prompts[0]

    def test_calibrate_does_not_call_a_missing_registry_a_founder_decision(self, fake_repo):
        """Calibration must predict what a run will actually do.

        It reported "founder decision required" (exit 10) for a repository with
        no registry, so a target that had simplified its process looked like a
        fault to repair before any work could start. A registry that exists and
        contradicts itself is still worth telling you about.
        """
        from neyma_product_driver.calibration import calibrate

        assert calibrate(fake_repo).founder_decision_required == ""

    def test_calibrate_still_reports_a_contradictory_registry(self, tmp_path):
        repo = _registry_repo(tmp_path, declare_finalizer=False)
        registry = repo / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml"
        registry.write_text(
            registry.read_text(encoding="utf-8")
            + "  - unit_id: P2\n    name: another\n    status: READY\n",
            encoding="utf-8",
        )
        from neyma_product_driver.calibration import calibrate

        assert "exactly one active unit" in calibrate(repo).founder_decision_required

    def test_the_undeclared_unit_invents_nothing(self, fake_repo):
        unit = RepositoryContextLoader(fake_repo).resolve_active_unit_optional()
        assert unit.is_declared is False
        assert unit.unit_id == ""
        assert unit.acceptance_criteria == []
        assert "do not invent" in unit.render().lower() or "Do not invent" in unit.render()


# --------------------------------------------------------------------------
# 2. Generated scenarios are the default for applicable product work
# --------------------------------------------------------------------------


class TestGeneratedScenariosAreTheDefault:
    def test_generation_is_enabled_without_being_asked(self):
        assert DriverConfig(neyma_repo=".", task="x").scenario_generation.enabled is True

    def test_a_bare_run_builds_a_planner(self, driver_config, monkeypatch):
        """No ``--auto-scenarios`` anywhere, and coverage is still generated."""
        import argparse

        from neyma_product_driver import cli

        built: dict[str, Any] = {}

        class FakePlanner:
            restore_failed = False
            approved_commands: list[str] = []

            def restore_from_store(self) -> None:
                built["restored"] = True

        monkeypatch.setattr(cli, "ScenarioPlanner", lambda **kw: FakePlanner())
        args = argparse.Namespace()
        planner = cli._make_planner(
            driver_config, args, None, Scenario(name="s"), object(), lambda _m: None
        )
        assert planner is not None
        assert built.get("restored") is True

    def test_no_auto_scenarios_switches_it_off(self, driver_config):
        import argparse

        from neyma_product_driver import cli

        args = argparse.Namespace(no_auto_scenarios=True)
        assert (
            cli._make_planner(
                driver_config, args, None, Scenario(name="s"), object(), lambda _m: None
            )
            is None
        )

    def test_both_flags_are_accepted_by_the_parser(self):
        from neyma_product_driver.cli import build_parser

        parser = build_parser()
        assert parser.parse_args(["run", "--task", "x", "--auto-scenarios"]).auto_scenarios
        assert parser.parse_args(["run", "--task", "x", "--no-auto-scenarios"]).no_auto_scenarios


# --------------------------------------------------------------------------
# 3. Scenario failures still cannot be hand-waved away
# --------------------------------------------------------------------------


class TestVerificationStillCannotBeTalkedAway:
    async def test_an_accept_over_a_failing_required_scenario_becomes_a_fix(self, loop_bits):
        """The one precedence rule that is not ceremony, and it is untouched.

        The evaluator may sincerely believe the product is fine. The suite
        measured something concrete that is not, and measurement wins.
        """
        config, store, state, scenario, _ = loop_bits
        result = await run_control_loop(
            config=config,
            scenario=scenario,
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept(), accept(), accept()]),
            make_executor=lambda d: FakeExecutor(d, passing=False),
            emit=lambda _m: None,
        )
        assert result.status is not RunStatus.ACCEPTED
        assert result.gate is not None and result.gate.blocks_acceptance

    async def test_a_review_cannot_rescue_a_failing_suite(self, loop_bits):
        """A supportive reviewer over a red suite must not produce an ACCEPT."""
        config, store, state, scenario, _ = loop_bits
        reviewer = FakeReviewerSession([supported_review(), supported_review()])
        result = await run_control_loop(
            config=config,
            scenario=scenario,
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept(), accept(), accept()]),
            make_executor=lambda d: FakeExecutor(d, passing=False),
            reviewer_factory=reviewer,
            emit=lambda _m: None,
        )
        assert result.status is not RunStatus.ACCEPTED


# --------------------------------------------------------------------------
# 4. High-consequence changes trigger a focused review, automatically
# --------------------------------------------------------------------------


class TestReviewIsProportionalToRisk:
    @pytest.mark.parametrize(
        "path,surface",
        [
            ("app/effects/effect_executor.py", "effect execution"),
            ("app/billing/payment_intent.py", "payment or banking"),
            ("app/auth/session_token.py", "authentication"),
            ("app/policy/rbac_rules.py", "authorization"),
            ("app/db/tenant_scope.py", "tenant isolation"),
            ("infra/secrets_loader.py", "secrets"),
            ("migrations/0003_drop_column.py", "destructive database operation"),
            ("app/integrations/webhook_sender.py", "write-capable external integration"),
            ("app/notify/email_dispatch.py", "outbound communication"),
            ("app/claims/dispute_flow.py", "claims, legal or compliance behaviour"),
            ("app/safety/kill_switch.py", "runtime safety invariant"),
        ],
    )
    def test_each_named_surface_is_high_consequence(self, path, surface):
        risk = assess_change_risk(diff_files=[path], diff_stat=" 1 file changed, 5 insertions(+)")
        assert risk.level is ChangeRisk.HIGH_CONSEQUENCE, risk.brief()
        assert surface in risk.surfaces

    def test_a_weakened_control_is_high_consequence_on_its_own(self):
        risk = assess_change_risk(
            diff_files=["docs/notes.md"],
            authority_findings=[{"kind": "weakened_control", "detail": "MUST NOT downgraded"}],
        )
        assert risk.level is ChangeRisk.HIGH_CONSEQUENCE
        assert risk.weakened_controls

    @pytest.mark.parametrize("kind", ["weakened_control", "removed_control", "file_deleted"])
    def test_every_authority_finding_kind_is_recognised(self, kind):
        """The kinds the authority module actually emits, not ones invented here."""
        from neyma_product_driver.authority import AuthorityFinding

        risk = assess_change_risk(
            diff_files=["README.md"],
            authority_findings=[AuthorityFinding(path="CLAUDE.md", kind=kind, detail="d")],
        )
        assert risk.level is ChangeRisk.HIGH_CONSEQUENCE, kind

    async def test_weakening_a_control_mid_run_earns_a_review(self, loop_bits, fake_repo):
        """The builder cannot unblock itself by deleting the rule it is failing.

        The run watches authority documents from before the first builder turn,
        so this is measured rather than read back from a file written after the
        run ends — which is where it used to be read from, and that file was
        never populated.
        """
        (fake_repo / "CLAUDE.md").write_text(
            "# authority\nThe builder MUST NOT write outside the repository.\n"
        )
        subprocess.run(["git", "commit", "-aqm", "state the control"], cwd=fake_repo, check=True)

        class WeakeningBuilder(FakeBuilder):
            async def send(self, prompt: str, timeout_s: int | None = None):
                (fake_repo / "CLAUDE.md").write_text(
                    "# authority\nThe builder should avoid writing outside the repository.\n"
                )
                return await super().send(prompt, timeout_s)

        reviewer = FakeReviewerSession([supported_review()])
        result = await drive(
            loop_bits,
            builder=WeakeningBuilder(),
            evaluator=FakeEvaluator([accept()]),
            reviewer_factory=reviewer,
        )
        assert result.risk.weakened_controls, result.risk.brief()
        assert reviewer.launches == 1
        assert result.authority_report.get("weakening_detected") is True

    def test_a_large_change_is_meaningful_not_high_consequence(self):
        risk = assess_change_risk(
            diff_files=[f"app/ui/panel_{i}.tsx" for i in range(20)],
            diff_stat=" 20 files changed, 900 insertions(+), 100 deletions(-)",
        )
        assert risk.level is ChangeRisk.MEANINGFUL
        assert risk.requires_independent_review is False

    def test_a_meaningful_change_that_worked_first_time_needs_no_review(self):
        risk = assess_change_risk(
            diff_files=[f"app/ui/panel_{i}.tsx" for i in range(20)],
            diff_stat=" 20 files changed, 900 insertions(+)",
        )
        assert risk.warrants_independent_review(iterations=1, uncovered_risks=0) is False
        assert risk.warrants_independent_review(iterations=2, uncovered_risks=0) is True

    def test_an_ordinary_change_never_warrants_a_review(self):
        risk = assess_change_risk(
            diff_files=["app/ui/label.tsx"], diff_stat=" 1 file changed, 2 insertions(+)"
        )
        assert risk.warrants_independent_review(iterations=5, uncovered_risks=3) is False

    async def test_a_high_consequence_change_is_reviewed_without_being_asked(
        self, loop_bits, fake_repo
    ):
        _touch_and_stage(fake_repo, "app/auth/session_token.py", "TOKEN = 1\n")
        reviewer = FakeReviewerSession([supported_review()])
        result = await drive(
            loop_bits,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            reviewer_factory=reviewer,
        )
        assert reviewer.launches == 1
        assert result.status is RunStatus.ACCEPTED
        assert result.reviews and result.reviews[0].verdict == "SUPPORTED"

    async def test_the_review_prompt_names_the_surface_it_was_called_for(
        self, loop_bits, fake_repo
    ):
        """A focused review, not a re-derivation of the whole repository."""
        _touch_and_stage(fake_repo, "app/db/tenant_scope.py", "SCOPE = 1\n")
        reviewer = FakeReviewerSession([supported_review()])
        await drive(
            loop_bits,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            reviewer_factory=reviewer,
        )
        assert reviewer.prompts
        assert "tenant isolation" in reviewer.prompts[0]
        assert "WHY THIS REVIEW WAS TRIGGERED" in reviewer.prompts[0]

    async def test_a_review_that_cannot_be_launched_is_never_an_accept(
        self, loop_bits, fake_repo
    ):
        _touch_and_stage(fake_repo, "app/billing/payouts.py", "AMOUNT = 1\n")
        result = await drive(
            loop_bits,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            reviewer_factory=None,
        )
        assert result.status is RunStatus.NEEDS_INDEPENDENT_REVIEW


# --------------------------------------------------------------------------
# 5. NEEDS_CHANGES goes back to the same builder, automatically
# --------------------------------------------------------------------------


class TestReviewFindingsReturnToTheBuilder:
    async def test_a_refusing_review_becomes_a_correction_for_the_same_builder(
        self, loop_bits, fake_repo
    ):
        """The step that used to be the founder's: read, decide, paste, wait."""
        _touch_and_stage(fake_repo, "app/db/tenant_scope.py", "SCOPE = 1\n")
        builder = FakeBuilder()
        reviewer = FakeReviewerSession([refusing_review(), supported_review()])
        result = await drive(
            loop_bits,
            builder=builder,
            evaluator=FakeEvaluator([accept(), accept()]),
            reviewer_factory=reviewer,
        )
        assert len(builder.prompts) >= 2, "the reviewer's findings never reached the builder"
        correction = builder.prompts[1]
        assert "INDEPENDENT REVIEW" in correction
        assert "app/repo/loads.py:88" in correction
        assert builder.session_id == "builder-session-1", "a new builder session was started"
        assert result.status is RunStatus.ACCEPTED

    async def test_a_reviewer_that_keeps_refusing_becomes_a_founder_question(
        self, loop_bits, fake_repo
    ):
        """Bounded. Two refusals is a decision, not a defect to keep grinding on."""
        _touch_and_stage(fake_repo, "app/db/tenant_scope.py", "SCOPE = 1\n")
        reviewer = FakeReviewerSession([refusing_review(), refusing_review(), refusing_review()])
        result = await drive(
            loop_bits,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept(), accept(), accept()]),
            reviewer_factory=reviewer,
        )
        assert result.status is RunStatus.NEEDS_USER
        assert reviewer.launches <= 2

    def test_the_review_budget_cannot_be_raised_without_limit(self):
        ReviewPolicyConfig(max_automatic_reviews=3)
        with pytest.raises(ValueError):
            ReviewPolicyConfig(max_automatic_reviews=9)


# --------------------------------------------------------------------------
# 6. Environment contradictions become investigations, not founder homework
# --------------------------------------------------------------------------


class TestContradictionsBecomeInvestigations:
    def test_a_suite_failure_against_a_success_report_triggers_investigation(self):
        from neyma_product_driver.investigator import should_investigate

        triggered, reason = should_investigate(
            builder_report="All tests pass and the implementation is complete.",
            suite_failed=True,
        )
        assert triggered
        assert "reported success" in reason

    def test_low_evaluator_confidence_triggers_investigation(self):
        from neyma_product_driver.investigator import should_investigate

        triggered, _ = should_investigate(evaluator_confidence=0.2)
        assert triggered

    def test_an_environment_blocker_is_the_investigators_problem(self):
        resolution = _FakeResolution(status="BLOCKED_ENVIRONMENT")
        triggered, reason = protocol_warrants_investigation(resolution)
        assert triggered
        assert "environment" in reason.lower()

    def test_a_self_contradictory_repository_is_investigated_not_terminal(self):
        resolution = _FakeResolution(status="BLOCKED_AUTHORITY", conflicts=[object()])
        assert protocol_warrants_investigation(resolution)[0] is True
        assert bool(requires_founder_authority(resolution)) is False

    async def test_the_loop_invokes_the_investigator_for_a_protocol_blocker(self, loop_bits):
        invoked: list[str] = []

        class FakeInvestigation:
            result = _FakeInvestigationResult()

            def investigate(self, **kw):
                invoked.append(kw.get("issue", ""))
                return self

        await drive(
            loop_bits,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            protocol_resolver=_FakeResolver(_FakeResolution(status="BLOCKED_ENVIRONMENT")),
            investigator_factory=lambda _reason: FakeInvestigation(),
        )
        assert invoked, "an environmental blocker reached nobody"


# --------------------------------------------------------------------------
# 7. Obsolete repository rules disappear when the repository stops stating them
# --------------------------------------------------------------------------


class TestObsoleteRulesDisappearWithTheRepository:
    def test_no_finalizer_protocol_means_no_finalizer_receipt_is_demanded(self, tmp_path):
        """The repository stopped saying it, so the driver stops asking for it."""
        repo = _registry_repo(tmp_path, declare_finalizer=False)
        audit = CompletionAuditor(repo).audit("Phase P1 is COMPLETE.")
        assert not any("finalizer" in m.lower() for m in audit.missing_evidence)
        assert not any("clean-clone" in m.lower() for m in audit.missing_evidence)

    def test_a_declared_finalizer_protocol_is_still_enforced(self, tmp_path):
        """And the moment it says it again, the requirement is back."""
        repo = _registry_repo(tmp_path, declare_finalizer=True)
        audit = CompletionAuditor(repo).audit("Phase P1 is COMPLETE.")
        assert any("finalizer" in m.lower() for m in audit.missing_evidence)

    def test_a_false_finalizer_claim_is_caught_either_way(self, tmp_path):
        """Honesty is not conditional on protocol. A lie is a lie."""
        repo = _registry_repo(tmp_path, declare_finalizer=False)
        audit = CompletionAuditor(repo).audit("The finalizer ran and passed.")
        assert audit.decision is AuditDecision.CONTRADICTED

    def test_a_cited_file_that_does_not_exist_is_always_a_contradiction(self, tmp_path):
        repo = _registry_repo(tmp_path, declare_finalizer=False)
        audit = CompletionAuditor(repo).audit("Done — see docs/evidence/proof-of-nothing.md")
        assert audit.decision is AuditDecision.CONTRADICTED

    def test_no_declared_rules_means_the_auditor_demands_no_artifacts(self, tmp_path):
        repo = _registry_repo(tmp_path, declare_finalizer=False)
        assert CompletionAuditor(repo, declared_rules=frozenset()).declared_rules() == frozenset()

    def test_a_topology_difference_alone_does_not_need_the_founder(self):
        """A commit-shape finding is a diagnostic now, not a full stop."""
        resolution = _FakeResolution(
            status="VIOLATION",
            violations=[_FakeViolation(rule_id="COMMIT_TOPOLOGY-1", detail="two content commits")],
            options=[_FakeOption()],
        )
        assert bool(requires_founder_authority(resolution)) is False


# --------------------------------------------------------------------------
# 8. The boundaries that were never ceremony are exactly where they were
# --------------------------------------------------------------------------


class TestHardBoundariesAreUnmoved:
    def test_a_repair_that_rewrites_history_still_stops_the_run(self):
        resolution = _FakeResolution(
            status="VIOLATION", options=[_FakeOption(rewrites_history=True)]
        )
        verdict = requires_founder_authority(resolution)
        assert verdict.requires_founder is True
        assert "history" in verdict.reason

    def test_a_repair_that_touches_pushed_history_still_stops_the_run(self):
        resolution = _FakeResolution(
            status="VIOLATION", options=[_FakeOption(affects_remote_history=True)]
        )
        assert requires_founder_authority(resolution).requires_founder is True

    def test_a_destructive_only_repair_still_stops_the_run(self):
        resolution = _FakeResolution(
            status="VIOLATION",
            options=[_FakeOption(destructive_operations=["git branch -D archive/p4"])],
        )
        verdict = requires_founder_authority(resolution)
        assert verdict.requires_founder is True
        assert verdict.operations == ["git branch -D archive/p4"]

    async def test_the_loop_asks_rather_than_performing_a_history_rewrite(self, loop_bits):
        result = await drive(
            loop_bits,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            protocol_resolver=_FakeResolver(
                _FakeResolution(status="VIOLATION", options=[_FakeOption(rewrites_history=True)])
            ),
        )
        assert result.status is RunStatus.REQUIRES_APPROVAL
        assert result.final_decision.decision is Decision.ASK_USER

    @pytest.mark.parametrize(
        "command",
        [
            "git push origin main",
            "git push --force origin main",
            "git reset --hard HEAD~3",
            "git remote set-url origin git@example.com:x/y.git",
            "cat ~/.aws/credentials",
        ],
    )
    def test_the_command_guard_is_untouched(self, command, fake_repo):
        from neyma_product_driver.command_guard import classify_command

        assert classify_command(command), f"{command!r} is no longer blocked"

    def test_the_driver_still_refuses_to_commit_or_push_on_your_behalf(self, fake_repo):
        with pytest.raises(ValueError):
            DriverConfig(neyma_repo=fake_repo, allow_auto_push=True)
        with pytest.raises(ValueError):
            DriverConfig(neyma_repo=fake_repo, allow_auto_commit=True)

    def test_promotion_into_the_permanent_suite_is_still_manual(self, fake_repo):
        from neyma_product_driver.config import ScenarioGenerationConfig

        with pytest.raises(ValueError):
            ScenarioGenerationConfig(promotion_requires_approval=False)

    def test_the_builder_is_never_told_it_may_push(self):
        from neyma_product_driver.prompts import (
            BUILDER_SYSTEM_APPEND,
            builder_correction_prompt,
            builder_task_prompt,
        )

        for text in (
            BUILDER_SYSTEM_APPEND,
            builder_task_prompt("t", "s"),
            builder_correction_prompt("c", 1),
        ):
            assert "Do NOT push" in text or "Do not push" in text

    def test_the_builder_is_told_ordinary_git_is_allowed(self):
        from neyma_product_driver.prompts import builder_task_prompt

        assert "local commit" in builder_task_prompt("t", "s")


# --------------------------------------------------------------------------
# 9. The report is written for the founder, not for the repository
# --------------------------------------------------------------------------


class TestTheShippingReport:
    async def test_it_leads_with_whether_this_can_ship(self, loop_bits, capsys):
        config, store, state, _scenario, _make = loop_bits
        result = await drive(
            loop_bits, builder=FakeBuilder(), evaluator=FakeEvaluator([accept()])
        )
        from neyma_product_driver.cli import _report_founder_summary

        _report_founder_summary(result, store, config)
        printed = capsys.readouterr().out

        assert "READY TO SHIP" in printed
        assert "founder action required:" in printed
        assert "push / merge" in printed
        for question in (
            "What can Neyma do now",
            "What real workflow did you exercise",
            "How many scenarios did you run",
            "What failures did you discover",
            "What did the builder fix",
            "What still fails",
            "What consequential risks remain",
            "Is it ready for you to try",
            "Is it ready to push or merge",
            "What should we build next",
        ):
            assert question in printed, question

    async def test_git_mechanics_do_not_bury_the_product_answer(self, loop_bits, capsys):
        """Protocol findings are one line, not the headline."""
        config, store, state, _scenario, _make = loop_bits
        result = await drive(
            loop_bits,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept()]),
            protocol_resolver=_FakeResolver(
                _FakeResolution(
                    status="VIOLATION",
                    violations=[_FakeViolation(rule_id="X", detail="a stale receipt")],
                    options=[_FakeOption()],
                )
            ),
        )
        from neyma_product_driver.cli import _report_founder_summary

        _report_founder_summary(result, store, config)
        printed = capsys.readouterr().out

        assert "stale receipt" not in printed
        assert "did not block this run" in printed
        assert printed.index("READY TO SHIP") < printed.index("recorded and did not block")

    async def test_an_unshippable_run_says_so(self, loop_bits, capsys):
        config, store, state, scenario, _make = loop_bits
        result = await run_control_loop(
            config=config,
            scenario=scenario,
            store=store,
            state=state,
            builder=FakeBuilder(),
            evaluator=FakeEvaluator([accept(), accept(), accept()]),
            make_executor=lambda d: FakeExecutor(d, passing=False),
            emit=lambda _m: None,
        )
        from neyma_product_driver.cli import _report_founder_summary

        _report_founder_summary(result, store, config)
        printed = capsys.readouterr().out

        assert "NOT READY TO SHIP" in printed
        assert "unresolved material findings:  0" not in printed


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _touch_and_stage(repo: Path, rel: str, content: str) -> None:
    """Create a file and stage it, so ``git diff --cached --name-only`` sees it."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", rel], cwd=repo, check=True)


def _registry_repo(tmp_path: Path, *, declare_finalizer: bool) -> Path:
    """A repository with one READY unit, optionally declaring a finalizer rule."""
    repo = tmp_path / "target"
    (repo / "docs" / "implementation").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("# authority\n")
    (repo / "docs" / "implementation" / "IMPLEMENTATION-REGISTRY.yaml").write_text(
        "units:\n"
        "  - unit_id: P1\n"
        "    name: the unit\n"
        "    status: READY\n"
        "    acceptance_criteria:\n"
        "      - criterion: it works\n"
        "        weight: 1.0\n"
        "        result: PENDING\n",
        encoding="utf-8",
    )
    if declare_finalizer:
        (repo / "docs" / "implementation" / "COMMIT-PROTOCOL.md").write_text(
            "# Commit protocol\n\n"
            "Derived status MUST be written only by the finalizer; no other session may "
            "write it.\n\n"
            "The clean-clone gate MUST run and produce a receipt before a unit is COMPLETE.\n",
            encoding="utf-8",
        )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


@dataclass
class _FakeOption:
    option_id: str = "A"
    rewrites_history: bool = False
    affects_remote_history: bool = False
    destructive_operations: list[str] = field(default_factory=list)
    disqualified: bool = False


@dataclass
class _FakeViolation:
    rule_id: str = ""
    detail: str = ""
    observed_state: str = ""


class _FakeStatus:
    def __init__(self, value: str) -> None:
        self.value = value


class _FakeResolution:
    def __init__(
        self,
        status: str = "CONSISTENT",
        violations: list[Any] | None = None,
        options: list[Any] | None = None,
        conflicts: list[Any] | None = None,
    ) -> None:
        self.status = _FakeStatus(status)
        self.violations = violations or []
        self.options = options or []
        self.recommended_option = self.options[0] if self.options else None
        self.conflicts = conflicts or []
        self.deadlocks: list[Any] = []
        self.environment_blockers = (
            [_FakeBlocker()] if status == "BLOCKED_ENVIRONMENT" else []
        )
        self.sources_read: list[str] = []
        self.next_safe_action = "look at it"

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return {"status": self.status.value}

    def summary_block(self) -> str:
        return f"protocol: {self.status.value}"


@dataclass
class _FakeBlocker:
    description: str = "the gate could not bind a port"
    evidence: list[str] = field(default_factory=list)

    def render(self) -> str:
        return self.description


class _FakeResolver:
    def __init__(self, resolution: _FakeResolution) -> None:
        self._resolution = resolution

    def resolve(self, **_kw) -> _FakeResolution:
        return self._resolution


class _FakeInvestigationResult:
    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return {"conclusion": "the port was already bound"}

    def summary_block(self) -> str:
        return "INVESTIGATION: the port was already bound"
