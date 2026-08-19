"""Decision precedence in the control loop, and the CLI protocol/approve flow.

All Claude sessions are faked. Nothing here consumes real Claude usage, and
nothing here touches the real Neyma repository.

What is under test is the one rule that replaced a five-level precedence table:

    a repository-protocol finding stops the run only when the repair the
    repository's own rules point at would rewrite history, change pushed or
    shared history, or destroy something.

Everything else — a stale receipt, a commit-shape difference, contradictory
protocol prose, an environmental gate failure — is recorded, reported, and
handed to the investigator, while the product loop keeps going. The old table
made every one of those outrank a scenario suite that had just watched the
product work, and the founder then relayed each finding to a builder by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from neyma_product_driver.cli import build_parser, run_control_loop
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
from neyma_product_driver.protocol_resolver import ProtocolResolver
from neyma_product_driver.scenarios import Scenario

from protocol_fixtures import (
    CONFLICTING_PROTOCOL_MD,
    TLS_ERROR,
    content_plus_finalizer_metadata,
    one_content_commit,
    p3_deadlock_repo,
)


# -- fakes -----------------------------------------------------------------


@dataclass
class FakeTurn:
    text: str = "P3 is COMPLETE. All nine findings are remediated."
    session_id: str | None = "builder-1"
    tool_uses: list[str] = field(default_factory=list)
    denied_requests: list[str] = field(default_factory=list)
    is_error: bool = False
    error_detail: str = ""


class FakeBuilder:
    def __init__(self) -> None:
        self.session_id = "builder-1"
        self.prompts: list[str] = []

    async def send(self, prompt: str, timeout_s: int | None = None) -> FakeTurn:
        self.prompts.append(prompt)
        return FakeTurn()


class FakeEvaluator:
    """Always ACCEPTs. Whatever blocks the run below did not come from the product."""

    def __init__(self, decision: EvaluatorDecision | None = None) -> None:
        self.session_id = "evaluator-1"
        self.decision = decision or EvaluatorDecision(
            decision=Decision.ACCEPT, summary="the product behaved well", observed_behavior=["ok"]
        )
        self.calls = 0

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision:
        self.calls += 1
        return self.decision


class FakeExecutor:
    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = artifact_dir
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        return ScenarioResult(
            scenario_name=scenario.name,
            assertions=[AssertionResult(kind="expect_visible", target="x", passed=True)],
        )


async def run_loop(repo_root: Path, tmp_path: Path, evaluator: FakeEvaluator | None = None):
    config = DriverConfig(
        neyma_repo=repo_root,
        driver_root=tmp_path / "driver",
        runs_dir=tmp_path / "driver" / "runs",
        task="do the work",
        max_iterations=2,
    )
    store = EvidenceStore(config.runs_dir, "20260722-000000")
    state = RunState(run_id=store.run_id, task="do the work", max_iterations=2)
    return await run_control_loop(
        config=config,
        scenario=Scenario(name="protocol-scenario"),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=evaluator or FakeEvaluator(),
        make_executor=FakeExecutor,
        emit=lambda _m: None,
        protocol_resolver=ProtocolResolver(repo_root),
    ), store


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------


async def test_a_stale_receipt_is_recorded_and_does_not_stop_the_product_loop(
    tmp_path: Path,
) -> None:
    """A receipt the repository has not regenerated is the repository's business.

    This asserted that the violation overrode the product verdict. The driver
    runs its own scenario suite and holds its own acceptance gate; the target
    repository's receipt being out of date says nothing about whether the
    product works, and stopping here is what sent the founder off to re-run a
    finalizer before any product question could be answered.
    """
    repo = one_content_commit(tmp_path / "neyma")
    repo.write_suite_receipt(commit="0" * 40, tree="1" * 40)  # stale receipt

    result, _store = await run_loop(repo.root, tmp_path)

    assert result.status is RunStatus.ACCEPTED
    assert result.protocol.status.value == "VIOLATION"
    assert any("receipt" in note.lower() for note in result.protocol_diagnostics), (
        "the stale receipt was neither enforced nor recorded — it simply vanished"
    )


async def test_a_repair_needing_a_history_rewrite_still_stops_the_run(tmp_path: Path) -> None:
    """The boundary that was never ceremony, on a real repository."""
    repo = p3_deadlock_repo(tmp_path / "neyma")

    result, _store = await run_loop(repo.root, tmp_path)

    assert result.status is RunStatus.REQUIRES_APPROVAL
    assert result.final_decision.decision is Decision.ASK_USER
    assert "rewrite" in result.final_decision.summary.lower()
    assert result.protocol.recommended_option.option_id == "A"
    assert result.protocol.recommended_option.rewrites_history is True


async def test_an_authority_conflict_is_diagnosed_rather_than_terminal(tmp_path: Path) -> None:
    """Contradictory protocol prose is a fact about the repository, not a wall.

    The repository saying two incompatible things is worth knowing and worth
    investigating. It is not a reason to refuse to build, and the safe repair the
    resolver recommends here needs nobody's approval.
    """
    repo = p3_deadlock_repo(tmp_path / "neyma")
    repo.write("docs/implementation/PROGRESS-PROTOCOL.md", CONFLICTING_PROTOCOL_MD)

    result, _store = await run_loop(repo.root, tmp_path)

    assert result.status is not RunStatus.BLOCKED
    assert result.protocol.status.value == "BLOCKED_AUTHORITY"
    assert any("BLOCKED_AUTHORITY" in note for note in result.protocol_diagnostics)


async def test_an_environmental_gate_failure_is_diagnosed_not_counted_as_a_pass(
    tmp_path: Path,
) -> None:
    """It is still not a PASS — the product's own gate decides that, not this one."""
    repo = one_content_commit(tmp_path / "neyma")
    repo.write_gate_receipt(passed=False, error=TLS_ERROR)

    result, _store = await run_loop(repo.root, tmp_path)

    assert result.status is not RunStatus.BLOCKED
    assert result.protocol.status.value == "BLOCKED_ENVIRONMENT"
    assert any("environment" in note.lower() for note in result.protocol_diagnostics)
    # And the driver's own acceptance evidence is what the outcome rests on.
    assert result.gate is not None


