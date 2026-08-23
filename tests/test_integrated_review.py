"""Required independent review as a transition inside the run loop.

The failure this file pins down is the P6/M3 run. Product Driver built M3,
verified it adversarially, and then stopped at ``AWAITING_INDEPENDENT_REVIEW``.
The founder ran a separate ``review`` command by hand, read the verdict by hand,
decided by hand that nothing needed sending back, and started the driver again
by hand. And the review itself was weaker than it looked: the reviewer could not
execute the M3 probe, suite or mutation battery, so part of what it "verified"
was Product Driver's own captured output handed back to it.

What is asserted here, in order:

1. a task that owes no review still behaves exactly as it did;
2. a task that owes one gets it, launched by the run, with no founder relay;
3. the reviewer is a different session from the builder, always;
4. the reviewer can re-run this repository's deterministic verification, and
   cannot write, commit, push, deploy, install, reach a network or read a
   secret — by any route;
5. a supported review lets the loop finish, and is bound to the exact tree it
   read;
6. a grounded refusal goes back to the SAME builder, and the correction is
   re-reviewed by a NEW reviewer against the corrected tree;
7. a review of an older tree can never satisfy a newer one;
8. an unresolvable review fails closed, and never becomes a product change;
9. an external action is reported as an external action, never manufactured;
10. accepting a task still does not complete the phase around it;
11. and — the weakness this file's own architecture still carried after all of
    the above — "the reviewer reproduced runtime evidence" means an oracle was
    satisfied by an observation, not that the boundary allowed a command. A
    successful `git status` used to be indistinguishable from a probe that ran
    and held.

Every Claude session is faked. Nothing here consumes Claude usage, and nothing
here touches the real Neyma repository.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from neyma_product_driver.cli import (
    _review_headline,
    _reviewer_command_policy,
    run_control_loop,
)
from neyma_product_driver.completion_auditor import AuditDecision, CompletionAuditor
from neyma_product_driver.config import DriverConfig, ReviewPolicyConfig
from neyma_product_driver.context import RepositoryContextLoader
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import (
    AssertionResult,
    Decision,
    EvaluatorDecision,
    RunState,
    RunStatus,
    ScenarioResult,
)
from neyma_product_driver.review_cycle import (
    BlockerKind,
    ReviewLedger,
    ReviewRoute,
    ReviewTrigger,
    TreeFingerprint,
    capture_fingerprint,
    grounded_findings,
    resolve_review_requirement,
    route_review,
)
from neyma_product_driver.reviewer import (
    IndependentReview,
    ReviewBlocker,
    ReviewCommand,
    ReviewCommandExpectation,
    ReviewFinding,
    IndependentReviewerSession,
    reviewer_system_prompt,
)
from neyma_product_driver.reviewer_boundary import (
    ReviewerCommandPolicy,
    classify_reviewer_tool,
)
from neyma_product_driver.reviewer_evidence import (
    DeclaredExpectations,
    EvidenceExpectation,
    EvidenceStatus,
    ReproducedEvidence,
    VerificationKind,
    classify_verification_kind,
    expectations_from_scenarios,
    observation_from_tool_response,
)
from neyma_product_driver.run_journal import RunJournal
from neyma_product_driver.scenario_validation import ApprovedCommands
from neyma_product_driver.scenarios import Scenario
from neyma_product_driver.task_scope import TaskResult

from test_scoped_completion import (
    HONEST_M3_REPORT,
    SIMPLIFIED_CLAUDE_MD,
    TASK_M3,
    PhaseRepo,
)

#: The same authority with the review rule taken out. A repository that states
#: no review rule is not given one it never asked for.
NO_REVIEW_CLAUDE_MD = SIMPLIFIED_CLAUDE_MD.replace(
    "A change that touches an effect boundary needs builder plus one focused\n"
    "independent review by a session that did not write it, before merge.",
    "A change that touches an effect boundary needs a careful diff read.",
)

TASK_PHASE_ACCEPTANCE = (
    "Complete P6 and take it through phase acceptance. All thirteen machines."
)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


@dataclass
class FakeTurn:
    text: str = HONEST_M3_REPORT
    session_id: str | None = "builder-session-1"
    tool_uses: list[str] = field(default_factory=list)
    denied_requests: list[str] = field(default_factory=list)
    is_error: bool = False
    error_detail: str = ""


class FakeBuilder:
    """One persistent session. Optionally edits the repository each turn.

    ``edits`` matters: a builder that changes nothing leaves the repository
    fingerprint identical, and half of what this file asserts is about what
    happens when it moves.
    """

    def __init__(self, repo: Path | None = None, edits: bool = False) -> None:
        self.session_id = "builder-session-1"
        self.prompts: list[str] = []
        self.repo = repo
        self.edits = edits
        self.turns = 0

    async def send(self, prompt: str, timeout_s: int | None = None) -> FakeTurn:
        self.prompts.append(prompt)
        self.turns += 1
        if self.edits and self.repo is not None:
            (self.repo / "src" / "external_effect.py").write_text(
                f"# the unit under construction, revision {self.turns}\n"
            )
        return FakeTurn()


class FakeEvaluator:
    def __init__(self, decisions: list[EvaluatorDecision] | None = None) -> None:
        self.session_id = "evaluator-session-1"
        self.decisions = list(decisions or [])

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision:
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
                AssertionResult(kind="expect_visible", target="the machine", passed=self.passing)
            ],
        )


class FakeReviewer:
    """A reviewer factory that records what each session was bound to.

    It answers the two questions this whole design turns on: was a NEW session
    created, and which repository state was it given?
    """

    def __init__(self, reviews: list[IndependentReview] | None = None) -> None:
        self.reviews = list(reviews or [])
        self.prompts: list[str] = []
        self.launches = 0
        self.bindings: list[dict[str, Any]] = []
        self.session_ids: list[str] = []
        self._binding: dict[str, Any] = {}

    def __call__(self, **binding: Any) -> "FakeReviewer":
        self.launches += 1
        self._binding = dict(binding)
        self.bindings.append(dict(binding))
        return self

    async def __aenter__(self) -> "FakeReviewer":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    @property
    def command_policy(self) -> Any:
        return None

    async def review(self, prompt: str) -> IndependentReview:
        self.prompts.append(prompt)
        review = self.reviews.pop(0) if self.reviews else supported()
        # What a real session does in `bind`: attach the harness's own record of
        # what was reviewed, and give the session a fresh identity.
        session_id = f"reviewer-session-{self.launches}"
        self.session_ids.append(session_id)
        review.reviewer_session_id = session_id
        review.builder_session_id = str(self._binding.get("builder_session_id", "") or "")
        review.review_scope_id = str(self._binding.get("scope_id", "") or "")
        fingerprint = self._binding.get("fingerprint")
        if fingerprint is not None:
            review.reviewed_fingerprint = fingerprint.to_dict()
        return review


def accept(**kw) -> EvaluatorDecision:
    return EvaluatorDecision(
        decision=Decision.ACCEPT,
        summary="the machine behaved",
        observed_behavior=["one winner, never two"],
        **kw,
    )


#: The oracle the reviewer in these fixtures declared for the suite it re-ran.
#: Named, deterministic, and — this is the part that matters — satisfied by an
#: observation the harness itself recorded rather than by the reviewer saying so.
SUITE_ORACLE = EvidenceExpectation(
    name="the effect-boundary suite passes",
    expect_exit_code=0,
    expect_contains=("passed",),
    source="reviewer-declared",
)


def supported(**kw) -> IndependentReview:
    """A review that really did reproduce runtime evidence.

    It carries the whole chain, because after this change nothing shorter counts:
    the command, the boundary's decision, the harness's observation of the exit
    code and output, the named expectation, and the fact that the observation
    satisfied it.
    """
    return IndependentReview(
        verdict="SUPPORTED",
        summary="the claim CAS admits exactly one winner; I re-ran the probe",
        confidence=0.9,
        evidence_reproduced=True,
        claimed_evidence_reproduced=True,
        commands_run=[
            ReviewCommand(
                command="pytest -q",
                purpose="re-run the effect-boundary suite",
                what_it_showed="green",
                expectation=ReviewCommandExpectation(
                    name=SUITE_ORACLE.name,
                    expect_exit_code=0,
                    expect_contains=["passed"],
                ),
            )
        ],
        executed_commands=[
            {
                "command": "pytest -q",
                "allowed": True,
                "basis": "read-only",
                "observed": True,
                "exit_code": 0,
            }
        ],
        reproduced_evidence=[
            ReproducedEvidence(
                command_requested="pytest -q",
                command_executed="pytest -q",
                exit_code=0,
                observed="12 passed in 1.20s",
                expectation=SUITE_ORACLE,
                expectation_satisfied=True,
                kind=VerificationKind.RUNTIME,
                status=EvidenceStatus.RUNTIME_REPRODUCED,
                detail=f"the observation satisfied {SUITE_ORACLE.name!r}",
                allowed=True,
                basis="read-only verification — pytest",
            ).to_dict()
        ],
        **kw,
    )


def refusing(**kw) -> IndependentReview:
    return IndependentReview(
        verdict="NOT_SUPPORTED",
        summary="the claim CAS is not atomic: the check and the write are separate statements",
        confidence=0.85,
        findings=[
            ReviewFinding(
                finding="the claim reads the row and then updates it in two statements",
                severity="blocker",
                evidence_path="src/external_effect.py:88",
                reasoning="two claimers interleave between the read and the write",
            )
        ],
        blocked_on=ReviewBlocker(kind=BlockerKind.PRODUCT_DEFECT.value, detail="see the finding"),
        **kw,
    )


def insufficient(kind: BlockerKind = BlockerKind.REVIEWER_CAPABILITY, **kw) -> IndependentReview:
    return IndependentReview(
        verdict="INSUFFICIENT_EVIDENCE",
        summary="I could not establish whether the effect is exactly-once",
        confidence=0.3,
        blocked_on=ReviewBlocker(
            kind=kind.value,
            detail=kw.pop("detail", "the probe's output is not reachable from here"),
            requested_action=kw.pop("requested_action", ""),
        ),
        **kw,
    )


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


@pytest.fixture
def m3_repo(tmp_path: Path) -> PhaseRepo:
    """A phase in progress, one unit being built, a stated review rule."""
    return PhaseRepo(tmp_path / "neyma")


def bits(repo: PhaseRepo, tmp_path: Path, *, task: str = TASK_M3, max_iterations: int = 3):
    config = DriverConfig(
        neyma_repo=repo.root,
        driver_root=tmp_path / "driver",
        runs_dir=tmp_path / "driver" / "runs",
        scenarios_dir=tmp_path / "driver" / "scenarios",
        task=task,
        max_iterations=max_iterations,
    )
    assert config.runs_dir is not None
    store = EvidenceStore(config.runs_dir, "20260822-000000")
    state = RunState(run_id=store.run_id, task=task, max_iterations=max_iterations)
    return config, store, state


async def drive(
    repo: PhaseRepo,
    tmp_path: Path,
    *,
    task: str = TASK_M3,
    builder: FakeBuilder | None = None,
    evaluator: FakeEvaluator | None = None,
    reviewer: Any = None,
    passing: bool = True,
    max_iterations: int = 3,
    config_edit: Any = None,
    auditor: bool = True,
):
    config, store, state = bits(repo, tmp_path, task=task, max_iterations=max_iterations)
    if config_edit is not None:
        config_edit(config)
    return await run_control_loop(
        config=config,
        scenario=Scenario(name="p6-m3"),
        store=store,
        state=state,
        builder=builder or FakeBuilder(repo.root),
        evaluator=evaluator or FakeEvaluator(),
        make_executor=lambda d: FakeExecutor(d, passing=passing),
        emit=lambda _m: None,
        repo_loader=RepositoryContextLoader(repo.root),
        auditor=CompletionAuditor(repo.root) if auditor else None,
        reviewer_factory=reviewer,
    ), store


# ==========================================================================
# 1. A task that owes no review behaves exactly as it did
# ==========================================================================


class TestNoReviewRequired:
    def test_a_repository_stating_no_review_rule_requires_none(self, m3_repo: PhaseRepo) -> None:
        (m3_repo.root / "CLAUDE.md").write_text(NO_REVIEW_CLAUDE_MD)
        requirement = resolve_review_requirement(
            m3_repo.root, m3_repo.scope(TASK_M3), unit=m3_repo.unit()
        )
        assert requirement.required is False
        assert requirement.brief() == "no independent review is required for this task"

    async def test_a_run_owing_no_review_accepts_without_launching_one(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        (m3_repo.root / "CLAUDE.md").write_text(NO_REVIEW_CLAUDE_MD)
        m3_repo.commit_all("simplify the process")
        reviewer = FakeReviewer()

        result, _store = await drive(m3_repo, tmp_path, reviewer=reviewer)

        assert result.status is RunStatus.ACCEPTED
        assert reviewer.launches == 0
        assert result.review_requirement is not None
        assert result.review_requirement.required is False

    def test_a_phase_review_criterion_does_not_bind_a_unit_inside_the_phase(
        self, m3_repo: PhaseRepo
    ) -> None:
        """The specific inference this design refuses to make.

        P6 lists an ``independent_review`` acceptance criterion because P6 will
        need one at phase acceptance. M3 is not P6. Reading the criterion as
        M3's requirement demands a review of thirteen units, twelve of which do
        not exist.
        """
        (m3_repo.root / "CLAUDE.md").write_text(NO_REVIEW_CLAUDE_MD)
        scope = m3_repo.scope(TASK_M3)
        assert scope.is_nested
        assert any(
            c["criterion"] == "independent_review" and c["result"] == "PENDING"
            for c in m3_repo.unit().acceptance_criteria
        )
        requirement = resolve_review_requirement(m3_repo.root, scope, unit=m3_repo.unit())
        assert requirement.required is False

    def test_a_run_that_merely_named_no_unit_is_not_at_phase_acceptance(
        self, m3_repo: PhaseRepo
    ) -> None:
        """`claims_phase_completion` is the strict evidence default, not intent.

        A task that says "do it" is held to the phase's evidence bar — and is
        still not the run that takes the phase through acceptance, so it does
        not inherit the phase's review criterion.
        """
        (m3_repo.root / "CLAUDE.md").write_text(NO_REVIEW_CLAUDE_MD)
        scope = m3_repo.scope("do the work")
        assert scope.claims_phase_completion is True
        assert scope.phase_completion_requested is False
        assert resolve_review_requirement(
            m3_repo.root, scope, unit=m3_repo.unit()
        ).required is False

    def test_a_task_that_really_claims_the_phase_inherits_its_review_criterion(
        self, m3_repo: PhaseRepo
    ) -> None:
        (m3_repo.root / "CLAUDE.md").write_text(NO_REVIEW_CLAUDE_MD)
        scope = m3_repo.scope(TASK_PHASE_ACCEPTANCE)
        assert scope.phase_completion_requested is True
        requirement = resolve_review_requirement(m3_repo.root, scope, unit=m3_repo.unit())
        assert requirement.required is True
        assert ReviewTrigger.PHASE_ACCEPTANCE_CRITERION in requirement.triggers


# ==========================================================================
# 2. A task that owes one gets it, launched by the run
# ==========================================================================


class TestReviewIsRequiredAndAutomatic:
    def test_the_repository_rule_binds_the_scoped_unit(self, m3_repo: PhaseRepo) -> None:
        requirement = resolve_review_requirement(
            m3_repo.root, m3_repo.scope(TASK_M3), unit=m3_repo.unit()
        )
        assert requirement.required is True
        assert ReviewTrigger.REPOSITORY_AUTHORITY in requirement.triggers
        assert requirement.scope_id == "P6/M3"
        assert requirement.parent_phase_id == "P6"
        assert any("CLAUDE.md" in source for source in requirement.sources)

    def test_a_retired_process_cannot_be_what_requires_the_review(
        self, m3_repo: PhaseRepo
    ) -> None:
        """A historical document mentioning review does not resurrect anything.

        The legacy finalization report states its rules in the present tense
        because they were true when it was written. It is a record, and a record
        cannot be the authority that binds a run.
        """
        (m3_repo.root / "CLAUDE.md").write_text(NO_REVIEW_CLAUDE_MD)
        (m3_repo.impl / "p4-old-review-pass.md").write_text(
            "# P4 — HISTORICAL — NOT CURRENT AUTHORITY\n\n"
            "Independent review MUST be performed by a fresh session.\n"
        )
        m3_repo.commit_all("keep the record")
        requirement = resolve_review_requirement(
            m3_repo.root, m3_repo.scope(TASK_M3), unit=m3_repo.unit()
        )
        assert requirement.required is False

    async def test_the_run_launches_the_review_itself(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        """The whole objective: no founder relay between build and review."""
        reviewer = FakeReviewer([supported()])

        result, _store = await drive(m3_repo, tmp_path, reviewer=reviewer)

        assert reviewer.launches == 1
        assert result.status is RunStatus.ACCEPTED
        assert result.reviews and result.reviews[0].verdict == "SUPPORTED"
        assert result.satisfying_review is not None

    async def test_the_reviewer_is_told_which_unit_and_which_state(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        reviewer = FakeReviewer([supported()])
        await drive(m3_repo, tmp_path, reviewer=reviewer)

        prompt = reviewer.prompts[0]
        assert "WHY THIS REVIEW IS REQUIRED" in prompt
        assert "THE EXACT STATE YOU ARE REVIEWING" in prompt
        assert "P6/M3" in prompt
        assert reviewer.bindings[0]["scope_id"] == "P6/M3"
        assert isinstance(reviewer.bindings[0]["fingerprint"], TreeFingerprint)

    async def test_a_required_review_that_cannot_launch_is_never_an_accept(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        result, _store = await drive(m3_repo, tmp_path, reviewer=None)
        assert result.status is RunStatus.NEEDS_INDEPENDENT_REVIEW
        assert result.satisfying_review is None

    async def test_the_review_step_never_runs_before_the_gate_is_satisfied(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        """A failing scenario is not review-ready; nothing is spent on it."""
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(
            m3_repo, tmp_path, reviewer=reviewer, passing=False, max_iterations=1
        )
        assert reviewer.launches == 0
        assert result.status is not RunStatus.ACCEPTED


# ==========================================================================
# 3. The reviewer is never the builder
# ==========================================================================


class TestReviewerIndependence:
    async def test_the_reviewer_session_is_not_the_builder_session(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        builder = FakeBuilder(m3_repo.root)
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(
            m3_repo, tmp_path, builder=builder, reviewer=reviewer
        )
        review = result.reviews[0]
        assert review.reviewer_session_id == "reviewer-session-1"
        assert review.builder_session_id == builder.session_id
        assert review.reviewer_session_id != review.builder_session_id
        assert result.satisfying_review.independent is True

    async def test_a_review_from_the_builder_session_satisfies_nothing(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        """Structural. The one property the mechanism rests on is checked."""

        class SelfReviewer(FakeReviewer):
            async def review(self, prompt: str) -> IndependentReview:
                review = await super().review(prompt)
                review.reviewer_session_id = "builder-session-1"
                return review

        reviewer = SelfReviewer([supported()])
        result, _store = await drive(m3_repo, tmp_path, reviewer=reviewer)

        assert result.status is RunStatus.NEEDS_INDEPENDENT_REVIEW
        assert result.satisfying_review is None

    def test_the_session_never_resumes_continues_or_forks(self, tmp_path: Path) -> None:
        session = IndependentReviewerSession(tmp_path)
        options = session._options()
        assert options.resume is None
        assert options.continue_conversation is False
        assert options.fork_session is False
        assert options.setting_sources == []

    def test_a_review_never_claims_to_have_inherited_builder_context(self) -> None:
        assert IndependentReview().inherited_builder_context is False


# ==========================================================================
# 4. The reviewer execution boundary
# ==========================================================================

APPROVED = ApprovedCommands(
    [
        ".venv/bin/python scripts/probe_phase6_external_effect.py",
        ".venv/bin/python scripts/mutate_phase6_external_effect.py",
        ".venv/bin/python -m pytest -q eval/tests/test_phase6_external_effect.py",
    ]
)


@pytest.fixture
def policy() -> ReviewerCommandPolicy:
    return ReviewerCommandPolicy(approved=APPROVED, max_commands=40)


class TestTheReviewerCanVerify:
    @pytest.mark.parametrize(
        "command",
        [
            "git diff HEAD",
            "git show HEAD",
            "git status --porcelain",
            "git log --oneline -20",
            "git rev-parse HEAD",
            "git ls-files src",
            "grep -rn CLAIMED src",
            "rg --files-with-matches effect_grants src",
            "ls -la src",
            "find src -name '*.py'",
            "head -n 40 src/external_effect.py",
            "wc -l src/external_effect.py",
            "pytest -q eval/tests/test_phase6_external_effect.py",
            "python -m pytest -q eval/tests",
        ],
    )
    def test_read_only_verification_is_allowed(
        self, policy: ReviewerCommandPolicy, command: str
    ) -> None:
        verdict = policy.decide(command)
        assert verdict.allowed, verdict.reason
        assert "read-only verification" in verdict.basis

    @pytest.mark.parametrize(
        "command",
        [
            ".venv/bin/python scripts/probe_phase6_external_effect.py",
            ".venv/bin/python scripts/probe_phase6_external_effect.py --case forged-capability",
            ".venv/bin/python scripts/mutate_phase6_external_effect.py",
        ],
    )
    def test_this_repositorys_own_deterministic_battery_is_allowed(
        self, policy: ReviewerCommandPolicy, command: str
    ) -> None:
        """The M3 gap, closed. These are the commands the reviewer could not run.

        They are allowed because a human wrote them into a scenario file — the
        same vocabulary the generated-scenario validator uses — not because this
        boundary has an opinion about probes.
        """
        verdict = policy.decide(command)
        assert verdict.allowed, verdict.reason
        assert "this repository declares" in verdict.basis

    def test_read_only_schema_inspection_comes_through_the_scenario_vocabulary(
        self,
    ) -> None:
        """The M3 `expect_state` shape, verbatim.

        `python -c '<code>'` is arbitrary code, so it is not read-only
        verification on its own — and it is exactly how this repository's
        scenario files express schema inspection. It runs because a human wrote
        that entry down, which is the only reason anything runs here.
        """
        schema_check = (
            '.venv/bin/python -c "import sqlite3,tempfile,os,sys; '
            "sys.path.insert(0,'src'); "
            "from freight_recon.schema import create_canonical_schema; "
            "p=os.path.join(tempfile.mkdtemp(),'probe.sqlite3'); "
            "c=sqlite3.connect(p); create_canonical_schema(c); "
            "print(sorted(r[1] for r in c.execute('PRAGMA table_info(effect_grants)')))\""
        )
        declared = ReviewerCommandPolicy(approved=ApprovedCommands([schema_check]))
        verdict = declared.decide(schema_check)
        assert verdict.allowed, verdict.reason

        # And the same string, in a repository that never declared it, does not.
        undeclared = ReviewerCommandPolicy(approved=APPROVED)
        assert undeclared.decide(schema_check).allowed is False

    def test_a_reviewer_with_no_policy_may_run_nothing(self) -> None:
        verdict = classify_reviewer_tool("Bash", {"command": "ls"}, None)
        assert verdict.allowed is False
        assert "read-only" in verdict.reason


class TestTheReviewerCannotChangeAnything:
    @pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
    def test_the_reviewer_cannot_edit_files(
        self, policy: ReviewerCommandPolicy, tool: str
    ) -> None:
        verdict = classify_reviewer_tool(
            tool, {"file_path": "src/external_effect.py", "content": "x"}, policy
        )
        assert verdict.allowed is False
        assert verdict.layer == "tool"

    @pytest.mark.parametrize(
        "command",
        [
            "echo x > src/external_effect.py",
            "tee src/external_effect.py",
            "cp /tmp/x src/external_effect.py",
            "mv src/a.py src/b.py",
            "rm src/external_effect.py",
            "mkdir src/new",
            "touch src/new.py",
            "chmod +x scripts/probe.py",
            "sed -i 's/a/b/' src/external_effect.py",
            "python -c \"open('src/x.py','w').write('x')\"",
        ],
    )
    def test_the_reviewer_cannot_write_through_the_shell(
        self, policy: ReviewerCommandPolicy, command: str
    ) -> None:
        assert policy.decide(command).allowed is False

    @pytest.mark.parametrize(
        "command",
        [
            "git add -A",
            "git commit -m 'fix'",
            "git commit --amend --no-edit",
            "git checkout -- src",
            "git restore src",
            "git stash",
            "git clean -fd",
            "git apply patch.diff",
        ],
    )
    def test_the_reviewer_cannot_commit_or_touch_git_state(
        self, policy: ReviewerCommandPolicy, command: str
    ) -> None:
        assert policy.decide(command).allowed is False

    @pytest.mark.parametrize(
        "command",
        [
            "git push",
            "git push --force origin main",
            "git push origin HEAD",
            "git rebase -i HEAD~3",
            "git reset --hard HEAD~1",
            "git filter-branch --all",
            "git merge origin/main",
        ],
    )
    def test_the_reviewer_cannot_push_merge_or_rewrite_history(
        self, policy: ReviewerCommandPolicy, command: str
    ) -> None:
        assert policy.decide(command).allowed is False

    @pytest.mark.parametrize(
        "command",
        [
            "curl -X POST https://api.example.com/effects",
            "curl -d 'x=1' https://api.example.com",
            "wget https://example.com/x",
            "kubectl apply -f deploy.yaml",
            "terraform apply",
            "aws s3 cp x s3://bucket",
            "docker push registry/image",
            "npm publish",
            "flyctl deploy",
        ],
    )
    def test_the_reviewer_cannot_deploy_or_make_mutating_network_calls(
        self, policy: ReviewerCommandPolicy, command: str
    ) -> None:
        assert policy.decide(command).allowed is False

    @pytest.mark.parametrize(
        "command",
        [
            "cat .env",
            "cat ~/.ssh/id_rsa",
            "cat ~/.aws/credentials",
            "grep -r SECRET ~/.config/gh",
            "security find-generic-password -s x",
        ],
    )
    def test_the_reviewer_cannot_read_secrets(
        self, policy: ReviewerCommandPolicy, command: str
    ) -> None:
        assert policy.decide(command).allowed is False

    def test_the_reviewer_cannot_read_a_secret_through_a_file_tool(
        self, policy: ReviewerCommandPolicy
    ) -> None:
        verdict = classify_reviewer_tool("Read", {"file_path": "/home/x/.ssh/id_ed25519"}, policy)
        assert verdict.allowed is False
        assert verdict.layer == "path"

    @pytest.mark.parametrize(
        "command",
        ["pip install requests", "npm install", "brew install jq", "uv pip install x"],
    )
    def test_the_reviewer_cannot_install_software(
        self, policy: ReviewerCommandPolicy, command: str
    ) -> None:
        assert policy.decide(command).allowed is False

    @pytest.mark.parametrize(
        "command",
        [
            "git status; git push",
            "git diff | sh",
            "grep -rn x src && rm -rf src",
            "echo $(git push)",
            "pytest -q `git push`",
        ],
    )
    def test_composition_cannot_smuggle_a_payload_past_an_allowed_head(
        self, policy: ReviewerCommandPolicy, command: str
    ) -> None:
        """The whole point of refusing composition: the head is not the command."""
        verdict = policy.decide(command)
        assert verdict.allowed is False
        assert verdict.layer in {"composition", "guard", "reviewer-floor"}

    def test_an_approved_scenario_command_still_meets_the_reviewer_floor(self) -> None:
        """A human writing a command into a scenario file is not a licence.

        A repository that one day writes `git commit` into a scenario must not
        thereby hand a reviewer the ability to commit.
        """
        loose = ReviewerCommandPolicy(approved=ApprovedCommands(["git commit -am wip"]))
        verdict = loose.decide("git commit -am wip")
        assert verdict.allowed is False
        assert verdict.layer in {"guard", "reviewer-floor"}

    def test_the_execution_budget_is_bounded(self) -> None:
        bounded = ReviewerCommandPolicy(approved=APPROVED, max_commands=2)
        for _ in range(2):
            bounded.record("git status", bounded.decide("git status"))
        verdict = bounded.decide("git status")
        assert verdict.allowed is False
        assert verdict.layer == "budget"

    def test_every_decision_is_recorded_for_the_run_evidence(
        self, policy: ReviewerCommandPolicy
    ) -> None:
        policy.record("git diff", policy.decide("git diff"))
        policy.record("git push", policy.decide("git push"))
        assert policy.allowed_count == 1
        assert len(policy.refused) == 1
        assert "push" in policy.refused[0].reason


class TestTheBoundaryIsEnforcedNotRequested:
    async def test_the_hook_denies_a_refused_command(self, tmp_path: Path) -> None:
        """A PreToolUse hook, because allowed_tools shadows can_use_tool.

        Adding Bash to the allow list while relying on the permission callback
        would have handed the reviewer an unrestricted shell.
        """
        session = IndependentReviewerSession(
            tmp_path, command_policy=ReviewerCommandPolicy(approved=APPROVED)
        )
        result = await session._pre_tool_use_hook(
            {"tool_name": "Bash", "tool_input": {"command": "git push"}}, None, None
        )
        decision = result["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "push" in decision["permissionDecisionReason"]
        assert session.executions[-1].allowed is False

    async def test_the_hook_allows_and_records_a_permitted_command(
        self, tmp_path: Path
    ) -> None:
        session = IndependentReviewerSession(
            tmp_path, command_policy=ReviewerCommandPolicy(approved=APPROVED)
        )
        assert await session._pre_tool_use_hook(
            {"tool_name": "Bash", "tool_input": {"command": "git diff HEAD"}}, None, None
        ) == {}
        assert session.executions[-1].allowed is True

    async def test_the_hook_denies_a_write_even_though_it_is_disallowed_upstream(
        self, tmp_path: Path
    ) -> None:
        session = IndependentReviewerSession(
            tmp_path, command_policy=ReviewerCommandPolicy(approved=APPROVED)
        )
        result = await session._pre_tool_use_hook(
            {"tool_name": "Write", "tool_input": {"file_path": "src/x.py"}}, None, None
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_bash_is_disallowed_outright_when_execution_is_off(self, tmp_path: Path) -> None:
        options = IndependentReviewerSession(tmp_path)._options()
        assert "Bash" not in options.allowed_tools
        assert "Bash" in options.disallowed_tools

    def test_bash_is_allowed_only_alongside_a_policy(self, tmp_path: Path) -> None:
        session = IndependentReviewerSession(
            tmp_path, command_policy=ReviewerCommandPolicy(approved=APPROVED)
        )
        options = session._options()
        assert "Bash" in options.allowed_tools
        assert "Bash" not in options.disallowed_tools
        assert "Write" in options.disallowed_tools

    def test_the_system_prompt_matches_the_capability(self, tmp_path: Path) -> None:
        without = reviewer_system_prompt(None)
        with_exec = reviewer_system_prompt(ReviewerCommandPolicy(approved=APPROVED))
        assert "cannot execute commands" in without
        assert "USE IT." not in without
        assert "USE IT." in with_exec
        assert "probe_phase6_external_effect" in with_exec

    def test_config_can_switch_execution_off_without_widening_anything(self) -> None:
        config = DriverConfig(
            neyma_repo=Path("."), review=ReviewPolicyConfig(reviewer_can_execute=False)
        )
        assert _reviewer_command_policy(config, Scenario(name="x")) is None
        assert classify_reviewer_tool("Bash", {"command": "ls"}, None).allowed is False


#: A probe this repository declares, with the expectation a human wrote next to
#: it. Used to prove the repository's own oracle is the one that gets applied.
PROBE = ".venv/bin/python scripts/probe_phase6_external_effect.py"


async def reviewer_ran(
    session: IndependentReviewerSession,
    command: str,
    *,
    response: Any = None,
    failed: bool = False,
    error: str = "",
    tool_use_id: str = "",
    observe: bool = True,
) -> None:
    """Drive one command through the reviewer's real hooks.

    Both of them, in the order the SDK fires them: ``PreToolUse`` decides and
    records the request, ``PostToolUse`` records what came back. ``observe=False``
    is the case where the second never arrives — a turn abandoned, a transport
    that dropped the result — which must not read as a command that ran.
    """
    tool_use_id = tool_use_id or f"tool-{len(session.executions) + 1}"
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": tool_use_id,
    }
    await session._pre_tool_use_hook(payload, tool_use_id, None)
    if not observe:
        return
    if failed:
        await session._post_tool_use_failure_hook(
            {**payload, "error": error}, tool_use_id, None
        )
        return
    await session._post_tool_use_hook(
        {**payload, "tool_response": response}, tool_use_id, None
    )


def declaring(command: str, **expectation: Any) -> IndependentReview:
    """A review claiming reproduced evidence, with a named oracle for ``command``."""
    return IndependentReview(
        verdict="SUPPORTED",
        summary="the effect boundary admits exactly one winner",
        evidence_reproduced=True,
        commands_run=[
            ReviewCommand(
                command=command,
                purpose="verify the invariant",
                what_it_showed="everything I needed",
                expectation=ReviewCommandExpectation(**expectation),
            )
        ],
    )


def executing_session(tmp_path: Path, declared: Any = None) -> IndependentReviewerSession:
    return IndependentReviewerSession(
        tmp_path,
        command_policy=ReviewerCommandPolicy(approved=APPROVED, declared=declared),
    )


class TestClaimedEvidenceIsCheckedAgainstTheBoundary:
    def test_a_reviewer_cannot_claim_to_have_run_what_it_never_ran(
        self, tmp_path: Path
    ) -> None:
        """`evidence_reproduced` is corrected downward, never upward."""
        session = executing_session(tmp_path)
        review = session.bind(
            IndependentReview(verdict="SUPPORTED", evidence_reproduced=True)
        )
        assert review.evidence_reproduced is False
        assert review.reproduced_runtime_evidence is False
        assert review.claimed_evidence_reproduced is True
        assert "not reviewer-reproduced" in review.evidence_basis()

    async def test_launching_a_permitted_command_is_not_reproducing_evidence(
        self, tmp_path: Path
    ) -> None:
        """The exact false green this change exists to remove.

        `git diff HEAD` is allowed, it is recorded, and the boundary is entirely
        happy with it. None of that observed one thing about whether the product
        works, and before this the run reported reproduced runtime evidence for
        it anyway.
        """
        session = executing_session(tmp_path)
        await reviewer_ran(session, "git diff HEAD", observe=False)
        review = session.bind(
            IndependentReview(verdict="SUPPORTED", evidence_reproduced=True)
        )
        assert review.commands_allowed, "the boundary did allow it"
        assert review.reproduced_runtime_evidence is False
        assert review.evidence_reproduced is False
        record = review.evidence.records[0]
        assert record.status is EvidenceStatus.OBSERVATION_MISSING
        assert record.allowed is True


# ==========================================================================
# 4b. "Reproduced" means an oracle was satisfied by an observation
# ==========================================================================


class TestReproducedEvidenceMeansASatisfiedOracle:
    async def test_an_allowed_command_with_the_expected_output_is_reproduced(
        self, tmp_path: Path
    ) -> None:
        session = executing_session(tmp_path)
        await reviewer_ran(
            session,
            PROBE,
            response={"exit_code": 0, "stdout": "winners=1\nexactly one winner\n"},
        )
        review = session.bind(
            declaring(
                PROBE,
                name="the effect boundary admits exactly one winner",
                expect_exit_code=0,
                expect_contains=["exactly one winner"],
            )
        )
        assert review.reproduced_runtime_evidence is True
        assert review.evidence_reproduced is True
        record = review.evidence.records[0]
        assert record.status is EvidenceStatus.RUNTIME_REPRODUCED
        assert record.expectation_satisfied is True
        assert record.exit_code == 0
        assert record.command_executed == PROBE
        assert "exactly one winner" in record.observed
        assert "reviewer-reproduced runtime evidence" in review.evidence_basis()

    async def test_an_allowed_command_with_the_wrong_output_is_not_reproduced(
        self, tmp_path: Path
    ) -> None:
        session = executing_session(tmp_path)
        await reviewer_ran(
            session,
            PROBE,
            response={"exit_code": 0, "stdout": "winners=2\ntwo claimers succeeded\n"},
        )
        review = session.bind(
            declaring(
                PROBE,
                name="the effect boundary admits exactly one winner",
                expect_exit_code=0,
                expect_contains=["exactly one winner"],
            )
        )
        assert review.reproduced_runtime_evidence is False
        assert review.evidence_reproduced is False
        record = review.evidence.records[0]
        assert record.status is EvidenceStatus.EXPECTATION_FAILED
        assert record.expectation_satisfied is False
        assert "does not contain" in record.detail

    async def test_output_that_contradicts_a_prohibited_string_is_not_reproduced(
        self, tmp_path: Path
    ) -> None:
        """The scenario `not_contains` shape, used as a reviewer's oracle."""
        session = executing_session(tmp_path)
        await reviewer_ran(
            session,
            PROBE,
            response={"exit_code": 0, "stdout": "grant ok\nDOUBLE EXECUTION detected\n"},
        )
        review = session.bind(
            declaring(
                PROBE,
                name="no effect executes twice",
                expect_exit_code=0,
                expect_absent=["DOUBLE EXECUTION"],
            )
        )
        assert review.reproduced_runtime_evidence is False
        record = review.evidence.records[0]
        assert record.status is EvidenceStatus.EXPECTATION_FAILED
        assert "must not appear" in record.detail

    async def test_an_unexpected_nonzero_exit_is_not_reproduced(
        self, tmp_path: Path
    ) -> None:
        session = executing_session(tmp_path)
        await reviewer_ran(
            session,
            PROBE,
            response={"exit_code": 3, "stdout": "exactly one winner\n"},
        )
        review = session.bind(
            declaring(
                PROBE,
                name="the effect boundary admits exactly one winner",
                expect_exit_code=0,
                expect_contains=["exactly one winner"],
            )
        )
        assert review.reproduced_runtime_evidence is False
        record = review.evidence.records[0]
        assert record.status is EvidenceStatus.EXPECTATION_FAILED
        assert "exit code was 3" in record.detail

    async def test_an_unannounced_nonzero_exit_establishes_nothing(
        self, tmp_path: Path
    ) -> None:
        """A command that failed with nobody expecting it to is not an inspection."""
        session = executing_session(tmp_path)
        await reviewer_ran(session, PROBE, response={"exit_code": 1, "stdout": "traceback"})
        review = session.bind(
            IndependentReview(verdict="SUPPORTED", evidence_reproduced=True)
        )
        record = review.evidence.records[0]
        assert record.status is EvidenceStatus.COMMAND_ERRORED
        assert review.reproduced_runtime_evidence is False

    async def test_a_declared_negative_control_is_reproduced_when_it_holds(
        self, tmp_path: Path
    ) -> None:
        """A probe that MUST fail is evidence exactly as much as one that must pass.

        This is why the oracle carries an expected exit code rather than a
        pass/fail flag: "the forged capability is rejected" is established by a
        non-zero exit and a refusal string, and a rule that only ever counted
        green would throw away half the adversarial battery.
        """
        session = executing_session(tmp_path)
        await reviewer_ran(
            session,
            f"{PROBE} --case forged-capability",
            response={"exit_code": 2, "stdout": "REJECTED: capability signature invalid\n"},
        )
        review = session.bind(
            declaring(
                f"{PROBE} --case forged-capability",
                name="a forged capability is rejected",
                expect_exit_code=2,
                expect_contains=["REJECTED"],
                expect_absent=["granted"],
            )
        )
        assert review.reproduced_runtime_evidence is True
        record = review.evidence.records[0]
        assert record.status is EvidenceStatus.RUNTIME_REPRODUCED
        assert record.exit_code == 2

    async def test_a_command_that_did_not_complete_is_not_reproduced(
        self, tmp_path: Path
    ) -> None:
        session = executing_session(tmp_path)
        await reviewer_ran(
            session, PROBE, failed=True, error="timed out after 120s", tool_use_id="t-1"
        )
        review = session.bind(
            declaring(PROBE, name="the invariant holds", expect_exit_code=0)
        )
        assert review.reproduced_runtime_evidence is False
        record = review.evidence.records[0]
        assert record.status is EvidenceStatus.COMMAND_ERRORED
        assert "timed out" in record.detail

    async def test_missing_output_cannot_satisfy_a_substring_oracle(
        self, tmp_path: Path
    ) -> None:
        session = executing_session(tmp_path)
        await reviewer_ran(session, PROBE, response={"exit_code": 0, "stdout": ""})
        review = session.bind(
            declaring(
                PROBE,
                name="the invariant is printed",
                expect_exit_code=0,
                expect_contains=["exactly one winner"],
            )
        )
        assert review.reproduced_runtime_evidence is False
        assert review.evidence.records[0].status is EvidenceStatus.EXPECTATION_FAILED

    async def test_a_tool_response_shape_this_cannot_read_is_an_absence(
        self, tmp_path: Path
    ) -> None:
        """An unreadable result degrades to "not observed", never to "satisfied"."""
        session = executing_session(tmp_path)
        await reviewer_ran(session, PROBE, response=None)
        review = session.bind(
            declaring(PROBE, name="the invariant holds", expect_exit_code=0)
        )
        record = review.evidence.records[0]
        assert record.status is EvidenceStatus.OBSERVATION_MISSING
        assert review.reproduced_runtime_evidence is False


