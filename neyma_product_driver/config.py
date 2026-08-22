"""Driver configuration.

Loaded from a JSON/YAML file (``driver.config.yaml`` by default), overridable by
CLI flags. Validation is strict about the things that matter for safety: the
Neyma repo must be a real git repository, permission mode may never be a
bypassing mode, and iteration count is bounded.

**Paths are derived, never remembered.** ``driver_root`` defaults to wherever
this package actually lives (see :func:`~neyma_product_driver.paths.discover_driver_root`);
``neyma_repo`` has no default at all and must be configured. An earlier version
of this file carried two absolute paths belonging to one machine, one of which
has since gone stale — a driver that silently falls back to an obsolete path
reads a repository that is not the product, and reports confidently about it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .paths import (
    ApprovedRoots,
    default_roots,
    discover_driver_root,
    expand_resolved,
)

# Permission modes that would let the builder act without the Neyma repo's own
# controls being consulted. Never selectable.
FORBIDDEN_PERMISSION_MODES = {"bypassPermissions", "dontAsk", "auto"}

PermissionModeLiteral = Literal["default", "acceptEdits", "plan"]


class BuilderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = "opus"
    # 'acceptEdits' matches Neyma's own settings.local.json defaultMode, so the
    # builder can autonomously Write/Edit ordinary source, test, documentation
    # and new files without an interactive approval the noninteractive harness
    # cannot service. This never bypasses permission checks: Neyma's own deny
    # rules for secrets and protected configuration (loaded via setting_sources)
    # still win, and the driver's PreToolUse hook still blocks pushes, history
    # rewrites, external effects and protected control surfaces even when a mode
    # or allow-rule would otherwise permit them. Bypassing modes stay rejected.
    permission_mode: PermissionModeLiteral = "acceptEdits"
    max_turns: int | None = 120
    # 'user', 'project', 'local' — required for the SDK to load Neyma's
    # CLAUDE.md, .claude/settings.json, hooks, skills and subagents. The SDK
    # loads none of these unless asked.
    setting_sources: list[Literal["user", "project", "local"]] = Field(
        default_factory=lambda: ["user", "project", "local"]
    )
    # Explicit Agent SDK tool permissions. The driver runs unattended, so the
    # builder's ordinary working set is auto-approved here (no interactive
    # can_use_tool callback, which would only hang unattended). This is the whole
    # working set the owner authorized: read/search, file create/edit, notebooks,
    # todos, and Bash (for pytest, linters, scripts, and local git including a
    # local commit). The PreToolUse hook still denies the hard-blocked subset —
    # remote publishing, force push, history rewrites, secret access, external
    # effects, system installs — even though these tools are allow-listed here.
    allowed_tools: list[str] = Field(
        default_factory=lambda: [
            "Read",
            "Grep",
            "Glob",
            "NotebookRead",
            "Write",
            "Edit",
            "MultiEdit",
            "NotebookEdit",
            "TodoWrite",
            "Bash",
            "BashOutput",
            "KillShell",
            "WebFetch",
            "WebSearch",
        ]
    )
    # Empty means "inherit whatever the Neyma repo's own settings allow".
    disallowed_tools: list[str] = Field(default_factory=list)
    stream_progress: bool = True
    turn_timeout_s: int = 1800

    @field_validator("permission_mode")
    @classmethod
    def _no_bypass(cls, v: str) -> str:
        if v in FORBIDDEN_PERMISSION_MODES:
            raise ValueError(
                f"permission_mode={v!r} is forbidden by this driver; "
                "the builder must never bypass permissions"
            )
        return v

    @field_validator("max_turns")
    @classmethod
    def _positive_turns(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("max_turns must be >= 1 when set")
        return v


class EvaluatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = "opus"
    max_turns: int | None = 30
    turn_timeout_s: int = 900
    # The evaluator reasons over collected evidence; it must not edit the repo.
    # It may read files and run read-only inspection commands.
    allowed_tools: list[str] = Field(default_factory=lambda: ["Read", "Grep", "Glob"])


class ScenarioRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_timeout_s: int = 300
    service_start_timeout_s: int = 120
    readiness_timeout_s: int = 90
    readiness_poll_interval_s: float = 1.0
    http_timeout_s: int = 30
    teardown_timeout_s: int = 60
    browser_enabled: bool = False
    headless: bool = True
    capture_trace: bool = True
    viewport_width: int = 1440
    viewport_height: int = 900


class ScenarioGenerationConfig(BaseModel):
    """Dynamic scenario generation and adaptive verification.

    **On by default.** Generating situations from the diff, the requirements and
    the failures a run has already seen is the driver's main advantage over
    running one handwritten scenario, and requiring ``--auto-scenarios`` on every
    invocation meant the default run was the weakest one available. It is
    switched off per run with ``--no-auto-scenarios`` when there is a concrete
    reason it cannot apply.

    Every bound below exists so that an enabled run stays finite and predictable:
    a wave cannot propose unlimited scenarios, waves cannot recur forever, and
    the whole suite cannot outgrow ``max_total_scenarios`` however many risks are
    identified. Making generation the default changes how much is verified; it
    does not change what a generated scenario is allowed to do, which is still
    bounded by the approved command set and the loopback-only host list.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True

    # -- budgets ----------------------------------------------------------
    max_initial_scenarios: int = 12
    max_adaptive_scenarios_per_wave: int = 8
    max_waves: int = 3
    max_total_scenarios: int = 30
    max_scenarios_per_risk_category: int = 6
    #: Wall-clock ceiling for one suite execution. Scenarios not reached are
    #: recorded as SKIPPED with that reason, never silently dropped.
    execution_budget_s: int = 1800

    # -- what informs generation ------------------------------------------
    diff_aware: bool = True
    use_prior_failures: bool = True
    use_investigation_findings: bool = True

    # -- promotion --------------------------------------------------------
    #: A generated scenario that caught a real defect and later passed becomes a
    #: promotion *candidate*. Promotion into the permanent suite is always an
    #: explicit human action; this switch cannot make it automatic, it can only
    #: be left at its only supported value.
    promotion_requires_approval: bool = True

    # -- execution --------------------------------------------------------
    #: Must be 1. The suite executor is sequential — see
    #: :attr:`~neyma_product_driver.scenario_suite.SuiteExecutor.MAX_PARALLEL`,
    #: which refuses any other value rather than ignoring it. This key exists so
    #: the execution model is stated where an operator will look for it, not
    #: because there is a choice to make.
    max_parallel: int = 1

    # -- the generator ----------------------------------------------------
    model: str = "opus"
    #: Turns the read-only generator session may spend before it must answer.
    #: It reads the repository to ground its proposals; against a large product
    #: that is tens of Read/Grep calls before it writes anything. Measured too
    #: low at 16: the session ended `error_max_turns` having produced nothing,
    #: and the wave was recorded as empty. Raise it for exploration-heavy tasks.
    #: This is a budget on a read-only session, not a guard.
    generator_max_turns: int = 40
    #: Commands generated scenarios may run, beyond those already written into
    #: the repository's scenario files. A generated scenario can never author a
    #: command; it can only choose one from this derived set.
    approved_commands: list[str] = Field(default_factory=list)
    #: Hosts a generated request may address. Loopback only, by design.
    local_http_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "localhost", "::1"]
    )

    @field_validator(
        "max_initial_scenarios",
        "max_adaptive_scenarios_per_wave",
        "max_waves",
        "max_total_scenarios",
        "max_scenarios_per_risk_category",
    )
    @classmethod
    def _positive_bound(cls, v: int) -> int:
        if v < 1:
            raise ValueError("scenario generation bounds must be >= 1")
        return v

    @field_validator("max_total_scenarios")
    @classmethod
    def _sane_total(cls, v: int) -> int:
        if v > 200:
            raise ValueError(
                "max_total_scenarios must be <= 200; an unbounded generated suite is "
                "not a verification strategy"
            )
        return v

    @field_validator("max_parallel")
    @classmethod
    def _no_unproven_parallelism(cls, v: int) -> int:
        if v != 1:
            raise ValueError(
                "max_parallel must be 1: scenarios share services, ports, databases and a "
                "filesystem workspace, and nothing yet proves a given pair is isolated. "
                "Parallel execution stays unavailable until it can be proven safe rather "
                "than assumed."
            )
        return v

    @field_validator("promotion_requires_approval")
    @classmethod
    def _promotion_stays_manual(cls, v: bool) -> bool:
        if not v:
            raise ValueError(
                "promotion_requires_approval cannot be disabled: the permanent regression "
                "suite is human-reviewed by definition, and a run may never write into it "
                "on your behalf"
            )
        return v


