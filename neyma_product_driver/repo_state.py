"""The repository's own state machine — FINALIZED, PRODUCING, BASELINE.

The product repository defines exactly three legal relationships between the
status it records and the commit that is checked out. `eval/tests/
test_status_reality.py::repo_state` is the authority:

    FINALIZED  recorded == HEAD^   and HEAD is a pure status-metadata commit.
               The at-rest state.
    PRODUCING  recorded == HEAD^^  and HEAD^ is a pure status-metadata commit,
               and HEAD is the next content commit. This state exists exactly
               while the next artifact is being produced on fresh content.
    BASELINE   recorded == HEAD    (pre-convention history only).

Anything else is illegal.

This module exists because the driver previously carried a SECOND, incompatible
interpretation of the same topology. Under that private model the legal
PRODUCING shape

    3d23173 (content) → f1e8e18 (finalizer metadata) → 72512b9 (next content)

read as "a status commit precedes a content commit — status was recorded for a
tree that did not exist yet", and the driver proposed rewriting certified
history to repair a repository that was never broken. A driver that enforces a
convention the repository does not state has no standing; where the two models
disagree, the repository wins. So there is one state machine, and it is this
one.

Nothing here mutates anything: every answer comes from git reads and the
recorded status block.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .git_topology import GitTopologyAnalyzer


_STATUS_BLOCK_RE = re.compile(r"```yaml\s*\n(#\s*status-block:.*?)```", re.S)
_SHA_RE = re.compile(r"\b[0-9a-fA-F]{7,40}\b")


class RepositoryState(str, Enum):
    FINALIZED = "FINALIZED"
    PRODUCING = "PRODUCING"
    BASELINE = "BASELINE"
    ILLEGAL = "ILLEGAL"
    UNDETERMINED = "UNDETERMINED"


class StateResolution(BaseModel):
    """Which legal state the repository is in, and the evidence for it."""

    model_config = ConfigDict(extra="ignore")

    state: RepositoryState = RepositoryState.UNDETERMINED
    recorded_commit: str = ""
    recorded_tree: str = ""
    head_commit: str = ""
    reason: str = ""

    #: The certified pair this state rests on. In PRODUCING and FINALIZED the
    #: content half is the commit the receipts legitimately name.
    certified_content_commit: str = ""
    certified_metadata_commit: str = ""

    #: Files a metadata commit carried that a metadata commit may not carry.
    stray_files: list[str] = Field(default_factory=list)
    source_path: str = ""

    @property
    def legal(self) -> bool:
        return self.state in (
            RepositoryState.FINALIZED,
            RepositoryState.PRODUCING,
            RepositoryState.BASELINE,
        )

    @property
    def determined(self) -> bool:
        return self.state is not RepositoryState.UNDETERMINED

    def describe(self) -> str:
        return f"{self.state.value}: {self.reason}"


def _status_block(text: str) -> dict:
    """The machine-maintained status block, however the repository writes it.

    Two shapes are in use and both are legitimate: a fenced ```yaml block, and a
    bare ``# status-block:``/``# derived-block:`` marker followed by plain
    ``key: value`` lines. Reading only one of them makes the state machine
    silently UNDETERMINED against a repository that is perfectly well formed.
    """
    match = _STATUS_BLOCK_RE.search(text)
    if match:
        try:
            block = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            block = None
        if isinstance(block, dict):
            return block

    # Unfenced marker form.
    out: dict = {}
    collecting = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.search(r"#\s*(?:status|derived)[-_ ]block:", stripped, re.I):
            collecting = True
            continue
        if collecting and re.search(r"#\s*end-(?:status|derived)[-_ ]block", stripped, re.I):
            break
        if not collecting:
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(\S.*?)\s*$", stripped)
        if m:
            out[m.group(1)] = m.group(2).strip().strip("\"'")
    return out


def _nested_lookup(data: object, key: str, depth: int = 0) -> str:
    """Find ``key`` anywhere in a nested mapping — derived blocks are nested."""
    if depth > 6 or not isinstance(data, dict):
        return ""
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    for nested in data.values():
        if isinstance(nested, dict):
            found = _nested_lookup(nested, key, depth + 1)
            if found:
                return found
        elif isinstance(nested, list):
            for item in nested:
                found = _nested_lookup(item, key, depth + 1)
                if found:
                    return found
    return ""


def read_recorded_state(repo: Path, status_rels: list[str]) -> tuple[str, str, str]:
    """``(recorded_commit, recorded_tree, source_path)`` from the status record.

    The machine-maintained status block is the repository's own authority on
    which content commit its derived status describes.
    """
    for rel in status_rels:
        path = repo / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue

        block = _status_block(text)
        commit = str(block.get("content_commit", "") or "")
        tree = str(block.get("content_tree", "") or "")

        if not commit and not rel.endswith(".md"):
            # A YAML status surface nests the derived values under a block key.
            try:
                loaded = yaml.safe_load(text)
            except (yaml.YAMLError, ValueError):
                loaded = None
            commit = _nested_lookup(loaded, "content_commit")
            tree = _nested_lookup(loaded, "content_tree")

        commit, tree = commit.strip(), tree.strip()
        if commit and _SHA_RE.fullmatch(commit):
            return commit, tree, rel
    return "", "", ""


def resolve_state(
    analyzer: "GitTopologyAnalyzer",
    status_rels: list[str] | None = None,
) -> StateResolution:
    """Compute the repository's state exactly as the repository defines it."""
    from .git_topology import _matches  # local import: avoids a cycle

    repo = analyzer.repo
    rels = list(status_rels or [])
    if not rels:
        # The status RECORD is repo-relative; `protocol.status_paths` holds bare
        # file names (they are matched against a commit's changed files), so the
        # discovered source categories are what can actually be opened.
        by_cat = analyzer.protocol.sources.by_category
        for category in ("current", "status", "build_status", "registry"):
            rels.extend(by_cat.get(category) or [])
        # Fall back to bare names resolved under the status directory of a
        # category we did find, so an unusual layout still resolves.
        for name in analyzer.protocol.status_paths:
            if name in rels:
                continue
            for known in list(rels):
                candidate = str(Path(known).parent / name)
                if (repo / candidate).exists() and candidate not in rels:
                    rels.append(candidate)
    # De-duplicate while keeping precedence order.
    seen: set[str] = set()
    rels = [r for r in rels if not (r in seen or seen.add(r))]

    recorded, recorded_tree, source = read_recorded_state(repo, rels)
    head = analyzer._git("rev-parse", "HEAD")

    res = StateResolution(
        recorded_commit=recorded,
        recorded_tree=recorded_tree,
        head_commit=head,
        source_path=source,
    )
    if not recorded or not head:
        res.state = RepositoryState.UNDETERMINED
        res.reason = (
            "the repository records no content_commit in its status block, so its own "
            "state machine cannot be evaluated"
        )
        return res

    # A recorded tree that is not the recorded commit's tree is a forgery in
    # EVERY state — the repository checks this before anything else, and so do we.
    if recorded_tree:
        actual = analyzer._git("rev-parse", f"{recorded}^{{tree}}")
        if actual and not _matches(recorded_tree, actual):
            res.state = RepositoryState.ILLEGAL
            res.reason = (
                f"the recorded content_tree {recorded_tree[:12]} is not the tree of the "
                f"recorded commit {recorded[:12]} ({actual[:12]})"
            )
            return res

    def pure_metadata(sha: str) -> tuple[bool, list[str]]:
        """A metadata commit may touch only finalizer-owned status/evidence files."""
        if not sha:
            return False, []
        files = analyzer._commit_files(sha)
        if not files:
            return False, []
        stray = [f for f in files if analyzer.classify_path(f).value == "CONTENT"]
        return (not stray), stray

    head1 = analyzer._first_parent(head)
    head2 = analyzer._first_parent(head1) if head1 else ""

    if _matches(recorded, head):
        res.state = RepositoryState.BASELINE
        res.certified_content_commit = head
        res.reason = (
            f"the recorded content commit {recorded[:12]} is HEAD — pre-convention "
            "baseline state"
        )
        return res

    if head1 and _matches(recorded, head1):
        ok, stray = pure_metadata(head)
        if not ok:
            res.state = RepositoryState.ILLEGAL
            res.stray_files = stray
            res.reason = (
                f"HEAD {head[:12]} records {recorded[:12]} but carries non-status files "
                f"{stray[:4]} — a metadata commit that smuggles substantive change"
            )
            return res
        res.state = RepositoryState.FINALIZED
        res.certified_content_commit = head1
        res.certified_metadata_commit = head
        res.reason = (
            f"the recorded content commit {recorded[:12]} is HEAD^ and HEAD {head[:12]} is a "
            "pure status-metadata commit — the at-rest finalized state"
        )
        return res

    if head2 and _matches(recorded, head2):
        ok, stray = pure_metadata(head1)
        if not ok:
            res.state = RepositoryState.ILLEGAL
            res.stray_files = stray
            res.reason = (
                f"HEAD^ {head1[:12]} is not a pure status-metadata commit (carries {stray[:4]}) "
                "— this is two unfinalized content commits, which the convention forbids"
            )
            return res
        res.state = RepositoryState.PRODUCING
        res.certified_content_commit = head2
        res.certified_metadata_commit = head1
        res.reason = (
            f"the recorded content commit {recorded[:12]} is HEAD^^, HEAD^ {head1[:12]} is a "
            f"pure status-metadata commit, and HEAD {head[:12]} is the next content commit — "
            "the producing state"
        )
        return res

    res.state = RepositoryState.ILLEGAL
    res.reason = (
        f"the status record names {recorded[:12]} but HEAD is {head[:12]}, which is neither "
        "HEAD, HEAD^ nor HEAD^^ of it — the status authority is stale beyond every legal state"
    )
    return res