class TestInspectionIsNotRuntimeEvidence:
    async def test_git_status_alone_is_never_reproduced_runtime_evidence(
        self, tmp_path: Path
    ) -> None:
        """The plainest form of the false green: a successful `git status`."""
        session = executing_session(tmp_path)
        await reviewer_ran(
            session, "git status --porcelain", response={"exit_code": 0, "stdout": ""}
        )
        review = session.bind(
            IndependentReview(verdict="SUPPORTED", evidence_reproduced=True)
        )
        assert review.reproduced_runtime_evidence is False
        assert review.evidence_reproduced is False
        assert review.evidence.records[0].status is EvidenceStatus.REVIEWER_INSPECTED

    async def test_git_status_with_an_oracle_is_structural_not_runtime(
        self, tmp_path: Path
    ) -> None:
        """Declaring an expectation for an inspection does not promote it.

        The expectation held, and that is worth recording — it is a real,
        machine-decided fact about the tree. It is still not a demonstration that
        anything works, so it can never be what makes `evidence_reproduced` true.
        """
        session = executing_session(tmp_path)
        await reviewer_ran(
            session,
            "git status --porcelain",
            response={"exit_code": 0, "stdout": " M src/external_effect.py\n"},
        )
        review = session.bind(
            declaring(
                "git status --porcelain",
                name="the implementation is uncommitted",
                expect_exit_code=0,
                expect_contains=["src/external_effect.py"],
            )
        )
        record = review.evidence.records[0]
        assert record.status is EvidenceStatus.STRUCTURAL_VERIFIED
        assert record.expectation_satisfied is True
        assert review.reproduced_runtime_evidence is False
        assert review.structurally_verified is True
        assert "structurally only" in review.evidence_basis()

    async def test_grep_is_inspection_however_certain_the_reviewer_sounds(
        self, tmp_path: Path
    ) -> None:
        session = executing_session(tmp_path)
        await reviewer_ran(
            session,
            "grep -rn CLAIMED src",
            response={"exit_code": 0, "stdout": "src/external_effect.py:88: CLAIMED\n"},
        )
        review = session.bind(
            IndependentReview(
                verdict="SUPPORTED",
                summary="I confirmed the compare-and-set is atomic by running the probe",
                evidence_reproduced=True,
                commands_run=[
                    ReviewCommand(
                        command="grep -rn CLAIMED src",
                        purpose="confirm atomicity",
                        what_it_showed="the CAS is atomic; the probe passes",
                    )
                ],
            )
        )
        assert review.reproduced_runtime_evidence is False
        assert review.evidence.records[0].status is EvidenceStatus.REVIEWER_INSPECTED

    @pytest.mark.parametrize(
        "command,expected",
        [
            ("git diff HEAD", VerificationKind.STRUCTURAL),
            ("git status --porcelain", VerificationKind.STRUCTURAL),
            ("grep -rn CLAIMED src", VerificationKind.STRUCTURAL),
            ("ls -la src", VerificationKind.STRUCTURAL),
            ("wc -l src/external_effect.py", VerificationKind.STRUCTURAL),
            ("pytest -q eval/tests", VerificationKind.RUNTIME),
            ("python -m pytest -q", VerificationKind.RUNTIME),
            (PROBE, VerificationKind.RUNTIME),
            (".venv/bin/python scripts/mutate_phase6_external_effect.py", VerificationKind.RUNTIME),
        ],
    )
    def test_what_counts_as_running_the_product(
        self, command: str, expected: VerificationKind
    ) -> None:
        assert classify_verification_kind(command) is expected


