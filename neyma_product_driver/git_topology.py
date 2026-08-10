"""Commit-topology analysis.

Read-only. Every git command here observes; none mutates. The driver never
commits, never moves a ref, never rewrites history — it reports what the
history *is* and what the repository's own rules say it should be.

The distinctions this module enforces:

    a commit message saying "content"  != a commit that changes content
    a commit touching status files     != a commit the finalizer produced
    a receipt existing                 != a receipt bound to this tree
    a receipt naming a commit          != a receipt that could have named it
    local history                      != history someone else has seen

Roles are assigned from the files a commit actually changed. A message is
secondary evidence at best: it is the one part of a commit that costs nothing
to get wrong.
"""

from __future__ import annotations

import json
import re
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .protocol_sources import DiscoveredProtocol, RuleKind
from .repo_state import RepositoryState, StateResolution, resolve_state

_GIT_TIMEOUT_S = 30

# Receipts and status surfaces the repository did not name explicitly, but which
# are recognizable by shape. Used only as a fallback when discovery found none.
_RECEIPT_NAME_RE = re.compile(r"^[A-Z][A-Z0-9-]*-RESULT\.json$|RECEIPT", re.I)
_REVIEW_DOC_RE = re.compile(r"(review|remediation|adjudicat|finding|audit)", re.I)
_FINALIZER_MARK_RE = re.compile(r"finali[sz]er|generated[_ ]by|^\+?\s*derived:", re.I | re.M)
# The ownership stamp the finalizer writes INTO the files it maintains. Unlike
# the diff marker above, this survives in the resulting file even when a
# finalizer run only changes derived VALUES (leaving the marker line as
# unchanged context that a -U0 diff never shows). A hand edit that does not
# reproduce this stamp is still told apart.
_FINALIZER_OWNERSHIP_MARK_RE = re.compile(
    r"(?:derived|status)[-_ ]block:\s*maintained by\b[^\n]*finali[sz]"
    r"|maintained by\b[^\n]*finali[sz]e[_-]?status[^\n]*\.py"
    r"|#\s*end-(?:derived|status)[-_ ]block",
    re.I,
)
_ARCHIVAL_REF_RE = re.compile(r"(archive|archival|backup|pre-|preserved|snapshot)", re.I)


class CommitRole(str, Enum):
    BASELINE = "BASELINE"
    CONTENT = "CONTENT"
    REMEDIATION_CONTENT = "REMEDIATION_CONTENT"
    REVIEW_EVIDENCE = "REVIEW_EVIDENCE"
    METADATA_STATUS = "METADATA_STATUS"
    FINALIZER_GENERATED = "FINALIZER_GENERATED"
    UNKNOWN = "UNKNOWN"


class PathClass(str, Enum):
    CONTENT = "CONTENT"
    STATUS = "STATUS"
    RECEIPT = "RECEIPT"
    REVIEW = "REVIEW"


class GitCommitRole(BaseModel):
    """One commit, classified from file-level evidence."""

    model_config = ConfigDict(extra="ignore")

    commit_sha: str
    tree_sha: str = ""
    role: CommitRole = CommitRole.UNKNOWN
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0

    # Observation detail, carried for the report and for plan reconstruction.
    subject: str = ""
    parents: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    content_files: list[str] = Field(default_factory=list)
    status_files: list[str] = Field(default_factory=list)
    receipt_files: list[str] = Field(default_factory=list)
    review_files: list[str] = Field(default_factory=list)
    pushed: bool | None = None

    @property
    def short(self) -> str:
        return self.commit_sha[:12]

    @property
    def is_content(self) -> bool:
        return self.role in (CommitRole.CONTENT, CommitRole.REMEDIATION_CONTENT)

    @property
    def is_status(self) -> bool:
        return self.role in (CommitRole.METADATA_STATUS, CommitRole.FINALIZER_GENERATED)

    def describe(self) -> str:
        sample = ", ".join(self.files[:3])
        more = f" (+{len(self.files) - 3})" if len(self.files) > 3 else ""
        return f"{self.short}  {self.role.value:<20} {sample}{more}"


