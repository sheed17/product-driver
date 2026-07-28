"""Measuring a working tree exactly, including the files git is not tracking.

Every preservation claim the driver makes rests on one question: *is the tree
that exists now byte-identical to the tree that existed before?* Answering it
with `git status` is not enough. Status reports what changed relative to HEAD;
it cannot produce a single value that changes if ANY byte anywhere changed, and
it says nothing durable about untracked files — which, in an in-progress
implementation episode, are often the entire new feature.

So the driver derives a real tree object for the whole working tree, using a
THROWAWAY index so the repository's real index is never touched:

    GIT_INDEX_FILE=<temp>  git read-tree HEAD   # seed the tracked set
    GIT_INDEX_FILE=<temp>  git add -A           # overlay the working tree
    GIT_INDEX_FILE=<temp>  git write-tree       # one hash for everything

Seeding from HEAD matters and is not optional. `git add -A` against an EMPTY
index treats an ignored-but-TRACKED file as untracked-and-ignored and silently
drops it, so the derived tree omits files that are genuinely part of the
repository and the comparison reports a difference that does not exist. Seeding
from HEAD keeps tracked files tracked; the overlay then adds, updates and
removes exactly what the working tree says.

Nothing here writes to the repository's index, refs or working tree.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_GIT_TIMEOUT_S = 300


def _git(repo: Path, *args: str, env: dict[str, str] | None = None,
         timeout: int = _GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
    full = dict(os.environ)
    if env:
        full.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full,
        check=False,
    )


def _out(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    proc = _git(repo, *args, env=env)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _out_raw(repo: Path, *args: str) -> str:
    """Output with LEADING whitespace preserved.

    `git status --porcelain` encodes staged/unstaged in two fixed columns, so
    the first line of a working-tree-only modification starts with a space.
    Stripping the whole output eats it and shifts that one line's path by a
    character — silently turning `src/kernel.py` into `rc/kernel.py`.
    """
    proc = _git(repo, *args)
    return proc.stdout.rstrip("\n") if proc.returncode == 0 else ""


def dirty_paths(repo: Path) -> list[str]:
    """Tracked paths that differ from HEAD or the index, sorted."""
    raw = _out_raw(repo, "status", "--porcelain", "--untracked-files=no")
    out: list[str] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        # "XY <path>", and for a rename "XY <old> -> <new>".
        path = line[3:] if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if path:
            out.append(path)
    return sorted(out)


def untracked_paths(repo: Path) -> list[str]:
    """Untracked, non-ignored paths, sorted."""
    raw = _out(repo, "ls-files", "--others", "--exclude-standard")
    return sorted(ln.strip() for ln in raw.splitlines() if ln.strip())


def index_hash(repo: Path) -> str:
    """A content hash of the repository's real index file.

    Used to prove a recovery left the index untouched. Read only — the file is
    hashed, never opened for writing.
    """
    path = Path(repo) / ".git" / "index"
    if not path.is_file():
        # A worktree or submodule keeps its index elsewhere; ask git.
        common = _out(repo, "rev-parse", "--git-path", "index")
        if common:
            candidate = Path(common)
            path = candidate if candidate.is_absolute() else Path(repo) / candidate
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def worktree_tree_hash(repo: Path) -> tuple[str, str]:
    """``(tree_sha, error)`` for the ENTIRE working tree, untracked included.

    Uses a throwaway index seeded from HEAD. The repository's own index is not
    read for writing and never modified.
    """
    repo = Path(repo)
    with tempfile.TemporaryDirectory(prefix="npd-wt-index-") as tmp:
        index = Path(tmp) / "index"
        env = {"GIT_INDEX_FILE": str(index)}

        head = _out(repo, "rev-parse", "--verify", "HEAD")
        if head:
            proc = _git(repo, "read-tree", head, env=env)
            if proc.returncode != 0:
                return "", f"could not seed the throwaway index from HEAD: {proc.stderr.strip()}"

        proc = _git(repo, "add", "-A", "--", ".", env=env)
        if proc.returncode != 0:
            return "", f"could not stage the working tree: {proc.stderr.strip()}"

        proc = _git(repo, "write-tree", env=env)
        if proc.returncode != 0:
            return "", f"could not write the working tree object: {proc.stderr.strip()}"
        return proc.stdout.strip(), ""


@dataclass(frozen=True)
class WorktreeState:
    """A complete, comparable measurement of a working tree at one moment."""

    tree: str = ""
    index: str = ""
    head: str = ""
    branch: str = ""
    dirty: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()
    file_count: int = 0
    error: str = ""

    @property
    def measured(self) -> bool:
        return bool(self.tree) and not self.error

    def to_dict(self) -> dict:
        return {
            "tree": self.tree,
            "index": self.index,
            "head": self.head,
            "branch": self.branch,
            "dirty": list(self.dirty),
            "untracked": list(self.untracked),
            "file_count": self.file_count,
            "error": self.error,
        }

    def differences(self, other: "WorktreeState") -> list[str]:
        """Human-readable reasons two measurements are not the same tree."""
        out: list[str] = []
        if self.tree != other.tree:
            out.append(
                f"working-tree tree hash changed: {self.tree[:12] or '(none)'} -> "
                f"{other.tree[:12] or '(none)'}"
            )
        lost = sorted(set(self.untracked) - set(other.untracked))
        if lost:
            out.append(f"untracked product files no longer present: {lost[:8]}")
        gone = sorted(set(self.dirty) - set(other.dirty))
        if gone:
            out.append(f"modified product files no longer modified: {gone[:8]}")
        return out


def capture_worktree_state(repo: Path) -> WorktreeState:
    """Measure everything needed to prove a working tree survived unchanged."""
    repo = Path(repo)
    tree, error = worktree_tree_hash(repo)
    count = 0
    if tree:
        listing = _out(repo, "ls-tree", "-r", "--name-only", tree)
        count = len([ln for ln in listing.splitlines() if ln.strip()])
    return WorktreeState(
        tree=tree,
        index=index_hash(repo),
        head=_out(repo, "rev-parse", "HEAD"),
        branch=_out(repo, "branch", "--show-current"),
        dirty=tuple(dirty_paths(repo)),
        untracked=tuple(untracked_paths(repo)),
        file_count=count,
        error=error,
    )


# --------------------------------------------------------------------------
# Preserving a working tree as a real, recoverable object
# --------------------------------------------------------------------------


@dataclass
class WorktreePreservation:
    """A ref whose commit tree IS the full working tree, untracked included."""

    ref: str = ""
    commit: str = ""
    tree: str = ""
    state_before: WorktreeState | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return bool(self.ref and self.commit and self.tree) and not self.errors

    def to_dict(self) -> dict:
        return {
            "ref": self.ref,
            "commit": self.commit,
            "tree": self.tree,
            "complete": self.complete,
            "errors": list(self.errors),
            "state_before": self.state_before.to_dict() if self.state_before else None,
        }


def preserve_worktree(
    repo: Path, ref: str, message: str = "working-tree preservation (incl. untracked)"
) -> WorktreePreservation:
    """Create ``ref`` pointing at a commit whose tree is the whole working tree.

    A preservation ref that points at HEAD preserves only what was already
    committed. The uncommitted work — the entire in-progress episode — is
    exactly what a rewrite is most likely to lose, so it is exactly what the
    preservation ref has to contain.
    """
    repo = Path(repo)
    state = capture_worktree_state(repo)
    pres = WorktreePreservation(state_before=state)
    if not state.measured:
        pres.errors.append(state.error or "could not measure the working tree")
        return pres

    pres.tree = state.tree
    parent = _out(repo, "rev-parse", "--verify", "HEAD")
    args = ["commit-tree", state.tree, "-m", message]
    if parent:
        args += ["-p", parent]
    proc = _git(repo, *args)
    if proc.returncode != 0:
        pres.errors.append(f"could not commit the preserved tree: {proc.stderr.strip()}")
        return pres
    pres.commit = proc.stdout.strip()

    proc = _git(repo, "update-ref", ref, pres.commit)
    if proc.returncode != 0:
        pres.errors.append(f"could not create preservation ref {ref}: {proc.stderr.strip()}")
        return pres
    pres.ref = ref
    return pres


def verify_worktree_restored(
    repo: Path, preservation: WorktreePreservation
) -> tuple[bool, str, WorktreeState]:
    """``(ok, detail, state_after)`` — is the working tree byte-identical again?

    This is the check that makes a preservation claim real rather than assumed.
    """
    state_after = capture_worktree_state(Path(repo))
    if not preservation.complete:
        return False, "the preservation itself did not complete; nothing can be verified against it", state_after
    if not state_after.measured:
        return False, state_after.error or "could not measure the working tree after the change", state_after
    if state_after.tree != preservation.tree:
        before = preservation.state_before
        detail = (
            f"working tree DIFFERS from the preserved state: expected tree "
            f"{preservation.tree[:12]}, got {state_after.tree[:12]}"
        )
        if before is not None:
            diffs = before.differences(state_after)
            if diffs:
                detail += " — " + "; ".join(diffs)
        return False, detail, state_after
    return (
        True,
        f"working tree byte-identical to the preserved state (tree {state_after.tree[:12]}, "
        f"{state_after.file_count} files, {len(state_after.untracked)} untracked)",
        state_after,
    )


def preservation_is_materialized(repo: Path, preservation: WorktreePreservation) -> tuple[bool, str]:
    """Is the preserved tree the tree currently IN the working directory?

    A finalizer must never certify a tree while the product work it was supposed
    to be certifying sits hidden in a ref or a stash. If the preserved tree is
    not the tree on disk, the thing being finalized is not the thing that was
    preserved.
    """
    ok, detail, _ = verify_worktree_restored(repo, preservation)
    return ok, detail
