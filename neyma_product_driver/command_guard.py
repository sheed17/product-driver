"""Command and tool-use classification, including indirect invocation.

WHAT THIS IS, HONESTLY
======================

This module is an **application-level** deny layer. It inspects the command
string the model asked to run, decodes the layers a command can hide behind
(``sh -c``, ``python -c``, ``$(...)``, base64, a wrapper script on disk), and
refuses the ones that cross a hard boundary.

It is **not** an operating-system security boundary, and this file will not
pretend otherwise. A determined process that can already execute arbitrary code
in this shell can defeat string classification — by computing a command from
arithmetic, by reading it from a network socket, by writing a script through a
Python file handle the classifier never sees. The honest claim is narrower and
still worth a great deal:

* every *direct* form of a hard-blocked action is refused;
* the *common indirect* forms — nested shells, interpreter ``-c`` payloads,
  command substitution, encoded payloads, and wrapper scripts whose contents can
  be read before execution — are decoded and refused;
* everything refused is *recorded*, so an attempt is visible in the run evidence
  even where it could not have been prevented.

The layers that carry the rest of the weight are documented in the README under
"Enforcement layers". In short: application-level (this module), target-repository
-level (Neyma's own hooks and deny rules, loaded via ``setting_sources``),
Claude-sandbox-level (the CLI's own filesystem/network sandbox), and
operating-system-level (which this driver does not configure — running the
builder as a dedicated local user with no credential access is a procedural
recommendation, not something this code enforces).

Design note: classification is deliberately layered rather than one giant regex.
``classify_command`` handles the string; ``CommandGuard`` adds the checks that
need to touch the filesystem (reading a wrapper script, confining a write).
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import ApprovedRoots, PathVerdict, real_path

# --------------------------------------------------------------------------
# Hard-blocked-action classification (direct forms)
# --------------------------------------------------------------------------
#
# The driver runs unattended, so ordinary work is NOT listed here — it runs
# autonomously. This is the narrow set of actions the driver must never perform
# on the owner's behalf: remote publishing, force push, unproven history
# rewrites, secret access, external/production effects, system-wide installs and
# machine-security changes. Everything else (local commits, restore, add, file
# create/edit/rename/delete, pytest/linters/scripts, local services) is
# deliberately absent so it is never blocked.

# Read-only `gh` subcommands. Everything else in the GitHub CLI mutates a remote
# or reads credentials, so `gh` is default-deny with this narrow exception.
_GH_READONLY = {"status", "browse", "help", "version", "search", "label"}

_FORBIDDEN_COMMAND_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # -- Remote publishing and force push --------------------------------------
    (re.compile(r"\bgit\s+push\b[^\n]*(?:--force\b|--force-with-lease\b|(?:^|\s)-f\b)"),
     "git force push (publishing + history overwrite of a remote)"),
    (re.compile(r"\bgit\s+push\b"), "git push (publishing to a remote)"),
    (re.compile(r"\bgit\s+(?:merge|rebase|pull)\b[^\n]*\borigin\b"), "git sync from a remote"),
    # Remote mutation that is not a push.
    (re.compile(r"\bgit\s+remote\s+(?:add|remove|rm|set-url|rename|prune)\b"),
     "git remote mutation (changing where the repository publishes)"),
    (re.compile(r"\bgit\s+(?:push|fetch)\b[^\n]*--prune\b"), "remote ref pruning"),
    (re.compile(r"\bgit\s+config\b[^\n]*\b(?:credential|url\.|remote\.)"),
     "git credential / remote configuration change"),
    # -- History rewrites ------------------------------------------------------
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git reset --hard (destroys committed/worktree state)"),
    (re.compile(r"\bgit\s+rebase\b"), "git rebase (history rewrite)"),
    (re.compile(r"\bgit\s+filter-branch\b"), "git filter-branch (history rewrite)"),
    (re.compile(r"\bgit\s+filter-repo\b"), "git filter-repo (history rewrite)"),
    (re.compile(r"\bgit\s+push\b[^\n]*--delete\b|\bgit\s+push\b[^\n]*:\s*\S"),
     "git push --delete (remote branch/tag deletion)"),
    # -- Deleting the repository / operating outside allowed paths -------------
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(?:/(?:\s|$)|~|\$HOME\b)"),
     "recursive delete of a root/home path"),
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+[^\n]*(?:/|\b)\.git(?:/|\b|\s|$)"),
     "deleting the .git repository"),
    # -- External / production effects ----------------------------------------
    (re.compile(r"\b(?:kubectl|helm|terraform|pulumi|serverless|vercel|netlify|flyctl|fly|heroku|render)\b"),
     "deploy tooling"),
    (re.compile(r"\b(?:docker|podman)\s+push\b"), "container push"),
    (re.compile(r"\baws\s+(?!.*\b(?:help|--version)\b)"), "AWS CLI"),
    (re.compile(r"\b(?:gcloud|az|doctl|linode-cli)\s+"), "cloud CLI"),
    (re.compile(r"\bnpm\s+publish\b|\bpoetry\s+publish\b|\btwine\s+upload\b|\byarn\s+publish\b"
                r"|\bnpx\s+[^\n]*\bpublish\b"),
     "package publish"),
    # `\b` before a hyphen never matches, so anchor on whitespace instead.
    (re.compile(r"(?i)\b(?:curl|wget|http|httpie)\b[^|;&]*"
                r"\s(?:-X\s*(?:POST|PUT|PATCH|DELETE)\b|--data\b|--data-\w+\b|--form\b|-d\s|-F\s"
                r"|--upload-file\b|-T\s|--method[= ]\s*(?:POST|PUT|PATCH|DELETE)\b)"),
     "outbound mutating HTTP request"),
    (re.compile(r"(?i)\b(?:slack|twilio|sendgrid|mailgun|postmark|resend|mailx|sendmail|mutt"
                r"|osascript)\b"),
     "outbound communication"),
    (re.compile(r"(?i)\b(?:stripe|braintree|paypal|adyen|square)\b"), "payment / billing effect"),
    (re.compile(r"(?i)\bprod(?:uction)?\b.*\b(?:db|database|psql|mysql|migrate)\b"),
     "production database access"),
    (re.compile(r"(?i)\bpsql\b.*\b(?:prod|production)\b"), "production database access"),
    # -- Reading secrets through the shell ------------------------------------
    (re.compile(r"(?i)\b(?:cat|bat|less|more|head|tail|xxd|base64|strings|cp|scp|rsync|open"
                r"|nano|vi|vim|emacs|awk|sed|grep|rg|jq|tee|dd|install)\b[^\n|;&]*"
                r"(?:\.env(?:\.[^\s/]*)?|(?:^|/)\.ssh/|id_(?:rsa|ed25519|ecdsa|dsa)\b|(?:^|/)\.aws/"
                r"|\.git-credentials\b|(?:^|/)\.netrc\b|\.pem\b|\.p12\b|(?:^|/)\.npmrc\b"
                r"|(?:^|/)\.pypirc\b|(?:^|/)\.docker/config\.json\b|(?:^|/)\.kube/"
                r"|\.credentials\.json\b|(?:^|/)\.config/(?:gcloud|gh)\b|\.keychain\b)"),
     "reading a secret / credential file through the shell"),
    (re.compile(r"(?i)\bsecurity\s+(?:find-|dump-|export|unlock)"), "macOS keychain access"),
    # Printing the environment wholesale, or a credential-shaped variable.
    (re.compile(r"(?i)(?:^|[\s;&|])(?:printenv|env)\s*(?:$|[;&|])"), "environment dump"),
    (re.compile(r"\$\{?(?:ANTHROPIC_API_KEY|OPENAI_API_KEY|GITHUB_TOKEN|GH_TOKEN"
                r"|[A-Z0-9_]*(?:_SECRET|_TOKEN|_PASSWORD|_API_KEY|_CREDENTIALS))\b"),
     "reading a credential environment variable"),
    # -- System-wide installs and machine-security changes --------------------
    (re.compile(r"(?i)\bsudo\b"), "sudo (privileged, machine-wide effect)"),
    (re.compile(r"(?i)\b(?:brew|apt|apt-get|yum|dnf|port|snap|pacman|zypper|apk)\s+install\b"),
     "system package install"),
    (re.compile(r"(?i)\bnpm\s+(?:install|i|add)\b[^\n]*\s-g\b|\b(?:yarn|pnpm)\s+global\s+add\b"),
     "global npm/yarn/pnpm install"),
    (re.compile(r"(?i)\b(?:softwareupdate|csrutil|spctl|nvram|scutil|systemsetup|launchctl)\b"),
     "machine-security / system setting change"),
    (re.compile(r"(?i)\bdefaults\s+write\b"), "system defaults change"),
    (re.compile(r"(?i)\bchmod\s+-R?\s*0?777\b|\bchflags\b"), "insecure permission / flag change"),
]

# Local history transformations are handled separately: blocked by default, but
# authorizable for a *specific* proven-unpushed, protocol-required, preserved
# change. See `preservation.py` and `CommandGuard.amendment_authorized`.
#
# `git reset --soft` is here alongside `--amend` because consolidating commits
# by soft-resetting to a baseline and re-committing rewrites local history just
# as thoroughly, and needs the same recoverability proof. `git rebase` is *not*
# authorizable at all — it stays in the unconditional block list above, because
# nothing here can yet prove an arbitrary rebase recoverable.
_HISTORY_TRANSFORM_PATTERNS = [
    (re.compile(r"\bgit\s+commit\b[^\n]*--amend\b"), "git commit --amend"),
    (re.compile(r"\bgit\s+reset\s+--soft\b"), "git reset --soft (commit consolidation)"),
]

# Operations that move a ref, re-point HEAD, or hide/replace the materialized
# working tree. They are denied WHILE A BUILDER OWNS THE WORKTREE, because the
# builder is the thing holding the only copy of the in-progress work.
#
# Run 3 is the case: a builder moved the product branch with `update-ref` after
# building a commit with `commit-tree`, and reset the worktree to a tree that did
# not contain the episode's untracked files. The work survived only because a
# preservation ref happened to exist. Nothing in the guard noticed, because each
# individual command looked innocuous: `commit-tree` writes an object and moves
# nothing, `update-ref` moves a ref and touches no file. It is the SEQUENCE that
# rewrites history, and the ownership that makes it unsafe.
_WORKTREE_OWNERSHIP_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgit\s+update-ref\b"), "git update-ref (moves a ref)"),
    (re.compile(r"\bgit\s+symbolic-ref\b(?![^\n]*--(?:short|quiet)?\s*$)"),
     "git symbolic-ref (re-points HEAD)"),
    (re.compile(r"\bgit\s+branch\b[^\n]*(?:\s-f\b|\s--force\b|\s-[a-zA-Z]*f[a-zA-Z]*\b)"),
     "git branch --force (moves a branch ref)"),
    (re.compile(r"\bgit\s+branch\b[^\n]*\s-(?:d|D)\b"), "git branch -d/-D (deletes a branch ref)"),
    (re.compile(r"\bgit\s+reset\b"), "git reset (moves the branch, index or worktree)"),
    (re.compile(r"\bgit\s+checkout\b(?![^\n]*\s-{1,2}[bB]\b)"),
     "git checkout (replaces the materialized worktree or moves HEAD)"),
    (re.compile(r"\bgit\s+switch\b"), "git switch (moves HEAD and the worktree)"),
    (re.compile(r"\bgit\s+restore\b"), "git restore (discards working-tree changes)"),
    (re.compile(r"\bgit\s+stash\b"), "git stash (hides the in-progress working tree)"),
    (re.compile(r"\bgit\s+clean\b"), "git clean (deletes untracked product files)"),
    (re.compile(r"\bgit\s+commit-tree\b"), "git commit-tree (builds a replacement commit)"),
    (re.compile(r"\bgit\s+worktree\s+(?:remove|prune|move)\b"), "git worktree removal/move"),
]

_WORKTREE_OWNERSHIP_REASON = (
    "{what} while a builder owns this product worktree. The builder holds the only "
    "materialized copy of the in-progress work, including untracked files, so a ref "
    "move or a worktree replacement can silently discard it. An exact ref transition "
    "is permitted only through a live approval naming that exact old ref, new ref, "
    "baseline and intended tree — and only when no builder owns the worktree."
)

_HISTORY_TRANSFORM_REASON = (
    "{what} — a local history rewrite without a preservation-backed authorization. "
    "The affected commits must be proven unreachable from every remote-tracking "
    "ref, the repository protocol must require the transformation, and a "
    "preservation ref plus git bundle must exist before anything moves"
)

# --------------------------------------------------------------------------
# Indirect invocation
# --------------------------------------------------------------------------

# Interpreters that take an inline program. The payload is extracted and
# re-classified, both as a shell command and (for Python-likes) against the
# code-level patterns below.
_INLINE_INTERPRETERS = re.compile(
    r"(?ix)"
    r"\b(?P<interp>sh|bash|zsh|dash|ksh|fish|python3?(?:\.\d+)?|perl|ruby|node|deno|php)\b"
    r"[^\n]*?\s(?P<flag>-c|-e|--command|--eval)\s+"
)

_EVAL_RE = re.compile(r"(?:^|[\s;&|(])eval\s+")
_SUBSTITUTION_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_BASE64_DECODE_RE = re.compile(
    r"(?i)\b(?:base64\s+(?:-d|-D|--decode)|openssl\s+base64\s+-d"
    r"|python3?\s+-c[^\n]*b64decode)\b"
)
_PIPE_TO_SHELL_RE = re.compile(r"(?i)\|\s*(?:sudo\s+)?(?:sh|bash|zsh|dash|ksh|python3?|node|perl|ruby)\b")

# Code-level effects the shell classifier would never see, because no shell
# token is involved. Split in two, because the same pattern means different
# things in different places.
#
# `_EFFECT_CODE_PATTERNS` cross a hard boundary wherever they appear — in an
# inline payload or in a script on disk. Reading a credential file or pushing
# through a git library is out of bounds either way.
_EFFECT_CODE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\bos\.environ\b[^\n]{0,80}(?:_KEY|_TOKEN|_SECRET|_PASSWORD|CREDENTIAL)"),
     "reading a credential environment variable from interpreted code"),
    (re.compile(r"(?i)\b(?:open|Path|read_text|read_bytes|load|shutil\.copy\w*|readFileSync)\b"
                r"[^\n]{0,120}"
                r"(?:\.env\b|\.ssh/|id_rsa|id_ed25519|\.aws/|\.netrc|\.git-credentials|\.pem\b)"),
     "reading a secret / credential file from interpreted code"),
    (re.compile(r"(?i)\bpygit2\b|\bgit\.Repo\b[^\n]{0,80}\bpush\b|\bremote\s*\([^\n]*\)\.push\b"),
     "git push from interpreted code"),
    (re.compile(r"(?i)\bsmtplib\b|\bboto3\b|\bgoogle\.cloud\b|\bparamiko\b|\btwilio\b|\bstripe\b"),
     "external service client from interpreted code"),
]

# `_INLINE_ONLY_CODE_PATTERNS` are a smell *only* in an inline `-c` payload.
# Spawning a subprocess or calling an HTTP verb is completely ordinary inside a
# repository script — the product is a freight integration, it makes API calls —
# so applying these to files on disk would block the driver's real work. Inside
# a one-liner typed at the shell, they are how a classifier gets stepped around.
_INLINE_ONLY_CODE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(?:subprocess|os\.system|os\.popen|os\.exec\w*|os\.spawn\w*|pty\.spawn"
                r"|commands\.getoutput|child_process|execSync|spawnSync)\b"),
     "spawning a subprocess from inside an inline interpreter payload"),
    (re.compile(r"(?i)\b(?:requests|httpx|aiohttp|urllib3?)\s*\.\s*(?:post|put|patch|delete)\b"),
     "outbound mutating HTTP from inside an inline interpreter payload"),
    (re.compile(r"(?i)\burllib\.request\.(?:urlopen|Request)\b[^\n]*\bdata\s*="),
     "outbound mutating HTTP from inside an inline interpreter payload"),
    (re.compile(r"(?i)\bsocket\b[^\n]{0,60}\b(?:connect|sendto|sendall)\b"),
     "raw outbound socket from inside an inline interpreter payload"),
]

# Suffixes we can read and classify before letting a wrapper script run.
_INSPECTABLE_SUFFIXES = {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".rb", ".pl", ""}

# Commands whose first token names a script/interpreter target worth inspecting.
_SCRIPT_RUNNERS = {
    "sh", "bash", "zsh", "dash", "ksh", "fish", "source", ".",
    "python", "python3", "node", "deno", "ruby", "perl", "php",
}

_MAX_INDIRECTION_DEPTH = 4
_MAX_SCRIPT_CHARS = 200_000


def _decode_base64_fragments(command: str) -> list[str]:
    """Return plausible decodings of long base64-looking literals in ``command``."""
    out: list[str] = []
    # 8 characters is the shortest padded base64 that can carry a real command
    # ("git push" encodes to 12). A short token that happens to decode is
    # harmless: the decoding is only ever *classified*, never trusted.
    for token in re.findall(r"[A-Za-z0-9+/=]{8,}", command):
        if len(token) % 4:
            continue
        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8", "replace")
        except (binascii.Error, ValueError):
            continue
        # Only interesting when it decodes to something text-shaped.
        if decoded.isprintable() or "\n" in decoded:
            out.append(decoded)
    return out


def _inline_payloads(command: str) -> list[str]:
    """Extract inline interpreter programs: ``sh -c '...'``, ``python -c "..."``."""
    payloads: list[str] = []
    for match in _INLINE_INTERPRETERS.finditer(command):
        rest = command[match.end():]
        try:
            parts = shlex.split(rest, comments=False, posix=True)
        except ValueError:
            # Unbalanced quotes: fall back to the raw tail, which is still worth
            # classifying — an unparseable command is not a safe command.
            payloads.append(rest)
            continue
        if parts:
            payloads.append(parts[0])
    return payloads


# A command whose *name* is computed at runtime cannot be classified at all:
# `$(echo git) push` and `g=git; $g push` both run git push while containing
# neither token adjacent. Refusing these is the honest response — the classifier
# is saying it cannot see what will run, not that it saw something bad.
#
# A `$VAR/rest/of/path` prefix is exempt: `$VENV/bin/python -m pytest` is
# ordinary, and the interpreter it names is still classified from the rest of
# the line.
_BARE_VAR_RE = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$")
_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=\S*")


def _computed_command_name(command: str) -> str | None:
    """Detect a command whose first token is produced by expansion."""
    for segment in re.split(r"[;&|]+|\n", command):
        stripped = segment.strip()
        # Step past any leading VAR=value assignment prefix; the real command
        # name follows it (`PYTHONPATH=src python -m pytest`).
        while stripped:
            first = stripped.split()[0]
            if not _ASSIGNMENT_RE.fullmatch(first):
                break
            parts = stripped.split(None, 1)
            stripped = parts[1].strip() if len(parts) > 1 else ""
        if not stripped:
            continue

        # Substitution in command position: `$(echo git) push`, `` `git` push ``.
        if stripped.startswith("$(") or stripped.startswith("`"):
            name = stripped.split()[0]
        else:
            name = stripped.split()[0]
            # `$VAR/bin/python` is a path prefix, not a computed command name —
            # the interpreter it names is still classified from the rest of the
            # line. Only a *bare* `$VAR` hides the whole command.
            if not _BARE_VAR_RE.fullmatch(name):
                continue

        return (
            f"a command whose name is computed at runtime ({name}) — the harness "
            "cannot classify what would actually run, so it is refused; "
            "use the literal command instead"
        )
    return None


def _substitutions(command: str) -> list[str]:
    out: list[str] = []
    for a, b in _SUBSTITUTION_RE.findall(command):
        inner = a or b
        if inner.strip():
            out.append(inner)
    return out


def _direct_reason(command: str) -> str | None:
    """Pattern match against the raw string only — no decoding."""
    for pattern, reason in _FORBIDDEN_COMMAND_PATTERNS:
        if pattern.search(command):
            return reason
    for pattern, what in _HISTORY_TRANSFORM_PATTERNS:
        if pattern.search(command):
            return _HISTORY_TRANSFORM_REASON.format(what=what)
    # `gh` is default-deny apart from a small read-only set. A leading flag
    # (`gh --version`, `gh --help`) names no subcommand and mutates nothing, so
    # the subcommand is the first token that is not a flag.
    for match in re.finditer(r"\bgh\s+((?:-{1,2}[A-Za-z-]+\s+)*)([a-z][a-z-]*)", command):
        subcommand = match.group(2)
        if subcommand not in _GH_READONLY:
            return f"GitHub CLI mutation (gh {subcommand})"
    return None


def _code_reason(payload: str, *, inline: bool) -> str | None:
    """Classify interpreted code.

    ``inline=True`` for a one-liner passed to ``-c``/``-e``; ``inline=False``
    for a file on disk, where the stricter inline-only patterns would fire on
    ordinary product code.
    """
    patterns = list(_EFFECT_CODE_PATTERNS)
    if inline:
        patterns += _INLINE_ONLY_CODE_PATTERNS
    for pattern, reason in patterns:
        if pattern.search(payload):
            return reason
    return None


def classify_worktree_ownership(command: str) -> str | None:
    """Reason ``command`` is unsafe while a builder owns the worktree, else None.

    Applied only when ownership is actually held: outside a builder-owned
    worktree these are ordinary git commands and must not be blocked.
    """
    if not command:
        return None
    for pattern, what in _WORKTREE_OWNERSHIP_PATTERNS:
        if pattern.search(command):
            return _WORKTREE_OWNERSHIP_REASON.format(what=what)
    return None


def classify_command(
    command: str,
    *,
    _depth: int = 0,
    allow_amend: bool = False,
    builder_owns_worktree: bool = False,
) -> str | None:
    """Return a reason string if ``command`` is hard-blocked, else ``None``.

    Decodes the common indirection layers before deciding: inline interpreter
    payloads, command substitution, ``eval``, and base64. ``allow_amend`` is set
    only by :class:`CommandGuard` when a specific amendment has passed every
    preservation precondition.
    """
    if not command:
        return None

    if builder_owns_worktree:
        owned = classify_worktree_ownership(command)
        if owned is not None:
            return owned

    direct = _direct_reason(command)
    if direct is not None:
        # An authorized history transformation is the one reason that can be
        # waived; every other classification still stands, so the rest of the
        # string keeps being checked rather than being waved through with it.
        waivable = direct.startswith(("git commit --amend", "git reset --soft"))
        if not (allow_amend and waivable):
            return direct

    computed = _computed_command_name(command)
    if computed is not None:
        return computed

    if _depth >= _MAX_INDIRECTION_DEPTH:
        return None

    # A decoded payload piped straight into a shell is never ordinary work.
    if _BASE64_DECODE_RE.search(command) and _PIPE_TO_SHELL_RE.search(command):
        return "executing a base64-decoded command (encoded command execution)"

    nested: list[str] = []
    nested.extend(_inline_payloads(command))
    nested.extend(_substitutions(command))
    if _BASE64_DECODE_RE.search(command) or _EVAL_RE.search(command):
        nested.extend(_decode_base64_fragments(command))

    for payload in nested:
        if not payload or payload == command:
            continue
        code = _code_reason(payload, inline=True)
        if code is not None:
            return code
        inner = classify_command(
            payload,
            _depth=_depth + 1,
            allow_amend=allow_amend,
            builder_owns_worktree=builder_owns_worktree,
        )
        if inner is not None:
            return f"{inner} (reached indirectly through an interpreter or substitution)"

    # A Python/Node payload can carry an effect with no shell token at all —
    # catch it on the whole string too, in case quoting defeated extraction.
    if re.search(r"(?i)\b(?:python3?(?:\.\d+)?|node|deno|ruby|perl)\b[^\n]*\s-(?:c|e)\s", command):
        code = _code_reason(command, inline=True)
        if code is not None:
            return code

    return None


# --------------------------------------------------------------------------
# File-tool path classification
# --------------------------------------------------------------------------

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
    re.compile(r"(?:^|/)\.claude/\.credentials\.json$"),
    re.compile(r"(?i)(?:^|/)[^/]*\.(?:pem|key|p12|pfx|keystore|jks)$"),
    re.compile(r"(?i)/Library/Keychains/|(?:^|/)[^/]*\.keychain(?:-db)?$"),
    # Browser profiles (cookies, saved credentials).
    re.compile(r"(?i)/(?:Google/Chrome|Chromium|BraveSoftware|Firefox|Microsoft Edge)/"),
    re.compile(r"(?i)(?:^|/)\.mozilla/"),
]

# Protected control surfaces: WRITING these is refused (they carry the harness's
# own authority), but reading them is fine. CLAUDE.md is deliberately NOT here —
# the builder may edit ordinary project docs, including CLAUDE.md, under the
# observability rules in `authority.py`.
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


def is_secret_path(path: str) -> bool:
    return _matches(path, _SECRET_PATH_PATTERNS)


def is_protected_config_path(path: str) -> bool:
    return _matches(path, _PROTECTED_CONFIG_PATTERNS)


def classify_tool_use(
    tool_name: str, tool_input: dict[str, Any], *, allow_amend: bool = False
) -> str | None:
    """Return a reason string if this tool use is hard-blocked, else ``None``.

    Ordinary work — reading non-secret files, writing/editing ordinary files,
    running non-destructive commands — returns ``None`` and is never blocked.
    This is the path-and-string layer only; approved-root confinement lives in
    :class:`CommandGuard`, because it needs to know the run's roots.
    """
    if tool_name == "Bash":
        return classify_command(str(tool_input.get("command", "")), allow_amend=allow_amend)

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
# The stateful guard: roots, wrapper scripts, authorized amendments
# --------------------------------------------------------------------------


@dataclass
class GuardDecision:
    """One classification outcome, with enough detail to record it."""

    reason: str | None
    #: Which enforcement layer produced it: "command", "path", "root", "script".
    layer: str = ""
    #: Every path this decision resolved, allowed or not.
    verdicts: list[PathVerdict] = field(default_factory=list)

    @property
    def denied(self) -> bool:
        return self.reason is not None


# Shell redirection targets — a write that never goes through a file tool.
_REDIRECT_RE = re.compile(r"(?:^|[^0-9<>])>>?\s*([^\s;&|<>]+)")
# Commands whose *target* argument is written to.
_WRITE_COMMANDS = re.compile(
    r"(?i)(?:^|[\s;&|])(?:tee|dd\s+of=|install|cp|mv|rsync|ln)\b"
)


class CommandGuard:
    """Classification that needs the filesystem: roots, scripts, amendments.

    Kept separate from the pure functions above so that the string-level rules
    stay trivially testable and so a caller without a configured root set still
    gets the full command classification.
    """

    def __init__(
        self,
        *,
        roots: ApprovedRoots | None = None,
        cwd: Path | None = None,
        amendment_authorized: bool = False,
        builder_owns_worktree: bool = False,
    ) -> None:
        self.roots = roots
        self.cwd = Path(cwd) if cwd is not None else None
        self.amendment_authorized = amendment_authorized
        #: Set while a builder holds this product worktree. See
        #: `_WORKTREE_OWNERSHIP_PATTERNS` for what it denies and why.
        self.builder_owns_worktree = builder_owns_worktree
        #: Every path denied during this session, for the run journal.
        self.denied_paths: list[PathVerdict] = []

    # -- roots ------------------------------------------------------------

    def check_write_path(self, raw: str) -> PathVerdict | None:
        """Confine one write target to the approved roots."""
        if not self.roots or not raw:
            return None
        verdict = self.roots.classify_write(raw, self.cwd)
        if not verdict.allowed:
            self.denied_paths.append(verdict)
        return verdict

    # -- wrapper scripts --------------------------------------------------

    def _script_targets(self, command: str) -> list[str]:
        """Paths that ``command`` would execute as a script."""
        targets: list[str] = []
        for segment in re.split(r"[;&|]+|\n", command):
            try:
                tokens = shlex.split(segment, comments=True, posix=True)
            except ValueError:
                continue
            if not tokens:
                continue
            head = os.path.basename(tokens[0])
            if head in _SCRIPT_RUNNERS:
                for token in tokens[1:]:
                    # `-m module` and `-c '<code>'` name no script on disk; the
                    # inline payload is already handled by classify_command.
                    if token in {"-m", "-c", "-e", "--command", "--eval"}:
                        break
                    if token.startswith("-"):
                        continue
                    targets.append(token)
                    break
            elif "/" in tokens[0] or tokens[0].startswith("."):
                targets.append(tokens[0])
        return targets

    def inspect_scripts(self, command: str) -> GuardDecision:
        """Read wrapper scripts referenced by ``command`` and classify them.

        This is what stops "write a push script in one turn, run it in the
        next". Two rules apply:

        * an executable script resolving outside every approved root is refused
          outright — the driver cannot vouch for code it does not govern;
        * a script inside an approved root is read and each of its command lines
          classified, so a ``git push`` in a shell wrapper is caught before it
          runs.

        The on-disk scan deliberately uses the *narrow* code rule: a repository
        script that spawns a subprocess or makes an HTTP call is ordinary
        product code, and blocking it would break the driver's real work. What
        it does catch is the actual bypass — shell-level hard-blocked commands
        written into a file, and credential reads.

        A script that cannot be read (binary, absent, too large) is not treated
        as safe by omission: outside the roots it is refused on location alone,
        and inside them it falls under the same trust the roots already express.
        """
        for raw in self._script_targets(command):
            resolved = real_path(raw, self.cwd)
            if self.roots and self.roots.root_for(resolved) is None:
                # Only complain about something that actually exists; a missing
                # path is a plain command error, not a bypass attempt.
                if resolved.exists():
                    verdict = PathVerdict(raw, resolved, None, "outside approved roots")
                    self.denied_paths.append(verdict)
                    return GuardDecision(
                        f"executing a script outside every approved root ({resolved})",
                        layer="script",
                        verdicts=[verdict],
                    )
                continue
            if not resolved.is_file():
                continue
            if resolved.suffix.lower() not in _INSPECTABLE_SUFFIXES:
                continue
            try:
                body = resolved.read_text(encoding="utf-8", errors="replace")[:_MAX_SCRIPT_CHARS]
            except OSError:
                continue
            reason = _code_reason(body, inline=False)
            if reason is None:
                for line in body.splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    reason = classify_command(
                        stripped,
                        allow_amend=self.amendment_authorized,
                        builder_owns_worktree=self.builder_owns_worktree,
                    )
                    if reason is not None:
                        break
            if reason is not None:
                return GuardDecision(
                    f"{reason} — found inside the script {raw} before executing it",
                    layer="script",
                )
        return GuardDecision(None)

    # -- the whole decision ----------------------------------------------

    def classify(self, tool_name: str, tool_input: dict[str, Any]) -> GuardDecision:
        """Classify one tool use across every layer this guard implements."""
        if tool_name == "Bash":
            command = str(tool_input.get("command", ""))
            reason = classify_command(
                command,
                allow_amend=self.amendment_authorized,
                builder_owns_worktree=self.builder_owns_worktree,
            )
            if reason is not None:
                return GuardDecision(reason, layer="command")

            script = self.inspect_scripts(command)
            if script.denied:
                return script

            verdicts: list[PathVerdict] = []
            for target in self._write_targets(command):
                verdict = self.check_write_path(target)
                if verdict is None:
                    continue
                verdicts.append(verdict)
                if not verdict.allowed:
                    return GuardDecision(
                        f"shell write outside the approved roots: {verdict.reason}",
                        layer="root",
                        verdicts=verdicts,
                    )
            return GuardDecision(None, verdicts=verdicts)

        reason = classify_tool_use(
            tool_name, tool_input, allow_amend=self.amendment_authorized
        )
        if reason is not None:
            return GuardDecision(reason, layer="path")

        if tool_name in _MUTATING_FILE_TOOLS:
            raw = str(tool_input.get("file_path") or tool_input.get("path") or "")
            verdict = self.check_write_path(raw)
            if verdict is not None and not verdict.allowed:
                return GuardDecision(
                    f"write outside the approved roots: {verdict.reason}",
                    layer="root",
                    verdicts=[verdict],
                )
            return GuardDecision(None, verdicts=[verdict] if verdict else [])

        return GuardDecision(None)

    def _write_targets(self, command: str) -> list[str]:
        """Paths a shell command would write to: redirections and copy targets."""
        targets = [m for m in _REDIRECT_RE.findall(command)]
        if _WRITE_COMMANDS.search(command):
            for segment in re.split(r"[;&|]+|\n", command):
                try:
                    tokens = shlex.split(segment, comments=True, posix=True)
                except ValueError:
                    continue
                if len(tokens) >= 2 and os.path.basename(tokens[0]) in {
                    "cp", "mv", "rsync", "install", "ln"
                }:
                    targets.append(tokens[-1])
                elif tokens and os.path.basename(tokens[0]) == "tee":
                    targets.extend(t for t in tokens[1:] if not t.startswith("-"))
        return [t for t in targets if t and not t.startswith("$") and t != "/dev/null"]


def enforcement_layers() -> list[tuple[str, str, str]]:
    """(layer, what it covers, what it cannot cover) — used by docs and doctor.

    Kept in code so the README and the ``doctor`` output cannot drift apart from
    what is actually implemented.
    """
    return [
        (
            "application-level (this driver)",
            "direct and common-indirect hard-blocked commands, secret paths, "
            "protected control surfaces, approved-root confinement, wrapper-script "
            "inspection, authorized-amendment gating",
            "arbitrary computed or runtime-fetched commands; anything an already-"
            "running process does through file handles the classifier never sees",
        ),
        (
            "target-repository-level (Neyma's own settings and hooks)",
            "the repository's own deny rules and PreToolUse hooks, loaded through "
            "setting_sources and authoritative over the driver's allowances",
            "nothing outside that repository's own configuration",
        ),
        (
            "Claude-sandbox-level (the Claude Code CLI)",
            "the CLI's own filesystem and network sandbox for the builder session, "
            "including the socket.bind() restriction the scenarios work around",
            "actions the sandbox is configured to permit",
        ),
        (
            "operating-system-level",
            "not configured by this driver",
            "everything — this is the layer a dedicated local user account with no "
            "credential access would provide, and it is a procedural recommendation "
            "rather than something this code enforces",
        ),
    ]


__all__ = [
    "CommandGuard",
    "GuardDecision",
    "classify_command",
    "classify_tool_use",
    "enforcement_layers",
    "is_protected_config_path",
    "is_secret_path",
]