async def test_a_consistent_repository_lets_the_product_verdict_stand(tmp_path: Path) -> None:
    repo = content_plus_finalizer_metadata(tmp_path / "neyma")

    result, _store = await run_loop(repo.root, tmp_path)

    assert result.status is RunStatus.ACCEPTED
    assert result.protocol.status.value == "CONSISTENT"


async def test_a_product_fix_survives_a_protocol_violation_and_records_it(tmp_path: Path) -> None:
    """A real product defect is still what the builder is told to fix."""
    repo = one_content_commit(tmp_path / "neyma")
    repo.write_suite_receipt(commit="0" * 40, tree="1" * 40)
    product_fix = EvaluatorDecision(
        decision=Decision.FIX,
        summary="the load list shows no owner",
        problems=["no owner rendered"],
        correction_prompt=(
            "On the load list, load LD560001 shows no accountable owner. Add a single named "
            "owner beside each open obligation, rendered as 'Owner: <name>'."
        ),
        requirement_reference="P3 acceptance criterion: observability_and_operational_behavior",
        product_principle_reference="accountable_owner",
        scenario="protocol-scenario",
        observed_result="The load list rendered LD560001 with no owner field.",
        expected_result="Each open obligation names exactly one accountable owner.",
        evidence_paths=["iteration-01/scenario.json"],
        preserve="The existing load ordering.",
        retest="Re-run the scenario and confirm 'Owner:' appears.",
        confidence=0.85,
    )

    result, _store = await run_loop(repo.root, tmp_path, FakeEvaluator(product_fix))

    first = result.state.iterations[0].decision
    assert first.decision is Decision.FIX
    assert "no owner rendered" in first.problems
    assert first.correction_prompt.startswith("On the load list")
    # The protocol finding is recorded beside the product defect, not mixed into
    # the correction. A builder handed both chases the repository instead of the
    # product, which is how a product-defect fix became a governance errand.
    assert not any("receipt" in p.lower() for p in first.problems)
    assert any("receipt" in note.lower() for note in result.state.iterations[0].notes)


async def test_the_resolution_is_persisted_with_the_run(tmp_path: Path) -> None:
    repo = p3_deadlock_repo(tmp_path / "neyma")
    _result, store = await run_loop(repo.root, tmp_path)

    per_iteration = store.iteration_dir(1) / "protocol-resolution.json"
    assert per_iteration.exists()
    assert store.load_protocol_resolution()["status"] == "REQUIRES_APPROVAL"


