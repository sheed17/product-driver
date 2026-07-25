"""Regression + integration coverage for run 20260723-083017.

The P4 run failed three ways at once:

  1. the builder could not Write/Edit (every mutation wanted a human approval
     the noninteractive harness could not give);
  2. the resolver mislabeled the finalized P3 pair — the content commit as the
     BASELINE and the finalizer metadata commit as a hand edit;
  3. it called receipts bound to HEAD^ stale, and then recommended an invalid
     "APPROVE P4 PROTOCOL AMENDMENT".

Every test here builds a real synthetic git repository. None touches the actual
Neyma repository.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from neyma_product_driver.builder import (
    BuilderSession,
    classify_command,
    classify_tool_use,
)
from neyma_product_driver.config import BuilderConfig
from neyma_product_driver.context import RepositoryContextLoader
from neyma_product_driver.git_topology import CommitRole
from neyma_product_driver.protocol_resolver import ProtocolResolver, ProtocolStatus

from protocol_fixtures import finalized_pair_with_content_baseline_pointer


def resolve(repo):
    return ProtocolResolver(repo.root).resolve()


# --------------------------------------------------------------------------
# Defect 2 & 3 — the finalized pair is CONSISTENT
# --------------------------------------------------------------------------


def test_the_finalized_pair_is_consistent(tmp_path: Path) -> None:
    """previous metadata → content → finalizer metadata is a legal, finished state."""
    resolution = resolve(finalized_pair_with_content_baseline_pointer(tmp_path / "neyma"))

    assert resolution.status is ProtocolStatus.CONSISTENT
    assert resolution.violations == []
    assert resolution.deadlocks == []
    assert resolution.options == []
    assert not resolution.blocks_finalization
    assert not resolution.requires_human_approval
    assert resolution.next_safe_action.startswith("proceed")


def test_the_content_commit_is_not_mislabeled_baseline(tmp_path: Path) -> None:
    """The registry records the content commit as baseline_commit; the resolver
    must step back to the historical baseline instead of dropping the pair."""
    resolution = resolve(finalized_pair_with_content_baseline_pointer(tmp_path / "neyma"))
    topo = resolution.topology

    roles = [c.role for c in topo.commits]
    assert roles == [CommitRole.CONTENT, CommitRole.FINALIZER_GENERATED]
    # The content commit is inside the analyzed range, not sitting on the
    # baseline line — the baseline was demoted to the previous metadata commit.
    assert topo.baseline_commit not in {c.commit_sha for c in topo.commits}
    assert "current content commit" in topo.baseline_source


def test_the_finalizer_commit_is_not_called_a_hand_edit(tmp_path: Path) -> None:
    """The finalizer metadata commit changes only derived VALUES; its ownership
    marker is unchanged context, so it must be read from the resulting state."""
    resolution = resolve(finalized_pair_with_content_baseline_pointer(tmp_path / "neyma"))
    topo = resolution.topology

    assert topo.commits[-1].role is CommitRole.FINALIZER_GENERATED
    assert topo.hand_edited_status_commits == []
    assert not any("hand-edited" in v.detail for v in resolution.violations)


def test_receipts_bound_to_head_parent_are_fresh(tmp_path: Path) -> None:
    resolution = resolve(finalized_pair_with_content_baseline_pointer(tmp_path / "neyma"))

    receipts = [r for r in resolution.topology.receipts if r.exists]
    assert receipts
    for r in receipts:
        assert r.fresh, r.detail
        assert "HEAD^" in r.fresh_reason
    assert not any(v.detail == "stale receipt" for v in resolution.violations)


def test_no_protocol_amendment_is_proposed(tmp_path: Path) -> None:
    """The invalid 'APPROVE P4 PROTOCOL AMENDMENT' must not be offered."""
    resolution = resolve(finalized_pair_with_content_baseline_pointer(tmp_path / "neyma"))

    assert resolution.options == []
    assert resolution.recommended_option is None
    phrases = " ".join(o.approval_phrase for o in resolution.options).upper()
    assert "PROTOCOL AMENDMENT" not in phrases
    report = resolution.render_report().upper()
    assert "PROTOCOL AMENDMENT" not in report
    assert "PROGRESS-PROTOCOL.MD" not in report
    assert "TEST_STATUS_REALITY.PY" not in report


# --------------------------------------------------------------------------
# Defect 1 — the builder can write autonomously
# --------------------------------------------------------------------------


def test_builder_defaults_to_accept_edits(tmp_path: Path) -> None:
    assert BuilderConfig().permission_mode == "acceptEdits"
    assert BuilderConfig().setting_sources == ["user", "project", "local"]


def _hook_denies(session: BuilderSession, tool: str, tool_input: dict) -> bool:
    """True when the builder's PreToolUse enforcement hook denies the tool."""
    result = asyncio.run(
        session._pre_tool_use_hook({"tool_name": tool, "tool_input": tool_input}, None, None)
    )
    return bool(result) and (
        result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )


def test_builder_uses_explicit_allowed_tools_without_a_callback(tmp_path: Path) -> None:
    """The unattended driver expresses permissions as explicit allowed_tools plus
    the PreToolUse enforcement hook — never an interactive can_use_tool callback,
    which an allow-listed tool would shadow and which could only hang unattended."""
    import warnings

    from claude_agent_sdk.types import (
        CanUseToolShadowedWarning,
        _warn_if_can_use_tool_shadowed,
    )

    session = BuilderSession(tmp_path, BuilderConfig())
    opts = session._options()
    assert opts.permission_mode == "acceptEdits"
    assert opts.can_use_tool is None
    assert {"Read", "Write", "Edit", "Bash"} <= set(opts.allowed_tools)

    # No shadow warning fires precisely because there is no callback to shadow:
    # the SDK's own emitter stays silent for these options.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _warn_if_can_use_tool_shadowed(opts)
    assert not [w for w in caught if issubclass(w.category, CanUseToolShadowedWarning)]