class TestProseCannotOverrideAnOracle:
    async def test_a_confident_summary_does_not_rescue_a_failed_expectation(
        self, tmp_path: Path
    ) -> None:
        session = executing_session(tmp_path)
        await reviewer_ran(
            session,
            PROBE,
            response={"exit_code": 1, "stdout": "FAILED: two claimers succeeded\n"},
        )
        review = session.bind(
            IndependentReview(
                verdict="SUPPORTED",
                summary="I ran the probe and it confirms exactly one winner. All green.",
                evidence_reproduced=True,
                commands_run=[
                    ReviewCommand(
                        command=PROBE,
                        purpose="prove exactly-once",
                        what_it_showed="passed cleanly, exactly one winner",
                        expectation=ReviewCommandExpectation(
                            name="the effect boundary admits exactly one winner",
                            expect_exit_code=0,
                            expect_contains=["exactly one winner"],
                        ),
                    )
                ],
            )
        )
        assert review.evidence_reproduced is False
        assert review.reproduced_runtime_evidence is False
        assert review.claimed_evidence_reproduced is True
        assert "the claim is not carried" in review.evidence_basis()

    def test_a_prior_product_driver_receipt_is_not_reviewer_reproduction(
        self, tmp_path: Path
    ) -> None:
        """Reasoning from the harness's own records reproduces nothing."""
        session = executing_session(tmp_path)
        review = session.bind(
            IndependentReview(
                verdict="SUPPORTED",
                summary=(
                    "Product Driver's receipt for the M3 probe records exit 0 and the "
                    "invariant string, so the claim is supported."
                ),
                evidence_reproduced=True,
                commands_run=[],
            )
        )
        assert review.evidence_reproduced is False
        assert review.reproduced_runtime_evidence is False
        assert list(review.evidence) == []
        assert "not reviewer-reproduced" in review.evidence_basis()

    def test_an_oracle_that_cannot_fail_is_not_an_oracle(self) -> None:
        """A reviewer cannot manufacture reproduction out of an empty assertion."""
        blank = EvidenceExpectation.from_any(
            {"name": "looks fine", "expect_exit_code": None,
             "expect_contains": ["", "   "], "expect_absent": []}
        )
        assert blank is not None
        assert blank.deterministic is False
        unnamed = EvidenceExpectation.from_any(
            {"name": "", "expect_exit_code": 0, "expect_contains": [], "expect_absent": []}
        )
        assert unnamed is not None
        assert unnamed.deterministic is False