async def test_the_loop_never_modifies_the_repository(tmp_path: Path) -> None:
    repo = p3_deadlock_repo(tmp_path / "neyma")
    before = (repo._git("status", "--porcelain"), repo.head(), repo._git("branch", "--list"))

    await run_loop(repo.root, tmp_path)

    assert (repo._git("status", "--porcelain"), repo.head(), repo._git("branch", "--list")) == before


# --------------------------------------------------------------------------
# The CLI
# --------------------------------------------------------------------------


def parse(argv: list[str]):
    return build_parser().parse_args(argv)


def cli_args(tmp_path: Path, repo, argv: list[str]):
    """Parse a command against a config file scoped to this test's temp dir."""
    import yaml

    config_path = tmp_path / "driver.config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "neyma_repo": str(repo.root),
                "driver_root": str(tmp_path / "driver"),
                "runs_dir": str(tmp_path / "runs"),
                "task": "do the work",
            }
        )
    )
    return parse(argv + ["--config", str(config_path)])


def test_the_documented_commands_parse() -> None:
    protocol = parse(["protocol", "--run", "20260722-000000"])
    assert protocol.command == "protocol"
    assert protocol.run == "20260722-000000"

    approve = parse(
        [
            "approve",
            "--run",
            "20260722-000000",
            "--option",
            "A",
            "--confirmation",
            "APPROVE P3 LOCAL HISTORY NORMALIZATION",
        ]
    )
    assert approve.option == "A"
    assert approve.confirmation == "APPROVE P3 LOCAL HISTORY NORMALIZATION"


async def test_the_protocol_command_reports_and_persists(tmp_path: Path, capsys) -> None:
    from neyma_product_driver.cli import cmd_protocol

    repo = p3_deadlock_repo(tmp_path / "neyma")
    code = await cmd_protocol(cli_args(tmp_path, repo, ["protocol", "--run", "run-1"]))
    printed = capsys.readouterr().out

    assert code == 22  # REQUIRES_APPROVAL
    assert "REPOSITORY PROTOCOL: REQUIRES_APPROVAL" in printed
    assert "APPROVE P3 LOCAL HISTORY NORMALIZATION" in printed
    assert (tmp_path / "runs" / "run-1" / "protocol-resolution.json").exists()


async def test_the_protocol_command_exits_zero_on_a_consistent_repository(
    tmp_path: Path, capsys
) -> None:
    from neyma_product_driver.cli import cmd_protocol

    repo = content_plus_finalizer_metadata(tmp_path / "neyma")
    assert await cmd_protocol(cli_args(tmp_path, repo, ["protocol", "--run", "run-1"])) == 0
    assert "CONSISTENT" in capsys.readouterr().out


async def test_the_protocol_command_lists_the_rules_it_discovered(tmp_path: Path, capsys) -> None:
    from neyma_product_driver.cli import cmd_protocol

    repo = p3_deadlock_repo(tmp_path / "neyma")
    await cmd_protocol(cli_args(tmp_path, repo, ["protocol", "--run", "run-1", "--sources"]))
    printed = capsys.readouterr().out

    assert "PROTOCOL SOURCES READ:" in printed
    assert "RULES DISCOVERED" in printed
    assert "[CANONICAL] COMMIT_TOPOLOGY" in printed


async def test_approve_refuses_a_plan_that_was_never_reported(tmp_path: Path, capsys) -> None:
    """An approval authorizes a plan someone read. There is nothing to have read."""
    from neyma_product_driver.cli import cmd_approve

    repo = p3_deadlock_repo(tmp_path / "neyma")
    args = cli_args(
        tmp_path,
        repo,
        [
            "approve",
            "--run",
            "never-reported",
            "--option",
            "A",
            "--confirmation",
            "APPROVE P3 LOCAL HISTORY NORMALIZATION",
        ],
    )

    code = await cmd_approve(args)
    printed = capsys.readouterr().out

    assert code == 3
    assert "No reported plan" in printed
    assert not (tmp_path / "runs" / "never-reported" / "protocol-approvals.json").exists()


