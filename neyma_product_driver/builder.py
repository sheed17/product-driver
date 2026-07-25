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

from .config import BuilderConfig
from .models import redact
from .prompts import BUILDER_SYSTEM_APPEND

# --------------------------------------------------------------------------
# Hard-blocked-action classification
# --------------------------------------------------------------------------
#
# The driver runs unattended, so ordinary work is NOT listed here — it runs
# autonomously. This is the narrow set of actions the driver must never perform
# on the owner's behalf: remote publishing, force push, history rewrites, secret
# access, external/production effects, system-wide installs and machine-security
# changes. Everything else (local commits, restore, add, file create/edit/rename/
# delete, pytest/linters/scripts) is deliberately absent so it is never blocked.
# Matching is on the raw command string, deliberately broad.

_FORBIDDEN_COMMAND_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # -- Remote publishing and force push --------------------------------------
    (re.compile(r"\bgit\s+push\b[^\n]*(?:--force\b|--force-with-lease\b|(?:^|\s)-f\b)"),
     "git force push (publishing + history overwrite of a remote)"),
    (re.compile(r"\bgit\s+push\b"), "git push (publishing to a remote)"),
    (re.compile(r"\bgit\s+(?:merge|rebase|pull)\b[^\n]*\borigin\b"), "git sync from a remote"),
    # -- History rewrites ------------------------------------------------------
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git reset --hard (destroys committed/worktree state)"),
    (re.compile(r"\bgit\s+rebase\b"), "git rebase (history rewrite)"),
    (re.compile(r"\bgit\s+filter-branch\b"), "git filter-branch (history rewrite)"),
    (re.compile(r"\bgit\s+filter-repo\b"), "git filter-repo (history rewrite)"),
    (re.compile(r"\bgit\s+commit\b[^\n]*--amend\b"), "git commit --amend (rewrites a commit)"),
    (re.compile(r"\bgit\s+push\b[^\n]*--delete\b|\bgit\s+push\b[^\n]*:\s*\S"), "git push --delete (remote branch/tag deletion)"),
    # -- Deleting the repository / operating outside allowed paths -------------
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(?:/(?:\s|$)|~|\$HOME\b)"), "recursive delete of a root/home path"),
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+[^\n]*(?:/|\b)\.git(?:/|\b|\s|$)"), "deleting the .git repository"),
    # -- External / production effects ----------------------------------------
    (re.compile(r"\bgh\s+(?:pr|release|repo|api|workflow|gist|secret|auth)\b"), "GitHub CLI mutation"),
    (re.compile(r"\b(?:kubectl|helm|terraform|pulumi|serverless|vercel|netlify|flyctl|fly|heroku|render)\b"), "deploy tooling"),
    (re.compile(r"\b(?:docker|podman)\s+push\b"), "container push"),
    (re.compile(r"\baws\s+(?!.*\b(?:help|--version)\b)"), "AWS CLI"),
    (re.compile(r"\b(?:gcloud|az|doctl|linode-cli)\s+"), "cloud CLI"),
    (re.compile(r"\bnpm\s+publish\b|\bpoetry\s+publish\b|\btwine\s+upload\b|\byarn\s+publish\b|\bnpx\s+[^\n]*\bpublish\b"),
     "package publish"),
    # `\b` before a hyphen never matches, so anchor on whitespace instead.
    (re.compile(r"(?i)\b(?:curl|wget|http|httpie)\b[^|;&]*\s(?:-X\s*(?:POST|PUT|PATCH|DELETE)\b|--data\b|--form\b|-d\s)"),
     "outbound mutating HTTP request"),
    (re.compile(r"(?i)\b(?:slack|twilio|sendgrid|mailgun|postmark|resend|mailx|sendmail|mutt)\b"), "outbound communication"),
    (re.compile(r"(?i)\b(?:stripe|braintree|paypal|adyen|square)\b"), "payment / billing effect"),
    (re.compile(r"(?i)\bprod(?:uction)?\b.*\b(?:db|database|psql|mysql|migrate)\b"), "production database access"),
    (re.compile(r"(?i)\bpsql\b.*\b(?:prod|production)\b"), "production database access"),
    # -- Reading secrets through the shell ------------------------------------
    (re.compile(r"(?i)\b(?:cat|bat|less|more|head|tail|xxd|base64|strings|cp|scp|rsync|open)\b[^\n|;&]*"
                r"(?:\.env(?:\.[^\s/]*)?|(?:^|/)\.ssh/|id_(?:rsa|ed25519|ecdsa|dsa)\b|(?:^|/)\.aws/"
                r"|\.git-credentials\b|(?:^|/)\.netrc\b|\.pem\b|\.p12\b)"),
     "reading a secret / credential file through the shell"),
    (re.compile(r"(?i)\bsecurity\s+(?:find-|dump-|export|unlock)"), "macOS keychain access"),
    # -- System-wide installs and machine-security changes --------------------
    (re.compile(r"(?i)\bsudo\b"), "sudo (privileged, machine-wide effect)"),
    (re.compile(r"(?i)\b(?:brew|apt|apt-get|yum|dnf|port|snap|pacman|zypper|apk)\s+install\b"), "system package install"),
    (re.compile(r"(?i)\bnpm\s+(?:install|i|add)\b[^\n]*\s-g\b|\b(?:yarn|pnpm)\s+global\s+add\b"), "global npm/yarn/pnpm install"),
    (re.compile(r"(?i)\b(?:softwareupdate|csrutil|spctl|nvram|scutil|systemsetup)\b"), "machine-security / system setting change"),
    (re.compile(r"(?i)\bdefaults\s+write\b"), "system defaults change"),
    (re.compile(r"(?i)\bchmod\s+-R?\s*0?777\b|\bchflags\b"), "insecure permission / flag change"),
]

