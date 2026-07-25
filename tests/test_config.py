"""Configuration validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from neyma_product_driver.config import (
    BuilderConfig,
    DriverConfig,
    load_config,
)


def test_defaults_point_at_the_expected_locations(driver_config: DriverConfig) -> None:
    assert driver_config.max_iterations == 3
    assert driver_config.builder.model == "opus"
    assert driver_config.evaluator.model == "opus"
    assert driver_config.run.browser_enabled is False
    assert driver_config.allow_auto_commit is False
    assert driver_config.allow_auto_push is False


def test_runs_and_scenarios_dirs_derive_from_driver_root(tmp_path: Path, fake_repo: Path) -> None:
    cfg = DriverConfig(neyma_repo=fake_repo, driver_root=tmp_path / "d")
    assert cfg.runs_dir == (tmp_path / "d" / "runs").resolve()
    assert cfg.scenarios_dir == (tmp_path / "d" / "scenarios").resolve()


@pytest.mark.parametrize("mode", ["bypassPermissions", "dontAsk", "auto"])
def test_bypassing_permission_modes_are_rejected(mode: str) -> None:
    with pytest.raises(ValidationError):
        BuilderConfig(permission_mode=mode)


def test_permitted_permission_modes(qty: None = None) -> None:
    for mode in ("default", "acceptEdits", "plan"):
        assert BuilderConfig(permission_mode=mode).permission_mode == mode


def test_builder_loads_neyma_settings_by_default() -> None:
    # Without these the SDK would not load CLAUDE.md, hooks, skills or subagents.
    assert BuilderConfig().setting_sources == ["user", "project", "local"]


@pytest.mark.parametrize("value", [0, -1, 21, 100])
def test_max_iterations_is_bounded(value: int, fake_repo: Path) -> None:
    with pytest.raises(ValidationError):
        DriverConfig(neyma_repo=fake_repo, max_iterations=value)


@pytest.mark.parametrize("value", [1, 5, 20])
def test_max_iterations_accepts_sane_values(value: int, fake_repo: Path) -> None:
    assert DriverConfig(neyma_repo=fake_repo, max_iterations=value).max_iterations == value


@pytest.mark.parametrize("field", ["allow_auto_commit", "allow_auto_push"])
def test_auto_commit_and_push_cannot_be_enabled(field: str, fake_repo: Path) -> None:
    with pytest.raises(ValidationError):
        DriverConfig(neyma_repo=fake_repo, **{field: True})


def test_validate_repo_flags_a_missing_repository(tmp_path: Path) -> None:
    cfg = DriverConfig(neyma_repo=tmp_path / "nope")
    problems = cfg.validate_repo()
    assert problems and "not found" in problems[0]


def test_validate_repo_flags_a_non_git_directory(tmp_path: Path) -> None:
    d = tmp_path / "plain"
    d.mkdir()
    (d / "CLAUDE.md").write_text("x")
    problems = DriverConfig(neyma_repo=d).validate_repo()
    assert any("Not a git repository" in p for p in problems)


def test_validate_repo_flags_missing_claude_md(tmp_path: Path) -> None:
    import subprocess

    d = tmp_path / "repo"
    d.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    problems = DriverConfig(neyma_repo=d).validate_repo()
    assert any("CLAUDE.md" in p for p in problems)


def test_validate_repo_passes_for_a_good_repo(driver_config: DriverConfig) -> None:
    assert driver_config.validate_repo() == []


def test_unknown_config_keys_are_rejected(fake_repo: Path) -> None:
    with pytest.raises(ValidationError):
        DriverConfig(neyma_repo=fake_repo, definitely_not_a_field=1)


def test_load_config_from_file_with_overrides(tmp_path: Path, fake_repo: Path) -> None:
    path = tmp_path / "driver.config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "neyma_repo": str(fake_repo),
                "driver_root": str(tmp_path),
                "max_iterations": 4,
                "builder": {"model": "sonnet"},
            }
        )
    )
    cfg = load_config(path, max_iterations=2, **{"evaluator.model": "haiku"})
    assert cfg.max_iterations == 2  # override wins
    assert cfg.builder.model == "sonnet"  # file value preserved
    assert cfg.evaluator.model == "haiku"  # dotted override applied


def test_load_config_ignores_none_overrides(tmp_path: Path, fake_repo: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump({"neyma_repo": str(fake_repo), "max_iterations": 7}))
    assert load_config(path, max_iterations=None).max_iterations == 7


def test_load_config_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "absent.yaml")


def test_scenario_path_resolution(driver_config: DriverConfig, tmp_path: Path) -> None:
    scen_dir = driver_config.scenarios_dir
    assert scen_dir is not None
    scen_dir.mkdir(parents=True)
    (scen_dir / "mine.yaml").write_text("name: mine\n")

    assert driver_config.scenario_path("mine").name == "mine.yaml"
    assert driver_config.scenario_path(str(scen_dir / "mine.yaml")).name == "mine.yaml"
    with pytest.raises(FileNotFoundError):
        driver_config.scenario_path("does-not-exist")
