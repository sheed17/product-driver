"""Fixtures for the reviewer experiments — copies of tests/conftest.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO))


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
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
