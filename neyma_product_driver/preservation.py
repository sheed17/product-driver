"""Local-history transformation: proof of safety, and the means of recovery.

The driver grants broad local git autonomy — status, diff, log, show, add,
commit, repository-authorized finalizers. One category is deliberately harder:
transforming history that already exists (``git commit --amend``, or
consolidating with ``git reset --soft`` and re-committing).

Those are permitted, but never on assertion. Seven mechanical preconditions must
hold, each independently checkable, before :class:`AmendmentAuthorization` will
report itself authorized:

1. every affected commit is unreachable from every known remote-tracking ref;
2. the repository's own protocol requires or permits the transformation;
3. the current branch, HEAD and tree are recorded;
4. a local preservation ref **and** a git bundle exist;
5. the expected resulting topology is stated in advance;
6. the resulting tree and required tests are verified afterwards;
7. the action and its recovery location are recorded.

1–5 gate the action. 6–7 are :meth:`AmendmentAuthorization.verify_result` and
the journal entry the caller writes.

Arbitrary ``git rebase`` is **not** covered and stays unconditionally blocked:
this module can prove an amend or a soft-reset consolidation recoverable, and
cannot yet prove the same of a general rebase.

Nothing here contacts a remote. Push state is read from local remote-tracking
refs, which is exactly as much as a local process can honestly know — and when
it cannot know, :attr:`PushState.determinable` is ``False`` and the
transformation is refused rather than assumed safe.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .worktree_state import (
    WorktreePreservation,
    capture_worktree_state,
    preserve_worktree,
    verify_worktree_restored,
)

PRESERVATION_REF_NAMESPACE = "refs/preservation"


def _git(repo: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _out(repo: Path, *args: str) -> str:
    proc = _git(repo, *args)
    return proc.stdout.strip() if proc.returncode == 0 else ""


# --------------------------------------------------------------------------
# Git identity — what the repository was, exactly
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GitIdentity:
    """A complete, comparable snapshot of where a repository stands.

    Recorded at the start and end of every run, and on both sides of any local
    history change. ``tree`` is the thing that actually matters when checking
    that a rewrite preserved content: two different commits with the same tree
    hold identical files.
    """

    repo: str = ""
    branch: str = ""
    head: str = ""
    tree: str = ""
    status_porcelain: str = ""
    tracked_dirty: int = 0
    untracked: int = 0
    remotes: list[str] = field(default_factory=list)
    remote_tracking_refs: list[str] = field(default_factory=list)
    captured_at: str = ""

    @property
    def clean(self) -> bool:
        return not self.status_porcelain.strip()

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "branch": self.branch,
            "head": self.head,
            "tree": self.tree,
            "tracked_dirty": self.tracked_dirty,
            "untracked": self.untracked,
            "clean": self.clean,
            "remotes": list(self.remotes),
            "remote_tracking_refs": list(self.remote_tracking_refs),
            "status_porcelain": self.status_porcelain,
            "captured_at": self.captured_at,
        }


def capture_identity(repo: Path) -> GitIdentity:
    """Read the repository's full identity. Read-only; never mutates anything."""
    repo = Path(repo)
    status = _out(repo, "status", "--porcelain")
    tracked = _out(repo, "status", "--porcelain", "--untracked-files=no")
    untracked_out = _out(repo, "ls-files", "--others", "--exclude-standard")
    return GitIdentity(
        repo=str(repo),
        branch=_out(repo, "branch", "--show-current"),
        head=_out(repo, "rev-parse", "HEAD"),
        tree=_out(repo, "rev-parse", "HEAD^{tree}"),
        status_porcelain=status,
        tracked_dirty=len([ln for ln in tracked.splitlines() if ln.strip()]),
        untracked=len([ln for ln in untracked_out.splitlines() if ln.strip()]),
        remotes=[r for r in _out(repo, "remote").splitlines() if r.strip()],
        remote_tracking_refs=[
            r for r in _out(repo, "for-each-ref", "--format=%(refname)", "refs/remotes").splitlines()
            if r.strip()
        ],
        captured_at=datetime.now(timezone.utc).isoformat(),
    )