async def test_approve_refuses_a_vague_confirmation(tmp_path: Path, capsys) -> None:
    from neyma_product_driver.cli import cmd_approve, cmd_protocol

    repo = p3_deadlock_repo(tmp_path / "neyma")
    await cmd_protocol(cli_args(tmp_path, repo, ["protocol", "--run", "run-1"]))
    capsys.readouterr()

    args = cli_args(
        tmp_path,
        repo,
        ["approve", "--run", "run-1", "--option", "A", "--confirmation", "go ahead with whatever"],
    )

    code = await cmd_approve(args)
    printed = capsys.readouterr().out

    assert code == 3
    assert "Not approved" in printed
    assert "exact required phrase" in printed
    assert not (tmp_path / "runs" / "run-1" / "protocol-approvals.json").exists()


async def test_approve_records_the_plan_and_emits_a_builder_prompt(tmp_path: Path, capsys) -> None:
    from neyma_product_driver.cli import cmd_approve, cmd_protocol

    repo = p3_deadlock_repo(tmp_path / "neyma")
    await cmd_protocol(cli_args(tmp_path, repo, ["protocol", "--run", "run-1"]))
    capsys.readouterr()

    args = cli_args(
        tmp_path,
        repo,
        [
            "approve",
            "--run",
            "run-1",
            "--option",
            "A",
            "--confirmation",
            "APPROVE P3 LOCAL HISTORY NORMALIZATION",
        ],
    )

    code = await cmd_approve(args)
    printed = capsys.readouterr().out

    assert code == 0
    assert "Approved: option A" in printed
    assert "APPROVED REPOSITORY-HISTORY REMEDIATION" in printed
    assert (tmp_path / "runs" / "run-1" / "protocol-approvals.json").exists()
    assert (tmp_path / "runs" / "run-1" / "remediation-prompt.md").exists()
    # The driver reports the plan; it does not run it.
    assert repo._git("branch", "--list").strip() == "* main"


async def test_an_approval_expires_when_the_repository_moves(tmp_path: Path, capsys) -> None:
    from neyma_product_driver.cli import cmd_approve, cmd_protocol

    repo = p3_deadlock_repo(tmp_path / "neyma")
    await cmd_protocol(cli_args(tmp_path, repo, ["protocol", "--run", "run-1"]))

    # The repository moves after the human read the plan.
    repo.write("src/kernel.py", "def kernel():\n    return 42\n")
    repo.commit("later work", "src/kernel.py")

    args = cli_args(
        tmp_path,
        repo,
        [
            "approve",
            "--run",
            "run-1",
            "--option",
            "A",
            "--confirmation",
            "APPROVE P3 LOCAL HISTORY NORMALIZATION",
        ],
    )
    capsys.readouterr()
    code = await cmd_approve(args)
    printed = capsys.readouterr().out

    assert code == 3
    assert "changed since it was reported" in printed
    assert not (tmp_path / "runs" / "run-1" / "protocol-approvals.json").exists()


async def test_the_reviewer_is_not_launched_against_an_invalid_topology(
    tmp_path: Path, capsys
) -> None:
    """The one reviewer whose independence cannot be recovered is not spent here."""
    from neyma_product_driver.cli import cmd_review

    repo = p3_deadlock_repo(tmp_path / "neyma")
    store = EvidenceStore(tmp_path / "runs", "run-1")
    store.save_state(RunState(run_id="run-1", task="x"))

    args = cli_args(tmp_path, repo, ["review", "--run", "run-1", "--yes"])
    code = await cmd_review(args)
    printed = capsys.readouterr().out

    assert code == 11
    assert "no reviewer was launched" in printed
    assert "reviews the wrong thing" in printed


async def test_the_reviewer_proceeds_once_the_topology_is_valid(tmp_path: Path, capsys) -> None:
    from neyma_product_driver.cli import cmd_review

    repo = content_plus_finalizer_metadata(tmp_path / "neyma")
    store = EvidenceStore(tmp_path / "runs", "run-1")
    store.save_state(RunState(run_id="run-1", task="x"))

    args = cli_args(tmp_path, repo, ["review", "--run", "run-1"])
    # Not interactive and not --yes: it stops at the authorization prompt, which
    # is *after* the protocol gate. Reaching that point is the assertion.
    code = await cmd_review(args)
    printed = capsys.readouterr().out

    assert "topology and authority are valid" in printed
    assert code == 3