class ReviewPolicyConfig(BaseModel):
    """When the driver spawns an independent reviewer on its own.

    Review is proportional to risk, and the driver spawns it rather than the
    founder. Ordinary work — an edit, a refactor, a test, a fix — gets none. A
    large change gets one when the run's own history suggests it would help. A
    change touching a high-consequence product surface always gets one. See
    :mod:`~neyma_product_driver.policy` for what counts as high consequence.

    A reviewer is read-only and bounded: ``max_automatic_reviews`` caps how many
    times a refusal may be recycled into a builder correction, so a reviewer that
    keeps refusing escalates to the founder instead of looping. A refusal that is
    corrected is re-reviewed — a fix nobody looked at is not a reviewed change.
    """

    model_config = ConfigDict(extra="forbid")

    #: Whether the driver may launch a reviewer without being asked. Off means
    #: the run reports that a review is warranted and stops, which is the old
    #: behaviour.
    automatic: bool = True
    #: How many refusing reviews may be turned into builder corrections before
    #: the refusal becomes a founder decision rather than another correction.
    #: At the default of 1 a run launches at most two reviews: one that refuses
    #: and is corrected, and one that judges the correction.
    max_automatic_reviews: int = 1
    #: Above either threshold, a change is MEANINGFUL rather than ordinary.
    meaningful_change_files: int = 10
    meaningful_change_lines: int = 400
    #: Model for the reviewer session. Empty means "the evaluator's model".
    model: str = ""

    #: Whether the reviewer may execute deterministic verification itself.
    #:
    #: On by default, and the difference is not cosmetic. A reviewer with no
    #: shell can only adjudicate the receipts this harness collected, so the
    #: harness's own honesty becomes an unexamined premise of the review that
    #: exists to examine it. Switching this off is a deliberate downgrade: the
    #: review still runs, and it reports ``evidence_reproduced: false`` so the
    #: founder summary says which kind of review it was.
    #:
    #: What it does NOT do is widen the reviewer. See
    #: :mod:`~neyma_product_driver.reviewer_boundary`: a command must be both a
    #: read-only verification action AND already allowed by Product Driver
    #: policy, and file writes, commits, pushes, deploys, network calls, secret
    #: reads and installs are refused whatever this is set to.
    reviewer_can_execute: bool = True
    #: How many commands one reviewer may run. A review that needs more than
    #: this is describing a verification gap, not performing a verification.
    reviewer_max_commands: int = 40
    #: Extra command *heads* this repository's reviewers may treat as read-only
    #: verification, beyond the built-in set. Written by a human, like the
    #: scenario approved-command list — never inferred, never generated.
    reviewer_extra_read_only: list[str] = Field(default_factory=list)

    @field_validator("reviewer_max_commands")
    @classmethod
    def _bounded_commands(cls, v: int) -> int:
        if v < 0:
            raise ValueError("reviewer_max_commands must be >= 0")
        return v

    @field_validator("max_automatic_reviews")
    @classmethod
    def _bounded_reviews(cls, v: int) -> int:
        if v < 0:
            raise ValueError("max_automatic_reviews must be >= 0")
        if v > 3:
            raise ValueError(
                "max_automatic_reviews must be <= 3: a reviewer that has refused three "
                "times is describing a decision, not a defect, and that decision is the "
                "founder's to make"
            )
        return v


