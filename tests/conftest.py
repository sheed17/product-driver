"""Shared fixtures. No test in this suite consumes real Claude usage."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--local-artifacts",
        action="store_true",
        default=False,
        help=(
            "also run tests marked local_artifacts, which replay git-ignored run "
            "directories or a product checkout on this machine"
        ),
    )


def pytest_collection_modifyitems(config, items):
    """The default suite is self-contained.

    A test that replays a local run directory or a product checkout answers a
    question about THIS machine, and its answer changes when that run is later
    resumed. It runs when asked for — ``--local-artifacts`` or
    ``NPD_LOCAL_ARTIFACTS=1`` — and is a documented skip otherwise.
    """
    if config.getoption("--local-artifacts") or os.environ.get("NPD_LOCAL_ARTIFACTS") == "1":
        return
    skip = pytest.mark.skip(
        reason="replays local run artifacts or a product checkout; opt in with --local-artifacts"
    )
    for item in items:
        if "local_artifacts" in item.keywords:
            item.add_marker(skip)


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