# --------------------------------------------------------------------------
# Precondition 1 — is it actually unpushed?
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PushState:
    """Whether a set of commits is reachable from any remote-tracking ref."""

    unpushed: tuple[str, ...] = ()
    pushed: tuple[str, ...] = ()
    determinable: bool = True
    detail: str = ""

    @property
    def all_unpushed(self) -> bool:
        # An empty set proves nothing. "No commits were found to be pushed" is
        # not the same statement as "these commits are unpushed", and treating
        # them alike would authorize a rewrite on the strength of a failed
        # rev-list.
        return self.determinable and bool(self.unpushed) and not self.pushed


def push_state(repo: Path, commits: list[str]) -> PushState:
    """Classify ``commits`` against the local remote-tracking refs.

    Three genuinely different answers, kept apart:

    * **no remotes at all** — nothing can have been published, so every commit
      is unpushed and the answer is determinable;
    * **remotes exist and remote-tracking refs exist** — ``git branch -r
      --contains`` gives a real answer per commit;
    * **remotes exist but no remote-tracking refs** — the local clone has no
      idea what the remote holds. That is *not* the same as "unpushed", so
      ``determinable`` is ``False`` and the caller must refuse.
    """
    repo = Path(repo)
    if not commits:
        # Fail closed. An empty list usually means an upstream rev-list failed,
        # and a silent "nothing is pushed" would be the most dangerous possible
        # answer to give.
        return PushState((), (), False, "no commits were supplied to classify")

    remotes = [r for r in _out(repo, "remote").splitlines() if r.strip()]
    if not remotes:
        return PushState(tuple(commits), (), True, "repository has no remotes configured")

    tracking = [
        r for r in _out(repo, "for-each-ref", "--format=%(refname)", "refs/remotes").splitlines()
        if r.strip()
    ]
    if not tracking:
        return PushState(
            (),
            (),
            False,
            f"remotes are configured ({', '.join(remotes)}) but this clone holds no "
            "remote-tracking refs, so push state cannot be determined locally",
        )

    unpushed: list[str] = []
    pushed: list[str] = []
    for sha in commits:
        proc = _git(repo, "branch", "-r", "--contains", sha)
        if proc.returncode != 0:
            return PushState(
                (), (), False, f"could not determine push state for {sha}: {proc.stderr.strip()}"
            )
        if proc.stdout.strip():
            pushed.append(sha)
        else:
            unpushed.append(sha)

    return PushState(
        tuple(unpushed),
        tuple(pushed),
        True,
        f"classified {len(commits)} commit(s) against {len(tracking)} remote-tracking ref(s)",
    )


def commits_in_range(repo: Path, base: str, tip: str = "HEAD") -> list[str]:
    """The commits ``base..tip``, oldest first.

    Raises on a git failure rather than returning ``[]``. An empty list and a
    failed lookup mean opposite things to a caller deciding whether a rewrite is
    safe, and the caller must not have to guess which one it got.
    """
    proc = _git(Path(repo), "rev-list", "--reverse", f"{base}..{tip}")
    if proc.returncode != 0:
        raise ValueError(
            f"could not list commits {base}..{tip} in {repo}: {proc.stderr.strip()}"
        )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


# --------------------------------------------------------------------------
# Preconditions 3–5 — record, preserve, and state the expected result
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PreservationRecord:
    """Where the pre-change state can be recovered from."""

    ref: str = ""
    ref_target: str = ""
    bundle_path: str = ""
    identity: GitIdentity | None = None
    created_at: str = ""
    errors: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """Both a ref and a bundle exist. Either alone is not enough.

        The ref recovers the commits while this clone survives; the bundle
        survives the clone being deleted. Recoverability means both.
        """
        return bool(self.ref and self.ref_target and self.bundle_path and not self.errors)

    def to_dict(self) -> dict:
        return {
            "ref": self.ref,
            "ref_target": self.ref_target,
            "bundle_path": self.bundle_path,
            "complete": self.complete,
            "created_at": self.created_at,
            "errors": list(self.errors),
            "identity": self.identity.to_dict() if self.identity else None,
            "recovery": self.recovery_instructions(),
        }

    def recovery_instructions(self) -> list[str]:
        if not self.ref_target:
            return []
        return [
            f"git reset --hard {self.ref_target}   # restore the exact pre-change commit",
            f"git log {self.ref}                   # inspect the preserved history",
            f"git bundle verify {self.bundle_path} # verify the offline copy",
            f"git fetch {self.bundle_path} '*:*'   # recover from the bundle into any clone",
        ]


