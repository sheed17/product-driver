"""Authority-file policy: the builder may edit them, but never quietly.

The blunt rule — "CLAUDE.md is off limits" — is wrong for this driver. A task
can legitimately require a documentation, control-system or authority change,
and a builder that cannot touch the file it was asked to correct is useless.

The rule that actually holds is different, and has two halves:

* **permitted, and loud.** Editing an authority document is allowed. Every such
  edit is captured before and after, diffed, and surfaced at the top of the run
  evidence and the founder summary. There is no such thing as an authority edit
  the founder has to go looking for.
* **never self-serving.** An authority edit that *removes or weakens a mandatory
  control* is detected and reported as a finding, whatever the task was. A
  builder must not be able to unblock itself by deleting the rule it is failing.

Some surfaces stay unwritable during an ordinary product run whatever the task
says: ``.claude/settings*.json``, ``.claude/hooks/`` and ``.mcp.json`` carry the
harness's own enforcement, and a run that can edit its own enforcement has none.
That block lives in :mod:`~neyma_product_driver.command_guard`; this module
covers the documents that *are* editable.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

# Documents that carry authority. Relative to the target repository, and matched
# by glob so a repository that keeps its registry elsewhere is still covered.
AUTHORITY_PATTERNS: tuple[str, ...] = (
    "CLAUDE.md",
    "AGENTS.md",
    "README.md",
    "PRODUCT.md",
    "ARCHITECTURE.md",
    "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
    "docs/implementation/BUILD-STATUS.yaml",
    "docs/implementation/CURRENT.md",
    "docs/specifications/acceptance/registry.md",
    "docs/implementation/APPROVED-SKIPS.yaml",
)

# The highest-signal one. Called out separately because the founder asked for it
# specifically, and because it is the document that governs the builder itself.
PRIMARY_AUTHORITY = "CLAUDE.md"

# A line stating a mandatory control. Removing or softening one of these is the
# thing this module exists to catch.
MANDATORY_MARKERS = re.compile(
    r"(?ix)\b("
    r"MUST\s+NOT|MUST|SHALL\s+NOT|SHALL|NEVER|ALWAYS|REQUIRED|PROHIBITED|FORBIDDEN"
    r"|MANDATORY|NON-?NEGOTIABLE|DO\s+NOT|CANNOT|MAY\s+NOT|BLOCKING|HARD\s+STOP"
    r"|NOT\s+CONTAINED|NEEDS\s+VALIDATION|OPEN\s+RISK|ACCEPTANCE\s+CRITERI"
    r"|GATE|GUARD|INVARIANT"
    r")\b"
)

# Softening rewrites: the control survives as a sentence but stops binding.
_WEAKENING_REWRITES: tuple[tuple[re.Pattern[str], re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bMUST NOT\b"), re.compile(r"(?i)\b(?:should not|avoid|prefer not)\b"),
     "MUST NOT downgraded to a recommendation"),
    (re.compile(r"(?i)\bMUST\b"), re.compile(r"(?i)\b(?:should|may|can|ideally)\b"),
     "MUST downgraded to a recommendation"),
    (re.compile(r"(?i)\bNEVER\b"), re.compile(r"(?i)\b(?:rarely|avoid|generally not|try not)\b"),
     "NEVER downgraded to a preference"),
    (re.compile(r"(?i)\bALWAYS\b"), re.compile(r"(?i)\b(?:usually|generally|typically|where possible)\b"),
     "ALWAYS downgraded to a tendency"),
    (re.compile(r"(?i)\bREQUIRED\b"), re.compile(r"(?i)\b(?:optional|recommended|encouraged)\b"),
     "REQUIRED downgraded to optional"),
    (re.compile(r"(?i)\bPROHIBITED|FORBIDDEN\b"), re.compile(r"(?i)\b(?:discouraged|not recommended)\b"),
     "PROHIBITED downgraded to discouraged"),
    (re.compile(r"(?i)\bBLOCKING\b"), re.compile(r"(?i)\b(?:non-blocking|advisory|warning)\b"),
     "a blocking gate downgraded to advisory"),
)

_MAX_DIFF_LINES = 400


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorityFinding:
    """One way an authority edit reduced the controls in force."""

    path: str
    kind: str  # "removed_control" | "weakened_control" | "file_deleted"
    detail: str
    before_line: str = ""
    after_line: str = ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "kind": self.kind,
            "detail": self.detail,
            "before_line": self.before_line,
            "after_line": self.after_line,
        }


@dataclass
class AuthorityChange:
    """One authority file that changed during a run."""

    path: str
    existed_before: bool
    exists_after: bool
    before_sha: str = ""
    after_sha: str = ""
    diff: str = ""
    findings: list[AuthorityFinding] = field(default_factory=list)

    @property
    def is_primary(self) -> bool:
        return Path(self.path).name == PRIMARY_AUTHORITY

    @property
    def weakened(self) -> bool:
        return bool(self.findings)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "is_primary_authority": self.is_primary,
            "existed_before": self.existed_before,
            "exists_after": self.exists_after,
            "before_sha": self.before_sha,
            "after_sha": self.after_sha,
            "weakened": self.weakened,
            "findings": [f.to_dict() for f in self.findings],
            "diff": self.diff,
        }


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def _mandatory_lines(text: str) -> list[str]:
    """Lines stating a mandatory control, normalized for comparison."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("```", "|---", "---")):
            continue
        # A quoted or blockquoted line is describing a rule, not stating one.
        if line.startswith(">"):
            continue
        if MANDATORY_MARKERS.search(line):
            out.append(" ".join(line.split()))
    return out


def _normalize(line: str) -> str:
    """Compare on words, so reflowing or renumbering is not a 'removal'."""
    return re.sub(r"[^a-z0-9 ]+", " ", line.lower())


def detect_weakening(before: str, after: str, path: str = "") -> list[AuthorityFinding]:
    """Find mandatory controls that were removed or softened between two texts.

    Removal is judged on *content*, not position: a rule that moved elsewhere in
    the document is still in force, and reporting it as deleted would make the
    real deletions impossible to see.
    """
    findings: list[AuthorityFinding] = []
    before_lines = _mandatory_lines(before)
    after_lines = _mandatory_lines(after)
    after_norm = [_normalize(line) for line in after_lines]
    after_blob = "\n".join(after_norm)

    for line in before_lines:
        norm = _normalize(line)
        if norm in after_blob:
            continue  # survived verbatim, or moved

        # Still present but softened? Find its closest surviving relative.
        match = difflib.get_close_matches(norm, after_norm, n=1, cutoff=0.6)
        if match:
            replacement = after_lines[after_norm.index(match[0])]
            softened = False
            for strong, weak, description in _WEAKENING_REWRITES:
                if strong.search(line) and not strong.search(replacement) and weak.search(replacement):
                    findings.append(AuthorityFinding(
                        path=path, kind="weakened_control", detail=description,
                        before_line=line[:300], after_line=replacement[:300],
                    ))
                    softened = True
                    break
            if softened:
                continue
            # A near-match that lost its mandatory marker entirely.
            if MANDATORY_MARKERS.search(line) and not MANDATORY_MARKERS.search(replacement):
                findings.append(AuthorityFinding(
                    path=path, kind="weakened_control",
                    detail="a mandatory control lost its binding language",
                    before_line=line[:300], after_line=replacement[:300],
                ))
                continue
            # Rewritten but still mandatory — not a weakening.
            continue

        findings.append(AuthorityFinding(
            path=path, kind="removed_control",
            detail="a mandatory control was deleted",
            before_line=line[:300],
        ))

    return findings


def _diff(before: str, after: str, path: str) -> str:
    lines = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=3,
    ))
    if len(lines) > _MAX_DIFF_LINES:
        kept = lines[:_MAX_DIFF_LINES]
        kept.append(f"... [diff truncated, {len(lines) - _MAX_DIFF_LINES} more lines]")
        lines = kept
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The watcher
# --------------------------------------------------------------------------


class AuthorityWatcher:
    """Snapshots authority documents so any change is visible after the fact.

    Usage is deliberately simple: snapshot at the start of a run, compare at the
    end. The before-text is held in memory rather than read back from git,
    because an authority file may be untracked, staged, or committed mid-run and
    the question being asked is "what did this run change", not "what does git
    think".
    """

    def __init__(self, repo: Path, patterns: Sequence[str] = AUTHORITY_PATTERNS) -> None:
        self.repo = Path(repo)
        self.patterns = tuple(patterns)
        self._before: dict[str, str | None] = {}

    def _resolve(self) -> list[Path]:
        found: list[Path] = []
        for pattern in self.patterns:
            if any(ch in pattern for ch in "*?["):
                found.extend(sorted(self.repo.glob(pattern)))
            else:
                found.append(self.repo / pattern)
        seen: set[Path] = set()
        unique: list[Path] = []
        for p in found:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        return unique

    @staticmethod
    def _read(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            return None

    def snapshot(self) -> dict[str, str | None]:
        """Record the current text of every authority document."""
        self._before = {}
        for path in self._resolve():
            rel = str(path.relative_to(self.repo)) if path.is_relative_to(self.repo) else str(path)
            self._before[rel] = self._read(path) if path.is_file() else None
        return dict(self._before)

    def changes(self) -> list[AuthorityChange]:
        """Compare against the snapshot and describe every authority change."""
        results: list[AuthorityChange] = []
        for rel, before in self._before.items():
            path = self.repo / rel
            after = self._read(path) if path.is_file() else None

            if before is None and after is None:
                continue
            if before == after:
                continue

            change = AuthorityChange(
                path=rel,
                existed_before=before is not None,
                exists_after=after is not None,
                before_sha=_blob_sha(before),
                after_sha=_blob_sha(after),
            )
            if before is not None and after is None:
                change.findings.append(AuthorityFinding(
                    path=rel, kind="file_deleted",
                    detail="an authority document was deleted outright",
                ))
                change.diff = _diff(before, "", rel)
            else:
                change.diff = _diff(before or "", after or "", rel)
                if before is not None and after is not None:
                    change.findings.extend(detect_weakening(before, after, rel))
            results.append(change)

        # Primary authority first, then anything weakened, then the rest.
        results.sort(key=lambda c: (not c.is_primary, not c.weakened, c.path))
        return results

    # -- reporting --------------------------------------------------------

    def report(self) -> dict:
        """A serializable summary for the run evidence."""
        changes = self.changes()
        return {
            "authority_files_watched": list(self._before),
            "changed": [c.to_dict() for c in changes],
            "changed_count": len(changes),
            "primary_authority_changed": any(c.is_primary for c in changes),
            "weakened_controls": [
                f.to_dict() for c in changes for f in c.findings
            ],
            "weakening_detected": any(c.weakened for c in changes),
        }


def _blob_sha(text: str | None) -> str:
    if text is None:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def render_authority_section(changes: Iterable[AuthorityChange]) -> list[str]:
    """Human-readable block for the founder summary. Loud on purpose."""
    changes = list(changes)
    if not changes:
        return ["AUTHORITY FILES: none changed."]

    lines = [f"AUTHORITY FILES CHANGED: {len(changes)}"]
    for change in changes:
        marker = "  ** " if change.is_primary else "     "
        lines.append(f"{marker}{change.path}"
                     f"{'  [PRIMARY AUTHORITY]' if change.is_primary else ''}"
                     f"{'  [CONTROLS WEAKENED]' if change.weakened else ''}")
        for finding in change.findings:
            lines.append(f"       - {finding.kind}: {finding.detail}")
            if finding.before_line:
                lines.append(f"           was: {finding.before_line[:160]}")
            if finding.after_line:
                lines.append(f"           now: {finding.after_line[:160]}")
    if any(c.weakened for c in changes):
        lines.append("")
        lines.append("  A mandatory control was removed or softened. Review this before "
                     "accepting any result from this run.")
    return lines