class TestEveryVerificationKeepsItsOwnResult:
    async def test_multiple_commands_preserve_per_evidence_results(
        self, tmp_path: Path
    ) -> None:
        """Four commands, four different outcomes, none of them merged."""
        session = executing_session(tmp_path)
        await reviewer_ran(
            session, PROBE, response={"exit_code": 0, "stdout": "exactly one winner\n"}
        )
        await reviewer_ran(
            session,
            ".venv/bin/python scripts/mutate_phase6_external_effect.py",
            response={"exit_code": 0, "stdout": "0 mutants survived\n"},
        )
        await reviewer_ran(
            session, "git status --porcelain", response={"exit_code": 0, "stdout": " M src/a.py\n"}
        )
        await reviewer_ran(session, "git push", response={"exit_code": 0, "stdout": ""})

        review = session.bind(
            IndependentReview(
                verdict="SUPPORTED",
                evidence_reproduced=True,
                commands_run=[
                    ReviewCommand(
                        command=PROBE,
                        purpose="exactly-once",
                        what_it_showed="one winner",
                        expectation=ReviewCommandExpectation(
                            name="exactly one winner",
                            expect_exit_code=0,
                            expect_contains=["exactly one winner"],
                        ),
                    ),
                    ReviewCommand(
                        command=".venv/bin/python scripts/mutate_phase6_external_effect.py",
                        purpose="mutation battery",
                        what_it_showed="all killed",
                        expectation=ReviewCommandExpectation(
                            name="every mutant is killed",
                            expect_exit_code=0,
                            expect_contains=["1 mutant survived"],
                        ),
                    ),
                    ReviewCommand(
                        command="git status --porcelain",
                        purpose="see the tree",
                        what_it_showed="one modified file",
                    ),
                ],
            )
        )

        statuses = [r.status for r in review.evidence]
        assert statuses == [
            EvidenceStatus.RUNTIME_REPRODUCED,
            EvidenceStatus.EXPECTATION_FAILED,
            EvidenceStatus.REVIEWER_INSPECTED,
            EvidenceStatus.REFUSED,
        ]
        # One satisfied runtime oracle is enough to make the claim true, and the
        # three that did not are still each recorded as what they were.
        assert review.reproduced_runtime_evidence is True
        assert len(review.evidence.runtime_reproduced) == 1
        assert len(review.evidence.failed) == 1
        assert len(review.evidence.refused) == 1
        assert review.evidence.records[0].command_requested == PROBE
        assert review.evidence.records[1].expectation_satisfied is False

    async def test_two_runs_of_the_same_command_are_two_facts(
        self, tmp_path: Path
    ) -> None:
        """Identical command text, different results; neither stands in for the other."""
        session = executing_session(tmp_path)
        await reviewer_ran(
            session, PROBE, tool_use_id="a", response={"exit_code": 0, "stdout": "one winner\n"}
        )
        await reviewer_ran(
            session, PROBE, tool_use_id="b", response={"exit_code": 1, "stdout": "two winners\n"}
        )
        review = session.bind(
            declaring(
                PROBE,
                name="exactly one winner",
                expect_exit_code=0,
                expect_contains=["one winner"],
            )
        )
        assert [r.exit_code for r in review.evidence] == [0, 1]
        assert [r.status for r in review.evidence] == [
            EvidenceStatus.RUNTIME_REPRODUCED,
            EvidenceStatus.EXPECTATION_FAILED,
        ]

    async def test_a_refused_command_can_never_become_evidence(
        self, tmp_path: Path
    ) -> None:
        """Fail-closed: the guard refuses, and nothing downstream can undo that."""
        session = executing_session(tmp_path)
        await reviewer_ran(session, "git push origin HEAD", observe=False)
        review = session.bind(
            declaring(
                "git push origin HEAD",
                name="the branch is pushed",
                expect_exit_code=0,
                expect_contains=["main"],
            )
        )
        record = review.evidence.records[0]
        assert record.allowed is False
        assert record.status is EvidenceStatus.REFUSED
        assert review.reproduced_runtime_evidence is False