def create_preservation(
    repo: Path,
    preservation_dir: Path,
    *,
    label: str = "amend",
    now: datetime | None = None,
) -> PreservationRecord:
    """Write a local backup ref and a git bundle for the current branch.

    Both are local artifacts. Nothing is contacted, nothing is published — a
    bundle is a file, and the ref lives under ``refs/preservation/`` where no
    ordinary branch operation will touch it.
    """
    repo = Path(repo)
    preservation_dir = Path(preservation_dir)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    identity = capture_identity(repo)
    errors: list[str] = []

    if not identity.head:
        return PreservationRecord(
            identity=identity,
            created_at=stamp,
            errors=("repository has no HEAD to preserve",),
        )

    safe_branch = "".join(c if c.isalnum() or c in "-_." else "-" for c in identity.branch or "detached")
    ref = f"{PRESERVATION_REF_NAMESPACE}/{stamp}-{label}-{safe_branch}"

    proc = _git(repo, "update-ref", ref, identity.head)
    if proc.returncode != 0:
        errors.append(f"could not create preservation ref {ref}: {proc.stderr.strip()}")
        ref = ""

    bundle_path = ""
    try:
        preservation_dir.mkdir(parents=True, exist_ok=True)
        candidate = preservation_dir / f"{stamp}-{label}-{safe_branch}.bundle"
        # `--all` captures every local ref, so the bundle is a complete offline
        # recovery point rather than just the branch tip.
        proc = _git(repo, "bundle", "create", str(candidate), "--all", timeout=300)
        if proc.returncode == 0 and candidate.exists():
            bundle_path = str(candidate)
        else:
            errors.append(f"could not create bundle: {proc.stderr.strip()}")
    except (OSError, subprocess.SubprocessError) as exc:
        errors.append(f"could not create bundle: {exc}")

    return PreservationRecord(
        ref=ref,
        ref_target=identity.head,
        bundle_path=bundle_path,
        identity=identity,
        created_at=stamp,
        errors=tuple(errors),
    )


# --------------------------------------------------------------------------
# The authorization itself
# --------------------------------------------------------------------------


