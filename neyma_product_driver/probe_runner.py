"""Probe execution and evidence collection.

A probe is the smallest action that would move a diagnosis. This module runs
them safely: read-only by default, timed out, and refused outright when the
action is consequential (a history rewrite, a push, a production write). The
investigator proposes probes; the runner is the one place that decides whether a
probe is allowed to touch anything.

It also does the opposite of a probe — bulk evidence *collection*: gathering the
git state, receipts, run artifacts, prior claims and status surfaces into
observations, so the investigator starts from primary evidence rather than prose.

Nothing here writes to the Neyma repository. The only files it creates are
investigation scratch artifacts under the run directory.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from .investigation_memory import (
    CLAIM_SIGNAL,
    Observation,
    ObservationKind,
    Probe,
    ProbeResult,
    Reliability,
)
from .models import redact

# Verbs that mutate history, push, or discard work. A probe naming one of these
# is never executed autonomously; it becomes an ASK FOUNDER. Shared with the
# remediation planner's own blocklist in spirit, restated here so the runner is
# self-contained.
CONSEQUENTIAL_PATTERNS = (
    r"\bgit\s+push\b",
    r"\bpush\s+--?force\b|\bpush\s+-f\b",
    r"\bgit\s+reset\b",
    r"\bgit\s+rebase\b",
    r"\bgit\s+commit\b",
    r"\bgit\s+merge\b",
    r"\bgit\s+cherry-pick\b",
    r"\bgit\s+revert\b",
    r"\bgit\s+(?:branch|tag)\s+-[dD]\b",
    r"\bgit\s+update-ref\b",
    r"\bgit\s+filter-branch\b",
    r"\bcommit\s+--amend\b",
    r"\bgit\s+clean\b",
    r"\bgit\s+stash\b",
    r"\bgit\s+checkout\s+--\b|\bgit\s+restore\b",
    r"\bgit\s+gc\b|\breflog\s+expire\b",
    r"\brm\s+-rf?\b",
    r"\bcurl\b[^|]*-X\s*(?:POST|PUT|DELETE|PATCH)\b",
    r"\bwget\b[^|]*--method=(?:POST|PUT|DELETE)\b",
    r"\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE)\b.*\b(?:INTO|TABLE|FROM|SET)\b",
    r"\b(?:mail|sendmail|slack|twilio|ses|sns)\b",
)

# Shell metacharacters that would let a "read-only" command do more than read.
_SHELL_INJECTION_RE = re.compile(r"[;&|`$><]|\|\||&&")

_CONSEQUENTIAL_RE = re.compile("|".join(CONSEQUENTIAL_PATTERNS), re.I)

# A COMMAND probe may invoke only these programs, and — for git — only these
# subcommands. A denylist of mutating verbs is a losing game (git alone has
# checkout, switch, apply, rm, mv, config, worktree, notes…), so the runner
# allowlists observation instead. Anything not named here is refused, which is
# the safe default: an unrecognized tool does not run.
_READONLY_GIT_SUBCOMMANDS = frozenset(
    {
        "status", "log", "show", "diff", "rev-parse", "rev-list", "cat-file",
        "ls-files", "ls-tree", "branch", "tag", "for-each-ref", "merge-base",
        "describe", "shortlog", "blame", "grep", "count-objects", "reflog",
        "name-rev", "symbolic-ref", "whatchanged", "cherry", "verify-commit",
    }
)
# reflog/branch/tag can mutate with the right flags; those flag forms are caught
# by CONSEQUENTIAL_PATTERNS, so the plain read forms remain allowed here.
_READONLY_TOOLS = frozenset(
    {
        "cat", "head", "tail", "wc", "ls", "find", "grep", "rg", "sort", "uniq",
        "python", "python3", "pytest", "ruff", "mypy", "flake8", "pyflakes",
        "sqlite3", "jq", "yq", "stat", "file", "sha256sum", "md5sum", "test",
        "echo", "true", "false", "env", "printenv", "which", "type",
    }
)

REGISTRY_REL = "docs/implementation/IMPLEMENTATION-REGISTRY.yaml"
BUILD_STATUS_REL = "docs/implementation/BUILD-STATUS.yaml"
SUITE_RESULT_REL = "docs/implementation/SUITE-RESULT.json"
GATE_RESULT_REL = "docs/implementation/GATE-RESULT.json"


def is_consequential(command_or_action: str) -> bool:
    return bool(_CONSEQUENTIAL_RE.search(command_or_action or ""))


def _command_is_readonly(argv: list[str]) -> tuple[bool, str]:
    """Allowlist the program (and, for git, the subcommand). Refuse everything else.

    A denylist of mutating verbs will always miss one; the runner instead names
    the tools it knows only observe, and refuses anything it does not recognize.
    """
    if not argv:
        return False, "empty command"
    tool = Path(argv[0]).name
    if tool == "git":
        sub = next((a for a in argv[1:] if not a.startswith("-")), "")
        if sub not in _READONLY_GIT_SUBCOMMANDS:
            return False, (
                f"git subcommand {sub!r} is not on the read-only allowlist; refused"
            )
        return True, ""
    if tool in _READONLY_TOOLS:
        return True, ""
    return False, (
        f"{tool!r} is not on the investigator's read-only command allowlist; refused. "
        "Use a PREDICATE probe for in-memory checks, or a built-in probe kind."
    )


def _within_repo(repo: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``repo``, or None if it would escape.

    A probe reads inside the repository it is investigating. An absolute path or
    a ``..`` traversal is refused rather than silently reading, say, /etc/passwd.
    """
    try:
        candidate = (repo / rel).resolve()
        repo_resolved = repo.resolve()
    except (OSError, ValueError):
        return None
    if candidate == repo_resolved or repo_resolved in candidate.parents:
        return candidate
    return None


