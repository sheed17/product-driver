"""The auditor inside the control loop, and the independent reviewer session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from neyma_product_driver.cli import run_control_loop
from neyma_product_driver.completion_auditor import AuditDecision, CompletionAuditor
from neyma_product_driver.config import DriverConfig
from neyma_product_driver.context import RepositoryContextLoader, load_founder_context
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import (
    AssertionResult,
    Decision,
    EvaluatorDecision,
    RunState,
    RunStatus,
    ScenarioResult,
)
from neyma_product_driver.reviewer import (
    IndependentReview,
    IndependentReviewerSession,
    parse_review,
    review_prompt,
)
from neyma_product_driver.scenarios import Scenario

from test_completion_auditor import RepoBuilder, all_but_independent_pass, criteria

DRIVER_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


class FakeBuilder:
    def __init__(self, report: str) -> None:
        self.session_id = "b1"
        self.report = report
        self.prompts: list[str] = []

    async def send(self, prompt: str, timeout_s: int | None = None):
        self.prompts.append(prompt)

        class T:
            text = self.report
            session_id = "b1"
            tool_uses: list[str] = []
            denied_requests: list[str] = []
            is_error = False
            error_detail = ""

        return T()


class AlwaysAccept:
    def __init__(self) -> None:
        self.session_id = "e1"
        self.prompts: list[str] = []

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision:
        self.prompts.append(prompt)
        return EvaluatorDecision(
            decision=Decision.ACCEPT, summary="the product behaves well", confidence=0.9
        )


def make_bits(repo: RepoBuilder, tmp_path: Path, max_iterations: int = 2):
    import shutil

    driver_root = tmp_path / "driver"
    driver_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DRIVER_ROOT / "founder_context", driver_root / "founder_context", dirs_exist_ok=True)

    config = DriverConfig(
        neyma_repo=repo.root, driver_root=driver_root, task="do it", max_iterations=max_iterations
    )
    assert config.runs_dir is not None
    store = EvidenceStore(config.runs_dir, "audit-run")
    state = RunState(run_id=store.run_id, task="do it", max_iterations=max_iterations)
    scenario = Scenario(name="audit-scenario")

    def make_executor(artifact_dir: Path):
        class Ex:
            service_logs: dict[str, str] = {}

            async def execute(self, sc):
                return ScenarioResult(
                    scenario_name=sc.name,
                    assertions=[AssertionResult(kind="expect_visible", target="x", passed=True)],
                )

        return Ex()

    return config, store, state, scenario, make_executor, driver_root


async def run_loop(repo: RepoBuilder, tmp_path: Path, report: str, max_iterations: int = 2):
    config, store, state, scenario, make_executor, driver_root = make_bits(repo, tmp_path, max_iterations)
    builder = FakeBuilder(report)
    evaluator = AlwaysAccept()
    result = await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=builder, evaluator=evaluator, make_executor=make_executor,
        emit=lambda _m: None,
        founder=load_founder_context(driver_root),
        repo_loader=RepositoryContextLoader(repo.root),
        auditor=CompletionAuditor(repo.root),
    )
    return result, store, builder, evaluator


@pytest.fixture
def repo(tmp_path: Path) -> RepoBuilder:
    b = RepoBuilder(tmp_path / "neyma")
    b.write("src/kernel.py", "# implementation\n")
    b.commit_all("init")
    return b


# --------------------------------------------------------------------------
# No completion is accepted without the auditor
# --------------------------------------------------------------------------


async def test_a_false_completion_claim_cannot_be_accepted(repo: RepoBuilder, tmp_path: Path) -> None:
    """Even though the product evaluator says ACCEPT."""
    result, store, builder, evaluator = await run_loop(
        repo, tmp_path, "All done. P3 is COMPLETE. The finalizer ran and 100% is done."
    )

    assert result.status is not RunStatus.ACCEPTED
    assert result.audit is not None
    assert result.audit.decision is AuditDecision.CONTRADICTED
    # The evaluator did say ACCEPT; the audit overrode it.
    assert evaluator.prompts, "the evaluator was never consulted"


async def test_the_audit_correction_reaches_the_builder(repo: RepoBuilder, tmp_path: Path) -> None:
    result, store, builder, _ = await run_loop(
        repo, tmp_path, "P3 is COMPLETE. The finalizer ran successfully.", max_iterations=2
    )
    assert len(builder.prompts) == 2
    correction = builder.prompts[1]
    assert "COMPLETION-CLAIM AUDIT" in correction
    assert "HIGHEST EVIDENCE-SUPPORTED state" in correction
    assert "PRESERVE all implementation code" in correction


async def test_an_honest_report_is_accepted(repo: RepoBuilder, tmp_path: Path) -> None:
    result, store, _, _ = await run_loop(
        repo, tmp_path, "I added a helper. RUNNABLE CHECKPOINT: run the tests."
    )
    assert result.status is RunStatus.ACCEPTED
    assert result.audit.decision is AuditDecision.VERIFIED


async def test_implemented_awaiting_review_is_neither_failure_nor_completion(
    tmp_path: Path,
) -> None:
    b = RepoBuilder(tmp_path / "neyma")
    b.write("src/kernel.py", "# impl\n")
    b.write_registry([b.unit("P3", "READY", all_but_independent_pass())])
    b.commit_all("implement")
    b.write_suite_receipt()
    b.write_gate_receipt()
    b.write_build_status(
        percent=91.0, content_commit=b.head_commit(), content_tree=b.head_tree(),
        finalizer_result="PASS", clean_clone_result="PASS",
    )
    b.commit_all("status metadata")

    result, store, _, _ = await run_loop(b, tmp_path, "Implementation is complete. P3 is COMPLETE.")

    assert result.status is RunStatus.NEEDS_INDEPENDENT_REVIEW
    assert result.status is not RunStatus.ACCEPTED  # not upgraded
    assert result.status is not RunStatus.BLOCKED   # not downgraded
    assert result.audit.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW
    assert result.audit.headline == "IMPLEMENTED — AWAITING INDEPENDENT REVIEW"
    assert result.audit.observed_state.progress.percent == pytest.approx(91.0)


async def test_the_audit_is_stored_in_the_run_evidence(repo: RepoBuilder, tmp_path: Path) -> None:
    _, store, _, _ = await run_loop(repo, tmp_path, "P3 is COMPLETE.")
    path = store.run_dir / "iteration-01" / "completion-audit.json"
    assert path.exists()

    data = json.loads(path.read_text())
    assert data["decision"] == "CONTRADICTED"
    assert data["contradictions"]
    assert data["observed_state"]["progress"]["percent"] == 0.0
    assert data["observed_state"]["active_unit_id"] == "P3"


async def test_the_auditor_runs_before_the_evaluator(repo: RepoBuilder, tmp_path: Path) -> None:
    """Order matters: control-plane facts are checked before product taste."""
    order: list[str] = []

    class RecordingAuditor(CompletionAuditor):
        def audit(self, *a, **kw):
            order.append("audit")
            return super().audit(*a, **kw)

    class RecordingEvaluator(AlwaysAccept):
        async def evaluate(self, prompt, timeout_s=None):
            order.append("evaluate")
            return await super().evaluate(prompt, timeout_s)

    config, store, state, scenario, make_executor, driver_root = make_bits(repo, tmp_path)
    await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=FakeBuilder("P3 is COMPLETE."), evaluator=RecordingEvaluator(),
        make_executor=make_executor, emit=lambda _m: None,
        founder=load_founder_context(driver_root),
        repo_loader=RepositoryContextLoader(repo.root),
        auditor=RecordingAuditor(repo.root),
    )
    assert order[:2] == ["audit", "evaluate"]


async def test_the_summary_block_is_displayed(repo: RepoBuilder, tmp_path: Path) -> None:
    config, store, state, scenario, make_executor, driver_root = make_bits(repo, tmp_path)
    lines: list[str] = []
    await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=FakeBuilder("P3 is COMPLETE and 100% done."), evaluator=AlwaysAccept(),
        make_executor=make_executor, emit=lines.append,
        founder=load_founder_context(driver_root),
        repo_loader=RepositoryContextLoader(repo.root),
        auditor=CompletionAuditor(repo.root),
    )
    text = "\n".join(lines)
    assert "COMPLETION CLAIM: CONTRADICTED" in text
    assert "IMPLEMENTATION STATE:" in text
    assert "VERIFIED PROGRESS:" in text
    assert "MISSING:" in text
    assert "NEXT SAFE ACTION:" in text


async def test_the_loop_never_modifies_the_repository(repo: RepoBuilder, tmp_path: Path) -> None:
    before_head = repo.head_commit()
    await run_loop(repo, tmp_path, "P3 is COMPLETE. Everything passed.")
    assert repo.head_commit() == before_head


# --------------------------------------------------------------------------
# The independent reviewer
# --------------------------------------------------------------------------


def test_the_reviewer_session_is_fresh_and_read_only(tmp_path: Path) -> None:
    session = IndependentReviewerSession(tmp_path)
    opts = session._options()

    # Never inherits the builder conversation.
    assert opts.resume is None
    assert opts.continue_conversation is False
    assert opts.fork_session is False

    # Read-only.
    assert set(opts.allowed_tools) == {"Read", "Grep", "Glob"}
    for forbidden in ("Write", "Edit", "Bash", "NotebookEdit"):
        assert forbidden in opts.disallowed_tools

    # Does not load the implementing session's project hooks or subagent lenses.
    assert opts.setting_sources == []


def test_the_reviewer_is_told_it_may_not_write_status() -> None:
    from neyma_product_driver.reviewer import REVIEWER_SYSTEM

    assert "YOU DO NOT WRITE STATUS" in REVIEWER_SYSTEM
    assert "read-only" in REVIEWER_SYSTEM
    assert "You did not write this implementation" in REVIEWER_SYSTEM
    assert "CANNOT_DETERMINE" in REVIEWER_SYSTEM


def test_the_review_prompt_carries_authority_and_evidence_not_conversation(
    repo: RepoBuilder,
) -> None:
    from neyma_product_driver.context import RepositoryContextLoader as RCL

    unit = RCL(repo.root).resolve_active_unit()
    audit = CompletionAuditor(repo.root).audit("P3 is COMPLETE.", unit=unit)
    prompt = review_prompt(
        unit=unit, audit=audit, builder_report="P3 is COMPLETE.",
        evidence_dir="/runs/x/iteration-01",
    )

    assert "INDEPENDENT REVIEW REQUEST" in prompt
    assert "You are reviewing work you did not perform" in prompt
    assert "a claim, not evidence" in prompt
    assert "DISCREPANCIES THE AUDITOR RAISED" in prompt
    assert "verified weighted progress: 0%" in prompt
    assert "receipts:" in prompt


def test_review_parsing_degrades_to_insufficient_never_to_pass() -> None:
    assert parse_review("it all looks fine to me").verdict == "INSUFFICIENT_EVIDENCE"
    assert parse_review("").verdict == "INSUFFICIENT_EVIDENCE"
    assert parse_review({"garbage": True}).verdict == "INSUFFICIENT_EVIDENCE"


def test_review_parsing_accepts_a_valid_structured_review() -> None:
    review = parse_review(
        {
            "verdict": "NOT_SUPPORTED",
            "summary": "criteria remain pending",
            "findings": [
                {
                    "finding": "independent_review is PENDING",
                    "severity": "blocker",
                    "evidence_path": "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
                    "reasoning": "read directly from the registry",
                }
            ],
            "adjudications": [
                {"discrepancy": "phase claimed COMPLETE", "ruling": "UPHELD", "basis": "registry says READY"}
            ],
            "criteria_assessment": [
                {"criterion": "independent_review", "assessment": "CANNOT_DETERMINE", "basis": "no artifact"}
            ],
            "confidence": 0.9,
        }
    )
    assert review.verdict == "NOT_SUPPORTED"
    assert len(review.blockers) == 1
    assert review.adjudications[0].ruling == "UPHELD"


def test_review_parsing_from_fenced_json() -> None:
    text = '```json\n{"verdict": "SUPPORTED", "summary": "ok", "findings": [], "adjudications": [], "criteria_assessment": [], "confidence": 0.8}\n```'
    assert parse_review(text).verdict == "SUPPORTED"


def test_a_review_never_claims_to_have_inherited_builder_context() -> None:
    assert IndependentReview().inherited_builder_context is False


async def test_the_reviewer_denies_every_non_read_tool(tmp_path: Path) -> None:
    session = IndependentReviewerSession(tmp_path)
    result = await session._can_use_tool("Bash", {"command": "ls"}, object())
    assert result.behavior == "deny"
    assert "read-only" in result.message
