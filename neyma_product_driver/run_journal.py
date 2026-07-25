"""The run journal, and the founder summary derived from it.

Broad autonomy is only defensible if what happened is fully visible afterwards.
This driver's answer to "should the Driver ask first?" is *no* — and the price
of that answer is that every run must be reconstructable from its evidence
without reading a transcript.

The journal records, for one run:

* the starting repository, branch, HEAD, tree and full working-tree status;
* the active unit and its acceptance criteria;
* builder and evaluator session ids;
* every tool use, every shell command, exit codes and timeouts;
* files created, modified, moved and deleted;
* authority files changed;
* local commits created, and any local-history change with its recovery point;
* preservation refs and bundles;
* test and scenario results;
* denied operations and external-boundary attempts;
* the ending branch, HEAD, tree and working-tree state;
* the exact stop reason, and the next safe action.

:meth:`RunJournal.founder_summary` renders the ten questions the founder asked
to have answered without opening a log.

Everything written here goes through the same redactor as the rest of the
evidence store, and journalling is best-effort by construction: a failure to
record must never be able to fail a run.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .authority import AuthorityChange, render_authority_section
from .models import redact
from .preservation import GitIdentity, capture_identity

JOURNAL_FILE = "journal.json"
SUMMARY_FILE = "FOUNDER-SUMMARY.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Entries
# --------------------------------------------------------------------------


@dataclass
class ToolUseEntry:
    at: str
    tool: str
    detail: str
    denied_reason: str | None = None
    layer: str = ""


@dataclass
class CommandEntry:
    at: str
    command: str
    exit_code: int | None = None
    timed_out: bool = False
    duration_s: float | None = None
    source: str = ""  # "scenario" | "builder" | "driver"


@dataclass
class FileChangeEntry:
    path: str
    change: str  # "created" | "modified" | "deleted" | "renamed"
    renamed_from: str = ""


@dataclass
class CommitEntry:
    at: str
    sha: str
    subject: str
    branch: str
    tree: str


@dataclass
class HistoryChangeEntry:
    at: str
    what: str
    authorized: bool
    recovery_ref: str = ""
    recovery_bundle: str = ""
    verified: bool | None = None
    detail: str = ""


@dataclass
class ExternalAttemptEntry:
    at: str
    tool: str
    detail: str
    reason: str
    layer: str = ""


# --------------------------------------------------------------------------
# The journal
# --------------------------------------------------------------------------


@dataclass
class RunJournal:
    """Everything one run did, in the order it did it."""

    run_id: str = ""
    task: str = ""
    repo: str = ""

    start_identity: GitIdentity | None = None
    end_identity: GitIdentity | None = None

    active_unit_id: str = ""
    active_unit_status: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)

    builder_session_id: str = ""
    evaluator_session_id: str = ""

    approved_roots: list[str] = field(default_factory=list)

    tool_uses: list[ToolUseEntry] = field(default_factory=list)
    commands: list[CommandEntry] = field(default_factory=list)
    file_changes: list[FileChangeEntry] = field(default_factory=list)
    commits: list[CommitEntry] = field(default_factory=list)
    history_changes: list[HistoryChangeEntry] = field(default_factory=list)
    preservation: list[dict[str, Any]] = field(default_factory=list)
    authority_report: dict[str, Any] = field(default_factory=dict)
    denied_paths: list[dict[str, str]] = field(default_factory=list)
    external_attempts: list[ExternalAttemptEntry] = field(default_factory=list)
    test_results: list[dict[str, Any]] = field(default_factory=list)
    scenario_results: list[dict[str, Any]] = field(default_factory=list)

    stop_reason: str = ""
    next_safe_action: str = ""
    founder_decision_required: str = ""
    incomplete: list[str] = field(default_factory=list)

    started_at: str = field(default_factory=_now)
    ended_at: str = ""

    # -- lifecycle --------------------------------------------------------

    def record_start(self, repo: Path) -> GitIdentity:
        """Capture the starting git identity. Read-only."""
        self.repo = str(repo)
        self.start_identity = capture_identity(Path(repo))
        return self.start_identity

    def record_end(self, repo: Path | None = None) -> GitIdentity:
        """Capture the ending git identity. Read-only."""
        target = Path(repo) if repo is not None else Path(self.repo)
        self.end_identity = capture_identity(target)
        self.ended_at = _now()
        return self.end_identity

    # -- recording --------------------------------------------------------

    def record_tool_use(
        self,
        tool: str,
        detail: str = "",
        denied_reason: str | None = None,
        layer: str = "",
    ) -> None:
        entry = ToolUseEntry(_now(), tool, redact(detail)[:500],
                             redact(denied_reason) if denied_reason else None, layer)
        self.tool_uses.append(entry)
        if denied_reason and _is_external_boundary(denied_reason):
            self.external_attempts.append(
                ExternalAttemptEntry(entry.at, tool, entry.detail, entry.denied_reason or "", layer)
            )

    def record_command(
        self,
        command: str,
        exit_code: int | None = None,
        timed_out: bool = False,
        duration_s: float | None = None,
        source: str = "",
    ) -> None:
        self.commands.append(
            CommandEntry(_now(), redact(command)[:1000], exit_code, timed_out, duration_s, source)
        )

    def record_file_change(self, path: str, change: str, renamed_from: str = "") -> None:
        self.file_changes.append(FileChangeEntry(str(path), change, renamed_from))

    def record_denied_path(self, path: str, reason: str) -> None:
        self.denied_paths.append({"path": str(path), "reason": redact(reason)})

    def record_commit(self, sha: str, subject: str, branch: str = "", tree: str = "") -> None:
        self.commits.append(CommitEntry(_now(), sha, redact(subject)[:300], branch, tree))

    def record_history_change(
        self,
        what: str,
        authorized: bool,
        recovery_ref: str = "",
        recovery_bundle: str = "",
        verified: bool | None = None,
        detail: str = "",
    ) -> None:
        self.history_changes.append(
            HistoryChangeEntry(_now(), what, authorized, recovery_ref,
                               recovery_bundle, verified, redact(detail))
        )

    def record_preservation(self, record: Any) -> None:
        self.preservation.append(record.to_dict() if hasattr(record, "to_dict") else dict(record))

    def record_authority(self, report: dict[str, Any]) -> None:
        self.authority_report = report

    def record_test_result(self, name: str, passed: bool, detail: str = "") -> None:
        self.test_results.append({"name": name, "passed": passed, "detail": redact(detail)[:2000]})

    def record_scenario_result(self, name: str, passed: bool, detail: str = "") -> None:
        self.scenario_results.append(
            {"name": name, "passed": passed, "detail": redact(detail)[:2000]}
        )

    def record_stop(
        self, reason: str, next_safe_action: str = "", founder_decision_required: str = ""
    ) -> None:
        self.stop_reason = redact(reason)
        self.next_safe_action = redact(next_safe_action)
        # A caller that says "none" means there is no decision — it must not
        # read as a decision named "none", which would then suppress the
        # weakened-controls escalation below it.
        if founder_decision_required.strip().lower() in {"", "none", "n/a", "no", "-"}:
            self.founder_decision_required = ""
        else:
            self.founder_decision_required = redact(founder_decision_required)

    # -- derived ----------------------------------------------------------

    @property
    def authority_changes_present(self) -> bool:
        return bool(self.authority_report.get("changed"))

    @property
    def controls_weakened(self) -> bool:
        return bool(self.authority_report.get("weakening_detected"))

    @property
    def recovery_points(self) -> list[str]:
        points: list[str] = []
        for record in self.preservation:
            if record.get("ref"):
                points.append(f"ref {record['ref']} -> {record.get('ref_target', '')[:12]}")
            if record.get("bundle_path"):
                points.append(f"bundle {record['bundle_path']}")
        return points

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task": redact(self.task),
            "repo": self.repo,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "start": self.start_identity.to_dict() if self.start_identity else None,
            "end": self.end_identity.to_dict() if self.end_identity else None,
            "active_unit": {
                "id": self.active_unit_id,
                "status": self.active_unit_status,
                "acceptance_criteria": list(self.acceptance_criteria),
            },
            "sessions": {
                "builder": self.builder_session_id,
                "evaluator": self.evaluator_session_id,
            },
            "approved_roots": list(self.approved_roots),
            "tool_uses": [asdict(t) for t in self.tool_uses],
            "commands": [asdict(c) for c in self.commands],
            "file_changes": [asdict(f) for f in self.file_changes],
            "commits": [asdict(c) for c in self.commits],
            "history_changes": [asdict(h) for h in self.history_changes],
            "preservation": list(self.preservation),
            "authority": self.authority_report,
            "denied_paths": list(self.denied_paths),
            "denied_operations": [
                asdict(t) for t in self.tool_uses if t.denied_reason
            ],
            "external_boundary_attempts": [asdict(e) for e in self.external_attempts],
            "test_results": list(self.test_results),
            "scenario_results": list(self.scenario_results),
            "stop_reason": self.stop_reason,
            "next_safe_action": self.next_safe_action,
            "founder_decision_required": self.founder_decision_required,
            "incomplete": list(self.incomplete),
        }

    # -- persistence ------------------------------------------------------

    def save(self, run_dir: Path) -> Path:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / JOURNAL_FILE
        path.write_text(
            json.dumps(self.to_dict(), indent=2, default=str, ensure_ascii=False),
            encoding="utf-8",
        )
        (run_dir / SUMMARY_FILE).write_text(self.founder_summary(), encoding="utf-8")
        return path

    # -- the founder summary ---------------------------------------------

    def founder_summary(self) -> str:
        """Answer the ten questions, without requiring the raw logs.

        Ordered so the two that most often need action — an authority change and
        a local-history change — are impossible to scroll past.
        """
        start, end = self.start_identity, self.end_identity
        lines: list[str] = [
            "# Founder summary",
            "",
            f"Run `{self.run_id or '(unnamed)'}`  ·  started {self.started_at}"
            f"{'  ·  ended ' + self.ended_at if self.ended_at else '  ·  (not finished)'}",
            "",
            "## 1. What did the Driver work on?",
            "",
            f"- Repository: `{self.repo or '(unrecorded)'}`",
            f"- Task: {redact(self.task) or '(none recorded)'}",
            f"- Active unit: {self.active_unit_id or '(none resolved)'}"
            f"{f' ({self.active_unit_status})' if self.active_unit_status else ''}",
        ]
        if self.acceptance_criteria:
            lines.append(f"- Acceptance criteria in force: {len(self.acceptance_criteria)}")
            lines.extend(f"    - {c}" for c in self.acceptance_criteria[:20])

        lines += ["", "## 2. What changed?", ""]
        if self.file_changes:
            counts: dict[str, int] = {}
            for change in self.file_changes:
                counts[change.change] = counts.get(change.change, 0) + 1
            lines.append("- Files: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
            for change in self.file_changes[:40]:
                suffix = f" (from {change.renamed_from})" if change.renamed_from else ""
                lines.append(f"    - {change.change}: {change.path}{suffix}")
            if len(self.file_changes) > 40:
                lines.append(f"    - ... and {len(self.file_changes) - 40} more")
        else:
            lines.append("- No file changes were recorded.")

        if self.commits:
            lines.append(f"- Local commits created: {len(self.commits)}")
            lines.extend(f"    - {c.sha[:12]} {c.subject}" for c in self.commits)
        else:
            lines.append("- No local commits were created.")

        lines += ["", "## 3. What evidence proves it?", ""]
        if self.test_results or self.scenario_results:
            for result in self.test_results:
                lines.append(f"- test `{result['name']}`: "
                             f"{'PASS' if result['passed'] else 'FAIL'}")
            for result in self.scenario_results:
                lines.append(f"- scenario `{result['name']}`: "
                             f"{'PASS' if result['passed'] else 'FAIL'}")
        else:
            lines.append("- No test or scenario results were recorded.")
        lines.append(f"- Tool uses recorded: {len(self.tool_uses)}; "
                     f"commands recorded: {len(self.commands)}")
        failed = [c for c in self.commands if c.exit_code not in (0, None) or c.timed_out]
        if failed:
            lines.append(f"- Commands that failed or timed out: {len(failed)}")
            for command in failed[:10]:
                status = "TIMEOUT" if command.timed_out else f"exit {command.exit_code}"
                lines.append(f"    - [{status}] {command.command[:160]}")

        lines += ["", "## 4. What was preserved?", ""]
        if self.recovery_points:
            lines.extend(f"- {point}" for point in self.recovery_points)
        else:
            lines.append("- Nothing needed preserving (no local history was transformed).")

        lines += ["", "## 5. What remains incomplete?", ""]
        if self.incomplete:
            lines.extend(f"- {item}" for item in self.incomplete)
        else:
            lines.append("- Nothing was recorded as incomplete.")

        lines += ["", "## 6. Did any authority file change?", ""]
        changes = _authority_changes_from(self.authority_report)
        lines.extend(f"    {line}" if line else "" for line in render_authority_section(changes))

        lines += ["", "## 7. Did any local history change?", ""]
        if self.history_changes:
            for change in self.history_changes:
                verified = (
                    "verified" if change.verified
                    else "NOT VERIFIED" if change.verified is False
                    else "unverified"
                )
                lines.append(
                    f"- {change.what} — "
                    f"{'authorized' if change.authorized else 'REFUSED'}, {verified}"
                )
                if change.detail:
                    lines.append(f"    {change.detail}")
        else:
            lines.append("- No local history was rewritten.")

        lines += ["", "## 8. Where is the recovery point?", ""]
        if start:
            lines.append(f"- Run started at `{start.head[:12]}` on `{start.branch}` "
                         f"(tree `{start.tree[:12]}`)")
            lines.append(f"- Restore with: `git reset --hard {start.head}`  "
                         "(review your working tree first)")
        else:
            lines.append("- No starting identity was recorded.")
        for point in self.recovery_points:
            lines.append(f"- {point}")

        lines += ["", "## 9. Was any external action attempted or denied?", ""]
        denied = [t for t in self.tool_uses if t.denied_reason]
        if self.external_attempts:
            lines.append(f"- **External-boundary attempts: {len(self.external_attempts)}**")
            for attempt in self.external_attempts:
                lines.append(f"    - {attempt.tool}: {attempt.detail[:160]}")
                lines.append(f"        denied — {attempt.reason}")
        else:
            lines.append("- No external action was attempted.")
        if denied:
            lines.append(f"- Total denied operations: {len(denied)}")
            for entry in denied[:20]:
                lines.append(f"    - [{entry.layer or 'guard'}] {entry.tool}: {entry.denied_reason}")
        if self.denied_paths:
            lines.append(f"- Writes denied outside the approved roots: {len(self.denied_paths)}")
            for entry in self.denied_paths[:20]:
                lines.append(f"    - {entry['path']}: {entry['reason']}")
        lines.append("- Nothing was pushed, deployed, published or sent externally: "
                     "the driver control process performs no remote action, and every "
                     "attempt above was denied before execution.")

        lines += ["", "## 10. What decision is required from the founder?", ""]
        if self.founder_decision_required:
            lines.append(f"- **{self.founder_decision_required}**")
        elif self.controls_weakened:
            lines.append("- **A mandatory control was removed or weakened — review section 6 "
                         "before accepting this run.**")
        else:
            lines.append("- None recorded.")

        lines += ["", "---", "",
                  f"**Stop reason:** {self.stop_reason or '(not recorded)'}", "",
                  f"**Next safe action:** {self.next_safe_action or '(not recorded)'}", ""]

        lines += ["## Git identity", "",
                  "| | start | end |", "|---|---|---|"]
        lines.append(f"| branch | `{start.branch if start else '?'}` | "
                     f"`{end.branch if end else '?'}` |")
        lines.append(f"| HEAD | `{(start.head[:12] if start else '?') or '(none)'}` | "
                     f"`{(end.head[:12] if end else '?') or '(none)'}` |")
        lines.append(f"| tree | `{(start.tree[:12] if start else '?') or '(none)'}` | "
                     f"`{(end.tree[:12] if end else '?') or '(none)'}` |")
        lines.append(f"| tracked dirty | {start.tracked_dirty if start else '?'} | "
                     f"{end.tracked_dirty if end else '?'} |")
        lines.append(f"| untracked | {start.untracked if start else '?'} | "
                     f"{end.untracked if end else '?'} |")
        lines.append("")
        if self.approved_roots:
            lines += ["## Approved write roots", ""]
            lines.extend(f"- {root}" for root in self.approved_roots)
            lines.append("")

        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

_EXTERNAL_MARKERS = (
    "push", "publish", "deploy", "remote", "github cli", "cloud cli", "aws",
    "outbound", "payment", "communication", "production", "container push",
    "package publish", "external service",
)


def _is_external_boundary(reason: str) -> bool:
    lowered = reason.lower()
    return any(marker in lowered for marker in _EXTERNAL_MARKERS)


def _authority_changes_from(report: dict[str, Any]) -> list[AuthorityChange]:
    """Rebuild AuthorityChange objects from a serialized report, for rendering."""
    from .authority import AuthorityFinding

    out: list[AuthorityChange] = []
    for raw in report.get("changed", []) or []:
        change = AuthorityChange(
            path=raw.get("path", ""),
            existed_before=bool(raw.get("existed_before")),
            exists_after=bool(raw.get("exists_after")),
            before_sha=raw.get("before_sha", ""),
            after_sha=raw.get("after_sha", ""),
            diff=raw.get("diff", ""),
        )
        change.findings = [
            AuthorityFinding(
                path=f.get("path", ""),
                kind=f.get("kind", ""),
                detail=f.get("detail", ""),
                before_line=f.get("before_line", ""),
                after_line=f.get("after_line", ""),
            )
            for f in raw.get("findings", []) or []
        ]
        out.append(change)
    return out