# Secret / credential surfaces: reading OR writing these is refused, on any file
# tool AND through the shell (above). The driver must never exfiltrate or alter
# credentials, tokens, keys, keychains or browser profiles.
_SECRET_PATH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:^|/)\.env(?:\.[^/]*)?$"),
    re.compile(r"(?:^|/)\.ssh(?:/|$)"),
    re.compile(r"(?:^|/)id_(?:rsa|ed25519|ecdsa|dsa)(?:\.pub)?$"),
    re.compile(r"(?:^|/)\.aws(?:/|$)"),
    re.compile(r"(?:^|/)\.azure(?:/|$)"),
    re.compile(r"(?:^|/)\.config/(?:gcloud|gh|gh-copilot)(?:/|$)"),
    re.compile(r"(?:^|/)\.kube(?:/|$)"),
    re.compile(r"(?:^|/)\.git-credentials$"),
    re.compile(r"(?:^|/)\.netrc$"),
    re.compile(r"(?:^|/)\.npmrc$"),
    re.compile(r"(?:^|/)\.pypirc$"),
    re.compile(r"(?:^|/)\.docker/config\.json$"),
    re.compile(r"(?i)(?:^|/)[^/]*\.(?:pem|key|p12|pfx|keystore|jks)$"),
    re.compile(r"(?i)/Library/Keychains/|(?:^|/)[^/]*\.keychain(?:-db)?$"),
    # Browser profiles (cookies, saved credentials).
    re.compile(r"(?i)/(?:Google/Chrome|Chromium|BraveSoftware|Firefox|Microsoft Edge)/"),
    re.compile(r"(?i)(?:^|/)\.mozilla/"),
]

# Protected control surfaces: WRITING these is refused (they carry the harness's
# own authority), but reading them is fine. CLAUDE.md is deliberately NOT here —
# the builder may edit ordinary project docs, including CLAUDE.md.
_PROTECTED_CONFIG_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:^|/)\.claude/settings(?:\.local)?\.json$"),
    re.compile(r"(?:^|/)\.claude/hooks/"),
    re.compile(r"(?:^|/)\.claude/settings/"),
    re.compile(r"(?:^|/)\.mcp\.json$"),
]

_MUTATING_FILE_TOOLS = {"Write", "Edit", "NotebookEdit", "MultiEdit"}
_READ_TOOLS = {"Read", "NotebookRead"}


def _matches(path: str, patterns: list[re.Pattern[str]]) -> bool:
    return bool(path) and any(p.search(path) for p in patterns)


def classify_command(command: str) -> str | None:
    """Return a reason string if ``command`` is hard-blocked, else None."""
    if not command:
        return None
    for pattern, reason in _FORBIDDEN_COMMAND_PATTERNS:
        if pattern.search(command):
            return reason
    return None


def classify_tool_use(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Return a reason string if this tool use is hard-blocked, else None.

    Ordinary work — reading non-secret files, writing/editing ordinary files,
    running non-destructive commands — returns None and is never blocked.
    """
    if tool_name == "Bash":
        return classify_command(str(tool_input.get("command", "")))

    path = str(tool_input.get("file_path") or tool_input.get("path") or "")

    if tool_name in _READ_TOOLS:
        if _matches(path, _SECRET_PATH_PATTERNS):
            return f"read of a secret/credential path ({path})"
        return None

    if tool_name in _MUTATING_FILE_TOOLS:
        if _matches(path, _SECRET_PATH_PATTERNS):
            return f"write to a secret/credential path ({path})"
        if _matches(path, _PROTECTED_CONFIG_PATTERNS):
            return f"write to a protected control surface ({path})"
        return None

    return None


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
    ) -> None:
        self.repo = Path(repo)
        self.config = config
        self.resume_session_id = resume_session_id
        self.session_id: str | None = resume_session_id
        self._client: ClaudeSDKClient | None = None
        self._on_progress = on_progress or (lambda _msg: None)
        self.denied_requests: list[str] = []

    # -- permission handling ---------------------------------------------

    async def _pre_tool_use_hook(
        self, input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        """The enforcement layer: blocks hard-blocked actions even when a
        ``allowed_tools`` entry, the repo's own settings, or the permission mode
        pre-approved the tool. This is what makes explicit tool permissions safe
        to grant unattended — the deny here is deterministic and always fires."""
        tool_name = str(input_data.get("tool_name", ""))
        tool_input = input_data.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            return {}
        reason = classify_tool_use(tool_name, tool_input)
        if not reason:
            return {}
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
        self._client = ClaudeSDKClient(options=self._options())
        await self._client.connect()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

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
