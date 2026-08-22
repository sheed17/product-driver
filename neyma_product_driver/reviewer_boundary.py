"""What an independent reviewer is allowed to execute, and what it never is.

A read-only reviewer that cannot run anything reviews *receipts*. It reads what
the harness collected, and its verdict rests on the harness having collected it
honestly — which is precisely the thing an independent review exists to check.
The M3 review is the worked example: the reviewer could not execute the
deterministic probe, suite or mutation battery, so half of what it "verified"
was Product Driver's own captured output handed back to it.

So the reviewer gets a shell. Not *a* shell — this one:

    a command is executable by a reviewer only when it is BOTH
      (1) an appropriate read-only verification action, AND
      (2) already allowed by Product Driver policy.

Neither half is sufficient. (1) alone would let a reviewer run any read-only
thing the guard has never heard of; (2) alone would let it run every ordinary
build action the *builder* is allowed to perform, which is the whole point of
the reviewer not being the builder.

**Nothing here is a second security model.** (2) is
:func:`~neyma_product_driver.command_guard.classify_command` — the same hard-block
classifier the builder, the scenario executor and the generated-scenario
validator all go through — plus
:class:`~neyma_product_driver.scenario_validation.ApprovedCommands`, the
human-authored command vocabulary harvested from the repository's own scenario
files. A reviewer may run the repository's probe because a human wrote that probe
into a scenario, not because this module has an opinion about probes.

(1) is the part that is specific to a reviewer, and it is deliberately narrow: a
closed set of read-only verification heads (``git`` in its read-only
subcommands, ``grep``/``rg``, ``ls``/``find``, ``head``/``tail``/``cat``,
``wc``/``sort``/``uniq``/``cut``/``diff``/``jq``, and ``pytest``), plus a set of
denials applied to *every* command whatever approved it: no git mutation, no
file mutation, no redirection, no network, no installer, no privilege change, no
secret read, no shell composition beyond an argument tail.

What that adds up to, stated as the reviewer's contract:

    may:    re-run the deterministic suites, probes and mutation batteries the
            repository already declares; read any non-secret file; inspect the
            diff, the tree, the history and the working-tree state; grep.
    may not: write or edit any file, create files, commit, push, merge, rewrite
            history, deploy, touch production, make a mutating network call,
            send mail/Slack/SMS, read a secret, install software, or reach any
            of those through a wrapper, a substitution or an interpreter payload.

Every decision — allowed or refused — is returned with the reason and the basis
that produced it, so the run evidence can say which commands the reviewer ran
itself and which it was refused.

Nothing in this module executes anything. It classifies; the reviewer session
enforces.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from typing import Any, Sequence

from .command_guard import (
    classify_command,
    classify_worktree_ownership,
    is_protected_config_path,
    is_secret_path,
)
from .scenario_validation import scan_shell_operators

# --------------------------------------------------------------------------
# (1) The read-only verification vocabulary
# --------------------------------------------------------------------------

#: ``git`` subcommands that only read. Anything not listed is refused, so a git
#: subcommand nobody thought about is refused rather than allowed by omission.
_GIT_READ_ONLY: frozenset[str] = frozenset(
    {
        "diff", "diff-tree", "diff-index", "show", "log", "status", "rev-parse",
        "rev-list", "ls-files", "ls-tree", "cat-file", "blame", "annotate",
        "describe", "shortlog", "grep", "name-rev", "symbolic-ref",
        "for-each-ref", "show-ref", "merge-base", "whatchanged", "count-objects",
        "verify-commit", "verify-tag", "check-ignore", "check-attr", "var",
    }
)

#: git options that turn a read into a write, on any subcommand.
_GIT_WRITING_OPTIONS = re.compile(
    r"(?:^|\s)(?:-o|--output(?:=|\s)|--output-directory|--exec|--textconv-cache"
    r"|--write-tree|--index-output)",
    re.I,
)

#: Non-git heads a reviewer may run, and the per-head options that would turn
#: each into a write. A head absent from this mapping is not a read-only
#: verification action as far as this module is concerned.
_READ_ONLY_HEADS: dict[str, tuple[re.Pattern[str] | None, str]] = {
    "grep": (None, "reads files and prints matches"),
    "egrep": (None, "reads files and prints matches"),
    "fgrep": (None, "reads files and prints matches"),
    "rg": (None, "reads files and prints matches"),
    "ls": (None, "lists directory entries"),
    "find": (
        re.compile(r"(?:^|\s)-(?:delete|exec|execdir|ok|okdir|fprint\w*|fls)\b"),
        "walks the tree and prints paths",
    ),
    "cat": (None, "prints a file"),
    "head": (None, "prints the start of a file"),
    "tail": (re.compile(r"(?:^|\s)-[a-zA-Z]*[fF]\b"), "prints the end of a file"),
    "wc": (None, "counts lines, words and bytes"),
    "sort": (re.compile(r"(?:^|\s)(?:-o|--output)\b"), "orders lines"),
    "uniq": (None, "collapses adjacent duplicate lines"),
    "cut": (None, "selects columns"),
    "tr": (None, "translates characters on a stream"),
    "nl": (None, "numbers lines"),
    "diff": (None, "compares two files"),
    "stat": (None, "prints file metadata"),
    "file": (None, "identifies a file type"),
    "basename": (None, "prints a path component"),
    "dirname": (None, "prints a path component"),
    "realpath": (None, "resolves a path"),
    "pwd": (None, "prints the working directory"),
    "jq": (re.compile(r"(?:^|\s)(?:-i|--in-place|--rawfile\b.*>)"), "queries JSON"),
    "shasum": (None, "hashes a file"),
    "sha256sum": (None, "hashes a file"),
    "md5sum": (None, "hashes a file"),
    "pytest": (None, "runs the deterministic test suite"),
    "py.test": (None, "runs the deterministic test suite"),
}

#: Heads that are read-only *only* in a specific form. ``python -m pytest`` is
#: verification; ``python -c '...'`` is arbitrary code and is not.
_PYTHON_HEADS = re.compile(r"^(?:python|python3|python3\.\d+)$")

# --------------------------------------------------------------------------
# Denials applied to EVERY reviewer command, whatever approved it
# --------------------------------------------------------------------------
#
# These sit above both gates. An approved scenario command is approved because a
# human wrote it down as verification — that does not make it a licence for the
# reviewer to commit, publish or install, and a repository that one day writes a
# `git commit` into a scenario file must not thereby hand one to a reviewer.

#: Any git subcommand that changes a ref, the index, the working tree, a remote
#: or the object store. Not a superset of the guard's hard blocks — a deliberate
#: overlap, because the reviewer's floor is higher than the builder's.
_GIT_MUTATING = re.compile(
    r"(?:^|[\s;&|])git\s+(?:-[^\s]+\s+)*"
    r"(?:add|am|apply|bisect|branch|checkout|cherry-pick|clean|clone|commit|"
    r"config|fetch|filter-branch|filter-repo|format-patch|gc|init|merge|mv|"
    r"notes|prune|pull|push|rebase|reflog|remote|replace|reset|restore|revert|"
    r"rm|sparse-checkout|stash|submodule|switch|tag|update-index|update-ref|"
    r"worktree|write-tree|repack|maintenance|hash-object)\b",
    re.I,
)

#: Commands whose ordinary job is to change the filesystem.
_FILE_MUTATING = re.compile(
    r"(?:^|[\s;&|])(?:rm|rmdir|mv|cp|mkdir|touch|truncate|tee|dd|install|ln|"
    r"chmod|chown|chgrp|chflags|patch|ed|shred|unlink|zip|unzip|gunzip|"
    r"xattr|mktemp|rsync|ditto)\b",
    re.I,
)

#: In-place editors and stream editors that can write through their own syntax
#: rather than through a redirection this module can see.
_IN_PLACE_EDITORS = re.compile(
    r"(?:^|[\s;&|])(?:sed|awk|gawk|nawk|perl|ruby|node|deno|osascript|vi|vim|"
    r"nano|emacs)\b",
    re.I,
)

#: Anything that talks to a network, mutating or not. A reviewer verifies what
#: is on this machine; a reviewer that can fetch cannot be reproduced.
_NETWORK = re.compile(
    r"(?:^|[\s;&|])(?:curl|wget|http|httpie|nc|ncat|netcat|telnet|ssh|scp|sftp|"
    r"ftp|socat|openssl\s+s_client|gh|glab|hub)\b",
    re.I,
)

#: Installing software changes the machine the verification runs on, which
#: destroys the only property a deterministic re-run has.
_INSTALLERS = re.compile(
    r"(?:^|[\s;&|])(?:pip|pip3|pipx|uv|poetry|npm|npx|yarn|pnpm|bun|brew|port|"
    r"apt|apt-get|aptitude|yum|dnf|apk|pacman|gem|conda|mamba|asdf|nix)\b"
    r"|(?:^|[\s;&|])(?:cargo|go)\s+install\b"
    r"|(?:^|[\s;&|])playwright\s+install\b",
    re.I,
)

#: Privilege, machine security and system state.
_PRIVILEGE = re.compile(
    r"(?:^|[\s;&|])(?:sudo|su|doas|chsh|csrutil|spctl|security|launchctl|"
    r"systemctl|service|defaults|scutil|dscl|killall|kill|pkill|shutdown|"
    r"reboot|crontab|at)\b",
    re.I,
)

_HARD_DENIALS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_GIT_MUTATING, "a git command that changes a ref, the index, the working tree or a remote"),
    (_FILE_MUTATING, "a command whose job is to change the filesystem"),
    (_IN_PLACE_EDITORS, "an editor or stream editor that can write through its own syntax"),
    (_NETWORK, "a command that reaches the network"),
    (_INSTALLERS, "a command that installs software and changes the machine under the test"),
    (_PRIVILEGE, "a privilege, machine-security or process-control command"),
)

#: Redirection is a write with no command name attached to it.
_REDIRECTION = frozenset({">", "<"})


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewerCommandVerdict:
    """Whether a reviewer may run one command, and what decided it."""

    allowed: bool
    #: Which gate allowed it: "read-only verification" or "approved scenario
    #: command". Empty when refused.
    basis: str = ""
    #: Why it was refused, in one sentence. Empty when allowed.
    reason: str = ""
    #: Which layer refused: "guard", "reviewer-floor", "vocabulary",
    #: "composition". Empty when allowed.
    layer: str = ""

    def brief(self) -> str:
        if self.allowed:
            return f"allowed ({self.basis})"
        return f"refused [{self.layer}]: {self.reason}"


@dataclass
class ReviewerExecution:
    """One command the reviewer actually asked to run, and what happened."""

    command: str
    allowed: bool
    basis: str = ""
    reason: str = ""
    layer: str = ""
    exit_code: int | None = None
    #: True when the reviewer's own turn reported the command produced output.
    #: The driver never re-runs the command to check; it records the request and
    #: the decision, which is what the boundary is accountable for.
    observed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "allowed": self.allowed,
            "basis": self.basis,
            "reason": self.reason,
            "layer": self.layer,
            "exit_code": self.exit_code,
        }

    def brief(self) -> str:
        return f"{'ran' if self.allowed else 'REFUSED'}: {self.command[:160]}" + (
            f" — {self.reason}" if not self.allowed and self.reason else ""
        )


# --------------------------------------------------------------------------
# The policy
# --------------------------------------------------------------------------


class ReviewerCommandPolicy:
    """Decides whether an independent reviewer may execute one shell command.

    ``approved`` is the human-authored scenario vocabulary
    (:class:`~neyma_product_driver.scenario_validation.ApprovedCommands`). It is
    optional: without it the reviewer still gets the read-only verification
    vocabulary, it simply cannot run the repository's own probes and batteries.
    """

    def __init__(
        self,
        *,
        approved: Any = None,
        max_commands: int = 40,
        extra_read_only: Sequence[str] = (),
    ) -> None:
        self.approved = approved
        self.max_commands = max(0, int(max_commands))
        #: Additional read-only heads a human explicitly added for this repo.
        self.extra_read_only = tuple(
            h.strip() for h in extra_read_only if str(h).strip()
        )
        self.executions: list[ReviewerExecution] = []

    # -- reporting --------------------------------------------------------

    @property
    def allowed_count(self) -> int:
        return sum(1 for e in self.executions if e.allowed)

    @property
    def refused(self) -> list[ReviewerExecution]:
        return [e for e in self.executions if not e.allowed]

    def record(self, command: str, verdict: ReviewerCommandVerdict) -> ReviewerExecution:
        entry = ReviewerExecution(
            command=command[:1000],
            allowed=verdict.allowed,
            basis=verdict.basis,
            reason=verdict.reason,
            layer=verdict.layer,
        )
        self.executions.append(entry)
        return entry

    def vocabulary_block(self, limit: int = 24) -> str:
        """What the reviewer is told it may run. Rendered from the real set."""
        lines = [
            "READ-ONLY VERIFICATION COMMANDS (always available):",
            "  git diff / show / log / status / rev-parse / ls-files / ls-tree /",
            "      cat-file / blame / grep / merge-base   (read-only subcommands only)",
            "  grep, rg, ls, find, cat, head, tail, wc, sort, uniq, cut, diff, jq, stat",
            "  pytest / python -m pytest   (targeted or whole; it only reads and reports)",
        ]
        entries = list(getattr(self.approved, "entries", ()) or ())
        if entries:
            lines += [
                "",
                "DETERMINISTIC COMMANDS THIS REPOSITORY ALREADY DECLARES "
                f"({len(entries)} approved by a human writing them into a scenario file; "
                "argument tails are permitted, shell composition is not):",
            ]
            lines += [f"  {entry}" for entry in entries[:limit]]
            if len(entries) > limit:
                lines.append(f"  ... and {len(entries) - limit} more")
        else:
            lines += [
                "",
                "This repository declares no approved scenario commands, so no probe or",
                "battery is available to you beyond the read-only set above.",
            ]
        return "\n".join(lines)

    # -- the decision -----------------------------------------------------

    def decide(self, command: str) -> ReviewerCommandVerdict:
        """Both gates, in the order that fails fastest and most safely."""
        raw = str(command or "")
        if not raw.strip():
            return ReviewerCommandVerdict(
                False, reason="empty command", layer="composition"
            )

        if self.max_commands and self.allowed_count >= self.max_commands:
            return ReviewerCommandVerdict(
                False,
                reason=(
                    f"the reviewer's execution budget of {self.max_commands} command(s) is "
                    "spent; a review that needs more than that is describing a verification "
                    "gap, not performing a verification"
                ),
                layer="budget",
            )

        # -- gate (2), first half: Product Driver's own hard-block classifier.
        #    Same function the builder and the scenario executor go through, so
        #    a reviewer cannot reach anything they cannot.
        guard = classify_command(raw)
        if guard is not None:
            return ReviewerCommandVerdict(
                False, reason=f"hard-blocked by the command guard: {guard}", layer="guard"
            )
        ownership = classify_worktree_ownership(raw)
        if ownership is not None:
            return ReviewerCommandVerdict(
                False, reason=f"worktree-ownership violation: {ownership}", layer="guard"
            )

        # -- composition. A reviewer command is one command. Pipes, chains,
        #    substitutions and redirections all let an allowed head carry a
        #    disallowed payload, and the vocabulary check below only ever sees
        #    the head.
        operators, unbalanced = scan_shell_operators(raw)
        if unbalanced:
            return ReviewerCommandVerdict(
                False,
                reason="unbalanced quoting: what this would actually run cannot be determined",
                layer="composition",
            )
        if operators:
            found = sorted({op for _position, op in operators})
            return ReviewerCommandVerdict(
                False,
                reason=(
                    f"shell composition outside quotes ({', '.join(found)}); a reviewer "
                    "command is a single command with an argument tail, so that what ran "
                    "is what was judged"
                ),
                layer="composition",
            )

        # -- the reviewer's own floor, applied to EVERY command whatever
        #    approved it. An approved scenario command is verification a human
        #    wrote down; it is not a licence to commit, publish or install.
        floor = self._floor_reason(raw)
        if floor is not None:
            return ReviewerCommandVerdict(False, reason=floor, layer="reviewer-floor")

        # -- gate (1) + gate (2), second half. Either the command is a read-only
        #    verification action, or a human already approved it by writing it
        #    into this repository's scenario files. Both have now passed the
        #    guard, the composition rule and the reviewer floor.
        read_only = self._read_only_reason(raw)
        if read_only is not None:
            return ReviewerCommandVerdict(
                True, basis=f"read-only verification — {read_only}"
            )

        if self.approved is not None:
            ok, why = self.approved.approves(raw)
            if ok:
                return ReviewerCommandVerdict(
                    True, basis="a deterministic command this repository declares"
                )
            return ReviewerCommandVerdict(
                False,
                reason=(
                    "not a read-only verification command, and not in the approved "
                    f"scenario vocabulary: {why}"
                ),
                layer="vocabulary",
            )

        return ReviewerCommandVerdict(
            False,
            reason=(
                "not a read-only verification command, and this run has no approved "
                "scenario vocabulary to draw a deterministic command from"
            ),
            layer="vocabulary",
        )

    # -- gates ------------------------------------------------------------

    def _floor_reason(self, command: str) -> str | None:
        """The denials that apply however the command was approved."""
        for pattern, why in _HARD_DENIALS:
            match = pattern.search(command)
            if match:
                return f"{why} ({match.group(0).strip()!r})"

        # Redirection characters are also operators, so the composition scan
        # above has normally caught them. This catches the form where quoting
        # made them inert to that scan but a shell would still honour them.
        for token in _tokens(command):
            if any(token.startswith(ch) for ch in _REDIRECTION):
                return f"a redirection ({token!r}) is a write with no command name on it"
            if is_secret_path(token):
                return f"a secret or credential path ({token})"
            if is_protected_config_path(token):
                return f"a protected control surface ({token})"
        return None

    def _read_only_reason(self, command: str) -> str | None:
        """Why this is a read-only verification action, or ``None`` if it is not."""
        tokens = _tokens(command)
        if not tokens:
            return None
        head = os.path.basename(tokens[0])

        if head == "git":
            sub = next((t for t in tokens[1:] if not t.startswith("-")), "")
            if sub not in _GIT_READ_ONLY:
                return None
            if _GIT_WRITING_OPTIONS.search(command):
                return None
            return f"git {sub}"

        if _PYTHON_HEADS.match(head):
            # Only `python -m pytest`. `-c`, `-` and a bare script are arbitrary
            # code, and arbitrary code is what the approved vocabulary is for.
            rest = tokens[1:]
            if len(rest) >= 2 and rest[0] == "-m" and rest[1] in {"pytest", "unittest"}:
                return f"python -m {rest[1]}"
            return None

        spec = _READ_ONLY_HEADS.get(head)
        if spec is not None:
            forbidden, why = spec
            if forbidden is not None and forbidden.search(command):
                return None
            return why

        if head in self.extra_read_only:
            return f"{head} (added to the read-only set by this repository's configuration)"

        return None


def _tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, comments=False, posix=True)
    except ValueError:
        return []


# --------------------------------------------------------------------------
# Tool-level boundary
# --------------------------------------------------------------------------

#: Tools an independent reviewer may use at all. Everything else is refused
#: before its input is even looked at.
REVIEWER_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob")

#: Tools that would let a reviewer change the product. Named explicitly so the
#: refusal message can say which one, and so the set is greppable.
REVIEWER_FORBIDDEN_TOOLS: tuple[str, ...] = (
    "Write", "Edit", "MultiEdit", "NotebookEdit", "NotebookWrite",
    "WebFetch", "WebSearch", "Task", "Agent", "SlashCommand",
)


def classify_reviewer_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    policy: ReviewerCommandPolicy | None,
) -> ReviewerCommandVerdict:
    """The whole reviewer boundary for one tool use.

    ``Bash`` is decided by ``policy`` — and is refused outright when there is
    none, so switching reviewer execution off never widens what a reviewer can
    do. Every other tool is decided by membership: a reviewer reads, greps and
    globs, and does nothing else.
    """
    name = str(tool_name or "")
    payload = tool_input if isinstance(tool_input, dict) else {}

    if name == "Bash":
        if policy is None:
            return ReviewerCommandVerdict(
                False,
                reason=(
                    "this reviewer is strictly read-only in this run — command execution "
                    "is not enabled, so it may not run any command"
                ),
                layer="tool",
            )
        return policy.decide(str(payload.get("command", "")))

    if name in REVIEWER_FORBIDDEN_TOOLS:
        return ReviewerCommandVerdict(
            False,
            reason=(
                f"{name} would let the reviewer change the product or reach outside it; "
                "an independent reviewer adjudicates, it does not implement"
            ),
            layer="tool",
        )

    if name in REVIEWER_TOOLS:
        path = str(payload.get("file_path") or payload.get("path") or "")
        if path and is_secret_path(path):
            return ReviewerCommandVerdict(
                False, reason=f"read of a secret/credential path ({path})", layer="path"
            )
        return ReviewerCommandVerdict(True, basis=f"{name} is read-only")

    return ReviewerCommandVerdict(
        False,
        reason=(
            f"{name} is not part of the independent reviewer's tool set "
            f"({', '.join(REVIEWER_TOOLS)}, and Bash under the command policy)"
        ),
        layer="tool",
    )
