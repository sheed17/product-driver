"""Subprocess execution, timeouts, readiness, and scenario parsing.

These exercise real subprocesses (cheap, local) but never a Claude session.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import yaml

from neyma_product_driver.config import ScenarioRunConfig
from neyma_product_driver.runner import (
    ProcessRunner,
    ServiceManager,
    child_env,
    http_request,
    wait_for_readiness,
)
from neyma_product_driver.scenarios import (
    Scenario,
    ScenarioExecutor,
    load_scenario,
)


# -- process runner --------------------------------------------------------


async def test_successful_command(tmp_path: Path) -> None:
    res = await ProcessRunner(tmp_path).run("echo hello")
    assert res.ok
    assert res.exit_code == 0
    assert "hello" in res.stdout


async def test_failing_command_records_exit_code(tmp_path: Path) -> None:
    res = await ProcessRunner(tmp_path).run("exit 3")
    assert not res.ok
    assert res.exit_code == 3


async def test_stderr_is_captured(tmp_path: Path) -> None:
    res = await ProcessRunner(tmp_path).run("echo oops >&2")
    assert "oops" in res.stderr


async def test_timeout_kills_the_command(tmp_path: Path) -> None:
    res = await ProcessRunner(tmp_path).run("sleep 30", timeout_s=1)
    assert res.timed_out
    assert res.exit_code is None
    assert res.duration_s < 15
    assert "timed out" in res.stderr


async def test_timeout_kills_child_processes_too(tmp_path: Path) -> None:
    """A timed-out command must not leave a detached child running."""
    marker = tmp_path / "still-alive"
    cmd = f"( sleep 3; touch {marker} ) & sleep 30"
    res = await ProcessRunner(tmp_path).run(cmd, timeout_s=1)
    assert res.timed_out
    await asyncio.sleep(4)
    assert not marker.exists(), "child process survived the group kill"


async def test_command_runs_in_the_configured_cwd(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("x")
    res = await ProcessRunner(tmp_path).run("ls")
    assert "marker.txt" in res.stdout


async def test_launch_failure_is_reported_not_raised(tmp_path: Path) -> None:
    res = await ProcessRunner(tmp_path / "missing-dir").run("echo hi")
    assert not res.ok


async def test_output_is_redacted(tmp_path: Path) -> None:
    res = await ProcessRunner(tmp_path).run("echo ghp_abcdefghijklmnopqrstuvwxyz012345")
    assert "ghp_abcdefghijklmnopqrstuvwxyz012345" not in res.stdout
    assert "REDACTED" in res.stdout


async def test_run_all_stops_at_the_first_failure(tmp_path: Path) -> None:
    results = await ProcessRunner(tmp_path).run_all(["echo a", "exit 1", "echo c"])
    assert len(results) == 2


async def test_run_all_can_continue_past_failures(tmp_path: Path) -> None:
    results = await ProcessRunner(tmp_path).run_all(
        ["echo a", "exit 1", "echo c"], stop_on_failure=False
    )
    assert len(results) == 3


def test_api_key_is_stripped_from_child_environment(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-propagate")
    assert "ANTHROPIC_API_KEY" not in child_env()


# -- services and readiness ------------------------------------------------


async def test_service_start_stop_and_log_capture(tmp_path: Path) -> None:
    mgr = ServiceManager(tmp_path, tmp_path / "logs")
    await mgr.start("echo starting-up; sleep 30", "svc")
    await asyncio.sleep(0.7)
    assert "starting-up" in mgr.log_text("svc")
    await mgr.stop_all()
    assert mgr.dead_services() == []  # cleared after stop


async def test_dead_service_is_detected(tmp_path: Path) -> None:
    mgr = ServiceManager(tmp_path, tmp_path / "logs")
    await mgr.start("exit 1", "crasher")
    await asyncio.sleep(0.7)
    assert "crasher" in mgr.dead_services()
    await mgr.stop_all()


async def test_readiness_passes_with_no_checks(tmp_path: Path) -> None:
    ok, _ = await wait_for_readiness([], cwd=tmp_path)
    assert ok


async def test_readiness_times_out_and_explains_why(tmp_path: Path) -> None:
    ok, detail = await wait_for_readiness(
        [{"tcp": "127.0.0.1:1"}], cwd=tmp_path, timeout_s=2, poll_interval_s=0.3
    )
    assert not ok
    assert "timed out" in detail and "tcp" in detail


async def test_readiness_via_command(tmp_path: Path) -> None:
    ok, _ = await wait_for_readiness([{"command": "true"}], cwd=tmp_path, timeout_s=5)
    assert ok


async def test_readiness_via_file(tmp_path: Path) -> None:
    (tmp_path / "ready").write_text("")
    ok, _ = await wait_for_readiness([{"file": "ready"}], cwd=tmp_path, timeout_s=5)
    assert ok


async def test_unknown_readiness_check_fails_closed(tmp_path: Path) -> None:
    ok, detail = await wait_for_readiness(
        [{"telepathy": "yes"}], cwd=tmp_path, timeout_s=1, poll_interval_s=0.3
    )
    assert not ok and "unrecognised" in detail


async def test_http_request_against_a_real_local_server(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>Neyma</h1>")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "http.server", "8931", "--directory", str(tmp_path),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        ok, _ = await wait_for_readiness(
            [{"http": "http://127.0.0.1:8931/", "expect_status": 200}],
            cwd=tmp_path, timeout_s=15, poll_interval_s=0.3,
        )
        assert ok
        obs = await http_request("http://127.0.0.1:8931/")
        assert obs.status == 200
        assert "Neyma" in obs.body_text
    finally:
        proc.terminate()
        await proc.wait()


async def test_http_request_to_a_dead_port_records_an_error() -> None:
    obs = await http_request("http://127.0.0.1:1/", timeout_s=2)
    assert obs.status is None
    assert obs.error


# -- scenario parsing ------------------------------------------------------


def test_shipped_scenarios_parse() -> None:
    root = Path(__file__).resolve().parent.parent / "scenarios"
    files = sorted(root.glob("*.yaml"))
    assert files, "no scenario templates found"
    for path in files:
        scenario = load_scenario(path)
        assert scenario.name
        assert scenario.mode in ("backend", "browser")


def test_scenario_parses_every_supported_section(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "full",
                "phase": "P3",
                "mode": "browser",
                "setup": ["echo setup"],
                "services": [{"name": "api", "command": "run", "env": {"A": "1"}}],
                "readiness": [{"http": "http://127.0.0.1:8000/", "expect_status": 200}],
                "app_url": "http://127.0.0.1:8000",
                "requests": [
                    {"name": "loads", "method": "POST", "path": "/api/loads",
                     "json": {"id": 1}, "expect_status": 201, "expect_contains": ["ok"]}
                ],
                "commands": [{"name": "t", "run": "pytest", "expect_exit_code": 0}],
                "fixtures": ["data/x.json"],
                "browser": {"steps": [{"goto": "/"}, {"click": "text=Go"},
                                      {"fill": "#q", "value": "LD1"},
                                      {"screenshot": "shot"}]},
                "expect_visible": ["Delivered"],
                "expect_state": [{"command": "sqlite3 db 'select 1'", "contains": ["1"]}],
                "forbidden": ["Traceback"],
                "teardown": ["echo bye"],
            }
        )
    )
    s = load_scenario(path)
    assert s.mode == "browser"
    assert s.requests[0].json_body == {"id": 1}
    assert s.requests[0].method == "POST"
    assert s.browser is not None and len(s.browser.steps) == 4
    assert s.expect_state[0].contains == ["1"]


def test_scenario_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump({"name": "x", "nonsense_key": 1}))
    with pytest.raises(Exception):
        load_scenario(path)


def test_scenario_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_scenario(tmp_path / "nope.yaml")


def test_scenario_summary_tells_the_builder_how_it_will_be_tested() -> None:
    s = Scenario(
        name="n", mode="backend",
        commands=[{"run": "pytest -q"}],
        expect_visible=["Awaiting approval"],
        forbidden=["Traceback"],
    )
    summary = s.summary()
    assert "pytest -q" in summary
    assert "Awaiting approval" in summary
    assert "Traceback" in summary


# -- scenario execution ----------------------------------------------------


async def test_setup_failure_aborts_before_the_product_is_exercised(tmp_path: Path) -> None:
    scenario = Scenario(
        name="s",
        setup=["exit 1"],
        commands=[{"run": f"touch {tmp_path / 'should-not-exist'}"}],
    )
    executor = ScenarioExecutor(tmp_path, ScenarioRunConfig(), tmp_path / "art")
    result = await executor.execute(scenario)

    assert result.error is not None and "setup" in result.error
    assert not (tmp_path / "should-not-exist").exists()
    assert not result.passed


async def test_startup_readiness_failure_is_reported_not_raised(tmp_path: Path) -> None:
    scenario = Scenario(
        name="s",
        services=[{"name": "svc", "command": "sleep 30"}],
        readiness=[{"tcp": "127.0.0.1:1"}],
    )
    cfg = ScenarioRunConfig(readiness_timeout_s=2, readiness_poll_interval_s=0.3)
    result = await ScenarioExecutor(tmp_path, cfg, tmp_path / "art").execute(scenario)

    assert not result.readiness_ok
    assert result.error is not None and "did not become ready" in result.error
    assert not result.passed


async def test_a_crashed_service_is_surfaced(tmp_path: Path) -> None:
    scenario = Scenario(
        name="s",
        services=[{"name": "svc", "command": "echo boom; exit 1"}],
        readiness=[{"tcp": "127.0.0.1:1"}],
    )
    cfg = ScenarioRunConfig(readiness_timeout_s=2, readiness_poll_interval_s=0.3)
    result = await ScenarioExecutor(tmp_path, cfg, tmp_path / "art").execute(scenario)
    assert "services exited" in result.readiness_detail


async def test_assertions_are_recorded_for_expected_and_forbidden_text(tmp_path: Path) -> None:
    scenario = Scenario(
        name="s",
        commands=[{"run": "echo Delivered; echo Traceback"}],
        expect_visible=["Delivered", "NeverPrinted"],
        forbidden=["Traceback"],
    )
    result = await ScenarioExecutor(tmp_path, ScenarioRunConfig(), tmp_path / "art").execute(scenario)

    by_target = {a.target: a for a in result.assertions}
    assert by_target["Delivered"].passed
    assert not by_target["NeverPrinted"].passed
    assert not by_target["Traceback"].passed  # forbidden text was present
    assert not result.passed


async def test_missing_fixture_is_recorded(tmp_path: Path) -> None:
    scenario = Scenario(name="s", fixtures=["nope/missing.json"])
    result = await ScenarioExecutor(tmp_path, ScenarioRunConfig(), tmp_path / "art").execute(scenario)
    fixture_assertions = [a for a in result.assertions if "fixture" in a.target]
    assert fixture_assertions and not fixture_assertions[0].passed


async def test_teardown_runs_even_after_a_failure(tmp_path: Path) -> None:
    marker = tmp_path / "torn-down"
    scenario = Scenario(
        name="s",
        services=[{"name": "svc", "command": "sleep 30"}],
        readiness=[{"tcp": "127.0.0.1:1"}],
        teardown=[f"touch {marker}"],
    )
    cfg = ScenarioRunConfig(readiness_timeout_s=1, readiness_poll_interval_s=0.3)
    await ScenarioExecutor(tmp_path, cfg, tmp_path / "art").execute(scenario)
    assert marker.exists()


async def test_browser_scenario_without_browser_support_is_blocked_not_crashed(tmp_path: Path) -> None:
    scenario = Scenario(name="s", mode="browser", app_url="http://127.0.0.1:8000")
    cfg = ScenarioRunConfig(browser_enabled=False)
    result = await ScenarioExecutor(tmp_path, cfg, tmp_path / "art").execute(scenario)
    assert result.error is not None and "browser" in result.error


async def test_state_check_not_contains(tmp_path: Path) -> None:
    scenario = Scenario(
        name="s",
        expect_state=[{"command": "echo clean", "contains": ["clean"], "not_contains": ["dirty"]}],
    )
    result = await ScenarioExecutor(tmp_path, ScenarioRunConfig(), tmp_path / "art").execute(scenario)
    assert all(a.passed for a in result.assertions)
    assert result.passed
