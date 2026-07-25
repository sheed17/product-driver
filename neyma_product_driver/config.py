"""Driver configuration.

Loaded from a JSON/YAML file (``driver.config.yaml`` by default), overridable by
CLI flags. Validation is strict about the things that matter for safety: the
Neyma repo must be a real git repository, permission mode may never be a
bypassing mode, and iteration count is bounded.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_DRIVER_ROOT = Path("/Users/sammyfammy/neyma-product-driver")
DEFAULT_NEYMA_REPO = Path("/Users/sammyfammy/freight-logistics-operational-teammate")

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


class DriverConfig(BaseModel):
    """Top-level configuration for a driver run."""

    model_config = ConfigDict(extra="forbid")

    neyma_repo: Path = DEFAULT_NEYMA_REPO
    driver_root: Path = DEFAULT_DRIVER_ROOT
    runs_dir: Path | None = None
    scenarios_dir: Path | None = None

    task: str = ""
    scenario: str = "backend_generic"

    max_iterations: int = 5
    builder: BuilderConfig = Field(default_factory=BuilderConfig)
    evaluator: EvaluatorConfig = Field(default_factory=EvaluatorConfig)
    run: ScenarioRunConfig = Field(default_factory=ScenarioRunConfig)

    # Safety switches. All default to the conservative choice.
    allow_dirty_tree: bool = True  # Neyma is normally mid-phase and dirty.
    require_branch: str | None = None
    allow_auto_commit: bool = False  # Never flipped on by the driver itself.
    allow_auto_push: bool = False
    confirm_api_key_billing: bool = False

    @field_validator("neyma_repo", "driver_root", mode="before")
    @classmethod
    def _expand(cls, v: Any) -> Any:
        if v is None:
            return v
        return Path(os.path.expanduser(str(v))).resolve()

    @field_validator("runs_dir", "scenarios_dir", mode="before")
    @classmethod
    def _expand_opt(cls, v: Any) -> Any:
        if v in (None, ""):
            return None
        return Path(os.path.expanduser(str(v))).resolve()

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
        if self.allow_auto_commit or self.allow_auto_push:
            raise ValueError(
                "allow_auto_commit / allow_auto_push cannot be enabled: the driver "
                "never commits or pushes on your behalf"
            )
        return self

    # -- repository checks (kept separate so unit tests can build a config
    #    pointing at a temp dir without a git repo present) -----------------

    def validate_repo(self) -> list[str]:
        """Return a list of problems with the configured Neyma repository."""
        problems: list[str] = []
        if not self.neyma_repo.exists():
            problems.append(f"Neyma repository not found: {self.neyma_repo}")
            return problems
        if not self.neyma_repo.is_dir():
            problems.append(f"Neyma repository path is not a directory: {self.neyma_repo}")
            return problems
        if not (self.neyma_repo / ".git").exists():
            problems.append(f"Not a git repository (no .git): {self.neyma_repo}")
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
        for default_name in ("driver.config.yaml", "driver.config.yml", "driver.config.json"):
            p = DEFAULT_DRIVER_ROOT / default_name
            if p.exists():
                loaded = yaml.safe_load(p.read_text()) or {}
                if isinstance(loaded, dict):
                    data = loaded
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
