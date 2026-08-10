"""Process, service, HTTP and browser execution.

This module does the actual *operating* of the product: it starts services,
waits for them to become ready, runs commands, makes real HTTP calls, and drives
a browser with Playwright. It never interprets results — that is the evaluator's
job — it only observes and records.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import signal
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Sequence

from .models import CommandResult, HttpObservation, redact

# Environment variables never inherited by child processes started by the
# driver, so a stray credential cannot leak into product logs we then store.
_STRIPPED_ENV_KEYS = {"ANTHROPIC_API_KEY"}


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV_KEYS}
    if extra:
        env.update(extra)
    return env


class ProcessRunner:
    """Runs shell commands with a hard timeout and captured output."""

    def __init__(
        self,
        cwd: Path,
        default_timeout_s: int = 300,
        default_env: dict[str, str] | None = None,
    ) -> None:
        self.cwd = Path(cwd)
        self.default_timeout_s = default_timeout_s
        #: Applied to every command unless a call overrides it. A scenario's
        #: `env:` configures the whole scenario — its services *and* the
        #: commands and state probes that inspect what those services did.
        #: Without this a state probe reads a different database than the
        #: service wrote to, and reports an empty result as a product failure.
        self.default_env = dict(default_env or {})

    async def run(
        self,
        command: str,
        timeout_s: int | None = None,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Run ``command`` via the shell, killing the whole process group on timeout."""
        timeout = timeout_s or self.default_timeout_s
        workdir = Path(cwd or self.cwd)
        started = time.monotonic()
        merged_env = {**self.default_env, **(env or {})}

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workdir),
                env=child_env(merged_env),
                start_new_session=True,  # own process group, so timeout kills children
            )
        except OSError as exc:
            return CommandResult(
                command=command,
                exit_code=None,
                stderr=f"failed to launch: {exc}",
                duration_s=time.monotonic() - started,
                cwd=str(workdir),
            )

        timed_out = False
        stdout_b: bytes = b""
        stderr_b: bytes = b""
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            _kill_process_group(proc)
            # Best-effort drain of whatever the process managed to emit.
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=10)
            except (asyncio.TimeoutError, Exception):
                stdout_b, stderr_b = b"", b""
            stderr_b += f"\n[driver] command timed out after {timeout}s and was killed".encode()

        return CommandResult(
            command=command,
            exit_code=None if timed_out else proc.returncode,
            stdout=redact(_decode(stdout_b)),
            stderr=redact(_decode(stderr_b)),
            duration_s=time.monotonic() - started,
            timed_out=timed_out,
            cwd=str(workdir),
        )

    async def run_all(
        self,
        commands: Sequence[str],
        timeout_s: int | None = None,
        stop_on_failure: bool = True,
    ) -> list[CommandResult]:
        results: list[CommandResult] = []
        for cmd in commands:
            res = await self.run(cmd, timeout_s=timeout_s)
            results.append(res)
            if stop_on_failure and not res.ok:
                break
        return results


