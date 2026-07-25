"""The investigate CLI command and its integration into the control loop.

The production reasoner (a Claude subagent) is replaced with a scripted one, so
these tests consume no Claude usage and touch no real repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from neyma_product_driver.cli import build_parser, cmd_investigate
from neyma_product_driver.investigation_memory import Hypothesis, Probe
from neyma_product_driver.probe_runner import clear_predicates, register_predicate

from investigation_fixtures import MiniRepo, ScriptedReasoner, fixed_hypotheses, probe_sequence


@pytest.fixture(autouse=True)
def _clean_predicates():
    clear_predicates()
    yield
    clear_predicates()


def _cli(tmp_path: Path, repo: MiniRepo, argv: list[str]):
    config_path = tmp_path / "driver.config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "neyma_repo": str(repo.root),
                "driver_root": str(tmp_path / "driver"),
                "runs_dir": str(tmp_path / "runs"),
                "task": "x",
            }
        )
    )
    return build_parser().parse_args(argv + ["--config", str(config_path)])


def _patch_reasoner(monkeypatch, hyps, probes) -> None:
    reasoner = ScriptedReasoner(hyps, probes)

    def factory(config):
        return reasoner, None  # (reasoner, challenger)

    monkeypatch.setattr("neyma_product_driver.cli._make_investigation_reasoner", factory)


async def test_the_investigate_command_reports_a_root_cause(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    register_predicate("m", lambda i: {"widget": "green", "corroborate": "yes"})

    _patch_reasoner(
        monkeypatch,
        fixed_hypotheses(
            Hypothesis(id="H1", statement="widget is red", predicted_observations=["widget=red"], confidence=0.4),
            Hypothesis(id="H2", statement="widget is green", predicted_observations=["widget=green", "corroborate=yes"], confidence=0.5),
        ),
        probe_sequence(Probe(id="pr1", question="colour?", kind="PREDICATE", command_or_action="m", targets_hypotheses=["H1", "H2"])),
    )

    args = _cli(tmp_path, repo, ["investigate", "--run", "run-1", "--issue", "what colour"])
    code = await cmd_investigate(args)
    printed = capsys.readouterr().out

    assert code == 0  # ROOT_CAUSE_FOUND
    assert "ROOT CAUSE FOUND" in printed
    assert "widget is green" in printed
    assert (tmp_path / "runs" / "run-1" / "investigation" / "timeline.md").exists()
    assert (tmp_path / "runs" / "run-1" / "investigation" / "result.json").exists()


async def test_the_investigate_command_exits_nonzero_without_a_root_cause(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    register_predicate("noise", lambda i: {"unrelated": "1"})

    _patch_reasoner(
        monkeypatch,
        fixed_hypotheses(Hypothesis(id="H1", statement="a", predicted_observations=["never=1"])),
        probe_sequence(Probe(id="pr1", question="q", kind="PREDICATE", command_or_action="noise")),
    )
    args = _cli(tmp_path, repo, ["investigate", "--run", "run-1", "--max-iterations", "3"])
    code = await cmd_investigate(args)

    assert code in (30, 31, 34)  # partial / needs-more / budget
    assert "ROOT CAUSE FOUND" not in capsys.readouterr().out


async def test_the_investigate_command_writes_a_correction_on_a_confident_diagnosis(
    tmp_path: Path, monkeypatch
) -> None:
    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    register_predicate("m", lambda i: {"root": "found", "again": "yes"})

    _patch_reasoner(
        monkeypatch,
        fixed_hypotheses(
            Hypothesis(id="H1", statement="the real cause", predicted_observations=["root=found", "again=yes"], confidence=0.5),
            Hypothesis(id="H2", statement="a decoy", predicted_observations=["root=other"], confidence=0.4),
        ),
        probe_sequence(Probe(id="pr1", question="q", kind="PREDICATE", command_or_action="m", targets_hypotheses=["H1", "H2"])),
    )
    args = _cli(tmp_path, repo, ["investigate", "--run", "run-1"])
    code = await cmd_investigate(args)

    assert code == 0
    assert (tmp_path / "runs" / "run-1" / "investigation-correction.md").exists()


def test_the_investigate_command_parses() -> None:
    args = build_parser().parse_args(["investigate", "--issue", "x", "--max-iterations", "10"])
    assert args.command == "investigate"
    assert args.issue == "x"
    assert args.max_iterations == 10


# --------------------------------------------------------------------------
# Loop integration
# --------------------------------------------------------------------------


async def test_the_loop_runs_an_investigation_when_a_trigger_fires(tmp_path: Path, monkeypatch) -> None:
    """A contradiction from the auditor triggers a diagnostic pass in the loop."""
    from dataclasses import dataclass, field

    from neyma_product_driver.cli import run_control_loop
    from neyma_product_driver.config import DriverConfig
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.models import (
        AssertionResult,
        Decision,
        EvaluatorDecision,
        RunState,
        ScenarioResult,
    )
    from neyma_product_driver.scenarios import Scenario
    from neyma_product_driver.investigator import Investigator
    from neyma_product_driver.investigation_memory import InvestigationMemory

    repo = MiniRepo(tmp_path / "repo")
    repo.commit("init")
    register_predicate("m", lambda i: {"cause": "x"})

    @dataclass
    class FakeTurn:
        text: str = "the sandbox blocks socket.bind, so the finalizer fails"
        session_id: str | None = "b1"
        tool_uses: list = field(default_factory=list)
        denied_requests: list = field(default_factory=list)
        is_error: bool = False
        error_detail: str = ""

    class FakeBuilder:
        session_id = "b1"

        async def send(self, prompt, timeout_s=None):
            return FakeTurn()

    class FakeEvaluator:
        session_id = "e1"

        async def evaluate(self, prompt, timeout_s=None):
            return EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", observed_behavior=["x"])

    class FakeExecutor:
        def __init__(self, artifact_dir):
            self.service_logs = {}

        async def execute(self, scenario):
            return ScenarioResult(
                scenario_name=scenario.name,
                assertions=[AssertionResult(kind="expect_visible", target="x", passed=True)],
            )

    config = DriverConfig(
        neyma_repo=repo.root, driver_root=tmp_path / "d", runs_dir=tmp_path / "d" / "runs",
        task="x", max_iterations=1,
    )
    store = EvidenceStore(config.runs_dir, "20260101-000000")
    state = RunState(run_id=store.run_id, task="x", max_iterations=1)

    calls = {"n": 0}

    def factory(reason):
        calls["n"] += 1
        reasoner = ScriptedReasoner(
            fixed_hypotheses(Hypothesis(id="H1", statement="a", predicted_observations=["cause=x"])),
            probe_sequence(Probe(id="pr1", question="q", kind="PREDICATE", command_or_action="m", targets_hypotheses=["H1"])),
        )
        return Investigator(repo.root, reasoner, memory=InvestigationMemory(store.run_dir))

    await run_control_loop(
        config=config, scenario=Scenario(name="s"), store=store, state=state,
        builder=FakeBuilder(), evaluator=FakeEvaluator(), make_executor=FakeExecutor,
        emit=lambda _m: None, investigator_factory=factory,
    )
    # The builder asserted an environmental blocker → the trigger fired → the
    # investigator ran, and its result was attached to the iteration record.
    assert calls["n"] >= 1
    assert state.iterations[-1].investigation is not None

