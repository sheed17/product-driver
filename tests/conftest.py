"""Shared fixtures. No test in this suite consumes real Claude usage."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    """Guarantee no test can accidentally bill or authenticate."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A minimal directory that passes DriverConfig.validate_repo()."""
    repo = tmp_path / "neyma"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# fake authority\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def driver_config(fake_repo: Path, tmp_path: Path):
    from neyma_product_driver.config import DriverConfig

    return DriverConfig(
        neyma_repo=fake_repo,
        driver_root=tmp_path / "driver",
        runs_dir=tmp_path / "driver" / "runs",
        scenarios_dir=tmp_path / "driver" / "scenarios",
        task="do the thing",
        max_iterations=3,
    )
