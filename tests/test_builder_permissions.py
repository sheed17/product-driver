"""Unattended-driver permission proofs for the builder session.

The driver runs on the owner's personal machine with no human watching, so
ordinary source-code work must run autonomously (no approval pause) while a
narrow set of hard-blocked actions stays denied deterministically.

These eight tests are the acceptance proofs the owner asked for. Each builds a
real, throwaway git repository in a temp directory; none touches the actual
Neyma repository. "Without human interaction" is proven two ways at once:

  * the tool is in the builder's ``allowed_tools`` (the SDK auto-approves it with
    no ``can_use_tool`` callback in the loop), AND the ``PreToolUse`` enforcement
    hook does not deny it — so nothing pauses; and
  * the operation actually runs against the real repo and has its intended
    effect.

A hard block is proven by the enforcement hook returning a ``deny`` decision.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from neyma_product_driver.builder import BuilderSession, classify_command, classify_tool_use
from neyma_product_driver.config import BuilderConfig


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


def _init_repo(root: Path) -> Path:
    """A minimal real git repository with one committed file."""
    root.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(
        ["git", *a], cwd=str(root), capture_output=True, text=True, check=True
    )
    run("init", "-q")
    run("config", "user.email", "t@e.com")
    run("config", "user.name", "t")
    run("config", "commit.gpgsign", "false")
    (root / "module.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "test_module.py").write_text(
        "from module import f\n\n\ndef test_f():\n    assert f() == 1\n", encoding="utf-8"
    )
    run("add", "-A")
    run("commit", "-qm", "baseline")
    return root


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
    )


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _session(root: Path) -> BuilderSession:
    return BuilderSession(root, BuilderConfig())


def _hook_denies(session: BuilderSession, tool: str, tool_input: dict) -> bool:
    """True when the builder's PreToolUse enforcement hook denies the tool."""
    result = asyncio.run(
        session._pre_tool_use_hook({"tool_name": tool, "tool_input": tool_input}, None, None)
    )
    return bool(result) and (
        result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


def _autonomous(session: BuilderSession, tool: str, tool_input: dict) -> bool:
    """True when this tool use runs with no human in the loop: the tool is
    allow-listed (auto-approved, no callback) and the enforcement hook lets it by."""
    allowed = set(BuilderConfig().allowed_tools)
    if tool not in allowed:
        return False
    if classify_tool_use(tool, tool_input) is not None:
        return False
    return not _hook_denies(session, tool, tool_input)


# --------------------------------------------------------------------------
# 1. create, edit, rename, delete a disposable file — no human interaction
# --------------------------------------------------------------------------


def test_builder_can_create_edit_rename_delete_without_human(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    session = _session(root)
    rel = "scratch/notes.py"

    # Create — Write is auto-approved and the hook does not deny it.
    assert _autonomous(session, "Write", {"file_path": rel})
    created = root / rel
    created.parent.mkdir(parents=True, exist_ok=True)
    created.write_text("x = 1\n", encoding="utf-8")
    assert created.exists()

    # Edit.
    assert _autonomous(session, "Edit", {"file_path": rel})
    created.write_text("x = 2\n", encoding="utf-8")
    assert created.read_text(encoding="utf-8") == "x = 2\n"

    # Rename — done via a non-destructive shell move, which is not hard-blocked.
    assert classify_command(f"git mv {rel} scratch/renamed.py") is None
    assert not _hook_denies(session, "Bash", {"command": f"mv {rel} scratch/renamed.py"})
    created.rename(root / "scratch/renamed.py")
    assert (root / "scratch/renamed.py").exists()

    # Delete.
    assert classify_command("rm scratch/renamed.py") is None
    assert not _hook_denies(session, "Bash", {"command": "rm scratch/renamed.py"})
    (root / "scratch/renamed.py").unlink()
    assert not (root / "scratch/renamed.py").exists()

    # Nothing was ever recorded as blocked.
    assert session.denied_requests == []


# --------------------------------------------------------------------------
# 2. modify an existing file
# --------------------------------------------------------------------------


def test_builder_can_modify_an_existing_file(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    session = _session(root)
    rel = "module.py"

    assert (root / rel).exists()
    assert _autonomous(session, "Edit", {"file_path": rel})
    (root / rel).write_text("def f():\n    return 42\n", encoding="utf-8")
    assert "42" in (root / rel).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 3. run pytest
# --------------------------------------------------------------------------


def test_builder_can_run_pytest(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    session = _session(root)
    command = "python -m pytest -q"

    # Running the test suite is ordinary work — never hard-blocked.
    assert classify_command(command) is None
    assert _autonomous(session, "Bash", {"command": command})

    # And it genuinely runs against the repo (use this interpreter, which has
    # pytest, and an isolated rootdir so the driver's own config isn't inherited).
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts="],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------
# 4. create a local commit
# --------------------------------------------------------------------------


def test_builder_can_create_a_local_commit(tmp_path: Path) -> None:
    root = _init_repo(tmp_path / "repo")
    session = _session(root)

    # A local commit is explicitly permitted when repository authority requires it.
    for command in ("git add -A", "git commit -m 'P4: content'"):
        assert classify_command(command) is None, command
        assert _autonomous(session, "Bash", {"command": command})

    before = _head(root)
    (root / "module.py").write_text("def f():\n    return 3\n", encoding="utf-8")
    _git(root, "add", "-A")
    proc = _git(root, "commit", "-qm", "P4: content")
    assert proc.returncode == 0, proc.stderr
    assert _head(root) != before  # a new local commit landed


# --------------------------------------------------------------------------
# 5. git push remains denied
# --------------------------------------------------------------------------


def test_git_push_remains_denied(tmp_path: Path) -> None:
    session = _session(_init_repo(tmp_path / "repo"))
    for command in ("git push", "git push origin main", "git push --force origin main"):
        assert classify_command(command) is not None, command
        assert _hook_denies(session, "Bash", {"command": command}), command


# --------------------------------------------------------------------------
# 6. destructive history rewriting remains denied
# --------------------------------------------------------------------------


def test_history_rewriting_remains_denied(tmp_path: Path) -> None:
    session = _session(_init_repo(tmp_path / "repo"))
    for command in (
        "git reset --hard HEAD~1",
        "git rebase -i HEAD~3",
        "git filter-branch --force --index-filter x",
        "git filter-repo --path secret --invert-paths",
        "git commit --amend -m rewrite",
    ):
        assert classify_command(command) is not None, command
        assert _hook_denies(session, "Bash", {"command": command}), command


# --------------------------------------------------------------------------
# 7. access to protected secret paths remains denied
# --------------------------------------------------------------------------


def test_secret_paths_remain_denied(tmp_path: Path) -> None:
    session = _session(_init_repo(tmp_path / "repo"))

    # Writing or reading a secret is denied on the file tools...
    for path in (".env", ".env.production", "deploy/.ssh/id_rsa", "app/service.pem"):
        assert classify_tool_use("Write", {"file_path": path}) is not None, path
        assert _hook_denies(session, "Write", {"file_path": path}), path
        assert _hook_denies(session, "Read", {"file_path": path}), path

    # ...and reading one through the shell is denied too.
    for command in ("cat .env", "base64 config/.aws/credentials", "cp ~/.ssh/id_rsa /tmp/x"):
        assert classify_command(command) is not None, command
        assert _hook_denies(session, "Bash", {"command": command}), command


# --------------------------------------------------------------------------
# 8. an external-effect command remains denied
# --------------------------------------------------------------------------


def test_external_effect_command_remains_denied(tmp_path: Path) -> None:
    session = _session(_init_repo(tmp_path / "repo"))
    for command in (
        "curl -X POST https://api.example.com/orders -d ref=1",
        "gh pr create --fill",
        "aws s3 cp x s3://bucket/x",
        "slack chat send '#ops' done",
        "stripe charges create --amount 500",
        "sudo apt-get install nginx",
    ):
        assert classify_command(command) is not None, command
        assert _hook_denies(session, "Bash", {"command": command}), command