def test_builder_services_ordinary_edits_without_a_human(tmp_path: Path) -> None:
    session = BuilderSession(tmp_path, BuilderConfig())
    allowed = set(BuilderConfig().allowed_tools)

    for path in (
        "src/freight_recon/pipeline/__init__.py",
        "eval/tests/test_operate_request_cli.py",
        "docs/design/note.md",
        "CLAUDE.md",  # ordinary project doc — editable now
    ):
        for tool in ("Write", "Edit"):
            # Auto-approved by allowed_tools, and the enforcement hook lets it by.
            assert tool in allowed, tool
            assert classify_tool_use(tool, {"file_path": path}) is None, (tool, path)
            assert not _hook_denies(session, tool, {"file_path": path}), (tool, path)
    # Nothing was recorded as blocked.
    assert session.denied_requests == []


def test_builder_still_refuses_secrets_protected_config_and_effects(tmp_path: Path) -> None:
    session = BuilderSession(tmp_path, BuilderConfig())

    denied_writes = [
        ".claude/settings.local.json",
        ".claude/hooks/guard.py",
        ".env",
        ".env.production",
        ".mcp.json",
        "config/id_rsa",
    ]
    for path in denied_writes:
        assert classify_tool_use("Write", {"file_path": path}) is not None, path
        assert _hook_denies(session, "Write", {"file_path": path}), path

    # Reading a secret is blocked too, even though Read is allow-listed.
    assert _hook_denies(session, "Read", {"file_path": ".env"})
    assert _hook_denies(session, "Read", {"file_path": "deploy/.ssh/id_ed25519"})

    for command in (
        "git push origin main",
        "git reset --hard HEAD~1",
        "git rebase -i HEAD~3",
        "sudo rm -rf /var",
        "curl -X POST https://api.example.com/pay -d amount=100",
    ):
        assert classify_command(command) is not None, command
        assert _hook_denies(session, "Bash", {"command": command}), command

    # Local commit / restore are NOT blocked — the owner authorized them.
    for command in ("git commit -m 'wip'", "git restore src/x.py", "git add -A"):
        assert classify_command(command) is None, command
        assert not _hook_denies(session, "Bash", {"command": command}), command


# --------------------------------------------------------------------------
# End-to-end: the run the driver would now allow
# --------------------------------------------------------------------------


def test_end_to_end_p4_is_unblocked(tmp_path: Path) -> None:
    """The full path the failed run should have taken:

      1. P4 is the sole READY unit
      2. the resolver accepts the finalized pair (no stop, no amendment)
      3. a builder-style session can create AND edit a disposable work file
      4. it can begin a bounded source implementation
      5. the driver does not stop on a false protocol amendment
      6. no push or destructive git action is possible
    """
    repo = finalized_pair_with_content_baseline_pointer(tmp_path / "neyma", unit_id="P4")

    head_before = repo.head()
    tracked_before = repo._git("status", "--porcelain", "--untracked-files=no")

    # 1. P4 is discovered as the sole READY unit.
    unit = RepositoryContextLoader(repo.root).resolve_active_unit()
    assert unit.unit_id == "P4"
    assert unit.status == "READY"

    # 2 & 5. The resolver accepts the pair and does not stop on an amendment.
    resolution = resolve(repo)
    assert resolution.status is ProtocolStatus.CONSISTENT
    assert not resolution.blocks_finalization
    assert resolution.options == []

    # 3 & 4. A builder-style session creates and edits a bounded source file —
    #        auto-approved via allowed_tools, and the enforcement hook lets it by.
    session = BuilderSession(repo.root, BuilderConfig())
    assert {"Write", "Edit"} <= set(BuilderConfig().allowed_tools)

    work_rel = "src/freight_recon/pipeline/effect_boundary.py"
    assert classify_tool_use("Write", {"file_path": work_rel}) is None
    assert not _hook_denies(session, "Write", {"file_path": work_rel})

    work = repo.root / work_rel
    work.parent.mkdir(parents=True, exist_ok=True)
    work.write_text("def execute_effect():\n    return None\n", encoding="utf-8")
    assert work.exists()

    assert not _hook_denies(session, "Edit", {"file_path": work_rel})
    work.write_text(
        "def execute_effect(kernel, handle, operation, params):\n"
        "    # bounded first slice of the P4 boundary\n"
        "    return kernel.claim_grant_cas(handle)\n",
        encoding="utf-8",
    )
    assert "claim_grant_cas" in work.read_text(encoding="utf-8")

    # 6. No push or history-rewriting git action is possible, and nothing the
    #    driver did moved the repository's committed state or tracked tree.
    for command in ("git push", "git reset --hard", "git rebase main", "git commit --amend"):
        assert classify_command(command) is not None
    # A local commit is permitted (the owner authorized it under repo authority).
    assert classify_command("git commit -am 'work'") is None
    assert repo.head() == head_before
    assert repo._git("status", "--porcelain", "--untracked-files=no") == tracked_before

    # Tidy the disposable work file (a real run would commit it under authority).
    work.unlink()