class TestTheRepositorysOwnOracleIsTheOneUsed:
    def test_expectations_are_harvested_from_scenario_files(self) -> None:
        scenario = Scenario.model_validate(
            {
                "name": "p6-m3",
                "commands": [
                    {
                        "name": "the effect boundary admits exactly one winner",
                        "run": PROBE,
                        "expect_exit_code": 0,
                        "expect_contains": ["exactly one winner"],
                    }
                ],
                "expect_state": [
                    {
                        "name": "no effect row is executed twice",
                        "command": "sqlite3 db 'select 1'",
                        "contains": ["executed=1"],
                        "not_contains": ["executed=2"],
                    }
                ],
            }
        )
        declared = expectations_from_scenarios([scenario])
        probe = declared.for_command(PROBE)
        assert probe is not None
        assert probe.name == "the effect boundary admits exactly one winner"
        assert probe.expect_contains == ("exactly one winner",)
        assert probe.source == "repository scenario"

        state = declared.for_command("sqlite3 db 'select 1'")
        assert state is not None
        assert state.expect_absent == ("executed=2",)

    def test_an_argument_tail_still_finds_the_declared_expectation(self) -> None:
        declared = DeclaredExpectations(
            [
                (PROBE, EvidenceExpectation(name="base", expect_exit_code=0)),
                (
                    f"{PROBE} --case forged",
                    EvidenceExpectation(name="forged", expect_exit_code=2),
                ),
            ]
        )
        assert declared.for_command(f"{PROBE} --quiet").name == "base"
        assert declared.for_command(f"{PROBE} --case forged").name == "forged"
        assert declared.for_command("pytest -q") is None

    async def test_a_humans_oracle_outranks_the_reviewers_own(
        self, tmp_path: Path
    ) -> None:
        """A reviewer cannot lower the bar a scenario file already set.

        The repository declared what this probe must show. A reviewer that
        declares something weaker for the same command is not the authority on
        it, so the human's expectation is the one applied — and here it fails.
        """
        declared = DeclaredExpectations(
            [
                (
                    PROBE,
                    EvidenceExpectation(
                        name="the effect boundary admits exactly one winner",
                        expect_exit_code=0,
                        expect_contains=("exactly one winner",),
                        source="repository scenario",
                    ),
                )
            ]
        )
        session = executing_session(tmp_path, declared=declared)
        await reviewer_ran(
            session, PROBE, response={"exit_code": 0, "stdout": "done\n"}
        )
        review = session.bind(
            declaring(PROBE, name="it ran", expect_exit_code=0, expect_contains=["done"])
        )
        record = review.evidence.records[0]
        assert record.expectation is not None
        assert record.expectation.source == "repository scenario"
        assert record.status is EvidenceStatus.EXPECTATION_FAILED
        assert review.reproduced_runtime_evidence is False

    def test_carrying_declared_oracles_changes_nothing_about_the_gates(self) -> None:
        """The oracle map is reporting, not policy. It cannot widen the boundary."""
        declared = DeclaredExpectations(
            [("git push", EvidenceExpectation(name="pushed", expect_exit_code=0))]
        )
        loose = ReviewerCommandPolicy(approved=APPROVED, declared=declared)
        strict = ReviewerCommandPolicy(approved=APPROVED)
        for command in ("git push", "rm -rf src", "pip install x", PROBE, "git diff"):
            assert loose.decide(command).allowed == strict.decide(command).allowed


