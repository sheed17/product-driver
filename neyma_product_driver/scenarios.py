"""Scenario definition and execution.

A scenario describes how to start the product, how to know it is ready, how to
operate it, and what must (and must not) be observable afterwards. Scenarios are
plain YAML so they can be written by hand per Neyma phase.

Execution is strictly observational: assertions are recorded, never enforced by
raising. The evaluator decides what the observations mean.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import ScenarioRunConfig
from .models import (
    AssertionResult,
    BrowserObservation,
    CommandResult,
    ScenarioResult,
    redact,
)
from .runner import ProcessRunner, ServiceManager, http_request, wait_for_readiness


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


class ServiceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    command: str
    env: dict[str, str] = Field(default_factory=dict)


class RequestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    method: str = "GET"
    path: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    json_body: Any = Field(default=None, alias="json")
    body: str | None = None
    expect_status: int | None = None
    expect_contains: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("expect_contains", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else (v or [])


class CommandSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    run: str
    expect_exit_code: int | None = 0
    expect_contains: list[str] = Field(default_factory=list)
    timeout_s: int | None = None

    @field_validator("expect_contains", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else (v or [])


class StateCheckSpec(BaseModel):
    """Inspect persisted state (SQLite/Postgres rows, files, event logs)."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    command: str
    contains: list[str] = Field(default_factory=list)
    not_contains: list[str] = Field(default_factory=list)
    timeout_s: int | None = None

    @field_validator("contains", "not_contains", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else (v or [])


class BrowserStep(BaseModel):
    """One browser interaction. Exactly one action key should be set."""

    model_config = ConfigDict(extra="forbid")

    goto: str | None = None
    click: str | None = None
    fill: str | None = None
    value: str | None = None
    press: str | None = None
    wait_for: str | None = None
    wait_ms: int | None = None
    screenshot: str | None = None
    expect_text: str | None = None


class BrowserSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[BrowserStep] = Field(default_factory=list)
    initial_screenshot: bool = True
    final_screenshot: bool = True


class Scenario(BaseModel):
    """A full scenario definition."""

    model_config = ConfigDict(extra="forbid")

    name: str
    phase: str = ""
    description: str = ""
    mode: Literal["backend", "browser"] = "backend"

    setup: list[str] = Field(default_factory=list)
    services: list[ServiceSpec] = Field(default_factory=list)
    readiness: list[dict[str, Any]] = Field(default_factory=list)
    app_url: str = ""

    requests: list[RequestSpec] = Field(default_factory=list)
    commands: list[CommandSpec] = Field(default_factory=list)
    fixtures: list[str] = Field(default_factory=list)
    browser: BrowserSpec | None = None

    expect_visible: list[str] = Field(default_factory=list)
    expect_state: list[StateCheckSpec] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)

    teardown: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("expect_visible", "forbidden", "fixtures", "setup", "teardown", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else (v or [])

    def summary(self) -> str:
        """Short description handed to the builder so it knows how it'll be tested."""
        lines = [f"scenario: {self.name} (mode={self.mode}, phase={self.phase or 'n/a'})"]
        if self.description:
            lines.append(self.description.strip())
        if self.services:
            lines.append("services started: " + ", ".join(s.command for s in self.services))
        if self.app_url:
            lines.append(f"app url: {self.app_url}")
        if self.requests:
            lines.append(
                "API calls: "
                + ", ".join(f"{r.method} {r.url or r.path}" for r in self.requests)
            )
        if self.commands:
            lines.append("commands: " + ", ".join(c.run for c in self.commands))
        if self.expect_visible:
            lines.append("must be observable: " + "; ".join(self.expect_visible))
        if self.forbidden:
            lines.append("must NOT appear: " + "; ".join(self.forbidden))
        return "\n".join(lines)


def load_scenario(path: str | Path) -> Scenario:
    """Parse a scenario YAML file."""
    p = Path(os.path.expanduser(str(path)))
    if not p.exists():
        raise FileNotFoundError(f"Scenario file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Scenario file must contain a mapping: {p}")
    return Scenario(**raw)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


class ScenarioExecutor:
    """Runs a scenario against the Neyma repo and records what it observed."""

    def __init__(
        self,
        repo: Path,
        run_config: ScenarioRunConfig,
        artifact_dir: Path,
    ) -> None:
        self.repo = Path(repo)
        self.cfg = run_config
        self.artifact_dir = Path(artifact_dir)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        result = ScenarioResult(scenario_name=scenario.name, mode=scenario.mode)
        runner = ProcessRunner(self.repo, default_timeout_s=self.cfg.command_timeout_s)
        services = ServiceManager(self.repo, self.artifact_dir)

        try:
            # 1. setup
            result.setup = await runner.run_all(scenario.setup, timeout_s=self.cfg.command_timeout_s)
            if any(not r.ok for r in result.setup):
                result.error = "setup command failed; product was not exercised"
                return await self._finish(result, scenario, runner, services)

            # 2. missing fixtures are a setup problem, recorded not raised
            for fixture in scenario.fixtures:
                fp = Path(fixture)
                if not fp.is_absolute():
                    fp = self.repo / fixture
                result.assertions.append(
                    AssertionResult(
                        kind="expect_state",
                        target=f"fixture exists: {fixture}",
                        passed=fp.exists(),
                        detail="" if fp.exists() else f"missing: {fp}",
                    )
                )

            # 3. services
            for svc in scenario.services:
                await services.start(svc.command, svc.name, env={**scenario.env, **svc.env})
                result.services_started.append(svc.name)

            # 4. readiness
            if scenario.services or scenario.readiness:
                ok, detail = await wait_for_readiness(
                    scenario.readiness,
                    cwd=self.repo,
                    timeout_s=self.cfg.readiness_timeout_s,
                    poll_interval_s=self.cfg.readiness_poll_interval_s,
                )
                result.readiness_ok = ok
                result.readiness_detail = detail
                dead = services.dead_services()
                if dead:
                    result.readiness_ok = False
                    result.readiness_detail += f" (services exited: {', '.join(dead)})"
                if not result.readiness_ok:
                    result.error = "product did not become ready; nothing was observed"
                    return await self._finish(result, scenario, runner, services)

            # 5. operate — commands
            for spec in scenario.commands:
                res = await runner.run(spec.run, timeout_s=spec.timeout_s or self.cfg.command_timeout_s)
                result.commands.append(res)
                if spec.expect_exit_code is not None:
                    result.assertions.append(
                        AssertionResult(
                            kind="expect_state",
                            target=f"{spec.name or spec.run}: exit == {spec.expect_exit_code}",
                            passed=res.exit_code == spec.expect_exit_code,
                            detail=f"got exit={res.exit_code}{' (timed out)' if res.timed_out else ''}",
                        )
                    )
                combined = f"{res.stdout}\n{res.stderr}"
                for needle in spec.expect_contains:
                    result.assertions.append(
                        AssertionResult(
                            kind="expect_visible",
                            target=f"{spec.name or spec.run}: contains {needle!r}",
                            passed=needle in combined,
                            detail="" if needle in combined else "not found in output",
                        )
                    )

            # 6. operate — API
            for spec in scenario.requests:
                url = spec.url or _join_url(scenario.app_url, spec.path or "/")
                obs = await http_request(
                    url,
                    method=spec.method,
                    headers=spec.headers,
                    json_body=spec.json_body,
                    body=spec.body,
                    timeout_s=self.cfg.http_timeout_s,
                    name=spec.name or url,
                )
                result.http.append(obs)
                if spec.expect_status is not None:
                    result.assertions.append(
                        AssertionResult(
                            kind="expect_state",
                            target=f"{spec.method} {url}: status == {spec.expect_status}",
                            passed=obs.status == spec.expect_status,
                            detail=f"got {obs.status if obs.status is not None else obs.error}",
                        )
                    )
                for needle in spec.expect_contains:
                    result.assertions.append(
                        AssertionResult(
                            kind="expect_visible",
                            target=f"{spec.method} {url}: contains {needle!r}",
                            passed=needle in obs.body_text,
                            detail="" if needle in obs.body_text else "not found in response body",
                        )
                    )

            # 7. operate — browser
            if scenario.mode == "browser":
                if not self.cfg.browser_enabled:
                    result.error = (
                        "scenario requires a browser but browser support is disabled "
                        "(set run.browser_enabled: true or pass --browser)"
                    )
                    return await self._finish(result, scenario, runner, services)
                result.browser = await self._run_browser(scenario)

            # 8. persisted-state checks
            for check in scenario.expect_state:
                res = await runner.run(
                    check.command, timeout_s=check.timeout_s or self.cfg.command_timeout_s
                )
                result.commands.append(res)
                combined = f"{res.stdout}\n{res.stderr}"
                for needle in check.contains:
                    result.assertions.append(
                        AssertionResult(
                            kind="expect_state",
                            target=f"{check.name or check.command}: contains {needle!r}",
                            passed=needle in combined,
                            detail="" if needle in combined else "not found in command output",
                        )
                    )
                for needle in check.not_contains:
                    result.assertions.append(
                        AssertionResult(
                            kind="expect_state",
                            target=f"{check.name or check.command}: must not contain {needle!r}",
                            passed=needle not in combined,
                            detail="" if needle not in combined else "unexpectedly present",
                        )
                    )

            # 9. global visible / forbidden checks over everything observed
            haystack = self._observed_text(result)
            for needle in scenario.expect_visible:
                result.assertions.append(
                    AssertionResult(
                        kind="expect_visible",
                        target=needle,
                        passed=needle in haystack,
                        detail="" if needle in haystack else "not present in any observed output",
                    )
                )
            for needle in scenario.forbidden:
                result.assertions.append(
                    AssertionResult(
                        kind="forbidden",
                        target=needle,
                        passed=needle not in haystack,
                        detail="" if needle not in haystack else "present in observed output",
                    )
                )

            return await self._finish(result, scenario, runner, services)

        except Exception as exc:  # never let a scenario crash the loop
            result.error = f"scenario execution raised: {type(exc).__name__}: {redact(str(exc))}"
            return await self._finish(result, scenario, runner, services)

    async def _finish(
        self,
        result: ScenarioResult,
        scenario: Scenario,
        runner: ProcessRunner,
        services: ServiceManager,
    ) -> ScenarioResult:
        """Always tear down, even after failure."""
        with contextlib.suppress(Exception):
            self.service_logs = services.all_logs()
        with contextlib.suppress(Exception):
            result.teardown = await runner.run_all(
                scenario.teardown, timeout_s=self.cfg.teardown_timeout_s, stop_on_failure=False
            )
        with contextlib.suppress(Exception):
            await services.stop_all()
        return result

    @staticmethod
    def _observed_text(result: ScenarioResult) -> str:
        parts: list[str] = []
        for group in (result.setup, result.commands, result.teardown):
            for r in group:
                parts.append(r.stdout)
                parts.append(r.stderr)
        for obs in result.http:
            parts.append(obs.body_text)
        if result.browser:
            parts.append(result.browser.visible_text)
            parts.extend(result.browser.console_errors)
        return "\n".join(p for p in parts if p)

    # -- browser ---------------------------------------------------------

    async def _run_browser(self, scenario: Scenario) -> BrowserObservation:
        """Drive the real UI with Playwright and capture what a user would see."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return BrowserObservation(
                steps=["playwright is not installed; run: pip install playwright && playwright install chromium"]
            )

        obs = BrowserObservation(url=scenario.app_url)
        shots_dir = self.artifact_dir / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        trace_path = self.artifact_dir / "trace.zip"
        spec = scenario.browser or BrowserSpec()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.cfg.headless)
            context = await browser.new_context(
                viewport={"width": self.cfg.viewport_width, "height": self.cfg.viewport_height}
            )
            if self.cfg.capture_trace:
                await context.tracing.start(screenshots=True, snapshots=True, sources=False)

            page = await context.new_page()
            page.on(
                "console",
                lambda msg: obs.console_errors.append(redact(f"[{msg.type}] {msg.text}"))
                if msg.type in ("error", "warning")
                else None,
            )
            page.on(
                "requestfailed",
                lambda req: obs.network_failures.append(
                    redact(f"{req.method} {req.url} — {(req.failure or '')}")
                ),
            )
            page.on(
                "response",
                lambda resp: obs.network_failures.append(redact(f"HTTP {resp.status} {resp.url}"))
                if resp.status >= 400
                else None,
            )

            try:
                if scenario.app_url:
                    await page.goto(scenario.app_url, wait_until="domcontentloaded", timeout=30_000)
                    obs.steps.append(f"opened {scenario.app_url}")
                if spec.initial_screenshot:
                    obs.screenshots.append(await self._shot(page, shots_dir, "01-initial"))

                for idx, step in enumerate(spec.steps, start=1):
                    await self._run_step(page, step, obs, shots_dir, idx, scenario.app_url)

                if spec.final_screenshot:
                    obs.screenshots.append(await self._shot(page, shots_dir, "99-final"))

                obs.url = page.url
                obs.title = await page.title()
                obs.visible_text = redact(
                    await page.evaluate("() => document.body ? document.body.innerText : ''")
                )
            except Exception as exc:
                obs.steps.append(f"ERROR: {type(exc).__name__}: {redact(str(exc))}")
                with contextlib.suppress(Exception):
                    obs.screenshots.append(await self._shot(page, shots_dir, "99-error"))
            finally:
                if self.cfg.capture_trace:
                    with contextlib.suppress(Exception):
                        await context.tracing.stop(path=str(trace_path))
                        obs.trace_path = str(trace_path)
                await context.close()
                await browser.close()

        return obs

    async def _run_step(
        self,
        page: Any,
        step: BrowserStep,
        obs: BrowserObservation,
        shots_dir: Path,
        idx: int,
        app_url: str,
    ) -> None:
        try:
            if step.goto is not None:
                target = step.goto if step.goto.startswith("http") else _join_url(app_url, step.goto)
                await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
                obs.steps.append(f"goto {target}")
            if step.click is not None:
                await page.click(step.click, timeout=15_000)
                obs.steps.append(f"clicked {step.click}")
            if step.fill is not None:
                await page.fill(step.fill, step.value or "", timeout=15_000)
                obs.steps.append(f"filled {step.fill} = {redact(step.value or '')}")
            if step.press is not None:
                await page.keyboard.press(step.press)
                obs.steps.append(f"pressed {step.press}")
            if step.wait_for is not None:
                await page.wait_for_selector(step.wait_for, timeout=20_000)
                obs.steps.append(f"waited for {step.wait_for}")
            if step.wait_ms is not None:
                await page.wait_for_timeout(step.wait_ms)
                obs.steps.append(f"waited {step.wait_ms}ms")
            if step.expect_text is not None:
                body = await page.evaluate("() => document.body ? document.body.innerText : ''")
                present = step.expect_text in body
                obs.steps.append(
                    f"expect_text {step.expect_text!r}: {'FOUND' if present else 'NOT FOUND'}"
                )
            if step.screenshot is not None:
                obs.screenshots.append(await self._shot(page, shots_dir, f"{idx:02d}-{step.screenshot}"))
        except Exception as exc:
            obs.steps.append(f"step {idx} FAILED: {type(exc).__name__}: {redact(str(exc))}")
            with contextlib.suppress(Exception):
                obs.screenshots.append(await self._shot(page, shots_dir, f"{idx:02d}-failed"))

    @staticmethod
    async def _shot(page: Any, shots_dir: Path, label: str) -> str:
        from .evidence import sanitize_filename

        path = shots_dir / f"{sanitize_filename(label)}.png"
        await page.screenshot(path=str(path), full_page=True)
        return str(path)


def _join_url(base: str, path: str) -> str:
    if not base:
        return path
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")