class ReceiptBinding(BaseModel):
    """A receipt file and the state it claims to validate."""

    model_config = ConfigDict(extra="ignore")

    name: str
    path: str
    exists: bool = False
    passed: bool = False
    recorded_commit: str = ""
    recorded_tree: str = ""
    matches_head: bool = False
    matches_known_commit: str = ""
    committed_in: str = ""
    self_referential: bool = False
    fresh: bool = False
    fresh_reason: str = ""
    detail: str = ""


class GitTopology(BaseModel):
    """The observed shape of history since the authorized baseline."""

    model_config = ConfigDict(extra="ignore")

    branch: str = ""
    head_commit: str = ""
    head_tree: str = ""
    baseline_commit: str = ""
    baseline_source: str = ""
    baseline_confidence: float = 0.0
    merge_base: str = ""
    default_branch_ref: str = ""

    commits: list[GitCommitRole] = Field(default_factory=list)  # baseline-exclusive, oldest first
    tags: list[str] = Field(default_factory=list)
    archival_refs: list[str] = Field(default_factory=list)
    remotes: list[str] = Field(default_factory=list)

    dirty_file_count: int = 0
    untracked_count: int = 0

    pushed_commits: list[str] = Field(default_factory=list)
    local_only_commits: list[str] = Field(default_factory=list)
    push_state_determinable: bool = True
    push_state_detail: str = ""

    receipts: list[ReceiptBinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    #: The repository's OWN state machine (FINALIZED/PRODUCING/BASELINE). This is
    #: the single authority on whether the observed arrangement is legal; the
    #: driver holds no competing interpretation of the same topology.
    state: StateResolution = Field(default_factory=StateResolution)

    @property
    def state_is_legal(self) -> bool:
        return self.state.legal

    # -- derived views -----------------------------------------------------

    @property
    def content_commits(self) -> list[GitCommitRole]:
        return [c for c in self.commits if c.is_content]

    @property
    def status_commits(self) -> list[GitCommitRole]:
        return [c for c in self.commits if c.is_status]

    @property
    def hand_edited_status_commits(self) -> list[GitCommitRole]:
        return [c for c in self.commits if c.role is CommitRole.METADATA_STATUS]

    @property
    def is_shared(self) -> bool:
        """True when any commit is on a remote — or cannot be shown not to be.

        "Unpushed" is the answer that makes a history rewrite look safe, so it
        is never the answer given by default.
        """
        return bool(self.pushed_commits) or not self.push_state_determinable

    @property
    def final_tree(self) -> str:
        return self.commits[-1].tree_sha if self.commits else self.head_tree

    def render_graph(self) -> str:
        lines = [f"{self.baseline_commit[:12]}  BASELINE  ({self.baseline_source})"]
        for commit in self.commits:
            marker = "local" if commit.pushed is False else ("pushed" if commit.pushed else "?")
            lines.append(f"  → {commit.describe()}   [{marker}]")
        if self.dirty_file_count or self.untracked_count:
            lines.append(
                f"  · working tree: {self.dirty_file_count} modified, "
                f"{self.untracked_count} untracked (uncommitted)"
            )
        return "\n".join(lines)


def expected_graph(protocol: DiscoveredProtocol, baseline: str) -> str:
    """Render the graph the repository's own topology rule describes."""
    rule = protocol.effective(RuleKind.COMMIT_TOPOLOGY)
    if rule is None or rule.parameters.get("max_content_commits") is None:
        return (
            "(the repository states no commit-topology rule the driver can render; "
            "no expected graph can be asserted)"
        )
    n = int(rule.parameters["max_content_commits"])
    owner = rule.parameters.get("metadata_commit_owner", "")
    lines = [f"{baseline[:12]}  BASELINE"]
    for i in range(n):
        label = "CONTENT" if n == 1 else f"CONTENT {i + 1}"
        lines.append(f"  → <one {label.lower()} commit>")
    if rule.parameters.get("requires_metadata_commit"):
        who = f"{owner}-generated " if owner else ""
        lines.append(f"  → <one {who}metadata/status commit>")
    lines.append(f"  [rule: {rule.rule_id} @ {rule.cite()}]")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Analyzer
# --------------------------------------------------------------------------


class GitTopologyAnalyzer:
    """Observes commit topology. Read-only by construction."""

    def __init__(self, repo: Path, protocol: DiscoveredProtocol) -> None:
        self.repo = Path(repo)
        self.protocol = protocol

    # -- git ---------------------------------------------------------------

    def _git(self, *args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(self.repo),
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_S,
                check=False,
            )
            return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def _git_ok(self, *args: str) -> bool:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(self.repo),
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_S,
                check=False,
            )
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    # -- baseline ----------------------------------------------------------

    def resolve_baseline(self) -> tuple[str, str, float]:
        """Find the authorized baseline the repository names, or the best proxy.

        Returns ``(commit, how_it_was_determined, confidence)``. The repository
        naming its own baseline is worth far more than any inference the driver
        can make, so recorded values are tried first.
        """
        keys = (
            "authorized_baseline",
            "baseline_commit",
            "phase_baseline",
            "baseline",
            "unit_baseline",
        )

        for category in ("build_status", "registry"):
            rel = self.protocol.sources.first(category)
            if not rel:
                continue
            path = self.repo / rel
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError, ValueError):
                continue
            found = _find_key(data, keys)
            if found:
                resolved = self._git("rev-parse", "--verify", f"{found}^{{commit}}")
                if resolved:
                    return self._promote_certified_pair(
                        *self._historical_baseline(resolved, f"recorded in {rel}", 0.95)
                    )

        for tag in self._git("tag", "--list").splitlines():
            if "baseline" in tag.lower():
                resolved = self._git("rev-parse", "--verify", f"{tag}^{{commit}}")
                if resolved:
                    return resolved, f"tag {tag}", 0.8

        for ref in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
            if not self._git_ok("rev-parse", "--verify", ref):
                continue
            base = self._git("merge-base", ref, "HEAD")
            head = self._git("rev-parse", "HEAD")
            if base and base != head:
                return base, f"merge-base with {ref}", 0.6

        root = (self._git("rev-list", "--max-parents=0", "HEAD").splitlines() or [""])[0]
        if root:
            return root, "root commit (no baseline is recorded in the repository)", 0.3
        return "", "undeterminable", 0.0

    def _first_parent(self, sha: str) -> str:
        parents = (self._git("log", "-1", "--format=%P", sha) or "").split()
        return parents[0] if parents else ""

    def current_pair(self, head: str) -> tuple[str, str]:
        """``(content_commit, metadata_commit)`` for the pair ending at HEAD.

        Read from the files each commit changed, never from a recorded field.
        Under the two-commit convention HEAD is either the content commit
        itself (no metadata commit written yet) or the metadata/status commit
        whose first parent is the content commit it records.
        """
        if not head:
            return "", ""
        role = self.classify_commit(head, seen_content=False)
        if role.is_status and not role.content_files:
            return self._first_parent(head), head
        return head, ""

    def _historical_baseline(
        self, recorded: str, source: str, confidence: float
    ) -> tuple[str, str, float]:
        """Demote a recorded *content-baseline* pointer to a true baseline.

        Neyma's two-commit convention records the CURRENT content commit under
        keys like ``baseline_commit`` (a commit cannot store its own hash, so
        the metadata commit points back at the content commit it records). Taken
        literally as the exclusion baseline, that value drops the current
        content commit — and the finalizer metadata commit on top of it — out of
        the analyzed range, mislabeling the live content commit BASELINE and its
        finalizer commit a hand edit. When the recorded value is the current
        content commit, the historical baseline is that commit's first parent
        (the previous unit's metadata commit), so the current pair is analyzed.
        Determined mechanically from first-parent topology and files changed,
        not from the recorded field's own name.
        """
        head = self._git("rev-parse", "HEAD")
        content, _metadata = self.current_pair(head)
        if content and _matches(recorded, content):
            parent = self._first_parent(content)
            if parent:
                note = (
                    f"{source}, which names the current content commit "
                    f"{content[:12]} (two-commit convention: baseline_commit records the "
                    f"content commit, not the historical baseline), so the historical "
                    f"baseline is its parent {parent[:12]}"
                )
                return parent, note, confidence
        return recorded, source, confidence

    def is_pure_finalizer_metadata(self, sha: str) -> bool:
        """Is ``sha`` a finalizer-written commit that touches ONLY derived status?

        The certified-pair boundary rests on this: a commit that carries any
        runtime content is not a metadata commit, however it is labelled.
        """
        if not sha:
            return False
        role = self.classify_commit(sha, seen_content=False)
        return role.role is CommitRole.FINALIZER_GENERATED and not role.content_files

    def _promote_certified_pair(
        self, base: str, source: str, confidence: float
    ) -> tuple[str, str, float]:
        """Promote a certified CONTENT baseline to its pure finalizer child.

        Neyma certifies a unit as a PAIR: the content commit, then the finalizer
        metadata commit that binds derived status and receipts to that content
        commit's tree. The recorded ``baseline_commit`` names the CONTENT half
        (a commit cannot record its own hash). Taking that value literally as the
        exclusion boundary leaves the finalizer metadata commit INSIDE the
        analyzed range, where it has no preceding content commit — so the driver
        reads a certified, already-adjudicated boundary as "status recorded for a
        tree that did not exist yet" and proposes rewriting history to fix a
        repository that is in fact legal.

        The whole certified pair belongs to the baseline. When the first-parent
        child of the recorded content commit is a pure finalizer metadata commit,
        that metadata commit is the true exclusion boundary.

        Determined mechanically from first-parent topology and files changed.
        """
        head = self._git("rev-parse", "HEAD")
        if not base or not head or _matches(base, head):
            return base, source, confidence

        raw = self._git("rev-list", "--first-parent", "--reverse", f"{base}..HEAD")
        shas = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not shas:
            return base, source, confidence

        child = shas[0]
        # Only a DIRECT first-parent child certifies this baseline.
        if not _matches(self._first_parent(child), base):
            return base, source, confidence
        if not self.is_pure_finalizer_metadata(child):
            return base, source, confidence

        note = (
            f"{source}, which names the content half {base[:12]} of a certified pair; its "
            f"first-parent child {child[:12]} is a pure finalizer metadata commit, so the "
            f"certified pair boundary — and therefore the exclusion baseline — is {child[:12]}"
        )
        return child, note, confidence

    # -- path classification ----------------------------------------------

    def classify_path(self, path: str) -> PathClass:
        """Classify one changed file using the repository's own path sets."""
        name = path.split("/")[-1]

        if name in set(self.protocol.receipt_paths) or _RECEIPT_NAME_RE.search(name):
            return PathClass.RECEIPT

        status_names = {p.split("/")[-1] for p in self.protocol.status_paths}
        finalizer_owned = {p for p in self.protocol.finalizer_owned_paths}
        if name in status_names or path in finalizer_owned:
            return PathClass.STATUS

        if path.endswith(".md") and _REVIEW_DOC_RE.search(name):
            return PathClass.REVIEW

        return PathClass.CONTENT

    # -- commits -----------------------------------------------------------

    def _commit_files(self, sha: str) -> list[str]:
        raw = self._git("show", "--pretty=format:", "--name-only", sha)
        return [ln.strip() for ln in raw.splitlines() if ln.strip()]

    def _looks_finalizer_generated(self, sha: str, status_files: list[str]) -> bool:
        """Did the finalizer write this status commit, or did a person?

        Two independent signals, either of which is sufficient:

          1. the diff carries a finalizer/derived marker (added or removed
             lines) — a freshly regenerated derived block, or a finalizer stamp;
          2. the RESULTING file at this commit carries the finalizer's ownership
             stamp ("...-block: maintained by scripts/finalize_status.py").

        The second signal matters because a finalizer run that only changes the
        derived VALUES (rebinding content_commit/content_tree to the commit it
        just certified) leaves the marker line itself unchanged — so it is
        context a ``-U0`` diff never emits, and signal 1 alone would misread the
        commit as a hand edit. A hand edit that does not reproduce the ownership
        stamp still fails both signals and stays METADATA_STATUS.
        """
        if not status_files:
            return False
        diff = self._git("show", "--format=", "-U0", sha, "--", *status_files[:20])
        if _FINALIZER_MARK_RE.search(diff):
            return True
        for path in status_files[:20]:
            content = self._git("show", f"{sha}:{path}")
            if content and _FINALIZER_OWNERSHIP_MARK_RE.search(content):
                return True
        return False

    def classify_commit(self, sha: str, seen_content: bool) -> GitCommitRole:
        files = self._commit_files(sha)
        tree = self._git("rev-parse", f"{sha}^{{tree}}")
        subject = self._git("log", "-1", "--format=%s", sha)
        parents = (self._git("log", "-1", "--format=%P", sha) or "").split()

        buckets: dict[PathClass, list[str]] = {c: [] for c in PathClass}
        for path in files:
            buckets[self.classify_path(path)].append(path)

        content = buckets[PathClass.CONTENT]
        status = buckets[PathClass.STATUS]
        receipts = buckets[PathClass.RECEIPT]
        review = buckets[PathClass.REVIEW]

        evidence: list[str] = []
        role = CommitRole.UNKNOWN
        confidence = 0.3

        if not files:
            evidence.append("commit changes no files")
        elif content:
            role = CommitRole.REMEDIATION_CONTENT if seen_content else CommitRole.CONTENT
            confidence = 0.9 if not (status or receipts) else 0.65
            evidence.append(
                f"changes {len(content)} runtime/test/specification file(s): "
                + ", ".join(content[:4])
            )
            if seen_content:
                evidence.append(
                    "a content commit already exists in this range, so this is further "
                    "content on top of reviewed content"
                )
            if status:
                evidence.append(
                    f"also changes derived status ({', '.join(status[:3])}) in the same commit"
                )
        elif status:
            if self._looks_finalizer_generated(sha, status):
                role = CommitRole.FINALIZER_GENERATED
                confidence = 0.75
                evidence.append(
                    "changes only derived status, and the diff carries a finalizer/derived "
                    "marker"
                )
            else:
                role = CommitRole.METADATA_STATUS
                confidence = 0.7
                evidence.append(
                    "changes only derived status, with no finalizer/derived marker in the "
                    "diff — consistent with a hand edit"
                )
            evidence.append("status files: " + ", ".join(status[:4]))
        elif receipts or review:
            role = CommitRole.REVIEW_EVIDENCE
            confidence = 0.8
            evidence.append("changes only receipts/review artifacts: " + ", ".join((receipts + review)[:4]))
        else:
            evidence.append("changed files did not classify: " + ", ".join(files[:4]))

        # The message is corroboration only. It can raise confidence in a
        # classification the files already support; it can never create one.
        lowered = subject.lower()
        if role in (CommitRole.CONTENT, CommitRole.REMEDIATION_CONTENT):
            if re.search(r"\b(fix|remediat|address|review)\w*\b", lowered):
                evidence.append(f"message corroborates remediation intent: {subject[:80]!r}")
        elif role in (CommitRole.METADATA_STATUS, CommitRole.FINALIZER_GENERATED):
            if re.search(r"\b(status|finaliz|progress)\w*\b", lowered):
                evidence.append(f"message corroborates a status commit: {subject[:80]!r}")

        return GitCommitRole(
            commit_sha=sha,
            tree_sha=tree,
            role=role,
            evidence=evidence,
            confidence=confidence,
            subject=subject,
            parents=parents,
            files=files,
            content_files=content,
            status_files=status,
            receipt_files=receipts,
            review_files=review,
        )

    # -- push state --------------------------------------------------------

    def _push_state(self, shas: list[str]) -> tuple[list[str], list[str], bool, str]:
        remotes = [r for r in self._git("remote").splitlines() if r.strip()]
        if not remotes:
            return [], list(shas), True, "the repository has no remotes; this history is local-only"

        remote_refs = self._git("for-each-ref", "--format=%(refname)", "refs/remotes")
        if not remote_refs.strip():
            return (
                [],
                list(shas),
                True,
                f"remote(s) configured ({', '.join(remotes)}) but no remote-tracking refs exist "
                "locally, so nothing here has been pushed from this clone",
            )

        pushed: list[str] = []
        local: list[str] = []
        undetermined: list[str] = []
        for sha in shas:
            if not self._git_ok("branch", "-r", "--contains", sha):
                # The query itself failed. "No containing remote branch" and
                # "could not ask" look identical in the output, so they must not
                # be conflated: unpushed is the answer that makes a rewrite look
                # safe, and it is not one to guess at.
                undetermined.append(sha)
                continue
            containing = self._git("branch", "-r", "--contains", sha)
            (pushed if containing.strip() else local).append(sha)

        if undetermined:
            return (
                pushed,
                local,
                False,
                f"push state could not be determined for {len(undetermined)} commit(s); "
                f"treat this history as potentially shared",
            )
        return (
            pushed,
            local,
            True,
            f"determined from remote-tracking refs ({', '.join(remotes)})",
        )

    # -- receipts ----------------------------------------------------------

    def _receipt_bindings(
        self,
        known: list[GitCommitRole],
        head: str,
        head_tree: str,
        state: StateResolution | None = None,
    ) -> list[ReceiptBinding]:
        bindings: list[ReceiptBinding] = []
        rels: list[str] = []
        for rel in self.protocol.sources.by_category.get("receipts", []):
            if rel.endswith(".json"):
                rels.append(rel)

        known_shas = {c.commit_sha for c in known}
        known_trees = {c.tree_sha for c in known}

        # Is HEAD a VERIFIED metadata-only finalizer commit? Only then may a
        # receipt bound to HEAD^ be fresh under the two-commit convention. The
        # FINALIZER_GENERATED role already guarantees the commit changed only
        # finalizer-owned status/receipt/review files (any unauthorized file
        # would have made it a content commit) and carries the finalizer's
        # ownership stamp — so "metadata commit containing unauthorized files"
        # and "hand-edited derived status" are both excluded here by construction.
        head_meta = next(
            (c for c in known if _matches(c.commit_sha, head)
             and c.role is CommitRole.FINALIZER_GENERATED),
            None,
        )
        head_parent = self._first_parent(head) if head_meta is not None else ""
        head_parent_tree = (
            self._git("rev-parse", f"{head_parent}^{{tree}}") if head_parent else ""
        )

        # The content half of the certified pair the repository's own state
        # machine says the current state rests on. In PRODUCING this is the
        # commit the on-disk receipts legitimately name.
        certified_content = state.certified_content_commit if state is not None else ""
        certified_tree = (
            self._git("rev-parse", f"{certified_content}^{{tree}}") if certified_content else ""
        )

        for rel in rels:
            path = self.repo / rel
            binding = ReceiptBinding(name=path.stem, path=rel, exists=path.exists())
            if not binding.exists:
                binding.detail = "receipt file absent"
                bindings.append(binding)
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                binding.detail = "receipt is not readable JSON"
                bindings.append(binding)
                continue
            if not isinstance(data, dict):
                binding.detail = "receipt is not a JSON object"
                bindings.append(binding)
                continue

            binding.recorded_commit = str(data.get("commit", ""))
            binding.recorded_tree = str(data.get("tree", ""))
            binding.passed = _receipt_passed(data)
            binding.matches_head = _matches(binding.recorded_commit, head) or _matches(
                binding.recorded_tree, head_tree
            )

            # Freshness — deliberately strict, and it is NOT weakened generally.
            # A receipt is fresh only when it validates the exact tree it is
            # presented for:
            #   * it binds directly to HEAD and HEAD's tree, OR
            #   * HEAD is a verified metadata-only finalizer commit and the
            #     receipt binds to HEAD^ AND HEAD^'s tree (the content commit the
            #     finalizer certified — Neyma's two-commit convention).
            # Anything else — a wrong tree, a HEAD^^-or-older binding, a receipt
            # that names the commit that introduced it — is not fresh.
            commit_is_head = _matches(binding.recorded_commit, head)
            tree_is_head = _matches(binding.recorded_tree, head_tree)
            binds_certified_baseline = (
                state is not None
                and state.state is RepositoryState.PRODUCING
                and bool(certified_content)
                and _matches(binding.recorded_commit, certified_content)
                and _matches(binding.recorded_tree, certified_tree)
            )
            binds_head_parent = (
                head_meta is not None
                and bool(head_parent)
                and _matches(binding.recorded_commit, head_parent)
                and _matches(binding.recorded_tree, head_parent_tree)
            )
            if commit_is_head and tree_is_head:
                binding.fresh = True
                binding.fresh_reason = "binds directly to HEAD and HEAD's tree"
            elif binds_head_parent:
                binding.fresh = True
                binding.matches_head = True
                binding.fresh_reason = (
                    f"binds to the content commit {head_parent[:12]} (HEAD^) and its tree, which "
                    f"the finalizer metadata commit HEAD certified — two-commit convention"
                )
            elif binds_certified_baseline:
                # PRODUCING: the repository is mid-unit. The receipts on disk are
                # the ones the finalizer wrote for the certified pair the new
                # content commit was built on, and they correctly name it. The
                # next unit's receipts do not exist yet and cannot; demanding
                # that a receipt already name HEAD in this state demands a
                # receipt for a suite that has not run, which is the exact false
                # green the whole receipt convention exists to prevent.
                binding.fresh = True
                binding.matches_head = True
                binding.fresh_reason = (
                    f"binds to the certified baseline {certified_content[:12]} and its tree in the "
                    f"PRODUCING state — the receipt names the pair HEAD was built on, and the "
                    f"receipt for HEAD is what this state is producing"
                )
            for sha in known_shas:
                if _matches(binding.recorded_commit, sha):
                    binding.matches_known_commit = sha
                    break
            if not binding.matches_known_commit:
                for tree in known_trees:
                    if _matches(binding.recorded_tree, tree):
                        binding.matches_known_commit = f"(tree) {tree}"
                        break

            binding.committed_in = self._git("log", "-1", "--format=%H", "--", rel)
            if binding.committed_in and _matches(binding.recorded_commit, binding.committed_in):
                # The receipt names the very commit that introduced it. That
                # value could not have been known when the file was written.
                binding.self_referential = True

            binding.detail = (
                f"passed={binding.passed}, names commit {binding.recorded_commit[:12] or '(none)'} / "
                f"tree {binding.recorded_tree[:12] or '(none)'}, "
                f"committed in {binding.committed_in[:12] or '(uncommitted)'}"
            )
            bindings.append(binding)
        return bindings

    # -- the analysis ------------------------------------------------------

    def analyze(self, baseline: str | None = None) -> GitTopology:
        """Observe history since the authorized baseline. Never raises."""
        head = self._git("rev-parse", "HEAD")
        head_tree = self._git("rev-parse", "HEAD^{tree}")
        branch = self._git("branch", "--show-current")

        # The repository's own state machine, resolved first: everything that
        # judges the observed arrangement defers to it.
        state = resolve_state(self)

        notes: list[str] = []
        if baseline:
            # A baseline the caller names but git cannot resolve is not a
            # baseline. It must never reach a generated command as if it were.
            resolved = self._git("rev-parse", "--verify", f"{baseline}^{{commit}}")
            if resolved:
                # A caller-supplied baseline names the same certified pair the
                # repository records, so the same boundary promotion applies.
                base, source, confidence = self._promote_certified_pair(
                    resolved, "supplied by the caller", 0.9
                )
            else:
                base, source, confidence = "", f"{baseline!r} does not resolve in this repository", 0.0
                notes.append(f"the supplied baseline {baseline!r} does not resolve to a commit")
        else:
            base, source, confidence = self.resolve_baseline()

        shas: list[str] = []
        if base and head:
            raw = self._git("rev-list", "--first-parent", "--reverse", f"{base}..HEAD")
            shas = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            if not shas and base != head:
                notes.append(
                    f"no commits between the baseline {base[:12]} and HEAD {head[:12]} on the "
                    "first-parent path"
                )
        elif not base:
            notes.append("the authorized baseline could not be determined")

        commits: list[GitCommitRole] = []
        seen_content = False
        for sha in shas:
            commit = self.classify_commit(sha, seen_content)
            if commit.is_content:
                seen_content = True
            commits.append(commit)

        pushed, local, determinable, push_detail = self._push_state(shas)
        for commit in commits:
            commit.pushed = commit.commit_sha in pushed if determinable else None

        status_lines = [ln for ln in self._git("status", "--porcelain").splitlines() if ln.strip()]
        tags = [t for t in self._git("tag", "--list").splitlines() if t.strip()]
        refs = [
            r.split("refs/heads/")[-1]
            for r in self._git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines()
            if r.strip()
        ]
        archival = sorted({r for r in refs + tags if _ARCHIVAL_REF_RE.search(r)})

        default_ref = ""
        for ref in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
            if self._git_ok("rev-parse", "--verify", ref):
                default_ref = ref
                break

        return GitTopology(
            branch=branch,
            head_commit=head,
            head_tree=head_tree,
            baseline_commit=base,
            baseline_source=source,
            baseline_confidence=confidence,
            merge_base=self._git("merge-base", default_ref, "HEAD") if default_ref else "",
            default_branch_ref=default_ref,
            commits=commits,
            tags=tags,
            archival_refs=archival,
            remotes=[r for r in self._git("remote").splitlines() if r.strip()],
            dirty_file_count=len([ln for ln in status_lines if not ln.startswith("??")]),
            untracked_count=len([ln for ln in status_lines if ln.startswith("??")]),
            pushed_commits=pushed,
            local_only_commits=local,
            push_state_determinable=determinable,
            push_state_detail=push_detail,
            receipts=self._receipt_bindings(commits, head, head_tree, state),
            notes=notes,
            state=state,
        )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _find_key(data: Any, keys: tuple[str, ...], depth: int = 0) -> str:
    """Find the first sha-shaped value stored under any of ``keys``."""
    if depth > 6 or not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{7,40}", value.strip()):
            return value.strip()
    for value in data.values():
        if isinstance(value, dict):
            found = _find_key(value, keys, depth + 1)
            if found:
                return found
        elif isinstance(value, list):
            for item in value:
                found = _find_key(item, keys, depth + 1)
                if found:
                    return found
    return ""


def _receipt_passed(data: dict[str, Any]) -> bool:
    if "passed" in data and isinstance(data["passed"], bool):
        return bool(data["passed"])
    try:
        if "exit_status" in data:
            return int(data.get("exit_status", 1)) == 0 and int(data.get("failed", 0) or 0) == 0
    except (TypeError, ValueError):
        return False
    return False


def _matches(a: str, b: str) -> bool:
    """Git ids match when either is a prefix of the other (short vs full sha)."""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return False
    n = min(len(a), len(b))
    return n >= 7 and a[:n] == b[:n]