class TestTheObservationIsReadFromTheToolNotTheReviewer:
    @pytest.mark.parametrize(
        "response,exit_code",
        [
            ({"exit_code": 0, "stdout": "ok"}, 0),
            ({"exitCode": 7, "stdout": "ok"}, 7),
            ({"returncode": 2, "stdout": "ok"}, 2),
        ],
    )
    def test_an_exit_code_is_read_wherever_the_sdk_reports_it(
        self, response: dict, exit_code: int
    ) -> None:
        observation = observation_from_tool_response({"command": "pytest"}, response)
        assert observation.exit_code == exit_code
        assert observation.exit_code_inferred is False

    def test_a_result_with_no_exit_code_is_marked_inferred(self) -> None:
        observation = observation_from_tool_response(
            {"command": "pytest"}, {"stdout": "12 passed"}
        )
        assert observation.exit_code == 0
        assert observation.exit_code_inferred is True

    def test_an_unreadable_result_carries_nothing_forward(self) -> None:
        observation = observation_from_tool_response({}, object())
        assert observation.has_result is False
        assert observation.exit_code is None

    def test_output_is_clipped_rather_than_stored_whole(self) -> None:
        observation = observation_from_tool_response(
            {"command": "pytest"}, {"stdout": "x" * 100_000}
        )
        assert len(observation.output) < 20_000
        assert "characters omitted" in observation.output

    async def test_the_command_that_ran_is_recorded_next_to_the_one_requested(
        self, tmp_path: Path
    ) -> None:
        session = executing_session(tmp_path)
        await reviewer_ran(session, PROBE, response={"exit_code": 0, "stdout": "ok"})
        entry = session.executions[-1]
        assert entry.command == PROBE
        assert entry.command_executed == PROBE
        assert entry.observed is True

    async def test_an_observation_with_nothing_to_join_to_is_dropped(
        self, tmp_path: Path
    ) -> None:
        """A PostToolUse for a command the boundary never classified invents nothing."""
        session = executing_session(tmp_path)
        assert session.command_policy is not None
        assert (
            session.command_policy.observe(
                tool_use_id="never-seen", command="pytest -q", exit_code=0
            )
            is None
        )
        assert session.executions == []

    def test_the_observation_hook_never_influences_the_session(self, tmp_path: Path) -> None:
        """It records; it does not decide. Enforcement stays in PreToolUse alone."""
        source = (
            Path(__file__).resolve().parents[1]
            / "neyma_product_driver"
            / "reviewer.py"
        ).read_text(encoding="utf-8")
        body = source[source.index("async def _post_tool_use_hook") : source.index("def _options")]
        assert "permissionDecision" not in body
        assert "deny" not in body


# ==========================================================================
# 5-7. Staleness, correction cycles, and the absence of false convergence
# ==========================================================================