def _decode(data: bytes | None) -> str:
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Terminate the process and everything it spawned."""
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.terminate()


class ServiceManager:
    """Starts long-running services and guarantees they are torn down.

    Service stdout/stderr is streamed to files under the iteration directory so
    startup output becomes evidence.
    """

    def __init__(self, cwd: Path, log_dir: Path) -> None:
        self.cwd = Path(cwd)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._procs: list[tuple[str, asyncio.subprocess.Process, Path]] = []
        #: name -> (command, env), so a service can be restarted without the
        #: caller re-supplying a command. Restarting is only ever a repeat of
        #: what this manager already started; a scenario can never name a new
        #: command through a restart step.
        self._declared: dict[str, tuple[str, dict[str, str]]] = {}

    async def start(self, command: str, name: str, env: dict[str, str] | None = None) -> Path:
        """Launch a service in the background; returns its log path."""
        self._declared[name] = (command, dict(env or {}))
        log_path = self.log_dir / f"service-{name}.log"
        # Append, so a restart's output joins the same evidence file rather
        # than erasing what the first incarnation logged.
        log_file = log_path.open("ab")
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(self.cwd),
            env=child_env(env),
            start_new_session=True,
        )
        self._procs.append((name, proc, log_path))
        return log_path

    async def start_declared(self, name: str) -> Path:
        """Start (again) a service this manager previously started.

        Raises ``KeyError`` when the name was never declared — a scenario may
        only act on services it itself started.
        """
        command, env = self._declared[name]
        return await self.start(command, name, env=env)

    async def stop(self, name: str, grace_s: float = 5.0) -> None:
        """Stop every live process registered under ``name``."""
        if name not in self._declared:
            raise KeyError(name)
        live = [entry for entry in self._procs if entry[0] == name]
        for _n, proc, _p in live:
            _kill_process_group(proc)
        for _n, proc, _p in live:
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(proc.wait(), timeout=grace_s)
        for _n, proc, _p in live:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        self._procs = [entry for entry in self._procs if entry[0] != name]

    async def restart(self, name: str, grace_s: float = 5.0) -> Path:
        """Stop and re-launch ``name``. The crash/restart-recovery primitive."""
        await self.stop(name, grace_s=grace_s)
        return await self.start_declared(name)

    def log_text(self, name: str, limit: int = 20_000) -> str:
        for svc_name, _proc, path in self._procs:
            if svc_name == name and path.exists():
                return redact(path.read_text(encoding="utf-8", errors="replace")[-limit:])
        return ""

    def all_logs(self, limit: int = 20_000) -> dict[str, str]:
        return {name: self.log_text(name, limit) for name, _p, _l in self._procs}

    def dead_services(self) -> list[str]:
        return [name for name, proc, _ in self._procs if proc.returncode is not None]

    async def stop_all(self, grace_s: float = 5.0) -> None:
        for _name, proc, _path in self._procs:
            _kill_process_group(proc)
        for _name, proc, _path in self._procs:
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(proc.wait(), timeout=grace_s)
        for _name, proc, _path in self._procs:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        self._procs.clear()


async def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    body: str | None = None,
    timeout_s: int = 30,
    name: str = "",
) -> HttpObservation:
    """Make one real HTTP request against the locally running product."""
    started = time.monotonic()

    def _do() -> HttpObservation:
        data: bytes | None = None
        hdrs = dict(headers or {})
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        elif body is not None:
            data = body.encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=hdrs, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            status = exc.code
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return HttpObservation(
                name=name or url,
                method=method.upper(),
                url=url,
                status=None,
                error=str(exc),
                duration_s=time.monotonic() - started,
            )

        parsed: Any = None
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            parsed = json.loads(raw)

        return HttpObservation(
            name=name or url,
            method=method.upper(),
            url=url,
            status=status,
            body_text=redact(raw[:20_000]),
            json_body=parsed,
            duration_s=time.monotonic() - started,
        )

    return await asyncio.to_thread(_do)


async def wait_for_readiness(
    checks: Sequence[dict[str, Any]],
    cwd: Path,
    timeout_s: int = 90,
    poll_interval_s: float = 1.0,
) -> tuple[bool, str]:
    """Poll readiness checks until all pass or the timeout expires.

    Each check is one of::

        {"http": "http://127.0.0.1:8000/health", "expect_status": 200}
        {"command": "pg_isready"}
        {"tcp": "127.0.0.1:5432"}
        {"file": "./.ready"}
    """
    if not checks:
        return True, "no readiness checks configured"

    deadline = time.monotonic() + timeout_s
    last_detail = "no attempt made"
    runner = ProcessRunner(cwd, default_timeout_s=max(5, int(poll_interval_s * 10)))

    while time.monotonic() < deadline:
        failures: list[str] = []
        for check in checks:
            ok, detail = await _evaluate_readiness_check(check, runner)
            if not ok:
                failures.append(detail)
        if not failures:
            return True, "all readiness checks passed"
        last_detail = "; ".join(failures)
        await asyncio.sleep(poll_interval_s)

    return False, f"readiness timed out after {timeout_s}s: {last_detail}"


async def _evaluate_readiness_check(
    check: dict[str, Any], runner: ProcessRunner
) -> tuple[bool, str]:
    if "http" in check:
        url = str(check["http"])
        expect = int(check.get("expect_status", 200))
        obs = await http_request(url, timeout_s=int(check.get("timeout_s", 5)))
        if obs.status == expect:
            return True, ""
        return False, f"HTTP {url} -> {obs.status or obs.error}, want {expect}"

    if "command" in check:
        res = await runner.run(str(check["command"]), timeout_s=int(check.get("timeout_s", 15)))
        if res.ok:
            return True, ""
        return False, f"command {check['command']!r} -> exit {res.exit_code}"

    if "tcp" in check:
        host, _, port = str(check["tcp"]).rpartition(":")
        ok = await _tcp_open(host or "127.0.0.1", int(port))
        return (True, "") if ok else (False, f"tcp {check['tcp']} closed")

    if "file" in check:
        p = Path(str(check["file"]))
        if not p.is_absolute():
            p = runner.cwd / p
        return (True, "") if p.exists() else (False, f"file {p} missing")

    return False, f"unrecognised readiness check: {check!r}"


async def _tcp_open(host: str, port: int, timeout_s: float = 3.0) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout_s
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


def parse_command(command: str) -> list[str]:
    """Split a command for display/inspection without executing it."""
    try:
        return shlex.split(command)
    except ValueError:
        return [command]
