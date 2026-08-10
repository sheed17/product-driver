"""The builder session — a persistent Claude Code session inside the Neyma repo.

The driver owns this session programmatically (it never types into an open
Terminal). The session runs with ``cwd`` set to the Neyma repository and with
``setting_sources=["user", "project", "local"]`` so that Neyma's own CLAUDE.md,
settings, hooks, skills and subagents are loaded and authoritative.

This driver runs UNATTENDED on the owner's personal machine, so ordinary
source-code work must never pause for an interactive approval nobody is there
to give. Permission handling is therefore expressed as explicit Agent SDK tool
permissions, not as an interactive ``can_use_tool`` callback:

  * ``allowed_tools`` names the working set (Read/Grep/Glob, Write/Edit/…,
    Bash and friends). Whole-tool allow entries auto-approve those tools with
    no human in the loop — which is exactly why there is no ``can_use_tool``
    callback: an allowed-tool entry would shadow it (the
    CanUseToolShadowedWarning), and an unattended callback would only ever be a
    place for the run to hang.
  * a ``PreToolUse`` hook is the deterministic enforcement layer. It fires even
    for tools ``allowed_tools`` (or the repo's own settings) pre-approve, and it
    ``deny``s the narrow set of hard-blocked actions — remote publishing, force
    push, history rewrites, secret/credential access, external/production
    effects, system-wide installs and machine-security changes — so a
    pre-approved ``Bash`` cannot smuggle a ``git push`` past it.
  * Neyma's own ``setting_sources`` deny rules load underneath as
    defense-in-depth.

Everything else — editing/creating/renaming/deleting ordinary repository files,
running Python/pytest/linters/scripts, and local git including a local commit
when repository authority requires it — runs autonomously with no approval pause.

Classification itself lives in :mod:`~neyma_product_driver.command_guard`, which
also documents honestly what string-level classification can and cannot enforce.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

from .command_guard import (
    CommandGuard,
    GuardDecision,
    classify_command,
    classify_tool_use,
    enforcement_layers,
)
from .config import BuilderConfig
from .models import redact
from .ownership import BUILDER_LOCK_NAME, RepoLock
from .paths import ApprovedRoots
from .prompts import BUILDER_SYSTEM_APPEND


def _head_of(repo: Path) -> str:
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo), capture_output=True, text=True, timeout=30, check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""

__all__ = [
    "BuilderSession",
    "BuilderTurn",
    "classify_command",
    "classify_tool_use",
    "enforcement_layers",
]


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------


@dataclass
class BuilderTurn:
    """What one builder turn produced."""

    text: str = ""
    session_id: str | None = None
    tool_uses: list[str] = field(default_factory=list)
    denied_requests: list[str] = field(default_factory=list)
    is_error: bool = False
    error_detail: str = ""
    num_turns: int = 0
    timed_out: bool = False

    @property
    def runnable_checkpoint(self) -> str:
        """Extract the RUNNABLE CHECKPOINT section the builder was asked for."""
        match = re.search(
            r"RUNNABLE\s+CHECKPOINT\b(.*)$", self.text, re.S | re.I
        )
        return match.group(1).strip() if match else ""

    @property
    def claims_runnable(self) -> bool:
        cp = self.runnable_checkpoint
        return bool(cp) and not re.match(r"^[:\s]*none\b", cp, re.I)


class BuilderSession:
    """Persistent Claude Code session driving work inside the Neyma repo."""

    def __init__(
        self,
        repo: Path,
        config: BuilderConfig,
        resume_session_id: str | None = None,
        on_progress: Callable[[str], None] | None = None,
        roots: ApprovedRoots | None = None,
        journal: Any | None = None,
        own_worktree: bool = True,
    ) -> None:
        self.repo = Path(repo)
        self.config = config
        self.resume_session_id = resume_session_id
        self.session_id: str | None = resume_session_id
        self._client: ClaudeSDKClient | None = None
        self._on_progress = on_progress or (lambda _msg: None)
        self.denied_requests: list[str] = []
        #: Approved write roots. ``None`` means confinement is not configured —
        #: the string-level rules still apply, but no root boundary is claimed.
        self.roots = roots
        #: Optional RunJournal. Every tool use, denial and command flows into it.
        self.journal = journal
        #: A builder OWNS the worktree it works in. While it does, ref movement
        #: and worktree replacement are denied: the builder is holding the only
        #: materialized copy of the in-progress episode, untracked files
        #: included, and a `update-ref` or `reset` underneath it destroys work
        #: that exists nowhere else.
        self.own_worktree = bool(own_worktree)
        self._worktree_lock: RepoLock | None = None
        self.guard = CommandGuard(
            roots=roots, cwd=self.repo, builder_owns_worktree=self.own_worktree
        )

    # -- permission handling ---------------------------------------------

    def authorize_amendment(self, authorized: bool) -> None:
        """Permit (or revoke) exactly one authorized local-history amendment.

        Only :mod:`~neyma_product_driver.preservation` should call this, and
        only after every mechanical precondition has passed. It is deliberately
        a session-level switch rather than a config flag, so an authorization
        cannot outlive the run that proved it.
        """
        self.guard.amendment_authorized = bool(authorized)

    async def _pre_tool_use_hook(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        """The enforcement layer: blocks hard-blocked actions even when an
        ``allowed_tools`` entry, the repo's own settings, or the permission mode
        pre-approved the tool. This is what makes explicit tool permissions safe
        to grant unattended — the deny here is deterministic and always fires."""
        tool_name = str(input_data.get("tool_name", ""))
        tool_input = input_data.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            return {}

        decision: GuardDecision = self.guard.classify(tool_name, tool_input)
        self._journal_tool_use(tool_name, tool_input, decision)

        if not decision.denied:
            return {}

        reason = decision.reason or "blocked"
        detail = redact(f"{tool_name}: {reason}")
        if detail not in self.denied_requests:
            self.denied_requests.append(detail)
        self._on_progress(f"  [HOOK BLOCKED] {detail}")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Blocked by the product-driver harness: {reason}. "
                    "The harness never performs this on the owner's behalf."
                ),
            }
        }

    def _journal_tool_use(
        self, tool_name: str, tool_input: dict[str, Any], decision: GuardDecision
    ) -> None:
        """Record the tool use, and any denial, for the run journal."""
        if self.journal is None:
            return
        try:
            self.journal.record_tool_use(
                tool=tool_name,
                detail=redact(
                    str(
                        tool_input.get("command")
                        or tool_input.get("file_path")
                        or tool_input.get("path")
                        or tool_input.get("url")
                        or tool_input.get("query")
                        or ""
                    )
                )[:400],
                denied_reason=redact(decision.reason) if decision.reason else None,
                layer=decision.layer,
            )
            for verdict in decision.verdicts:
                if not verdict.allowed:
                    self.journal.record_denied_path(str(verdict.resolved), verdict.reason or "")
        except Exception:  # pragma: no cover - journalling must never break a run
            pass

    # -- lifecycle --------------------------------------------------------

    def _options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            cwd=str(self.repo),
            model=self.config.model,
            permission_mode=self.config.permission_mode,
            # Load Neyma's CLAUDE.md, settings, hooks, skills and subagents.
            setting_sources=list(self.config.setting_sources),
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": BUILDER_SYSTEM_APPEND,
            },
            max_turns=self.config.max_turns,
            # Explicit tool permissions: the working set is auto-approved with no
            # human in the loop, so normal implementation work never pauses. The
            # PreToolUse hook below is the deterministic net over the hard blocks.
            # There is intentionally NO can_use_tool callback — a whole-tool
            # allowed_tools entry would shadow it, and unattended it could only
            # hang. Anything NOT in allowed_tools and not covered by the mode is
            # left to the CLI's non-interactive default (deny), never a prompt.
            allowed_tools=list(self.config.allowed_tools),
            disallowed_tools=list(self.config.disallowed_tools),
            hooks={"PreToolUse": [HookMatcher(hooks=[self._pre_tool_use_hook])]},
            resume=self.resume_session_id,
            # Never fork: corrections must land in the same conversation.
            fork_session=False,
        )

    async def __aenter__(self) -> "BuilderSession":
        if self.own_worktree:
            # Taken BEFORE the session connects, so nothing can be issued against
            # an unowned worktree. Non-blocking: if another builder already owns
            # this tree, two builders must not share it.
            lock = RepoLock(
                self.repo,
                BUILDER_LOCK_NAME,
                kind="builder-worktree",
                target_commit=_head_of(self.repo),
                session_id=self.resume_session_id or "",
            )
            lock.acquire()
            self._worktree_lock = lock

        try:
            self._client = ClaudeSDKClient(options=self._options())
            await self._client.connect()
        except BaseException:
            self._release_worktree()
            raise
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    def _release_worktree(self) -> None:
        lock, self._worktree_lock = self._worktree_lock, None
        if lock is not None:
            lock.release()

    async def close(self) -> None:
        try:
            if self._client is not None:
                try:
                    await self._client.disconnect()
                finally:
                    self._client = None
        finally:
            # Released on every exit path, including failure: a crashed builder
            # must not leave a worktree permanently owned.
            self._release_worktree()

    # -- interaction ------------------------------------------------------

    async def send(self, prompt: str, timeout_s: int | None = None) -> BuilderTurn:
        """Send a prompt to the persistent session and stream the reply."""
        if self._client is None:
            raise RuntimeError("BuilderSession is not connected; use 'async with'")

        timeout = timeout_s or self.config.turn_timeout_s
        before = len(self.denied_requests)
        try:
            return await asyncio.wait_for(self._collect(prompt, before), timeout=timeout)
        except asyncio.TimeoutError:
            self._on_progress(f"  [TIMEOUT] builder turn exceeded {timeout}s")
            return BuilderTurn(
                session_id=self.session_id,
                is_error=True,
                timed_out=True,
                error_detail=f"builder turn exceeded {timeout}s",
                denied_requests=self.denied_requests[before:],
            )

    async def _collect(self, prompt: str, denied_before: int) -> BuilderTurn:
        assert self._client is not None
        turn = BuilderTurn()
        chunks: list[str] = []

        await self._client.query(prompt)
        async for message in self._client.receive_response():
            if isinstance(message, SystemMessage):
                sid = (message.data or {}).get("session_id")
                if sid:
                    self.session_id = sid
                continue

            if isinstance(message, AssistantMessage):
                if message.session_id:
                    self.session_id = message.session_id
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
                        if self.config.stream_progress:
                            self._on_progress(block.text)
                    elif isinstance(block, ToolUseBlock):
                        label = _describe_tool_use(block)
                        turn.tool_uses.append(label)
                        if self.config.stream_progress:
                            self._on_progress(f"  · {label}")
                    elif isinstance(block, ThinkingBlock):
                        continue
                if message.error:
                    turn.is_error = True
                    turn.error_detail = str(message.error)

            elif isinstance(message, ResultMessage):
                if message.session_id:
                    self.session_id = message.session_id
                turn.num_turns = message.num_turns
                if message.is_error:
                    turn.is_error = True
                    turn.error_detail = redact(
                        message.result or (", ".join(message.errors or []) or message.subtype)
                    )
                if not chunks and message.result:
                    chunks.append(message.result)

        turn.text = redact("".join(chunks).strip())
        turn.session_id = self.session_id
        turn.denied_requests = self.denied_requests[denied_before:]
        return turn


def _describe_tool_use(block: ToolUseBlock) -> str:
    """Short, safe, human-readable line for terminal progress."""
    data = block.input or {}
    if block.name == "Bash":
        return f"Bash: {redact(str(data.get('command', '')))[:160]}"
    for key in ("file_path", "path", "pattern", "url", "query"):
        if key in data:
            return f"{block.name}: {redact(str(data[key]))[:160]}"
    return block.name
