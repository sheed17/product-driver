"""`evaluate` may not certify what verification did not establish.

The command ran one scenario, handed the result to the evaluator, and then wrote
whatever the evaluator said. A required scenario could FAIL, the evaluator could
return ACCEPT, and the command persisted ACCEPTED and exited 0 — the one thing
the whole driver exists to prevent. The authoritative gate
(``evaluate_gate`` via ``_apply_suite_precedence``) was never consulted on this
path, because it was only wired into the loop's suite branch.

These tests pin the reachable exploit and the three neighbouring states that
share its cause: the pass that never happened, and the pass that cannot show its
evidence. No Claude session is consumed here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from neyma_product_driver import cli as cli_mod
from neyma_product_driver.config import DriverConfig
from neyma_product_driver.models import (
    AssertionResult,
    Decision,
    EvaluatorDecision,
    RunStatus,
    ScenarioResult,
)
from neyma_product_driver import scenario_suite as suite_mod
from neyma_product_driver.scenario_suite import CASE_RECORD_FILENAME
from neyma_product_driver.scenarios import Scenario


# -- fakes -----------------------------------------------------------------


class FakeUnit:
    unit_id = "U-042"
    status = "IN_PROGRESS"

    def criteria_labels(self) -> list[str]:
        return ["an approved invoice is paid exactly once (weight 3): PENDING"]


class FakeRepoContext:
    head_commit = "abc123"
    branch = "main"
    dirty_file_count = 0
    files_consulted: list[str] = []

    def __init__(self) -> None:
        self.active_unit = FakeUnit()


class FakeFounder:
    version = "founder-v1"
    files: list[str] = []
    rubric: dict = {"categories": [], "never_acceptable": []}


class FakeEvaluatorSession:
    """An evaluator that always says the product is fine."""

    decision = EvaluatorDecision(
        decision=Decision.ACCEPT,
        summary="the product looked right to me",
        observed_behavior=["the invoice showed as paid"],
    )

    def __init__(self, *_a, **_kw) -> None:
        self.session_id = "evaluator-session-1"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision:
        return self.decision


class FailingExecutor:
    """Runs the scenario and disagrees with it: a plain, reachable FAILED."""

    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        return ScenarioResult(
            scenario_name=scenario.name,
            assertions=[
                AssertionResult(
                    kind="expect_state",
                    target="payments",
                    passed=False,
                    detail="payments=2 — the invoice was paid twice",
                )
            ],
        )


class PassingExecutor:
    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        return ScenarioResult(
            scenario_name=scenario.name,
            assertions=[
                AssertionResult(kind="expect_state", target="payments", passed=True)
            ],
        )


class BlockedExecutor:
    """The product was never observed: readiness never came up."""

    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        return ScenarioResult(
            scenario_name=scenario.name,
            readiness_ok=False,
            readiness_detail="the service never became reachable on 127.0.0.1:8931",
            error="readiness never passed",
        )


# -- harness ---------------------------------------------------------------


@pytest.fixture
def evaluate_bits(driver_config: DriverConfig, monkeypatch):
    """Everything `cmd_evaluate` reaches for, faked. Returns (config, run_scenario)."""
    scenario = Scenario(name="backend_generic", mode="backend")
    assert driver_config.scenarios_dir is not None
    driver_config.scenarios_dir.mkdir(parents=True, exist_ok=True)
    (driver_config.scenarios_dir / "backend_generic.yaml").write_text(
        json.dumps(scenario.model_dump(mode="json", exclude_defaults=True)), encoding="utf-8"
    )

    monkeypatch.setattr(cli_mod, "_config_from_args", lambda _args: driver_config)
    monkeypatch.setattr(cli_mod, "_preflight_api_key", lambda _config: True)
    monkeypatch.setattr(cli_mod, "load_scenario", lambda _path: scenario)
    monkeypatch.setattr(cli_mod, "load_founder_context", lambda _root: FakeFounder())
    monkeypatch.setattr(
        cli_mod,
        "RepositoryContextLoader",
        lambda _repo: type("L", (), {"load": lambda _self, topics=(): FakeRepoContext()})(),
    )
    monkeypatch.setattr(cli_mod, "evaluator_prompt", lambda **_kw: "evaluator prompt")
    monkeypatch.setattr(
        "neyma_product_driver.evaluator.EvaluatorSession", FakeEvaluatorSession
    )

    def run(executor_cls) -> int:
        monkeypatch.setattr(cli_mod, "ScenarioExecutor", lambda *_a, **_kw: executor_cls(_a[2]))
        args = argparse.Namespace(
            config=None, repo=None, scenario="backend_generic", task=None
        )
        import asyncio

        return asyncio.run(cli_mod.cmd_evaluate(args))

    return driver_config, run, scenario


def _latest_run_dir(config: DriverConfig) -> Path:
    assert config.runs_dir is not None
    runs = sorted(p for p in config.runs_dir.iterdir() if p.is_dir())
    assert runs, "the evaluate command wrote no run directory"
    return runs[-1]


def _latest_state(config: DriverConfig) -> dict:
    path = _latest_run_dir(config) / "state.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_suite(config: DriverConfig) -> dict:
    """The suite record the command persisted beside its decision."""
    matches = sorted(_latest_run_dir(config).rglob("suite-result.json"))
    assert matches, "the evaluate command persisted no suite result"
    return json.loads(matches[-1].read_text(encoding="utf-8"))


# -- the reproduced exploit ------------------------------------------------


class TestEvaluateCannotCertifyAFailure:
    def test_failed_scenario_with_an_accepting_evaluator_is_not_accepted(
        self, evaluate_bits
    ):
        """The exact path an independent reviewer reproduced.

        scenario FAILED → evaluator ACCEPT → ACCEPTED persisted → exit 0.
        """
        config, run, _scenario = evaluate_bits

        code = run(FailingExecutor)

        state = _latest_state(config)
        assert state["status"] != RunStatus.ACCEPTED.value
        assert state["final_decision"]["decision"] != Decision.ACCEPT.value
        assert code != 0, "a failed required scenario must not exit as a success"

    def test_the_persisted_run_says_why_it_was_not_accepted(self, evaluate_bits):
        """The refusal has to be legible, not just a different exit code."""
        config, run, _scenario = evaluate_bits

        run(FailingExecutor)

        state = _latest_state(config)
        problems = " ".join(state["final_decision"]["problems"]).lower()
        assert "paid twice" in problems or "payments" in problems

    def test_a_passing_scenario_can_still_be_accepted(self, evaluate_bits):
        """The gate must not simply refuse everything."""
        config, run, _scenario = evaluate_bits

        code = run(PassingExecutor)

        state = _latest_state(config)
        assert state["status"] == RunStatus.ACCEPTED.value
        assert code == 0


class TestEvaluateRequiresRealVerification:
    def test_a_scenario_that_never_observed_the_product_is_not_accepted(
        self, evaluate_bits
    ):
        """Missing required result: readiness failed, so nothing was verified."""
        config, run, _scenario = evaluate_bits

        code = run(BlockedExecutor)

        state = _latest_state(config)
        assert state["status"] != RunStatus.ACCEPTED.value
        assert code != 0

    def test_a_skipped_required_scenario_is_not_accepted(self, evaluate_bits, monkeypatch):
        """Required SKIPPED: the scenario never ran, so nothing was verified.

        The skip condition is injected at the point the suite decides one —
        not the verdict, which the gate still has to reach on its own.
        """
        config, run, _scenario = evaluate_bits
        monkeypatch.setattr(
            suite_mod.SuiteExecutor,
            "_skip_reason",
            lambda *_a, **_kw: "its prerequisite service was unavailable",
        )

        class UnreachableExecutor(PassingExecutor):
            async def execute(self, scenario: Scenario) -> ScenarioResult:
                raise AssertionError("a skipped scenario must not be executed")

        code = run(UnreachableExecutor)

        state = _latest_state(config)
        assert state["status"] != RunStatus.ACCEPTED.value
        assert code != 0

    def test_a_pass_whose_evidence_is_gone_is_not_accepted(self, evaluate_bits, monkeypatch):
        """Missing/corrupt evidence: a pass that cannot show its record.

        The scenario genuinely passed. What it cannot do is produce the record
        that proves it, and an unprovable pass is not one this command may
        certify.
        """
        config, run, _scenario = evaluate_bits

        def corrupt_write(artifact_dir, _result, **_kw):
            Path(artifact_dir).mkdir(parents=True, exist_ok=True)
            (Path(artifact_dir) / CASE_RECORD_FILENAME).write_text(
                "{ not json", encoding="utf-8"
            )

        monkeypatch.setattr(suite_mod, "write_case_evidence", corrupt_write)

        code = run(PassingExecutor)

        state = _latest_state(config)
        assert state["status"] != RunStatus.ACCEPTED.value
        assert code != 0

        # Both rules that produce that refusal, pinned separately. The gate
        # blocks because the evidence did not resolve; the outcome is *also*
        # downgraded out of PASSED, so no reader of the record sees a pass that
        # cannot be checked. Asserting only the first leaves the second free to
        # be deleted with every test still green.
        outcome = _latest_suite(config)["outcomes"][0]
        assert outcome["evidence_verified"] is False
        assert outcome["outcome"] != "PASSED"


class TestTheLoopWithoutGeneratedCoverage:
    """The same defect, in the command people actually run.

    ``run`` without ``--auto-scenarios`` executed one scenario and produced no
    suite result, and the gate was reached only when a suite result existed. So
    the default path had the identical false accept: required scenario FAILED,
    evaluator ACCEPT, status ACCEPTED.
    """

    @pytest.mark.asyncio
    async def test_a_failed_scenario_is_not_accepted_without_a_planner(
        self, driver_config: DriverConfig
    ):
        from neyma_product_driver.cli import run_control_loop
        from neyma_product_driver.evidence import EvidenceStore
        from neyma_product_driver.models import RunState

        class FakeBuilder:
            session_id = "builder-1"

            async def send(self, prompt: str, timeout_s: int | None = None):
                class Turn:
                    text = "I did the work.\n\nRUNNABLE CHECKPOINT: run `make demo`."
                    session_id = "builder-1"
                    tool_uses: list[str] = []
                    denied_requests: list[str] = []
                    is_error = False
                    error_detail = ""

                return Turn()

        class AlwaysAccept:
            session_id = "evaluator-1"

            async def evaluate(self, prompt: str, timeout_s: int | None = None):
                return FakeEvaluatorSession.decision

        assert driver_config.runs_dir is not None
        store = EvidenceStore(driver_config.runs_dir, "20260812-noplanner")
        state = RunState(run_id=store.run_id, task="do the thing", max_iterations=1)

        result = await run_control_loop(
            config=driver_config,
            store=store,
            state=state,
            scenario=Scenario(name="backend_generic"),
            builder=FakeBuilder(),
            evaluator=AlwaysAccept(),
            make_executor=lambda artifact_dir: FailingExecutor(artifact_dir),
            planner=None,
            emit=lambda _m: None,
        )

        assert result.status != RunStatus.ACCEPTED
        assert result.final_decision.decision != Decision.ACCEPT