class DriverConfig(BaseModel):
    """Top-level configuration for a driver run."""

    model_config = ConfigDict(extra="forbid")

    # No default: the target repository is always stated explicitly. There is no
    # fallback path, because a wrong-but-plausible repository is worse than a
    # clear failure to start.
    neyma_repo: Path
    # Derived from where this package actually lives, not from a remembered path.
    driver_root: Path = Field(default_factory=discover_driver_root)
    runs_dir: Path | None = None
    scenarios_dir: Path | None = None
    #: Local backup refs and git bundles created before any local-history change.
    preservation_dir: Path | None = None
    #: Scratch space for temporary probes and throwaway workspaces.
    temp_workspace_root: Path | None = None
    #: Additional roots the builder may write into, beyond the four derived ones.
    extra_writable_roots: list[Path] = Field(default_factory=list)

    # A Driver-maintenance run works on the Product Driver itself, so driver_root
    # joins the approved roots. Off for ordinary product runs, where the driver's
    # own source is not the builder's business.
    driver_maintenance: bool = False

    task: str = ""
    scenario: str = "backend_generic"

    max_iterations: int = 5
    builder: BuilderConfig = Field(default_factory=BuilderConfig)
    evaluator: EvaluatorConfig = Field(default_factory=EvaluatorConfig)
    run: ScenarioRunConfig = Field(default_factory=ScenarioRunConfig)
    scenario_generation: ScenarioGenerationConfig = Field(
        default_factory=ScenarioGenerationConfig
    )
    review: ReviewPolicyConfig = Field(default_factory=ReviewPolicyConfig)

    # Safety switches. All default to the conservative choice.
    allow_dirty_tree: bool = True  # Neyma is normally mid-phase and dirty.
    require_branch: str | None = None
    allow_auto_commit: bool = False  # Never flipped on by the driver itself.
    allow_auto_push: bool = False
    confirm_api_key_billing: bool = False

    # Local-history transformation (amend / soft-reset consolidation). Off by
    # default, and enabling it does not make the transformation automatic: each
    # one must still satisfy every mechanical precondition in
    # :mod:`~neyma_product_driver.preservation` — unpushed, protocol-required,
    # and preserved to a local ref and bundle before anything moves. Arbitrary
    # rebases stay blocked regardless, because nothing here can prove their
    # recoverability yet.
    allow_local_history_rewrite: bool = False

    @field_validator("neyma_repo", "driver_root", mode="before")
    @classmethod
    def _expand(cls, v: Any) -> Any:
        if v is None:
            return v
        if str(v).strip() == "":
            raise ValueError(
                "neyma_repo must name the target repository; it is never inferred"
            )
        return expand_resolved(v)

    @field_validator(
        "runs_dir", "scenarios_dir", "preservation_dir", "temp_workspace_root", mode="before"
    )
    @classmethod
    def _expand_opt(cls, v: Any) -> Any:
        if v in (None, ""):
            return None
        return expand_resolved(v)

    @field_validator("extra_writable_roots", mode="before")
    @classmethod
    def _expand_list(cls, v: Any) -> Any:
        if not v:
            return []
        return [expand_resolved(item) for item in v]

    @field_validator("max_iterations")
    @classmethod
    def _bounded_iterations(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_iterations must be >= 1")
        if v > 20:
            raise ValueError("max_iterations must be <= 20 (uncontrolled loops are not allowed)")
        return v

    @model_validator(mode="after")
    def _derive_and_check(self) -> "DriverConfig":
        if self.runs_dir is None:
            object.__setattr__(self, "runs_dir", self.driver_root / "runs")
        if self.scenarios_dir is None:
            object.__setattr__(self, "scenarios_dir", self.driver_root / "scenarios")
        if self.preservation_dir is None:
            object.__setattr__(self, "preservation_dir", self.driver_root / "preservation")
        if self.temp_workspace_root is None:
            object.__setattr__(self, "temp_workspace_root", self.driver_root / "tmp")
        if self.allow_auto_commit or self.allow_auto_push:
            # These govern the DRIVER control process, which never commits or
            # pushes. They say nothing about the builder session, which may
            # create local commits when the target repository's own authority
            # requires them. See README "Who may do what".
            raise ValueError(
                "allow_auto_commit / allow_auto_push cannot be enabled: the driver "
                "control process never commits or pushes on your behalf"
            )
        return self

    # -- approved local roots ---------------------------------------------

    def writable_roots(self) -> ApprovedRoots:
        """The local roots the builder may write into during this run.

        Derived, not hardcoded: the target repository, this run's artifact
        directory, the preservation directory and the temporary workspace —
        plus the Product Driver itself when this is a maintenance run.
        """
        assert self.runs_dir is not None
        assert self.preservation_dir is not None
        assert self.temp_workspace_root is not None
        return default_roots(
            target_repo=self.neyma_repo,
            runs_dir=self.runs_dir,
            preservation_dir=self.preservation_dir,
            temp_workspace_root=self.temp_workspace_root,
            driver_root=self.driver_root if self.driver_maintenance else None,
            extra=self.extra_writable_roots,
        )

    # -- repository checks (kept separate so unit tests can build a config
    #    pointing at a temp dir without a git repo present) -----------------

    def validate_repo(self) -> list[str]:
        """Return a list of problems with the configured Neyma repository.

        Every problem names the configured path, so a run pointed at a stale or
        half-deleted directory says which path it was given rather than failing
        somewhere deeper with a confusing symptom.
        """
        problems: list[str] = []
        if not self.neyma_repo.exists():
            problems.append(
                f"Neyma repository not found: {self.neyma_repo}. "
                "Set neyma_repo in driver.config.yaml or pass --repo; "
                "the driver never falls back to a previously-used path."
            )
            return problems
        if not self.neyma_repo.is_dir():
            problems.append(f"Neyma repository path is not a directory: {self.neyma_repo}")
            return problems
        if not (self.neyma_repo / ".git").exists():
            problems.append(
                f"Not a git repository (no .git): {self.neyma_repo}. "
                "This looks like a leftover directory rather than the product repository."
            )
        if not (self.neyma_repo / "CLAUDE.md").exists():
            problems.append(
                f"CLAUDE.md not found in {self.neyma_repo}; the builder would run "
                "without Neyma's project authority"
            )
        return problems

    def scenario_path(self, name: str | None = None) -> Path:
        """Resolve a scenario name or path to a YAML file."""
        raw = name or self.scenario
        candidate = Path(os.path.expanduser(raw))
        if candidate.suffix in {".yaml", ".yml"} and candidate.exists():
            return candidate.resolve()
        assert self.scenarios_dir is not None
        for suffix in (".yaml", ".yml"):
            p = self.scenarios_dir / f"{raw}{suffix}"
            if p.exists():
                return p
        p = self.scenarios_dir / raw
        if p.exists():
            return p
        raise FileNotFoundError(
            f"Scenario {raw!r} not found (looked in {self.scenarios_dir} and as a direct path)"
        )


def load_config(path: str | Path | None = None, **overrides: Any) -> DriverConfig:
    """Load configuration from YAML/JSON, applying CLI overrides on top."""
    data: dict[str, Any] = {}
    if path is not None:
        p = Path(os.path.expanduser(str(path)))
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p}")
        loaded = yaml.safe_load(p.read_text()) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Config file must contain a mapping: {p}")
        data = loaded
    else:
        # Search the derived driver root and the working directory — never a
        # remembered absolute path.
        roots: list[Path] = [discover_driver_root(), Path.cwd()]
        for root in dict.fromkeys(roots):
            for default_name in ("driver.config.yaml", "driver.config.yml", "driver.config.json"):
                p = root / default_name
                if p.exists():
                    loaded = yaml.safe_load(p.read_text()) or {}
                    if isinstance(loaded, dict):
                        data = loaded
                    break
            if data:
                break

    for key, value in overrides.items():
        if value is None:
            continue
        if "." in key:  # e.g. "builder.model"
            section, field = key.split(".", 1)
            data.setdefault(section, {})
            if isinstance(data[section], dict):
                data[section][field] = value
        else:
            data[key] = value

    return DriverConfig(**data)


def api_key_present() -> bool:
    """True when ANTHROPIC_API_KEY is set (which changes billing to API keys)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