class TestAReviewIsAboutOneExactTree:
    def test_the_fingerprint_moves_when_an_uncommitted_file_changes(
        self, m3_repo: PhaseRepo
    ) -> None:
        """The normal case: the implementation is not committed yet."""
        before = capture_fingerprint(m3_repo.root)
        m3_repo.write("src/external_effect.py", "# corrected\n")
        after = capture_fingerprint(m3_repo.root)
        assert before.head == after.head
        assert before.tree == after.tree
        assert not before.matches(after)

    def test_the_fingerprint_moves_when_a_commit_lands(self, m3_repo: PhaseRepo) -> None:
        before = capture_fingerprint(m3_repo.root)
        m3_repo.write("src/external_effect.py", "# corrected\n")
        m3_repo.commit_all("correct it")
        assert not before.matches(capture_fingerprint(m3_repo.root))

    def test_the_fingerprint_moves_when_an_untracked_file_is_rewritten(
        self, m3_repo: PhaseRepo
    ) -> None:
        """The normal case for a unit being built new: nothing is tracked yet.

        An untracked file has no blob in the object store, so `git diff HEAD`
        says nothing about it. A fingerprint that hashed only untracked *names*
        would let a supported review of one implementation stand over a
        completely rewritten one with the same filename.
        """
        m3_repo.write("src/brand_new_unit.py", "def claim(): pass\n")
        before = capture_fingerprint(m3_repo.root)
        m3_repo.write("src/brand_new_unit.py", "def claim(): raise RuntimeError\n")
        after = capture_fingerprint(m3_repo.root)
        assert before.untracked == after.untracked, "the filename did not change"
        assert not before.matches(after)

    def test_the_fingerprint_sees_inside_a_new_untracked_directory(
        self, m3_repo: PhaseRepo
    ) -> None:
        """`git status --porcelain` collapses an untracked directory into one line."""
        m3_repo.write("src/pkg/mod.py", "A = 1\n")
        before = capture_fingerprint(m3_repo.root)
        m3_repo.write("src/pkg/mod.py", "A = 2\n")
        assert not before.matches(capture_fingerprint(m3_repo.root))

    def test_an_identical_tree_matches_itself(self, m3_repo: PhaseRepo) -> None:
        assert capture_fingerprint(m3_repo.root).matches(capture_fingerprint(m3_repo.root))

    def test_a_clean_tree_carries_no_dirty_component(self, m3_repo: PhaseRepo) -> None:
        fingerprint = capture_fingerprint(m3_repo.root)
        assert fingerprint.dirty_digest == ""
        assert fingerprint.head and fingerprint.tree

    async def test_a_review_cannot_survive_an_untracked_only_correction(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        """End to end: the builder corrects an untracked file and nothing else."""

        class UntrackedOnlyBuilder(FakeBuilder):
            async def send(self, prompt: str, timeout_s: int | None = None) -> FakeTurn:
                self.prompts.append(prompt)
                self.turns += 1
                (m3_repo.root / "src" / "never_committed.py").write_text(
                    f"# revision {self.turns}\n"
                )
                return FakeTurn()

        builder = UntrackedOnlyBuilder(m3_repo.root)
        reviewer = FakeReviewer([refusing(), supported()])
        result, _store = await drive(
            m3_repo, tmp_path, builder=builder, reviewer=reviewer
        )

        assert reviewer.launches == 2
        first, second = reviewer.bindings[0]["fingerprint"], reviewer.bindings[1]["fingerprint"]
        assert not first.matches(second)
        assert result.review_ledger.records[0].stale is True

    def test_reproduced_runtime_evidence_does_not_rescue_a_stale_review(self) -> None:
        """The strongest possible review of the wrong tree is still of the wrong tree.

        Worth pinning separately now that a review can carry a satisfied runtime
        oracle: reproduced evidence is evidence about the state it was produced
        against, and a builder correction retires it exactly as it retires
        anything else.
        """
        ledger = ReviewLedger()
        review = supported()
        assert review.reproduced_runtime_evidence is True
        old = TreeFingerprint(head="a" * 40, tree="b" * 40, dirty_digest="c" * 64)
        new = TreeFingerprint(head="a" * 40, tree="b" * 40, dirty_digest="d" * 64)
        record = ledger.record(review, old, builder_session_id="builder-session-1")
        review.reviewer_session_id = "reviewer-session-1"

        assert ledger.invalidate_stale(new) == [record]
        assert record.stale is True
        assert ledger.satisfying(new) is None
        assert record.satisfies(new) is False

    def test_reproduced_runtime_evidence_does_not_make_a_self_review_independent(
        self,
    ) -> None:
        """A builder measuring its own work is still the builder."""
        ledger = ReviewLedger()
        review = supported()
        review.reviewer_session_id = "builder-session-1"
        fingerprint = TreeFingerprint(head="a" * 40, tree="b" * 40)
        record = ledger.record(
            review, fingerprint, builder_session_id="builder-session-1"
        )
        assert review.reproduced_runtime_evidence is True
        assert record.independent is False
        assert record.satisfies(fingerprint) is False
        assert ledger.satisfying(fingerprint) is None

    def test_a_supported_review_of_an_older_tree_satisfies_nothing(self) -> None:
        old = TreeFingerprint(head="a" * 40, tree="b" * 40, dirty_digest="c" * 64)
        new = TreeFingerprint(head="a" * 40, tree="b" * 40, dirty_digest="d" * 64)
        ledger = ReviewLedger()
        ledger.record(supported(), old, builder_session_id="builder-1")

        assert ledger.satisfying(old) is not None
        assert ledger.invalidate_stale(new)
        assert ledger.satisfying(new) is None
        assert ledger.satisfying(old) is None, "a retired review must stay retired"
        assert ledger.invalidations

    def test_a_refusing_review_never_satisfies_even_at_its_own_tree(self) -> None:
        fingerprint = TreeFingerprint(head="a" * 40, tree="b" * 40)
        ledger = ReviewLedger()
        ledger.record(refusing(), fingerprint, builder_session_id="builder-1")
        assert ledger.satisfying(fingerprint) is None

    def test_a_review_from_the_builder_session_never_satisfies(self) -> None:
        fingerprint = TreeFingerprint(head="a" * 40, tree="b" * 40)
        ledger = ReviewLedger()
        review = supported()
        review.reviewer_session_id = "builder-1"
        ledger.record(review, fingerprint, builder_session_id="builder-1")
        assert ledger.satisfying(fingerprint) is None

    def test_the_auditor_re_derives_the_tree_rather_than_trusting_the_record(
        self, m3_repo: PhaseRepo
    ) -> None:
        """A stale record handed to the auditor discharges nothing.

        The auditor captures the CURRENT fingerprint itself, so a caller cannot
        clear a review requirement by passing a review of an earlier tree.
        """
        scope = m3_repo.scope(TASK_M3)
        ledger = ReviewLedger()
        entry = ledger.record(
            supported(),
            capture_fingerprint(m3_repo.root),
            scope_id=scope.scope_id,
            builder_session_id="builder-1",
        )
        auditor = CompletionAuditor(m3_repo.root)

        fresh = auditor.audit(
            HONEST_M3_REPORT, unit=m3_repo.unit(), scope=scope, satisfying_review=entry
        )
        assert fresh.decision is AuditDecision.VERIFIED

        m3_repo.write("src/external_effect.py", "# changed after the review\n")
        stale = auditor.audit(
            HONEST_M3_REPORT, unit=m3_repo.unit(), scope=scope, satisfying_review=entry
        )
        assert stale.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW


class TestCorrectionCycles:
    async def test_a_grounded_refusal_reaches_the_same_builder(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        builder = FakeBuilder(m3_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m3_repo, tmp_path, builder=builder, reviewer=reviewer
        )

        assert len(builder.prompts) >= 2, "the reviewer's findings never reached the builder"
        correction = builder.prompts[1]
        assert "INDEPENDENT REVIEW" in correction
        assert "src/external_effect.py:88" in correction
        assert builder.session_id == "builder-session-1", "a new builder session was started"
        assert result.status is RunStatus.ACCEPTED

    async def test_a_correction_gets_a_brand_new_reviewer_session(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        builder = FakeBuilder(m3_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])

        result, _store = await drive(
            m3_repo, tmp_path, builder=builder, reviewer=reviewer
        )

        assert reviewer.launches == 2
        assert len(set(reviewer.session_ids)) == 2
        first, second = reviewer.bindings[0]["fingerprint"], reviewer.bindings[1]["fingerprint"]
        assert not first.matches(second), "the second reviewer read the same tree as the first"
        assert result.satisfying_review.fingerprint.matches(second)

    async def test_the_older_review_is_recorded_as_retired_not_reused(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        builder = FakeBuilder(m3_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])
        result, _store = await drive(
            m3_repo, tmp_path, builder=builder, reviewer=reviewer
        )
        assert result.review_ledger.invalidations
        assert result.review_ledger.records[0].stale is True

    async def test_the_second_reviewer_is_told_what_the_first_alleged(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        """It was not there. It is told, and told it is not bound by it."""
        builder = FakeBuilder(m3_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])
        await drive(m3_repo, tmp_path, builder=builder, reviewer=reviewer)

        second = reviewer.prompts[1]
        assert "WHAT AN EARLIER REVIEWER ALLEGED" in second
        assert "You are a different session" in second

    async def test_a_reviewer_that_keeps_refusing_becomes_a_founder_decision(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        builder = FakeBuilder(m3_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), refusing(), refusing()])
        result, _store = await drive(
            m3_repo, tmp_path, builder=builder, reviewer=reviewer
        )
        assert result.status is RunStatus.NEEDS_USER
        assert reviewer.launches <= 2

    async def test_the_supported_review_upgrades_the_scoped_task_result(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        """AWAITING_INDEPENDENT_REVIEW becomes VERIFIED, by re-asking the auditor."""
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(m3_repo, tmp_path, reviewer=reviewer)

        completion = result.audit.completion
        assert completion is not None
        assert completion.task_scope == "P6/M3"
        assert completion.task_result is TaskResult.VERIFIED


class TestRoutingKeepsTheOwnersApart:
    def test_a_refusal_with_no_citable_finding_is_not_a_builder_correction(self) -> None:
        review = IndependentReview(
            verdict="NOT_SUPPORTED",
            summary="I do not like this design",
            findings=[ReviewFinding(finding="it feels wrong", severity="blocker")],
        )
        assert grounded_findings(review) == []
        routing = route_review(
            review,
            refusals_so_far=1,
            correction_budget=1,
            execution_available=True,
            execution_used=True,
        )
        assert routing.route is ReviewRoute.FOUNDER_DECISION

    def test_a_harness_limitation_never_becomes_a_product_change(self) -> None:
        routing = route_review(
            insufficient(BlockerKind.VERIFICATION_HARNESS),
            refusals_so_far=1,
            correction_budget=1,
            execution_available=True,
            execution_used=True,
        )
        assert routing.route is ReviewRoute.UNRESOLVED
        assert "driver's verification gap" in routing.reason

    def test_a_reviewer_that_did_not_use_its_capability_is_asked_once_more(self) -> None:
        first = route_review(
            insufficient(),
            refusals_so_far=1,
            correction_budget=1,
            execution_available=True,
            execution_used=False,
        )
        assert first.route is ReviewRoute.RETRY_WITH_EXECUTION

        second = route_review(
            insufficient(),
            refusals_so_far=2,
            correction_budget=1,
            execution_available=True,
            execution_used=False,
            retried_with_execution=True,
        )
        assert second.route is ReviewRoute.UNRESOLVED

    def test_an_authority_ambiguity_goes_to_the_founder(self) -> None:
        routing = route_review(
            insufficient(BlockerKind.REPOSITORY_AUTHORITY),
            refusals_so_far=1,
            correction_budget=1,
            execution_available=True,
            execution_used=True,
        )
        assert routing.route is ReviewRoute.FOUNDER_DECISION

    def test_an_external_action_is_reported_at_the_boundary(self) -> None:
        review = insufficient(
            BlockerKind.EXTERNAL_ACTION,
            detail="CI only runs after a push, which I may not perform",
            requested_action="push the branch and report the CI result",
        )
        routing = route_review(
            review,
            refusals_so_far=1,
            correction_budget=1,
            execution_available=True,
            execution_used=True,
        )
        assert routing.route is ReviewRoute.EXTERNAL_ACTION
        assert routing.requested_action == "push the branch and report the CI result"


class TestFailClosed:
    async def test_an_unresolvable_review_blocks_rather_than_accepting(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        review = insufficient(BlockerKind.VERIFICATION_HARNESS)
        review.executed_commands = [{"command": "pytest -q", "allowed": True}]
        reviewer = FakeReviewer([review])

        result, _store = await drive(m3_repo, tmp_path, reviewer=reviewer)

        assert result.status is RunStatus.BLOCKED
        assert result.satisfying_review is None
        assert any("unresolved" in note for note in result.state.iterations[-1].notes)

    async def test_an_unresolvable_review_sends_the_builder_nothing(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        """The loop that does not converge, made impossible."""
        review = insufficient(BlockerKind.VERIFICATION_HARNESS)
        review.executed_commands = [{"command": "pytest -q", "allowed": True}]
        builder = FakeBuilder(m3_repo.root)
        reviewer = FakeReviewer([review])

        await drive(m3_repo, tmp_path, builder=builder, reviewer=reviewer)

        assert len(builder.prompts) == 1, "a measurement gap was sent to the builder as a fix"

    async def test_a_reviewer_that_ran_nothing_is_asked_once_more_then_fails_closed(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        """Insufficient evidence from a reviewer that never used its shell.

        Once — with the vocabulary spelled out and the earlier answer in front
        of it. A second identical answer is the answer, and the run stops rather
        than accepting or inventing a correction.
        """
        builder = FakeBuilder(m3_repo.root)
        reviewer = FakeReviewer([insufficient(), insufficient()])

        result, _store = await drive(
            m3_repo, tmp_path, builder=builder, reviewer=reviewer
        )

        assert reviewer.launches == 2
        assert "THIS IS THE SECOND ASK" in reviewer.prompts[1]
        assert result.status is RunStatus.BLOCKED
        assert len(builder.prompts) == 1, "a measurement gap was sent to the builder"

    async def test_an_external_ci_requirement_stops_at_the_founder_boundary(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        review = insufficient(
            BlockerKind.EXTERNAL_ACTION,
            detail="the repository contract requires CI, which only runs after a push",
            requested_action="push the branch, then report what CI said",
        )
        review.executed_commands = [{"command": "pytest -q", "allowed": True}]
        reviewer = FakeReviewer([review])

        result, _store = await drive(m3_repo, tmp_path, reviewer=reviewer)

        assert result.status is RunStatus.NEEDS_USER
        assert "push the branch" in " ".join(result.final_decision.problems)
        # And nothing was fabricated in its place.
        assert result.satisfying_review is None

    async def test_a_failing_suite_still_prevents_acceptance_however_supportive(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        reviewer = FakeReviewer([supported(), supported(), supported()])
        result, _store = await drive(
            m3_repo, tmp_path, reviewer=reviewer, passing=False
        )
        assert result.status is not RunStatus.ACCEPTED


# ==========================================================================
# 8. Task acceptance is still not phase acceptance
# ==========================================================================


class TestTaskAcceptanceIsNotPhaseAcceptance:
    async def test_a_reviewed_task_leaves_the_phase_exactly_where_it_was(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(m3_repo, tmp_path, reviewer=reviewer)

        assert result.status is RunStatus.ACCEPTED
        completion = result.audit.completion
        assert completion.parent_phase == "P6"
        assert completion.parent_phase_execution_state == "IN_PROGRESS"
        assert completion.parent_phase_accepted is False
        joined = " | ".join(completion.does_not_imply)
        assert "P6 is COMPLETE" in joined
        assert "next phase is unblocked" in joined

    async def test_a_supported_review_scores_no_repository_criterion(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        """The reviewer writes no status, and neither does the run."""
        before = (m3_repo.impl / "IMPLEMENTATION-REGISTRY.yaml").read_text()
        reviewer = FakeReviewer([supported()])
        await drive(m3_repo, tmp_path, reviewer=reviewer)
        after = (m3_repo.impl / "IMPLEMENTATION-REGISTRY.yaml").read_text()
        assert before == after
        assert all(
            c["result"] == "PENDING" for c in m3_repo.unit().acceptance_criteria
        )

    async def test_the_registry_is_the_only_thing_that_moves_a_phase(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(m3_repo, tmp_path, reviewer=reviewer)
        assert result.state.task_scope["parent_phase_execution_state"] == "IN_PROGRESS"


# ==========================================================================
# 9. The founder summary tells the truth about the review
# ==========================================================================


def _journal_with_review(**kw) -> RunJournal:
    journal = RunJournal(run_id="r1", task="build P6/M3", repo=".")
    journal.builder_session_id = "builder-session-1"
    journal.record_independent_review(**kw)
    return journal


class TestTheFounderSummaryReportsTheReview:
    def test_no_requirement_says_so_plainly(self) -> None:
        journal = _journal_with_review(requirement=None)
        section = "\n".join(journal._review_lines())
        assert "**Required?** No." in section

    def test_a_required_review_that_never_ran_says_so(self) -> None:
        requirement = resolve_review_requirement(Path("."), _NestedScope())
        requirement.add(ReviewTrigger.CHANGE_RISK, "it touches effect execution")
        journal = _journal_with_review(requirement=requirement, reviews=[])
        section = "\n".join(journal._review_lines())
        assert "**Required?** Yes." in section
        assert "no reviewer produced a verdict" in section
        assert journal.verification_established is False

    def test_a_supported_reproduced_review_is_reported_as_such(self) -> None:
        requirement = resolve_review_requirement(Path("."), _NestedScope())
        requirement.add(ReviewTrigger.REPOSITORY_AUTHORITY, "the repository requires one")
        ledger = ReviewLedger()
        review = supported()
        review.reviewer_session_id = "reviewer-session-1"
        entry = ledger.record(
            review, TreeFingerprint(head="a" * 40, tree="b" * 40), builder_session_id="builder-session-1"
        )
        journal = _journal_with_review(
            requirement=requirement,
            ledger=ledger,
            satisfying=entry,
            reviews=[review],
            automatic=True,
        )
        section = "\n".join(journal._review_lines())
        assert "**Ran automatically?** Yes" in section
        assert "**Verdict:** **SUPPORTED**" in section
        assert "reproduce runtime evidence itself?** **Yes**" in section
        # Not "it ran N commands" — what it ran, what the oracle was, and that
        # the oracle held. A founder has to be able to disbelieve this line.
        assert "what it actually reproduced" in section
        assert "pytest -q" in section
        assert SUITE_ORACLE.name in section
        assert "RUNTIME_REPRODUCED" in section
        assert "different sessions." in section
        assert journal.review_satisfied_scope is True

    def test_a_reviewer_that_only_inspected_is_not_reported_as_reproducing(self) -> None:
        """The false green, at the place a founder would have read it.

        The reviewer ran something, the boundary allowed it, and the old summary
        said runtime evidence had been reproduced. What it actually did was list
        the tree.
        """
        requirement = resolve_review_requirement(Path("."), _NestedScope())
        requirement.add(ReviewTrigger.REPOSITORY_AUTHORITY, "the repository requires one")
        review = IndependentReview(
            verdict="SUPPORTED",
            confidence=0.8,
            evidence_reproduced=False,
            claimed_evidence_reproduced=True,
            reproduced_evidence=[
                ReproducedEvidence(
                    command_requested="git status --porcelain",
                    command_executed="git status --porcelain",
                    exit_code=0,
                    observed=" M src/external_effect.py",
                    kind=VerificationKind.STRUCTURAL,
                    status=EvidenceStatus.REVIEWER_INSPECTED,
                    detail="no deterministic expectation was named",
                    allowed=True,
                ).to_dict()
            ],
        )
        review.reviewer_session_id = "reviewer-session-1"
        journal = _journal_with_review(
            requirement=requirement, reviews=[review], automatic=True
        )
        section = "\n".join(journal._review_lines())
        assert "reproduce runtime evidence itself?** **No.**" in section
        assert "The claim is not carried" in section
        assert "1 further command(s) the reviewer ran with no named expectation" in section
        assert journal.review_reproduced_evidence is False

    def test_a_failed_expectation_is_reported_rather_than_dropped(self) -> None:
        requirement = resolve_review_requirement(Path("."), _NestedScope())
        requirement.add(ReviewTrigger.REPOSITORY_AUTHORITY, "the repository requires one")
        review = IndependentReview(
            verdict="NOT_SUPPORTED",
            reproduced_evidence=[
                ReproducedEvidence(
                    command_requested=".venv/bin/python scripts/probe.py",
                    exit_code=1,
                    expectation=EvidenceExpectation(
                        name="exactly one winner", expect_exit_code=0
                    ),
                    expectation_satisfied=False,
                    kind=VerificationKind.RUNTIME,
                    status=EvidenceStatus.EXPECTATION_FAILED,
                    detail="exit code was 1, not the expected 0",
                    allowed=True,
                ).to_dict()
            ],
        )
        journal = _journal_with_review(
            requirement=requirement, reviews=[review], automatic=True
        )
        section = "\n".join(journal._review_lines())
        assert "Ran without its expectation holding" in section
        assert "exactly one winner" in section
        assert journal.review_unmet_expectations

    def test_structural_verification_is_named_as_structural(self) -> None:
        requirement = resolve_review_requirement(Path("."), _NestedScope())
        requirement.add(ReviewTrigger.REPOSITORY_AUTHORITY, "the repository requires one")
        review = IndependentReview(
            verdict="SUPPORTED",
            reproduced_evidence=[
                ReproducedEvidence(
                    command_requested="git diff HEAD",
                    exit_code=0,
                    expectation=EvidenceExpectation(
                        name="the guard is present in the diff",
                        expect_contains=("compare_and_set",),
                    ),
                    expectation_satisfied=True,
                    kind=VerificationKind.STRUCTURAL,
                    status=EvidenceStatus.STRUCTURAL_VERIFIED,
                    allowed=True,
                ).to_dict()
            ],
        )
        journal = _journal_with_review(
            requirement=requirement, reviews=[review], automatic=True
        )
        section = "\n".join(journal._review_lines())
        assert "reproduce runtime evidence itself?** **No.**" in section
        assert "Verified structurally by the reviewer" in section
        assert "not a demonstration that the product behaves" in section

    def test_a_review_that_only_read_records_is_not_dressed_up(self) -> None:
        requirement = resolve_review_requirement(Path("."), _NestedScope())
        requirement.add(ReviewTrigger.REPOSITORY_AUTHORITY, "the repository requires one")
        review = IndependentReview(
            verdict="SUPPORTED", confidence=0.8, evidence_reproduced=False
        )
        review.reviewer_session_id = "reviewer-session-1"
        journal = _journal_with_review(
            requirement=requirement, reviews=[review], automatic=True
        )
        section = "\n".join(journal._review_lines())
        assert "reproduce runtime evidence itself?** **No.**" in section
        assert "harness's own honesty is a premise" in section

    def test_an_invalidated_review_is_visible_in_the_summary(self) -> None:
        requirement = resolve_review_requirement(Path("."), _NestedScope())
        requirement.add(ReviewTrigger.REPOSITORY_AUTHORITY, "the repository requires one")
        ledger = ReviewLedger()
        old = TreeFingerprint(head="a" * 40, tree="b" * 40, dirty_digest="c" * 64)
        review = supported()
        review.reviewer_session_id = "reviewer-session-1"
        ledger.record(review, old, builder_session_id="builder-session-1")
        ledger.invalidate_stale(TreeFingerprint(head="a" * 40, tree="b" * 40, dirty_digest="d" * 64))
        journal = _journal_with_review(
            requirement=requirement, ledger=ledger, reviews=[review], automatic=True
        )
        section = "\n".join(journal._review_lines())
        assert "retired an earlier review" in section
        assert journal.review_satisfied_scope is False

    def test_an_unsupported_required_review_cannot_read_as_proven(self) -> None:
        journal = RunJournal(run_id="r1")
        journal.run_status = "ACCEPTED"
        journal.gate_status = "VERIFIED"
        assert journal.verification_established is True
        journal.review_required = True
        assert journal.verification_established is False
        journal.review_verdict = "SUPPORTED"
        journal.review_satisfied_scope = True
        assert journal.verification_established is True

    def test_the_section_appears_in_the_file_the_founder_opens(self, tmp_path: Path) -> None:
        journal = _journal_with_review(requirement=None)
        journal.record_start(Path.cwd())
        journal.record_end(Path.cwd())
        journal.save(tmp_path / "run")
        summary = (tmp_path / "run" / "FOUNDER-SUMMARY.md").read_text(encoding="utf-8")
        assert "### 5. Independent review" in summary
        assert summary.index("### 4.") < summary.index("### 5.") < summary.index("### 6.")

    async def test_the_shipping_headline_names_the_evidence_basis(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        reviewer = FakeReviewer([supported()])
        result, _store = await drive(m3_repo, tmp_path, reviewer=reviewer)
        headline = _review_headline(result)
        assert "SUPPORTED" in headline
        assert "satisfies this task" in headline

    async def test_the_ledger_is_written_to_the_run_directory(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        """Which reviews were taken, of which trees, by which sessions."""
        import json

        builder = FakeBuilder(m3_repo.root, edits=True)
        reviewer = FakeReviewer([refusing(), supported()])
        await drive(m3_repo, tmp_path, builder=builder, reviewer=reviewer)

        ledger = json.loads(
            (tmp_path / "driver" / "runs" / "20260822-000000" / "independent-review-ledger.json")
            .read_text(encoding="utf-8")
        )
        assert len(ledger["reviews"]) == 2
        assert ledger["reviews"][0]["superseded_by"], "the retired review is not marked"
        assert ledger["reviews"][1]["verdict"] == "SUPPORTED"
        assert ledger["reviews"][0]["reviewed"]["identity"] != (
            ledger["reviews"][1]["reviewed"]["identity"]
        )
        assert ledger["invalidations"]

    async def test_the_journal_records_what_the_reviewer_ran(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        reviewer = FakeReviewer([supported()])
        result, store = await drive(m3_repo, tmp_path, reviewer=reviewer)
        journal = RunJournal(run_id=store.run_id)
        journal.builder_session_id = "builder-session-1"
        journal.record_independent_review(
            requirement=result.review_requirement,
            ledger=result.review_ledger,
            satisfying=result.satisfying_review,
            reviews=result.reviews,
            automatic=True,
        )
        record = journal.to_dict()["independent_review"]
        assert record["required"] is True
        assert record["verdict"] == "SUPPORTED"
        assert record["reviewer_reproduced_runtime_evidence"] is True
        assert record["reviewed"]["head"]
        assert record["satisfied_the_scoped_task"] is True


@dataclass
class _NestedScope:
    """The minimum a requirement needs, without building a repository."""

    scope_id: str = "P6/M3"
    parent_phase_id: str = "P6"
    is_nested: bool = False
    claims_phase_completion: bool = False
    phase_completion_requested: bool = False


# ==========================================================================
# 10. Nothing in the control loop knows what M3 is
# ==========================================================================


class TestTheArchitectureIsGeneric:
    def test_no_control_loop_module_names_a_particular_unit(self) -> None:
        """M3 is the fixture that exposed the weakness, not a branch in the code."""
        import re

        root = Path(__file__).resolve().parents[1] / "neyma_product_driver"
        offenders: list[str] = []
        for module in ("cli.py", "review_cycle.py", "reviewer.py", "reviewer_boundary.py"):
            source = (root / module).read_text(encoding="utf-8")
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"'):
                    continue
                if re.search(r"""(?:==|!=)\s*["']P\d+/[A-Z]+\d+["']""", stripped):
                    offenders.append(f"{module}: {stripped}")
        assert offenders == [], offenders

    async def test_the_same_loop_serves_a_differently_named_unit(
        self, m3_repo: PhaseRepo, tmp_path: Path
    ) -> None:
        """M4 is not special-cased either; nothing about it is."""
        m3_repo.write_current(
            "# CURRENT\n\n| Phase | Status |\n|---|---|\n| P6 | IN PROGRESS |\n\n"
            "M4 — the Effect Attempt (`P6-CP-4`) is the unit being built.\n"
        )
        m3_repo.commit_all("advance to M4")
        task = (
            "# Build P6 / M4 — Effect Attempt. Only that.\n\n"
            "Implement the canonical M4 specification. Do not begin M5."
        )
        reviewer = FakeReviewer([supported()])

        result, _store = await drive(m3_repo, tmp_path, task=task, reviewer=reviewer)

        assert result.review_requirement.scope_id == "P6/M4"
        assert result.review_requirement.required is True
        assert reviewer.launches == 1
        assert result.status is RunStatus.ACCEPTED
        assert result.audit.completion.task_scope == "P6/M4"