@dataclass
class AmendmentAuthorization:
    """The outcome of checking every precondition for one history change."""

    repo: str = ""
    requested: str = ""
    before: GitIdentity | None = None
    after: GitIdentity | None = None
    push: PushState | None = None
    preservation: PreservationRecord | None = None
    protocol_requires: bool = False
    protocol_evidence: str = ""
    expected_topology: str = ""
    failures: list[str] = field(default_factory=list)
    verified: bool | None = None
    verification_detail: str = ""

    #: Full working-tree preservation, untracked files included. The commit-only
    #: preservation above cannot capture an in-progress episode, which is exactly
    #: what a rewrite is most likely to destroy.
    worktree: "WorktreePreservation | None" = None
    worktree_verified: bool | None = None
    worktree_verification_detail: str = ""

    @property
    def authorized(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "requested": self.requested,
            "authorized": self.authorized,
            "failures": list(self.failures),
            "protocol_requires": self.protocol_requires,
            "protocol_evidence": self.protocol_evidence,
            "expected_topology": self.expected_topology,
            "push_state": {
                "unpushed": list(self.push.unpushed) if self.push else [],
                "pushed": list(self.push.pushed) if self.push else [],
                "determinable": self.push.determinable if self.push else False,
                "detail": self.push.detail if self.push else "",
            },
            "preservation": self.preservation.to_dict() if self.preservation else None,
            "before": self.before.to_dict() if self.before else None,
            "after": self.after.to_dict() if self.after else None,
            "verified": self.verified,
            "verification_detail": self.verification_detail,
            "recovery_point": (
                self.preservation.recovery_instructions() if self.preservation else []
            ),
        }

    def verify_result(self, expected_tree: str | None = None) -> bool:
        """Precondition 6: confirm the resulting tree is what was expected.

        Called *after* the transformation. An amend or a soft-reset
        consolidation must not change file content — only how commits are
        arranged — so the tree hash is the exact right check: it is identical
        when nothing was lost, and different the moment something was.
        """
        assert self.before is not None
        self.after = capture_identity(Path(self.repo))
        target = expected_tree or self.before.tree
        if not self.after.tree:
            self.verified = False
            self.verification_detail = "could not read the resulting tree"
        elif self.after.tree == target:
            self.verified = True
            self.verification_detail = (
                f"tree unchanged at {self.after.tree[:12]}; HEAD moved "
                f"{self.before.head[:12]} -> {self.after.head[:12]}"
            )
        else:
            self.verified = False
            self.verification_detail = (
                f"tree CHANGED: expected {target[:12]}, got {self.after.tree[:12]} — "
                "content was altered by a transformation that should only have "
                "rearranged commits"
            )

        # The committed tree is only half the question. A rewrite that leaves
        # HEAD's tree identical can still have discarded the entire uncommitted
        # episode — the untracked files a soft reset or a checkout quietly drops.
        # Verifying only the commit tree is how a "verified" restoration loses
        # work, so the working tree is verified too, against a preservation that
        # actually captured the untracked files.
        if self.worktree is not None:
            ok, detail, _state = verify_worktree_restored(Path(self.repo), self.worktree)
            self.worktree_verified = ok
            self.worktree_verification_detail = detail
            if not ok:
                self.verified = False
        else:
            self.worktree_verified = False
            self.worktree_verification_detail = (
                "no working-tree preservation was captured, so untracked product files "
                "cannot be proven to have survived; restoration is assumed, not proven"
            )
            self.verified = False

        return bool(self.verified)

    @property
    def restoration_proven(self) -> bool:
        """Both the committed tree AND the working tree were proven identical.

        Not a default. `verified is None` means `verify_result` never ran, and an
        unverified rewrite is a failed rewrite: "the tree is probably fine"
        is precisely the claim that let a lost working tree go unnoticed.
        """
        return self.verified is True and self.worktree_verified is True

    def assert_restoration_proven(self) -> None:
        """Raise unless restoration was actually proven. Verification is mandatory."""
        if self.restoration_proven:
            return
        if self.verified is None:
            raise PreservationNotVerified(
                "verify_result() was never called: a rewrite whose restoration is only "
                "assumed is a failed rewrite"
            )
        detail = "; ".join(
            d for d in (self.verification_detail, self.worktree_verification_detail) if d
        )
        raise PreservationNotVerified(f"restoration was not proven: {detail}")


def authorize_amendment(
    repo: Path,
    preservation_dir: Path,
    *,
    commits: list[str],
    requested: str,
    protocol_requires: bool,
    protocol_evidence: str = "",
    expected_topology: str = "",
    allow_local_history_rewrite: bool = False,
    now: datetime | None = None,
) -> AmendmentAuthorization:
    """Check every precondition, and preserve, before a history change.

    Returns an :class:`AmendmentAuthorization` whose ``authorized`` is ``True``
    only when all of 1–5 hold. A preservation ref and bundle are created as part
    of the check — deliberately, because "we would have preserved it if we had
    gone ahead" is not preservation.

    The caller is responsible for precondition 7: recording the returned object
    (via ``to_dict()``) in the run journal.
    """
    repo = Path(repo)
    auth = AmendmentAuthorization(
        repo=str(repo),
        requested=requested,
        protocol_requires=protocol_requires,
        protocol_evidence=protocol_evidence,
        expected_topology=expected_topology,
    )

    # Precondition 3 — record where we started, before anything else.
    auth.before = capture_identity(repo)
    if not auth.before.head:
        auth.failures.append("repository has no HEAD; nothing to transform")
        return auth

    # The founder switch. Off by default; on, it still proves nothing by itself.
    if not allow_local_history_rewrite:
        auth.failures.append(
            "allow_local_history_rewrite is disabled in the driver configuration"
        )

    # Precondition 2 — the repository's own protocol must ask for this.
    if not protocol_requires:
        auth.failures.append(
            "the repository protocol does not require or permit this transformation; "
            "the driver does not rewrite history to tidy it up"
        )

    # Precondition 5 — the expected result must be stated in advance, so the
    # post-check compares against an intention rather than against whatever
    # happened to occur.
    if not expected_topology.strip():
        auth.failures.append("no expected resulting topology was stated in advance")

    # Precondition 1 — unpushed, provably. An empty commit set is refused
    # outright: there is nothing to have proven.
    if not commits:
        auth.failures.append(
            "no commits were identified as affected, so nothing could be proven "
            "unpushed; the transformation is refused"
        )
    auth.push = push_state(repo, commits)
    if not auth.push.determinable:
        auth.failures.append(
            f"push state is not determinable locally ({auth.push.detail}); "
            "history that might be shared is never transformed"
        )
    elif auth.push.pushed:
        auth.failures.append(
            "these commits are reachable from a remote-tracking ref and are therefore "
            f"shared or pushed: {', '.join(s[:12] for s in auth.push.pushed)}"
        )

    # Precondition 4 — preserve before permitting. Done even when an earlier
    # precondition already failed: a recovery point costs nothing and the
    # evidence of the attempt is worth keeping.
    auth.preservation = create_preservation(
        repo, preservation_dir, label="amend", now=now
    )
    if not auth.preservation.complete:
        detail = "; ".join(auth.preservation.errors) or "ref or bundle missing"
        auth.failures.append(f"preservation is incomplete ({detail})")

    # Precondition 4b — preserve the WORKING TREE, untracked files included.
    # The ref and bundle above capture commits; an in-progress episode lives in
    # files git has never been told about, and those are the ones a rewrite
    # loses. A preservation that cannot restore them is not a preservation.
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    auth.worktree = preserve_worktree(
        repo,
        f"{PRESERVATION_REF_NAMESPACE}/{stamp}-worktree",
        message="working-tree preservation before authorized amendment (incl. untracked)",
    )
    if not auth.worktree.complete:
        detail = "; ".join(auth.worktree.errors) or "working-tree ref missing"
        auth.failures.append(
            f"working-tree preservation is incomplete ({detail}); untracked product files "
            "could not be captured, so restoration could not be proven afterwards"
        )

    return auth


