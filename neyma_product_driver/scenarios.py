"""Scenario definition and execution.

A scenario describes how to start the product, how to know it is ready, how to
operate it, and what must (and must not) be observable afterwards. Scenarios are
plain YAML so they can be written by hand per Neyma phase.

Execution is strictly observational: assertions are recorded, never enforced by
raising. The evaluator decides what the observations mean.

Two execution shapes exist, and they are mutually exclusive per scenario:

*Phase form* — the original, and the only one a handwritten YAML file uses. The
executor runs the fixed phase order ``setup → fixtures → services → readiness →
commands → requests → browser → expect_state → global checks → teardown``. Every
existing scenario file keeps working unchanged.

*Step form* — an optional ordered ``steps:`` list, used by scenarios the planner
compiles. Order is the whole point: "approve, then read back", "approve twice",
"approve then restart the service" and "two operators approve simultaneously"
are different situations only because of sequencing, and the phase form cannot
express any of them. When ``steps`` is present the phase-level operate fields
must be empty, so there is never an ambiguous mix of the two.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import ScenarioRunConfig
from .models import (
    AssertionResult,
    BrowserObservation,
    BrowserTextExpectation,
    HttpObservation,
    RiskEvidence,
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
    #: Per-request override, in seconds; fractional allowed. A scenario that
    #: exercises "timed out before the effect landed" needs a deadline shorter
    #: than the run-wide one, and often shorter than a second.
    timeout_s: float | None = None

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
    #: Seconds; fractional allowed.
    timeout_s: float | None = None

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
    #: Seconds; fractional allowed.
    timeout_s: float | None = None

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


#: The step kinds the executor knows how to perform. Nothing outside this set is
#: executable, which is what lets a generated scenario be checked mechanically:
#: a proposed operation either compiles to one of these or it is refused.
StepKind = Literal[
    "command",
    "request",
    "parallel_requests",
    "browser",
    "state_check",
    "fixture",
    "wait",
    "restart_service",
    "stop_service",
    "start_service",
]


class ScenarioStep(BaseModel):
    """One ordered operation.

    Exactly one payload field is meaningful per ``kind``; the rest stay unset.
    A flat model rather than a discriminated union keeps the YAML readable and
    keeps the compiler in :mod:`~neyma_product_driver.scenario_plan` total.
    """

    model_config = ConfigDict(extra="forbid")

    kind: StepKind
    name: str = ""
    command: CommandSpec | None = None
    state_check: StateCheckSpec | None = None
    request: RequestSpec | None = None
    #: Issued concurrently. This is the only concurrency the executor offers,
    #: and it is what a double-submit or two-operator race actually needs.
    requests: list[RequestSpec] = Field(default_factory=list)
    browser: BrowserSpec | None = None
    #: Written under ``<artifact-dir>/fixtures/``; never at a caller-chosen path.
    fixture_name: str = ""
    fixture_content: str = ""
    #: Names a service declared in ``services``. A step may never name a command.
    service: str = ""
    wait_ms: int | None = None

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> "ScenarioStep":
        required = {
            "command": self.command is not None,
            "state_check": self.state_check is not None,
            "request": self.request is not None,
            "parallel_requests": bool(self.requests),
            "browser": self.browser is not None,
            "fixture": bool(self.fixture_name),
            "wait": self.wait_ms is not None,
            "restart_service": bool(self.service),
            "stop_service": bool(self.service),
            "start_service": bool(self.service),
        }[self.kind]
        if not required:
            raise ValueError(f"step kind {self.kind!r} is missing its payload")
        return self


class RiskClaim(BaseModel):
    """One explicit declaration: *this* scenario verifies *that* risk, by *these* oracles.

    The mechanism exists because risk coverage was being decided by a label
    match. A run identified "persistence_failure" as a P1 risk; the permanent
    scenario migrated a legacy database and read the resulting schema back, and
    passed; and the acceptance gate still reported the risk unverified, because
    the only thing it could see was that no *generated* scenario carried the tag
    ``persistence_failure``. Asking a builder to add coverage that already
    existed was the only move left, and it did not converge.

    So coverage becomes a statement a human writes down and a machine checks:

    .. code-block:: yaml

        verifies:
          - risk_category: persistence_failure
            claim: "a pre-M3 database migrates to the canonical effect shape"
            checks: ["the M3 migration battery"]
            observations: ["A LEGACY DATABASE MIGRATES TO THE CANONICAL EFFECT SHAPE"]

    ``checks`` names commands or state checks *in this same scenario* — every
    one must have executed and every assertion it produced must have passed.
    ``observations`` are literal substrings, matched exactly as ``expect_visible``
    is: against the output of the named ``checks`` when there are any, and
    against everything the run observed when there are not. At least one of the
    two is required: a claim with no oracle is an opinion, and this file has no
    place to store opinions.

    Nothing a model writes reaches here. Generated scenarios are compiled from
    :class:`~neyma_product_driver.scenario_plan.GeneratedScenario`, which has no
    field that could produce a ``RiskClaim``, and the suite discards any claim
    arriving on a generated entry regardless.
    """

    model_config = ConfigDict(extra="forbid")

    #: A ``RiskCategory`` member. Anything else is a load-time error: an
    #: unrecognised category would silently match no risk and the declaration
    #: would read as coverage while providing none.
    risk_category: str
    #: What this claim asserts, in the author's words. Recorded and rendered so
    #: the mapping is auditable; never matched against anything.
    claim: str
    checks: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)

    @field_validator("checks", "observations", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else (v or [])

    @field_validator("risk_category")
    @classmethod
    def _known_category(cls, v: str) -> str:
        # Imported here rather than at module scope: scenario_plan imports this
        # module, so the dependency only runs in this direction on demand.
        from .scenario_plan import RiskCategory

        value = (v or "").strip()
        try:
            RiskCategory(value)
        except ValueError:
            raise ValueError(
                f"unknown risk_category {v!r} in a verifies: entry. It must be one of "
                + ", ".join(sorted(c.value for c in RiskCategory))
            ) from None
        return value

    @model_validator(mode="after")
    def _has_an_oracle(self) -> "RiskClaim":
        if not self.claim.strip():
            raise ValueError("a verifies: entry must state the claim it makes")
        if not self.checks and not self.observations:
            raise ValueError(
                f"the verifies: entry {self.claim!r} names neither a check nor an "
                "observation, so nothing about it could pass or fail. Name the command "
                "or state check that must pass, or the literal text the product must "
                "emit."
            )
        return self


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

    #: Ordered form. Mutually exclusive with the phase-level operate fields
    #: above; see the module docstring. Empty for every handwritten scenario.
    steps: list[ScenarioStep] = Field(default_factory=list)

    expect_visible: list[str] = Field(default_factory=list)
    expect_state: list[StateCheckSpec] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)

    #: Explicit, human-authored risk coverage: which identified risk categories
    #: this scenario verifies, and by which oracles. Empty for every scenario
    #: that declares none — and a scenario that declares none simply covers no
    #: risk category, which is the same fail-closed position as before.
    verifies: list[RiskClaim] = Field(default_factory=list)

    teardown: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("expect_visible", "forbidden", "fixtures", "setup", "teardown", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else (v or [])

    @model_validator(mode="after")
    def _one_execution_shape(self) -> "Scenario":
        if not self.steps:
            return self
        conflicting = [
            name
            for name, value in (
                ("commands", self.commands),
                ("requests", self.requests),
                ("browser", self.browser),
                ("expect_state", self.expect_state),
            )
            if value
        ]
        if conflicting:
            raise ValueError(
                "a scenario using ordered 'steps' must not also set "
                + ", ".join(conflicting)
                + " — the two execution shapes have different orderings, and "
                "silently interleaving them would make the observed sequence a guess"
            )
        return self

    @model_validator(mode="after")
    def _claims_name_real_checks(self) -> "Scenario":
        """Every ``verifies: checks:`` name must be a check this scenario runs.

        A name that matches nothing would produce a claim that can never be
        established — which fails closed, but silently and for the wrong reason.
        A reader would see a declaration and a permanent gap and have no way to
        tell a typo from a genuine absence of evidence, so the typo is a
        load-time error.
        """
        known = self.check_names()
        for claim in self.verifies:
            unknown = [name for name in claim.checks if name not in known]
            if unknown:
                raise ValueError(
                    f"the verifies: entry {claim.claim!r} names check(s) this scenario does "
                    f"not run: {', '.join(sorted(unknown))}. Named checks must match the "
                    "`name:` of a command or state check in this same scenario"
                    + (f" (available: {', '.join(sorted(known))})" if known else "")
                )
        return self

    @model_validator(mode="after")
    def _claims_name_a_check_that_can_emit_them(self) -> "Scenario":
        """A claim may not require an observation none of its checks can produce.

        The sibling above catches a name that matches nothing. This catches the
        other half of the same defect: a name that matches the *wrong* check.
        Observations are matched against the output of the named checks only —
        see :meth:`ScenarioExecutor._resolve_risk_claims` — so a claim that
        names commands A and B while the literal it requires is emitted by C
        can never be established, no matter how correct the product is. It
        fails closed forever, and it reads on the gate as a product defect
        rather than as the mapping error it is.

        Statically, the one thing this file knows about what a check emits is
        what the check itself *declares*: a command's ``expect_contains`` and a
        state check's ``contains``. So the rule is scoped to exactly that and no
        further. When a literal has a declared producer in this scenario and the
        claim requiring it names none of them, that is decidable and wrong.
        When it has no declared producer — free-form narration from a probe or a
        mutation battery — nothing here can attribute it, and this stays silent
        rather than guessing; that residue is what a per-scenario readiness test
        is for.
        """
        declared: dict[str, set[str]] = {}
        for spec in self.commands:
            if spec.name:
                for literal in spec.expect_contains:
                    declared.setdefault(literal, set()).add(spec.name)
        for check in self.expect_state:
            if check.name:
                for literal in check.contains:
                    declared.setdefault(literal, set()).add(check.name)
        for step in self.steps:
            if step.command is not None and step.command.name:
                for literal in step.command.expect_contains:
                    declared.setdefault(literal, set()).add(step.command.name)
            if step.state_check is not None and step.state_check.name:
                for literal in step.state_check.contains:
                    declared.setdefault(literal, set()).add(step.state_check.name)

        for claim in self.verifies:
            if not claim.checks:
                # No named checks: observations are matched against everything
                # the run observed, so no attribution is being asserted.
                continue
            named = set(claim.checks)
            for literal in claim.observations:
                producers = declared.get(literal)
                if producers and not (producers & named):
                    raise ValueError(
                        f"the verifies: entry {claim.claim!r} requires the observation "
                        f"{literal!r}, but the check(s) it names "
                        f"({', '.join(sorted(named))}) do not include any check that "
                        f"declares it. In this scenario {literal!r} is declared by "
                        f"{', '.join(sorted(producers))}. Observations are matched only "
                        "against the output of the named checks, so this claim could "
                        "never be established. Name the check that produces it."
                    )
        return self

    def check_names(self) -> set[str]:
        """The names of every command and state check this scenario runs."""
        names: set[str] = set()
        for spec in self.commands:
            if spec.name:
                names.add(spec.name)
        for check in self.expect_state:
            if check.name:
                names.add(check.name)
        for step in self.steps:
            if step.command is not None and step.command.name:
                names.add(step.command.name)
            if step.state_check is not None and step.state_check.name:
                names.add(step.state_check.name)
        return names

    def declared_risk_categories(self) -> set[str]:
        """Risk categories this scenario *claims* to verify.

        A claim, never a result. What was actually established is decided by
        execution and recorded on the :class:`ScenarioResult`.
        """
        return {claim.risk_category for claim in self.verifies}

    @property
    def uses_steps(self) -> bool:
        return bool(self.steps)

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
        if self.steps:
            lines.append("ordered steps: " + ", ".join(self._step_label(s) for s in self.steps))
        if self.expect_visible:
            lines.append("must be observable: " + "; ".join(self.expect_visible))
        if self.forbidden:
            lines.append("must NOT appear: " + "; ".join(self.forbidden))
        return "\n".join(lines)

    @staticmethod
    def _step_label(step: ScenarioStep) -> str:
        if step.name:
            return f"{step.kind}:{step.name}"
        if step.kind == "request" and step.request is not None:
            return f"request:{step.request.method} {step.request.url or step.request.path}"
        if step.kind == "parallel_requests":
            return f"parallel_requests:x{len(step.requests)}"
        if step.kind in {"restart_service", "stop_service", "start_service"}:
            return f"{step.kind}:{step.service}"
        return step.kind


#: What a handwritten scenario may be called. A permanent scenario's name is
#: also its identity: ``_assemble_suite`` uses it verbatim as the suite id, and
#: the evidence directory is derived from that id by folding everything outside
#: this set to ``-``. ``approve twice`` and a generated ``approve-twice`` were
#: therefore two required scenarios and one directory — the second overwrote the
#: first's record while the acceptance gate credited both as verified. Closing
#: it where the name is authored costs nothing: every scenario this repository
#: ships already complies.
PERMANENT_NAME = re.compile(r"[A-Za-z0-9._-]+")


def load_scenario(path: str | Path) -> Scenario:
    """Parse a scenario YAML file."""
    p = Path(os.path.expanduser(str(path)))
    if not p.exists():
        raise FileNotFoundError(f"Scenario file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Scenario file must contain a mapping: {p}")
    scenario = Scenario(**raw)
    if not PERMANENT_NAME.fullmatch(scenario.name):
        raise ValueError(
            f"{p}: scenario name {scenario.name!r} must be made only of letters, digits, "
            "'.', '_' and '-'. A handwritten scenario's name is used verbatim as its "
            "identity in the suite and as the name of its evidence directory, and "
            "characters outside that set fold together — two names that fold to the same "
            "directory are one piece of evidence and two claims about it."
        )
    return scenario


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
        approved_commands: Any = None,
    ) -> None:
        self.repo = Path(repo)
        self.cfg = run_config
        self.artifact_dir = Path(artifact_dir)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.service_logs: dict[str, str] = {}
        #: fixture name -> absolute path, for ``{{fixture:NAME}}`` substitution.
        self._fixtures: dict[str, str] = {}
        #: The set validation approved this run's commands against, when there
        #: is one. Held so that a command whose text *changed* between
        #: validation and execution can be judged again against the same rule
        #: rather than trusted because an earlier, different string passed.
        self.approved_commands = approved_commands
        #: check name -> whether every assertion that check produced passed.
        #: Populated as the run proceeds; a check that never ran is absent, and
        #: absent is not "passed". This is what makes a ``verifies:`` claim a
        #: statement about execution rather than about the scenario file.
        self._check_outcomes: dict[str, bool] = {}
        #: check name -> everything that check printed, so a claim's literal
        #: observations are matched against the output of the check that is
        #: supposed to produce them rather than against the whole run.
        self._check_output: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        result = ScenarioResult(scenario_name=scenario.name, mode=scenario.mode)
        self._check_outcomes = {}
        self._check_output = {}
        runner = ProcessRunner(
            self.repo,
            default_timeout_s=self.cfg.command_timeout_s,
            default_env=scenario.env,
        )
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

            if scenario.uses_steps:
                # 5-8 (ordered form). A browser step is what declares the need
                # for a browser, so the capability check moves inside.
                await self._run_steps(scenario, result, runner, services)
            else:
                # 5. operate — commands
                for spec in scenario.commands:
                    await self._do_command(result, spec, runner)

                # 6. operate — API
                for spec in scenario.requests:
                    await self._do_request(result, spec, scenario.app_url)

                # 7. operate — browser
                if scenario.mode == "browser":
                    if not self.cfg.browser_enabled:
                        result.error = (
                            "scenario requires a browser but browser support is disabled "
                            "(set run.browser_enabled: true or pass --browser)"
                        )
                        return await self._finish(result, scenario, runner, services)
                    observation = await self._run_browser(scenario, scenario.browser)
                    result.browser = observation
                    self._assert_browser_text(result, observation)

                # 8. persisted-state checks
                for check in scenario.expect_state:
                    await self._do_state_check(result, check, runner)

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

            # 10. resolve the scenario's declared risk claims against what was
            #     actually observed. Deliberately last, and deliberately only on
            #     the path where the product was exercised to completion: a run
            #     that returned early verified nothing, and records no claim
            #     rather than an unestablished one nobody asked for.
            self._resolve_risk_claims(scenario, result, haystack)

            return await self._finish(result, scenario, runner, services)

        except Exception as exc:  # never let a scenario crash the loop
            result.error = f"scenario execution raised: {type(exc).__name__}: {redact(str(exc))}"
            return await self._finish(result, scenario, runner, services)

    # -- ordered steps ----------------------------------------------------

    async def _run_steps(
        self,
        scenario: Scenario,
        result: ScenarioResult,
        runner: ProcessRunner,
        services: ServiceManager,
    ) -> None:
        """Perform ``scenario.steps`` in order, recording everything observed.

        A step that cannot run is recorded as a failed assertion rather than
        raised: the evaluator judges observations, and "the service named by a
        restart step does not exist" is an observation about the scenario, not a
        crash of the harness.
        """
        for index, step in enumerate(scenario.steps, start=1):
            label = step.name or f"step {index} ({step.kind})"
            result.steps_performed.append(f"{index:02d}. {step.kind}: {label}")

            if step.kind == "command" and step.command is not None:
                await self._do_command(result, step.command, runner)

            elif step.kind == "state_check" and step.state_check is not None:
                await self._do_state_check(result, step.state_check, runner)

            elif step.kind == "request" and step.request is not None:
                await self._do_request(result, step.request, scenario.app_url)

            elif step.kind == "parallel_requests":
                # Concurrency is the point: issued together, awaited together.
                observations = await asyncio.gather(
                    *(
                        self._do_request(result, spec, scenario.app_url, record=False)
                        for spec in step.requests
                    )
                )
                for spec, obs in zip(step.requests, observations):
                    result.http.append(obs)
                    self._assert_request(result, spec, obs)

            elif step.kind == "browser" and step.browser is not None:
                if not self.cfg.browser_enabled:
                    result.assertions.append(
                        AssertionResult(
                            kind="expect_state",
                            target=f"{label}: browser step",
                            passed=False,
                            detail="browser support is disabled (set run.browser_enabled: true)",
                        )
                    )
                    continue
                obs = await self._run_browser(scenario, step.browser, label=label)
                self._assert_browser_text(result, obs)
                self._merge_browser(result, obs)

            elif step.kind == "fixture":
                self._write_fixture(result, step)

            elif step.kind == "wait":
                await asyncio.sleep(min(max(int(step.wait_ms or 0), 0), 60_000) / 1000.0)

            elif step.kind in {"restart_service", "stop_service", "start_service"}:
                await self._do_service_step(result, step, services, label, scenario)

    async def _do_service_step(
        self,
        result: ScenarioResult,
        step: ScenarioStep,
        services: ServiceManager,
        label: str,
        scenario: Scenario,
    ) -> None:
        action = {"restart_service": "restart", "stop_service": "stop", "start_service": "start"}[
            step.kind
        ]
        try:
            if step.kind == "restart_service":
                await services.restart(step.service)
            elif step.kind == "stop_service":
                await services.stop(step.service)
            else:
                await services.start_declared(step.service)
        except KeyError:
            result.assertions.append(
                AssertionResult(
                    kind="expect_state",
                    target=f"{label}: {action} service {step.service!r}",
                    passed=False,
                    detail=(
                        f"no service named {step.service!r} is declared by this scenario; "
                        "a step may only act on a service the scenario itself started"
                    ),
                )
            )
            return

        detail = f"service {step.service!r} {action}ed"
        # A restarted service is not a ready service. Without waiting, the very
        # next step races the new process's bind and reports a connection error
        # as a product defect — which is exactly the false signal a recovery
        # scenario must not produce.
        if step.kind in {"restart_service", "start_service"} and scenario.readiness:
            ready, why = await wait_for_readiness(
                scenario.readiness,
                cwd=self.repo,
                timeout_s=self.cfg.readiness_timeout_s,
                poll_interval_s=self.cfg.readiness_poll_interval_s,
            )
            if not ready:
                result.assertions.append(
                    AssertionResult(
                        kind="expect_state",
                        target=f"{label}: service {step.service!r} became ready again",
                        passed=False,
                        detail=why,
                    )
                )
                return
            detail += "; readiness checks passed again"

        result.assertions.append(
            AssertionResult(
                kind="expect_state",
                target=f"{label}: {action} service {step.service!r}",
                passed=True,
                detail=detail,
            )
        )

    def _write_fixture(self, result: ScenarioResult, step: ScenarioStep) -> None:
        """Materialize a temporary fixture inside the run's artifact directory.

        The caller supplies a *name*, never a path: the destination is derived
        here, so no generated scenario can write outside the evidence root.
        """
        from .evidence import sanitize_filename

        fixtures_dir = self.artifact_dir / "fixtures"
        fixtures_dir.mkdir(parents=True, exist_ok=True)
        path = fixtures_dir / sanitize_filename(step.fixture_name)
        # Belt. The destination is derived here rather than supplied, so this
        # should be true by construction — which is exactly why it is worth
        # asserting: the fixture path is the one value that later becomes an
        # argument to a subprocess, and "should be true by construction" is
        # what was believed about the validated command string too.
        if not self._inside_fixtures_dir(str(path)):
            result.assertions.append(
                AssertionResult(
                    kind="expect_state",
                    target=f"fixture {step.fixture_name}",
                    passed=False,
                    detail=(
                        f"the fixture would be written to {path}, which is outside this "
                        f"run's fixture directory {fixtures_dir}"
                    ),
                )
            )
            return
        try:
            path.write_text(step.fixture_content, encoding="utf-8")
        except OSError as exc:
            result.assertions.append(
                AssertionResult(
                    kind="expect_state",
                    target=f"fixture {step.fixture_name}",
                    passed=False,
                    detail=f"could not write fixture: {exc}",
                )
            )
            return
        self._fixtures[step.fixture_name] = str(path)
        result.fixtures_written.append(str(path))

    def _substitute(self, text: str | None) -> str:
        """Replace ``{{fixture:NAME}}`` with the fixture's absolute path.

        The only substitution the executor performs. A fixture that was never
        written leaves its placeholder in place, so the failure is visible in
        the evidence instead of silently becoming an empty string.
        """
        if not text or "{{fixture:" not in text:
            return text or ""
        return re.sub(
            r"\{\{fixture:([^}]+)\}\}",
            lambda m: self._fixtures.get(m.group(1).strip(), m.group(0)),
            text,
        )

    def _inside_fixtures_dir(self, candidate: str) -> bool:
        """Whether ``candidate`` resolves inside this run's fixture directory."""
        fixtures_dir = (self.artifact_dir / "fixtures").resolve()
        try:
            resolved = Path(candidate).resolve()
        except (OSError, ValueError):
            return False
        return resolved != fixtures_dir and fixtures_dir in resolved.parents

    def _substitution_problem(self, original: str | None, command: str) -> str:
        """Why the *executed* command may not run, or "".

        The safety boundary judged one string; ``{{fixture:NAME}}`` expansion
        then handed a different string to the subprocess. Everything that made
        the first string safe — no shell composition, an approved prefix, an
        argument tail a human would recognise — was established about text that
        no longer exists by the time anything runs.

        So when substitution changed the command, the command it became is put
        back through the same rule. Two things are asked of it: every path a
        placeholder expanded to must be one this run wrote into its own fixture
        directory, and the resulting string must still be approved. A command
        that was not substituted is untouched — it *is* the string validation
        judged.
        """
        if command == (original or ""):
            return ""

        for value in self._fixtures.values():
            if value in command and not self._inside_fixtures_dir(value):
                return (
                    f"a fixture placeholder expanded to {value!r}, which is outside this "
                    "run's fixture directory"
                )

        if self.approved_commands is None:
            return ""
        ok, why = self.approved_commands.approves(command)
        if ok:
            return ""
        return (
            f"the command validation approved ({original!r}) is not the command that would "
            f"have run ({command!r}), and what it became is not approved: {why}"
        )

    @staticmethod
    def _assert_browser_text(result: ScenarioResult, obs: BrowserObservation) -> None:
        """Score what the browser sequence asked for, and whether it happened.

        Without this a browser scenario whose only oracle is ``expect_text``
        cannot fail: the check ran, the page disagreed, and the result was a
        line of narration nothing compared against anything. The generator is
        told ``expect_text`` is a valid browser oracle, and
        ``GeneratedScenario.has_observable_outcome`` counts it as one, so a
        proposal built entirely on it validated and then passed unconditionally.

        A step that *raised* is scored for the same reason. Clicking a selector
        that is not on the page is not a passing interaction, and a scenario
        whose every step blew up would otherwise finish with no assertions at
        all — which ``ScenarioResult.passed`` reads as success.

        And underneath both, the floor: a browser session that never loaded a
        page is scored as a failure whatever else it did or did not record.
        Every oracle here is derived from something the page produced, so a
        session that reached no page produces no oracles, and ``all([])`` is
        ``True``. That is how a navigation timeout, and separately a missing
        playwright install, each came back as a PASSED required scenario with
        zero assertions and verified evidence. The floor does not depend on
        anyone remembering to record a failure at each degraded exit; it asks
        the one question those exits all have in common.
        """
        if not obs.page_loaded:
            result.assertions.append(
                AssertionResult(
                    kind="expect_state",
                    target="browser session: the product's page was loaded",
                    passed=False,
                    detail=(
                        "no page was ever successfully loaded in this browser session, so "
                        "nothing about the product was observed"
                        + (f" ({obs.steps[-1]})" if obs.steps else "")
                    ),
                )
            )
        for failure in obs.step_failures:
            result.assertions.append(
                AssertionResult(
                    kind="expect_state",
                    target=failure,
                    passed=False,
                    detail="the browser step did not complete, so nothing after it was observed",
                )
            )
        for expectation in obs.text_expectations:
            where = f"{expectation.label}: " if expectation.label else ""
            result.assertions.append(
                AssertionResult(
                    kind="expect_visible",
                    target=(
                        f"{where}browser step {expectation.step}: "
                        f"expect_text {expectation.text!r}"
                    ),
                    passed=expectation.present,
                    detail=""
                    if expectation.present
                    else "not present in the page text at that point",
                )
            )

    def _merge_browser(self, result: ScenarioResult, obs: BrowserObservation) -> None:
        if result.browser is None:
            result.browser = obs
            return
        merged = result.browser
        merged.url = obs.url or merged.url
        merged.title = obs.title or merged.title
        merged.visible_text = obs.visible_text or merged.visible_text
        # Extended, not replaced. `visible_text` keeps meaning "the last page",
        # which is what the evaluator and the failure excerpts show; the
        # accumulated channel is what the scenario-level expectations search,
        # because a `forbidden` string is forbidden *anywhere* the run looked.
        merged.observed_texts.extend(obs.observed_texts)
        merged.page_loaded = merged.page_loaded or obs.page_loaded
        merged.screenshots.extend(obs.screenshots)
        merged.console_errors.extend(obs.console_errors)
        merged.network_failures.extend(obs.network_failures)
        merged.steps.extend(obs.steps)
        merged.text_expectations.extend(obs.text_expectations)
        merged.step_failures.extend(obs.step_failures)
        merged.trace_path = obs.trace_path or merged.trace_path

    # -- shared observation recording -------------------------------------

    def _record_check(
        self, name: str, result: ScenarioResult, start: int, output: str = ""
    ) -> None:
        """Remember whether the named check established anything.

        A check establishes something only when it produced at least one
        assertion and every one of them passed. Zero assertions means the spec
        stated no expectation, so it observed the product without judging it —
        ``all([])`` is True and would have read as a pass, which is precisely
        the "it ran, therefore it is covered" reasoning this must not do.
        """
        if not name:
            return
        produced = result.assertions[start:]
        passed = bool(produced) and all(a.passed for a in produced)
        # Two checks may share a name; the conjunction is the honest reading.
        self._check_outcomes[name] = self._check_outcomes.get(name, True) and passed
        self._check_output[name] = self._check_output.get(name, "") + "\n" + output

    def _resolve_risk_claims(
        self, scenario: Scenario, result: ScenarioResult, haystack: str
    ) -> None:
        """Decide, mechanically, which declared risk claims this run established.

        Every input is something the run produced: which named checks ran, which
        of their assertions passed, and the text the product actually emitted.
        Nothing consults a model, and no field on this scenario can assert a
        claim into existence — the claim names its oracles and the oracles
        either held or they did not.
        """
        for claim in scenario.verifies:
            problems: list[str] = []
            for name in claim.checks:
                state = self._check_outcomes.get(name)
                if state is None:
                    problems.append(f"the check {name!r} did not run")
                elif not state:
                    problems.append(f"the check {name!r} ran and did not pass")
            # Scoped to the named checks when there are any: the check that is
            # supposed to establish the claim is the one that must have emitted
            # the text. Matching against the whole run would let one command's
            # output satisfy a claim about another's.
            observed = (
                "\n".join(self._check_output.get(name, "") for name in claim.checks)
                if claim.checks
                else haystack
            )
            missing = [text for text in claim.observations if text not in observed]
            if missing:
                where = (
                    f"in the output of {', '.join(repr(n) for n in claim.checks)}"
                    if claim.checks
                    else "anywhere in what this run observed"
                )
                problems.append(
                    "the product never emitted "
                    + ", ".join(repr(text) for text in missing)
                    + f" {where}"
                )
            result.risk_evidence.append(
                RiskEvidence(
                    risk_category=claim.risk_category,
                    claim=claim.claim,
                    scenario_name=scenario.name,
                    checks=list(claim.checks),
                    observations=list(claim.observations),
                    established=not problems,
                    reason="; ".join(problems),
                )
            )

    async def _do_command(
        self, result: ScenarioResult, spec: CommandSpec, runner: ProcessRunner
    ) -> None:
        command = self._substitute(spec.run)
        refusal = self._substitution_problem(spec.run, command)
        if refusal:
            result.assertions.append(
                AssertionResult(
                    kind="expect_state",
                    target=f"{spec.name or spec.run}: runs the command that was approved",
                    passed=False,
                    detail=refusal,
                )
            )
            return
        res = await runner.run(command, timeout_s=spec.timeout_s or self.cfg.command_timeout_s)
        result.commands.append(res)
        start = len(result.assertions)
        if spec.expect_exit_code is not None:
            result.assertions.append(
                AssertionResult(
                    kind="expect_state",
                    target=f"{spec.name or command}: exit == {spec.expect_exit_code}",
                    passed=res.exit_code == spec.expect_exit_code,
                    detail=f"got exit={res.exit_code}{' (timed out)' if res.timed_out else ''}",
                )
            )
        combined = f"{res.stdout}\n{res.stderr}"
        for needle in spec.expect_contains:
            result.assertions.append(
                AssertionResult(
                    kind="expect_visible",
                    target=f"{spec.name or command}: contains {needle!r}",
                    passed=needle in combined,
                    detail="" if needle in combined else "not found in output",
                )
            )
        self._record_check(spec.name, result, start, combined)

    async def _do_state_check(
        self, result: ScenarioResult, check: StateCheckSpec, runner: ProcessRunner
    ) -> None:
        command = self._substitute(check.command)
        refusal = self._substitution_problem(check.command, command)
        if refusal:
            result.assertions.append(
                AssertionResult(
                    kind="expect_state",
                    target=f"{check.name or check.command}: runs the command that was approved",
                    passed=False,
                    detail=refusal,
                )
            )
            return
        res = await runner.run(command, timeout_s=check.timeout_s or self.cfg.command_timeout_s)
        result.commands.append(res)
        start = len(result.assertions)
        combined = f"{res.stdout}\n{res.stderr}"
        for needle in check.contains:
            result.assertions.append(
                AssertionResult(
                    kind="expect_state",
                    target=f"{check.name or command}: contains {needle!r}",
                    passed=needle in combined,
                    detail="" if needle in combined else "not found in command output",
                )
            )
        for needle in check.not_contains:
            result.assertions.append(
                AssertionResult(
                    kind="expect_state",
                    target=f"{check.name or command}: must not contain {needle!r}",
                    passed=needle not in combined,
                    detail="" if needle not in combined else "unexpectedly present",
                )
            )
        self._record_check(check.name, result, start, combined)

    async def _do_request(
        self,
        result: ScenarioResult,
        spec: RequestSpec,
        app_url: str,
        record: bool = True,
    ) -> HttpObservation:
        url = self._substitute(spec.url) or _join_url(app_url, self._substitute(spec.path) or "/")
        obs = await http_request(
            url,
            method=spec.method,
            headers=spec.headers,
            json_body=spec.json_body,
            body=self._substitute(spec.body) if spec.body is not None else None,
            timeout_s=spec.timeout_s or self.cfg.http_timeout_s,
            name=spec.name or url,
        )
        if record:
            result.http.append(obs)
            self._assert_request(result, spec, obs)
        return obs

    @staticmethod
    def _assert_request(
        result: ScenarioResult, spec: RequestSpec, obs: HttpObservation
    ) -> None:
        if spec.expect_status is not None:
            result.assertions.append(
                AssertionResult(
                    kind="expect_state",
                    target=f"{spec.method} {obs.url}: status == {spec.expect_status}",
                    passed=obs.status == spec.expect_status,
                    detail=f"got {obs.status if obs.status is not None else obs.error}",
                )
            )
        for needle in spec.expect_contains:
            result.assertions.append(
                AssertionResult(
                    kind="expect_visible",
                    target=f"{spec.method} {obs.url}: contains {needle!r}",
                    passed=needle in obs.body_text,
                    detail="" if needle in obs.body_text else "not found in response body",
                )
            )

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
        """Everything the run actually observed — which is what was promised.

        The generator is told that ``expected_observations`` and
        ``forbidden_observations`` are matched against everything the run
        observed, including visible browser text. While the browser channel
        held only the final page, that was not true: a traceback rendered on
        the first of two screens was not in the haystack, so the product's
        primary UI-quality oracle scored PASS against text it had never seen.
        """
        parts: list[str] = []
        for group in (result.setup, result.commands, result.teardown):
            for r in group:
                parts.append(r.stdout)
                parts.append(r.stderr)
        for obs in result.http:
            parts.append(obs.body_text)
        if result.browser:
            parts.extend(result.browser.observed_texts)
            parts.append(result.browser.visible_text)
            parts.extend(result.browser.console_errors)
        return "\n".join(p for p in parts if p)

    # -- browser ---------------------------------------------------------

    async def _run_browser(
        self,
        scenario: Scenario,
        spec: BrowserSpec | None,
        label: str = "",
    ) -> BrowserObservation:
        """Drive the real UI with Playwright and capture what a user would see.

        One browser session per call. A scenario that needs page state to
        survive across interactions puts those interactions in a single browser
        step; separate steps deliberately get separate sessions, which is what
        makes "reopen the page after the backend changed" expressible.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            detail = (
                "playwright is not installed; run: pip install playwright && "
                "playwright install chromium"
            )
            # Recorded structurally as well as narratively. `obs.steps` is
            # narration and nothing scores it, so this exit used to hand back an
            # observation that looked, to everything downstream, exactly like a
            # browser session with nothing to report.
            return BrowserObservation(steps=[detail], step_failures=[detail])

        from .evidence import sanitize_filename

        obs = BrowserObservation(url=scenario.app_url)
        shots_dir = self.artifact_dir / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"{sanitize_filename(label)}-" if label else ""
        trace_path = self.artifact_dir / f"{prefix}trace.zip" if prefix else self.artifact_dir / "trace.zip"
        spec = spec or BrowserSpec()

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
                    # Only after goto *returns*. A navigation that raised
                    # reached no page, and the whole point of this flag is that
                    # nothing downstream has to infer that from narration.
                    obs.page_loaded = True
                    obs.steps.append(f"opened {scenario.app_url}")
                    await self._capture_text(page, obs)
                if spec.initial_screenshot:
                    obs.screenshots.append(await self._shot(page, shots_dir, f"{prefix}01-initial"))

                for idx, step in enumerate(spec.steps, start=1):
                    await self._run_step(
                        page, step, obs, shots_dir, idx, scenario.app_url, prefix
                    )

                if spec.final_screenshot:
                    obs.screenshots.append(await self._shot(page, shots_dir, f"{prefix}99-final"))

                obs.url = page.url
                obs.title = await page.title()
                obs.visible_text = redact(
                    await page.evaluate("() => document.body ? document.body.innerText : ''")
                )
                if obs.visible_text and obs.visible_text not in obs.observed_texts:
                    obs.observed_texts.append(obs.visible_text)
            except Exception as exc:
                detail = f"ERROR: {type(exc).__name__}: {redact(str(exc))}"
                obs.steps.append(detail)
                # Structural as well as narrative, for the same reason as the
                # missing-playwright exit above: `_assert_browser_text` reads
                # `step_failures`, and a session-level failure recorded only in
                # `steps` produced a scenario with no assertions, which
                # `ScenarioResult.passed` reads as a pass.
                obs.step_failures.append(detail)
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
        prefix: str = "",
    ) -> None:
        try:
            if step.goto is not None:
                # The same resolution the safety boundary used, not a second
                # opinion. The executor previously treated anything starting
                # with the four letters `http` as an absolute URL while the
                # validator only inspected `http://` and `https://`, so
                # `http:/host/x` was never checked and was still navigated to:
                # Chromium's parser reconstitutes the authority and the
                # approved app_url is replaced.
                from .scenario_validation import resolve_browser_target

                target, problem = resolve_browser_target(app_url=app_url, goto=step.goto)
                if problem:
                    raise ValueError(problem)
                await page.goto(target, wait_until="domcontentloaded", timeout=30_000)
                obs.page_loaded = True
                obs.steps.append(f"goto {target}")
                await self._capture_text(page, obs)
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
                # Recorded structurally as well, because the caller turns these
                # into assertions. Narration alone is not an oracle.
                obs.text_expectations.append(
                    BrowserTextExpectation(
                        text=step.expect_text, present=present, step=idx, label=prefix.strip("-")
                    )
                )
            if step.screenshot is not None:
                obs.screenshots.append(
                    await self._shot(page, shots_dir, f"{prefix}{idx:02d}-{step.screenshot}")
                )
        except Exception as exc:
            detail = f"step {idx} FAILED: {type(exc).__name__}: {redact(str(exc))}"
            obs.steps.append(detail)
            obs.step_failures.append(detail)
            with contextlib.suppress(Exception):
                obs.screenshots.append(
                    await self._shot(page, shots_dir, f"{prefix}{idx:02d}-failed")
                )

    @staticmethod
    async def _capture_text(page: Any, obs: BrowserObservation) -> None:
        """Record the page text as it is now, into the accumulating channel.

        Called at every point the page changes. Capturing only once, after the
        step loop, discarded every intermediate screen — including the one the
        scenario navigated away from because of what it showed.
        """
        with contextlib.suppress(Exception):
            text = redact(
                await page.evaluate("() => document.body ? document.body.innerText : ''")
            )
            if text:
                obs.observed_texts.append(text)

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