# --------------------------------------------------------------------------
# Predicate registry — pure, in-memory probes
# --------------------------------------------------------------------------
#
# A predicate is a named pure function the investigator can run without touching
# the repository at all: "does this guard reject this fixture?", "does this
# parser accept this input?". Tests register their own; the investigator can run
# a temporary in-memory mutation probe this way.

PredicateFn = Callable[[dict[str, Any]], dict[str, str]]
_PREDICATES: dict[str, PredicateFn] = {}


def register_predicate(name: str, fn: PredicateFn) -> None:
    _PREDICATES[name] = fn


def clear_predicates() -> None:
    _PREDICATES.clear()


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------


class ProbeRunner:
    """Executes one probe safely and turns its output into signals."""

    def __init__(self, repo: Path, scratch_dir: Path | None = None) -> None:
        self.repo = Path(repo)
        self.scratch_dir = Path(scratch_dir) if scratch_dir else None

    # -- git ---------------------------------------------------------------

    def _git(self, *args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(self.repo), capture_output=True, text=True, timeout=30, check=False
            )
            return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def repo_fingerprint(self) -> str:
        return f"{self._git('rev-parse', 'HEAD')}:{self._git('status', '--porcelain')}"

    # -- dispatch ----------------------------------------------------------

    def run(self, probe: Probe) -> ProbeResult:
        """Run one probe. Never raises; refuses anything consequential."""
        result = ProbeResult(probe_id=probe.id)

        if not probe.read_only or is_consequential(probe.command_or_action):
            result.refused = True
            result.refusal_reason = (
                "probe is not read-only or names a consequential action; "
                "the investigator will not run it autonomously"
            )
            return result

        start = time.monotonic()
        try:
            handler = {
                "COMMAND": self._run_command,
                "SHELL": self._run_command,
                "PREDICATE": self._run_predicate,
                "CLASSIFY_COMMITS": self._run_classify_commits,
                "CHECK_RECEIPT": self._run_check_receipt,
                "FILE_STAT": self._run_file_stat,
                "PROTOCOL_RESOLVE": self._run_protocol_resolve,
                "SQL_READONLY": self._run_sql_readonly,
                "HTTP_GET": self._run_http_get,
                "GREP": self._run_grep,
            }.get(probe.kind.upper(), self._unknown_kind)
            handler(probe, result)
        except Exception as exc:  # a probe must never crash the investigation
            result.error = f"{type(exc).__name__}: {redact(str(exc))}"
        result.duration_s = round(time.monotonic() - start, 3)

        # Interpretation rules extract further signals from raw output. They run
        # for every kind, so a COMMAND probe becomes as machine-readable as a
        # built-in one.
        self._apply_rules(probe, result)
        return result

    def _unknown_kind(self, probe: Probe, result: ProbeResult) -> None:
        result.error = f"unknown probe kind: {probe.kind}"

    # -- kinds -------------------------------------------------------------

    def _run_command(self, probe: Probe, result: ProbeResult) -> None:
        command = probe.command_or_action
        if _SHELL_INJECTION_RE.search(command):
            result.refused = True
            result.refusal_reason = "command contains shell metacharacters; refused as unsafe to run read-only"
            return
        argv = command.split()
        allowed, why = _command_is_readonly(argv)
        if not allowed:
            result.refused = True
            result.refusal_reason = why
            return
        try:
            proc = subprocess.run(
                argv, cwd=str(self.repo), capture_output=True, text=True, timeout=probe.timeout, check=False
            )
            result.ran = True
            result.exit_code = proc.returncode
            result.output = redact((proc.stdout + proc.stderr).strip())[:20000]
            result.signals["exit_code"] = str(proc.returncode)
        except subprocess.TimeoutExpired:
            result.error = f"timed out after {probe.timeout}s"
            result.signals["timed_out"] = "true"
        except (OSError, ValueError) as exc:
            result.error = f"{type(exc).__name__}: {redact(str(exc))}"

    def _run_grep(self, probe: Probe, result: ProbeResult) -> None:
        pattern = str(probe.inputs.get("pattern", probe.command_or_action))
        path = _within_repo(self.repo, str(probe.inputs.get("path", ".")))
        if path is None:
            result.refused = True
            result.refusal_reason = "path escapes the repository; refused"
            return
        matches = 0
        sample = ""
        try:
            targets = [path] if path.is_file() else path.rglob("*")
            regex = re.compile(pattern)
            for p in targets:
                if not p.is_file() or ".git" in p.parts:
                    continue
                try:
                    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                        if regex.search(line):
                            matches += 1
                            if not sample:
                                sample = f"{p.relative_to(self.repo)}: {line.strip()[:120]}"
                except OSError:
                    continue
                if matches > 500:
                    break
        except (re.error, ValueError) as exc:
            result.error = str(exc)
            return
        result.ran = True
        result.output = redact(sample)
        result.signals["match_count"] = str(matches)
        result.signals["matched"] = "true" if matches else "false"

    def _run_predicate(self, probe: Probe, result: ProbeResult) -> None:
        fn = _PREDICATES.get(probe.command_or_action)
        if fn is None:
            result.error = f"no registered predicate {probe.command_or_action!r}"
            return
        signals = fn(dict(probe.inputs))
        result.ran = True
        result.signals.update({k: str(v) for k, v in signals.items()})

    def _run_classify_commits(self, probe: Probe, result: ProbeResult) -> None:
        from .git_topology import GitTopologyAnalyzer
        from .protocol_sources import discover_protocol

        protocol = discover_protocol(self.repo)
        topology = GitTopologyAnalyzer(self.repo, protocol).analyze(
            baseline=probe.inputs.get("baseline")
        )
        result.ran = True
        result.signals["content_commit_count"] = str(len(topology.content_commits))
        result.signals["commit_count"] = str(len(topology.commits))
        result.signals["baseline_resolved"] = "true" if topology.baseline_commit else "false"
        from .protocol_sources import RuleKind

        topo_rule = protocol.states(RuleKind.COMMIT_TOPOLOGY, "max_content_commits")
        if topo_rule is not None:
            allowed = int(topo_rule.parameters["max_content_commits"])
            result.signals["allowed_content_commits"] = str(allowed)
            result.signals["topology_valid"] = str(len(topology.content_commits) <= allowed).lower()
        result.output = topology.render_graph()

    def _run_check_receipt(self, probe: Probe, result: ProbeResult) -> None:
        rel = str(probe.inputs.get("path", SUITE_RESULT_REL))
        path = _within_repo(self.repo, rel)
        if path is None:
            result.refused = True
            result.refusal_reason = "receipt path escapes the repository; refused"
            return
        head_tree = self._git("rev-parse", "HEAD^{tree}")
        head = self._git("rev-parse", "HEAD")
        if not path.exists():
            result.ran = True
            result.signals["receipt_exists"] = "false"
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result.error = "receipt is not readable JSON"
            return
        rc = str(data.get("commit", ""))
        rt = str(data.get("tree", ""))
        fresh = _prefix_match(rc, head) or _prefix_match(rt, head_tree)
        result.ran = True
        result.signals["receipt_exists"] = "true"
        result.signals["receipt_fresh"] = str(fresh).lower()
        result.signals["receipt_commit"] = rc[:12]
        result.signals["head_commit"] = head[:12]
        result.output = f"receipt names {rc[:12]} / {rt[:12]}; HEAD is {head[:12]} / {head_tree[:12]}"

    def _run_file_stat(self, probe: Probe, result: ProbeResult) -> None:
        rel = str(probe.inputs.get("path", probe.command_or_action))
        path = _within_repo(self.repo, rel)
        if path is None:
            result.refused = True
            result.refusal_reason = "path escapes the repository; refused"
            return
        result.ran = True
        result.signals["exists"] = str(path.exists()).lower()
        if path.exists() and path.is_file():
            import hashlib

            data = path.read_bytes()
            result.signals["sha256"] = hashlib.sha256(data).hexdigest()[:16]
            result.signals["size"] = str(len(data))

    def _run_protocol_resolve(self, probe: Probe, result: ProbeResult) -> None:
        from .protocol_resolver import ProtocolResolver

        resolution = ProtocolResolver(self.repo).resolve(baseline=probe.inputs.get("baseline"))
        result.ran = True
        result.signals["protocol_status"] = resolution.status.value
        result.signals["deadlock_count"] = str(len(resolution.deadlocks))
        result.signals["violation_count"] = str(len(resolution.violations))
        result.output = resolution.summary_block()

    def _run_sql_readonly(self, probe: Probe, result: ProbeResult) -> None:
        import sqlite3

        query = probe.command_or_action.strip()
        if not re.match(r"(?is)^\s*(select|pragma|explain|with)\b", query):
            result.refused = True
            result.refusal_reason = "only SELECT/PRAGMA/EXPLAIN queries are permitted read-only"
            return
        db = probe.inputs.get("db")
        if not db:
            result.error = "no db path provided in probe.inputs['db']"
            return
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            rows = con.execute(query).fetchall()
            con.close()
        except sqlite3.Error as exc:
            result.error = f"sqlite: {exc}"
            return
        result.ran = True
        result.signals["row_count"] = str(len(rows))
        if rows and len(rows[0]) == 1:
            result.signals["value"] = str(rows[0][0])
        result.output = redact(json.dumps(rows[:20], default=str))

    def _run_http_get(self, probe: Probe, result: ProbeResult) -> None:
        import urllib.error
        import urllib.request

        url = probe.command_or_action.strip()
        if not re.match(r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])[:/]", url):
            result.refused = True
            result.refusal_reason = "only local http(s) GETs are permitted; refused a non-local URL"
            return
        try:
            with urllib.request.urlopen(url, timeout=probe.timeout) as resp:  # noqa: S310 (local only)
                body = resp.read(200_000).decode("utf-8", errors="replace")
                result.signals["http_status"] = str(resp.status)
        except urllib.error.HTTPError as exc:
            result.signals["http_status"] = str(exc.code)
            body = ""
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {redact(str(exc))}"
            return
        result.ran = True
        result.output = redact(body)[:20000]

    # -- interpretation ----------------------------------------------------

    def _apply_rules(self, probe: Probe, result: ProbeResult) -> None:
        """Extract further signals from raw output via the probe's own rules."""
        haystack = result.output or ""
        for rule in probe.interpretation_rules:
            try:
                m = re.search(rule.pattern, haystack, re.I | re.M)
            except re.error:
                continue
            if not m:
                # Record absence explicitly when a rule names a boolean signal,
                # so "not present" is a fact the engine can use.
                result.signals.setdefault(f"{rule.signal}_present", "false")
                continue
            if rule.value == "$1" and m.groups():
                value = m.group(1)
            elif rule.value.startswith("$") and rule.value[1:].isdigit() and m.groups():
                idx = int(rule.value[1:])
                value = m.group(idx) if idx <= len(m.groups()) else m.group(0)
            else:
                value = rule.value or "true"
            result.signals[rule.signal] = str(value)
            result.signals[f"{rule.signal}_present"] = "true"