class PreservationNotVerified(Exception):
    """A rewrite completed without proving the state was restored."""


class HiddenWorkNotFinalizable(Exception):
    """A finalizer was asked to certify a tree that is not the preserved work."""


def assert_preserved_tree_materialized(
    repo: Path,
    preservation: WorktreePreservation | None,
    *,
    review_artifact: dict | None = None,
) -> None:
    """Refuse to finalize while the run's preserved product tree is not on disk.

    A finalizer certifies *a tree*. If a preservation ref created during this run
    holds the real product work while the working directory holds something else
    — a stashed episode, a checked-out older tree, a reset that dropped untracked
    files — then the thing being certified is not the thing that was built, and
    the resulting receipt attests to a state nobody reviewed.

    The only way past this is an approved exact review artifact that names the
    reviewed commit and tree, states a machine-checkable restoration procedure,
    and proves the intended product tree IS the tree being finalized.
    """
    if preservation is None or not preservation.complete:
        return

    ok, detail = _worktree_matches(repo, preservation)
    if ok:
        return

    if review_artifact:
        approved = bool(review_artifact.get("approved"))
        reviewed_commit = str(review_artifact.get("reviewed_commit", "") or "")
        reviewed_tree = str(review_artifact.get("reviewed_tree", "") or "")
        procedure = str(review_artifact.get("restoration_procedure", "") or "")
        current = capture_worktree_state(Path(repo))
        proves_intended = bool(reviewed_tree) and reviewed_tree == current.tree
        if approved and reviewed_commit and reviewed_tree and procedure and proves_intended:
            return
        raise HiddenWorkNotFinalizable(
            f"{detail}. A review artifact was supplied but does not authorize this: it must be "
            "approved, name the exact reviewed commit and tree, state a machine-checkable "
            "restoration procedure, and prove the intended product tree is the tree being "
            f"finalized (reviewed_tree={reviewed_tree[:12] or '(none)'}, "
            f"worktree={current.tree[:12] or '(none)'})"
        )

    raise HiddenWorkNotFinalizable(
        f"{detail}. A hidden or stashed product tree may not be finalized: the preserved work "
        "is not the work in the worktree, so the receipt would certify a tree nobody built."
    )


def _worktree_matches(repo: Path, preservation: WorktreePreservation) -> tuple[bool, str]:
    ok, detail, _state = verify_worktree_restored(Path(repo), preservation)
    return ok, detail
