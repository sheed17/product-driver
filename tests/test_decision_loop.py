"""Control-loop routing, iteration bounds, and safety guarantees.

All Claude sessions are faked. Nothing here consumes real Claude usage.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from neyma_product_driver.builder import classify_command, classify_tool_use
from neyma_product_driver.cli import run_control_loop
from neyma_product_driver.config import DriverConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import (
    AssertionResult,
    Decision,
    EvaluatorDecision,
    RunState,
    RunStatus,
    ScenarioResult,
)
from neyma_product_driver.scenarios import Scenario


# -- fakes -----------------------------------------------------------------


@dataclass
class FakeTurn:
    text: str = "I did the work.\n\nRUNNABLE CHECKPOINT: run `make demo`."
    session_id: str | None = "builder-session-1"
    tool_uses: list[str] = field(default_factory=list)
    denied_requests: list[str] = field(default_factory=list)
    is_error: bool = False
    error_detail: str = ""


class FakeBuilder:
    """Records every prompt it receives so the test can assert on corrections."""

    def __init__(self, session_id: str = "builder-session-1") -> None:
        self.session_id = session_id
        self.prompts: list[str] = []
        self.turn = FakeTurn(session_id=session_id)

    async def send(self, prompt: str, timeout_s: int | None = None) -> FakeTurn:
        self.prompts.append(prompt)
        return self.turn


class FakeEvaluator:
    """Returns a scripted sequence of decisions."""

    def __init__(self, decisions: list[EvaluatorDecision]) -> None:
        self.session_id = "evaluator-session-1"
        self.decisions = list(decisions)
        self.prompts: list[str] = []
        self.calls = 0

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision:
        self.prompts.append(prompt)
        self.calls += 1
        if self.decisions:
            return self.decisions.pop(0)
        return EvaluatorDecision(decision=Decision.FIX, correction_prompt="keep going")


class FakeExecutor:
    def __init__(self, artifact_dir: Path, passing: bool = True) -> None:
        self.artifact_dir = artifact_dir
        self.service_logs: dict[str, str] = {}
        self.passing = passing
        self.runs = 0

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        self.runs += 1
        return ScenarioResult(
            scenario_name=scenario.name,
            assertions=[
                AssertionResult(kind="expect_visible", target="something", passed=self.passing)
            ],
        )


@pytest.fixture
def loop_bits(driver_config: DriverConfig, tmp_path: Path):
    assert driver_config.runs_dir is not None
    store = EvidenceStore(driver_config.runs_dir, "20260721-000000")
    state = RunState(run_id=store.run_id, task="build the thing", max_iterations=driver_config.max_iterations)
    scenario = Scenario(name="test-scenario")
    executors: list[FakeExecutor] = []

    def make_executor(artifact_dir: Path) -> FakeExecutor:
        ex = FakeExecutor(artifact_dir)
        executors.append(ex)
        return ex

    return driver_config, store, state, scenario, make_executor, executors


def accept(**kw) -> EvaluatorDecision:
    return EvaluatorDecision(decision=Decision.ACCEPT, summary="good", observed_behavior=["saw it"], **kw)


_counter = {"n": 0}


def fix(prompt: str | None = None, **kw) -> EvaluatorDecision:
    """A fully grounded FIX — the only kind the quality gate lets through."""
    _counter["n"] += 1
    n = _counter["n"]
    default = (
        f"On the load list, load LD56000{n} shows no accountable owner. Add a single "
        f"named owner beside each open obligation, rendered as 'Owner: <name>', so an "
        f"operator can tell at a glance who moves it next. Do not change the ordering."
    )
    base = dict(
        decision=Decision.FIX,
        summary="bad",
        problems=["p"],
        correction_prompt=prompt if prompt is not None else default,
        requirement_reference="P3 acceptance criterion: observability_and_operational_behavior",
        product_principle_reference="accountable_owner",
        scenario="test-scenario",
        observed_result=f"The load list rendered LD56000{n} with no owner field at all.",
        expected_result="Each open obligation names exactly one accountable owner.",
        evidence_paths=["iteration-01/scenario.json"],
        preserve="The existing load ordering and the delivered/undelivered split.",
        retest="Re-run the test-scenario and confirm 'Owner:' appears for every open load.",
        confidence=0.85,
    )
    base.update(kw)
    return EvaluatorDecision(**base)


def ask_user() -> EvaluatorDecision:
    return EvaluatorDecision(decision=Decision.ASK_USER, summary="A or B?")


def blocked() -> EvaluatorDecision:
    return EvaluatorDecision(decision=Decision.BLOCKED, summary="no chromium", problems=["chromium missing"])


# -- routing ---------------------------------------------------------------


async def test_accept_stops_immediately_and_saves_evidence(loop_bits) -> None:
    config, store, state, scenario, make_executor, executors = loop_bits
    builder, evaluator = FakeBuilder(), FakeEvaluator([accept()])

    result = await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=builder, evaluator=evaluator, make_executor=make_executor, emit=lambda _m: None,
    )

    assert result.status is RunStatus.ACCEPTED
    assert evaluator.calls == 1
    assert len(builder.prompts) == 1
    assert (store.run_dir / "accepted" / "record.json").exists()
    assert store.load_state().status is RunStatus.ACCEPTED


async def test_ask_user_stops_without_sending_a_correction(loop_bits) -> None:
    config, store, state, scenario, make_executor, _ = loop_bits
    builder, evaluator = FakeBuilder(), FakeEvaluator([ask_user()])

    result = await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=builder, evaluator=evaluator, make_executor=make_executor, emit=lambda _m: None,
    )

    assert result.status is RunStatus.NEEDS_USER
    assert len(builder.prompts) == 1  # the task only; no correction was sent
    assert not (store.run_dir / "accepted").exists()


async def test_blocked_stops_immediately(loop_bits) -> None:
    config, store, state, scenario, make_executor, _ = loop_bits
    builder, evaluator = FakeBuilder(), FakeEvaluator([blocked()])

    result = await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=builder, evaluator=evaluator, make_executor=make_executor, emit=lambda _m: None,
    )

    assert result.status is RunStatus.BLOCKED
    assert len(builder.prompts) == 1
    assert result.final_decision.problems == ["chromium missing"]


async def test_fix_sends_a_correction_to_the_same_session_and_retests(loop_bits) -> None:
    config, store, state, scenario, make_executor, executors = loop_bits
    builder = FakeBuilder(session_id="same-session")
    correction = (
        "Show who owns the next step on every open load. Render it as "
        "'Owner: <name>' directly beneath the load reference so a dispatcher can "
        "see accountability without opening the load. Leave closed loads unchanged."
    )
    evaluator = FakeEvaluator([fix(correction), accept()])

    result = await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=builder, evaluator=evaluator, make_executor=make_executor, emit=lambda _m: None,
    )

    assert result.status is RunStatus.ACCEPTED
    assert len(builder.prompts) == 2
    assert "Show who owns the next step" in builder.prompts[1]
    # The builder receives the full grounded correction, not just the free text.
    assert "OBSERVED RESULT:" in builder.prompts[1]
    assert "EXPECTED RESULT:" in builder.prompts[1]
    assert "RETEST:" in builder.prompts[1]
    assert "CORRECTION" in builder.prompts[1]
    # The scenario was re-run after the correction.
    assert len(executors) == 2
    # Same builder session throughout.
    assert state.builder_session_id == "same-session"


async def test_prior_problems_are_fed_back_to_the_evaluator(loop_bits) -> None:
    config, store, state, scenario, make_executor, _ = loop_bits
    builder = FakeBuilder()
    evaluator = FakeEvaluator([fix(), accept()])

    await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=builder, evaluator=evaluator, make_executor=make_executor, emit=lambda _m: None,
    )

    assert "PROBLEMS YOU RAISED IN THE PREVIOUS ITERATION" in evaluator.prompts[1]


# -- iteration bounds ------------------------------------------------------


async def test_max_iterations_is_enforced(loop_bits) -> None:
    config, store, state, scenario, make_executor, executors = loop_bits
    assert config.max_iterations == 3
    builder = FakeBuilder()
    evaluator = FakeEvaluator([fix(), fix(), fix(), fix(), fix()])

    result = await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=builder, evaluator=evaluator, make_executor=make_executor, emit=lambda _m: None,
    )

    assert result.status is RunStatus.MAX_ITERATIONS
    assert evaluator.calls == 3
    assert len(builder.prompts) == 3
    assert len(executors) == 3


async def test_the_last_iteration_does_not_send_an_untested_correction(loop_bits) -> None:
    """Sending a correction we can never retest would be a false 'in progress'."""
    config, store, state, scenario, make_executor, _ = loop_bits
    config.max_iterations = 1
    state.max_iterations = 1
    builder = FakeBuilder()

    await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=builder, evaluator=FakeEvaluator([fix()]),
        make_executor=make_executor, emit=lambda _m: None,
    )

    assert len(builder.prompts) == 1
    assert state.iterations[-1].correction_prompt_sent == ""
    assert "budget exhausted" in " ".join(state.iterations[-1].notes)


async def test_single_iteration_can_still_accept(loop_bits) -> None:
    config, store, state, scenario, make_executor, _ = loop_bits
    config.max_iterations = 1
    result = await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=FakeBuilder(), evaluator=FakeEvaluator([accept()]),
        make_executor=make_executor, emit=lambda _m: None,
    )
    assert result.status is RunStatus.ACCEPTED


async def test_stop_request_halts_before_the_next_iteration(loop_bits) -> None:
    config, store, state, scenario, make_executor, _ = loop_bits
    store.request_stop("user asked")
    builder = FakeBuilder()

    result = await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=builder, evaluator=FakeEvaluator([accept()]),
        make_executor=make_executor, emit=lambda _m: None,
    )

    assert result.status is RunStatus.STOPPED
    assert builder.prompts == []  # never even started a turn


# -- evidence written by the loop -----------------------------------------


async def test_every_iteration_is_persisted(loop_bits) -> None:
    config, store, state, scenario, make_executor, _ = loop_bits
    await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=FakeBuilder(), evaluator=FakeEvaluator([fix(), accept()]),
        make_executor=make_executor, emit=lambda _m: None,
    )

    assert (store.run_dir / "iteration-01" / "decision.json").exists()
    assert (store.run_dir / "iteration-01" / "correction-prompt.md").exists()
    assert (store.run_dir / "iteration-02" / "decision.json").exists()
    assert len(state.iterations) == 2


async def test_session_ids_are_recorded_for_resume(loop_bits) -> None:
    config, store, state, scenario, make_executor, _ = loop_bits
    await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=FakeBuilder(session_id="b-42"), evaluator=FakeEvaluator([accept()]),
        make_executor=make_executor, emit=lambda _m: None,
    )
    reloaded = store.load_state()
    assert reloaded.builder_session_id == "b-42"
    assert reloaded.evaluator_session_id == "evaluator-session-1"


async def test_builder_error_is_recorded_but_does_not_crash_the_loop(loop_bits) -> None:
    config, store, state, scenario, make_executor, _ = loop_bits
    builder = FakeBuilder()
    builder.turn = FakeTurn(is_error=True, error_detail="rate limited", text="")

    result = await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=builder, evaluator=FakeEvaluator([blocked()]),
        make_executor=make_executor, emit=lambda _m: None,
    )

    assert result.status is RunStatus.BLOCKED
    assert any("rate limited" in n for n in state.iterations[0].notes)


# -- the driver never commits or pushes ------------------------------------


async def test_the_loop_never_mutates_the_repository(loop_bits, fake_repo: Path) -> None:
    config, store, state, scenario, make_executor, _ = loop_bits

    def head() -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=fake_repo, capture_output=True, text=True
        ).stdout.strip()

    def status() -> str:
        return subprocess.run(
            ["git", "status", "--porcelain"], cwd=fake_repo, capture_output=True, text=True
        ).stdout

    before_head, before_status = head(), status()

    await run_control_loop(
        config=config, scenario=scenario, store=store, state=state,
        builder=FakeBuilder(), evaluator=FakeEvaluator([fix(), accept()]),
        make_executor=make_executor, emit=lambda _m: None,
    )

    assert head() == before_head, "the driver created a commit"
    assert status() == before_status, "the driver modified the working tree"


# -- consequential-action classification -----------------------------------


# The driver runs unattended on the owner's own machine. The hard-block set is
# deliberately narrow: remote publishing, force push, history rewrites, secret
# access, external/production effects, system-wide installs and machine-security
# changes. Everything else — including a local commit, restore, add — runs
# autonomously (Neyma's own settings still add their own deny rules underneath).
@pytest.mark.parametrize(
    "command",
    [
        # remote publishing / force push
        "git push origin main",
        "git push --force",
        "git push --force-with-lease origin main",
        # history rewrites
        "git reset --hard HEAD~1",
        "git rebase -i HEAD~3",
        "git filter-branch --force --index-filter x",
        "git filter-repo --path secret --invert-paths",
        "git commit --amend -m rewrite",
        # deleting the repository / operating outside allowed paths
        "rm -rf /",
        "rm -rf ~/",
        # external / production effects
        "gh pr create --fill",
        "gh pr merge 12",
        "gh release create v1",
        "kubectl apply -f k8s/",
        "terraform apply",
        "docker push myimage",
        "npm publish",
        "twine upload dist/*",
        "aws s3 cp x s3://bucket",
        "gcloud run deploy",
        "curl -X POST https://api.example.com/send",
        "curl --data 'x=1' https://hooks.slack.com/abc",
        "slack chat send '#ops' done",
        "stripe charges create --amount 500",
        "psql production -c 'delete from loads'",
        # reading secrets through the shell
        "cat .env",
        "cp ~/.ssh/id_rsa /tmp/x",
        # system-wide installs / machine-security changes
        "sudo rm -rf /var",
        "brew install nginx",
        "apt-get install nginx",
        "npm install -g typescript",
        "defaults write com.apple.finder x",
        "softwareupdate -i -a",
    ],
)
def test_consequential_commands_are_classified(command: str) -> None:
    assert classify_command(command) is not None, f"{command!r} was not classified"


@pytest.mark.parametrize(
    "command",
    [
        ".venv/bin/python -m pytest eval/ -q",
        "git status --porcelain",
        "git diff --stat",
        "git log --oneline -5",
        "git show HEAD",
        "git add -A",
        "git restore src/x.py",
        "git commit -m 'P4: content'",  # a local commit is permitted
        "ls -la",
        "mv src/old.py src/new.py",  # renaming a repo file
        "rm build/artifact.txt",  # deleting an ordinary repo file
        "python scripts/run_workflow.py --tenant acme",
        "ruff check .",
        "mypy src",
        "curl http://127.0.0.1:8000/health",
        "sqlite3 data/db.sqlite3 'select count(*) from loads'",
    ],
)
def test_ordinary_development_commands_are_allowed(command: str) -> None:
    assert classify_command(command) is None, f"{command!r} was wrongly blocked"


def test_local_commit_and_restore_are_permitted() -> None:
    # The owner explicitly authorized local git status/diff/log/show/add/restore
    # and a local commit when repository authority requires it.
    for cmd in ("git commit -m 'wip'", "git restore .", "git add -A"):
        assert classify_command(cmd) is None, cmd


def test_history_rewrites_and_force_push_stay_blocked() -> None:
    for cmd in (
        "git push --force",
        "git reset --hard",
        "git rebase main",
        "git filter-repo --path x",
        "git commit --amend",
    ):
        assert classify_command(cmd) is not None, cmd


@pytest.mark.parametrize(
    "path",
    [
        ".claude/settings.json",
        ".claude/settings.local.json",
        ".claude/hooks/pre_bash.py",
        ".mcp.json",
    ],
)
def test_writes_to_protected_control_surfaces_are_blocked(path: str) -> None:
    assert classify_tool_use("Edit", {"file_path": path}) is not None
    assert classify_tool_use("Write", {"file_path": path}) is not None


@pytest.mark.parametrize(
    "path",
    [".env", ".env.production", "deploy/.ssh/id_rsa", "app/service.pem", "config/.aws/credentials"],
)
def test_secret_paths_are_blocked_on_read_and_write(path: str) -> None:
    assert classify_tool_use("Write", {"file_path": path}) is not None
    assert classify_tool_use("Read", {"file_path": path}) is not None


def test_ordinary_file_edits_are_allowed() -> None:
    assert classify_tool_use("Edit", {"file_path": "src/freight_recon/checkpoint.py"}) is None
    # CLAUDE.md is an ordinary project doc now — editable, not a control surface.
    assert classify_tool_use("Edit", {"file_path": "CLAUDE.md"}) is None
    assert classify_tool_use("Write", {"file_path": "/repo/CLAUDE.md"}) is None


def test_read_only_research_is_allowed() -> None:
    assert classify_tool_use("WebSearch", {"query": "playwright tracing"}) is None
    assert classify_tool_use("Read", {"file_path": "CLAUDE.md"}) is None
    assert classify_tool_use("Read", {"file_path": "src/freight_recon/checkpoint.py"}) is None