def _prefix_match(a: str, b: str) -> bool:
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return False
    n = min(len(a), len(b))
    return n >= 7 and a[:n] == b[:n]


# --------------------------------------------------------------------------
# Evidence collection — start from primary evidence, not prose
# --------------------------------------------------------------------------


class EvidenceCollector:
    """Gathers the current repository and run state into observations.

    Re-run before every investigation step so the investigator never reasons
    over a stale snapshot. It records claims (builder/reviewer) as REPORTED, and
    everything it verifies itself as DIRECT — the distinction the engine relies
    on to keep prose from ever standing in for proof.
    """

    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)

    def _git(self, *args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(self.repo), capture_output=True, text=True, timeout=30, check=False
            )
            return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def _read_json(self, rel: str) -> dict[str, Any] | None:
        path = self.repo / rel
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def collect(
        self,
        *,
        iteration: int = 0,
        builder_report: str = "",
        reviewer_report: str = "",
        extra: list[Observation] | None = None,
    ) -> list[Observation]:
        """Fresh observations from git, receipts, status and any supplied claims."""
        obs: list[Observation] = []
        n = [0]

        def add(**kw: Any) -> None:
            n[0] += 1
            obs.append(Observation(id=f"O{iteration}.{n[0]}", iteration=iteration, **kw))

        # -- git state (DIRECT) --------------------------------------------
        head = self._git("rev-parse", "HEAD")
        branch = self._git("branch", "--show-current")
        status = [ln for ln in self._git("status", "--porcelain").splitlines() if ln.strip()]
        dirty = len([ln for ln in status if not ln.startswith("??")])
        untracked = len([ln for ln in status if ln.startswith("??")])
        add(
            source="git",
            kind=ObservationKind.GIT_STATE,
            reliability=Reliability.DIRECT,
            content=f"HEAD {head[:12]} on {branch or '(detached)'}, {dirty} modified, {untracked} untracked",
            signals={
                "head": head[:12],
                "branch": branch,
                "dirty_files": str(dirty),
                "untracked_files": str(untracked),
            },
        )

        # -- suite receipt (DIRECT) ----------------------------------------
        suite = self._read_json(SUITE_RESULT_REL)
        if suite is not None:
            failed = _as_int(suite.get("failed"), 1)
            exit_status = _as_int(suite.get("exit_status"), 1)
            add(
                source=SUITE_RESULT_REL,
                kind=ObservationKind.TEST_RESULT,
                reliability=Reliability.DIRECT,
                evidence_path=SUITE_RESULT_REL,
                content=f"canonical suite receipt: exit={exit_status}, failed={failed}, passed={suite.get('passed')}",
                signals={
                    "suite_exit": str(exit_status),
                    "suite_failed": str(failed),
                    "suite_green": str(exit_status == 0 and failed == 0).lower(),
                },
            )
            for node in (suite.get("failed_nodes") or [])[:8]:
                add(
                    source=SUITE_RESULT_REL,
                    kind=ObservationKind.TEST_RESULT,
                    reliability=Reliability.DIRECT,
                    evidence_path=SUITE_RESULT_REL,
                    content=f"failing node: {node}",
                    signals={"failing_node": str(node)[:80]},
                )

        gate = self._read_json(GATE_RESULT_REL)
        if gate is not None:
            add(
                source=GATE_RESULT_REL,
                kind=ObservationKind.COMMAND_RESULT,
                reliability=Reliability.DIRECT,
                evidence_path=GATE_RESULT_REL,
                content=f"clean-clone gate receipt: passed={gate.get('passed')}",
                signals={"clean_clone_passed": str(bool(gate.get("passed"))).lower()},
            )

        # -- build status (DERIVED; it is generated) -----------------------
        try:
            build = yaml.safe_load((self.repo / BUILD_STATUS_REL).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError, ValueError):
            build = None
        if isinstance(build, dict):
            snap = build.get("snapshot", {}) if isinstance(build.get("snapshot"), dict) else {}
            fin = str(snap.get("finalizer_result", "") or "")
            if fin:
                add(
                    source=BUILD_STATUS_REL,
                    kind=ObservationKind.FILE_CONTENT,
                    reliability=Reliability.DERIVED,
                    evidence_path=BUILD_STATUS_REL,
                    content=f"BUILD-STATUS finalizer_result: {fin[:120]}",
                    signals={"finalizer_reported": _finalizer_signal(fin)},
                )

        # -- claims (REPORTED; never proof) --------------------------------
        if builder_report.strip():
            add(
                source="builder report",
                kind=ObservationKind.BUILDER_CLAIM,
                reliability=Reliability.REPORTED,
                content=f"builder claims: {builder_report.strip()[:300]}",
                signals={CLAIM_SIGNAL: _claim_signal(builder_report)},
            )
        if reviewer_report.strip():
            add(
                source="reviewer report",
                kind=ObservationKind.REVIEWER_CLAIM,
                reliability=Reliability.REPORTED,
                content=f"reviewer claims: {reviewer_report.strip()[:300]}",
                signals={},
            )

        obs.extend(extra or [])
        return obs

    def environment(self, names: list[str]) -> Observation:
        """Report named environment variables by presence, never by value."""
        present = {name: ("set" if os.environ.get(name) else "unset") for name in names}
        return Observation(
            source="environment",
            kind=ObservationKind.ENVIRONMENT,
            reliability=Reliability.DIRECT,
            content="env: " + ", ".join(f"{k}={v}" for k, v in present.items()),
            signals={f"env_{k}": v for k, v in present.items()},
        )


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _finalizer_signal(text: str) -> str:
    if re.search(r"REFUS|DECLINE|WILL NOT", text, re.I):
        return "refused"
    if re.search(r"NOT\s+EXECUTED|NOT\s+RUN|PENDING", text, re.I):
        return "not_run"
    if re.search(r"FAIL|ERROR", text, re.I):
        return "failed"
    return "executed"


def _claim_signal(text: str) -> str:
    """A coarse label for what a claim is about, so a probe can contradict it."""
    t = text.lower()
    if "socket" in t or "bind" in t:
        return "socket"
    if "complete" in t:
        return "complete"
    if "finaliz" in t:
        return "finalizer"
    return "other"
