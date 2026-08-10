"""Exclusive ownership of a repository: who may finalize, who owns the worktree.

Two incidents produced this module.

**Two finalizers ran at once.** `finalize_status.py` had no mutual exclusion, so
a second finalizer started while the first was mid-run. Both deleted receipts,
both re-ran suites, and both wrote derived status — against a moving tree. The
surviving status record described a state neither run actually certified.

**A dead process was inferred from a missing log.** The driver decided a
finalizer had died because an expected log file was not on disk, and started a
replacement. `nohup` outlives its parent and buffers output, so the absence of a
log proves nothing at all: the original was alive the whole time. That inference
is what produced the second finalizer.

So liveness is never inferred from artifacts. It is decided by `flock`, which
the kernel releases when the owning process dies and not one moment sooner:

  * acquiring is non-blocking — a second finalizer fails immediately, before it
    can delete a receipt, run a suite or write status;
  * a lock cannot go stale while its owner lives, because the owner holds the
    file descriptor;
  * a lock left by a dead process is reclaimed automatically, because the kernel
    already released it — no timeout heuristic, and no "the log is missing so it
    must be dead" guess.

The JSON record beside the lock identifies the owner for humans. It is
descriptive only: it never decides whether the lock may be taken.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Generator

FINALIZER_LOCK_NAME = "neyma-finalizer.lock"
BUILDER_LOCK_NAME = "neyma-builder-worktree.lock"


def _git_dir(repo: Path) -> Path:
    """The real .git directory, so a worktree locks with its parent repository."""
    repo = Path(repo)
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            candidate = Path(proc.stdout.strip())
            return candidate if candidate.is_absolute() else repo / candidate
    except (OSError, subprocess.SubprocessError):
        pass
    return repo / ".git"


def process_alive(pid: int) -> bool:
    """Is ``pid`` a live process?

    Signal 0 performs the existence and permission check without delivering
    anything. EPERM means the process exists but belongs to another user — which
    is still very much alive.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


@dataclass
class LockRecord:
    """Who holds a lock, and what they are doing with it."""

    pid: int = 0
    started_at: float = 0.0
    started_at_iso: str = ""
    repository: str = ""
    target_commit: str = ""
    run_id: str = ""
    session_id: str = ""
    host: str = ""
    kind: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "started_at": self.started_at,
            "started_at_iso": self.started_at_iso,
            "repository": self.repository,
            "target_commit": self.target_commit,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "host": self.host,
            "kind": self.kind,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LockRecord":
        return cls(
            pid=int(data.get("pid", 0) or 0),
            started_at=float(data.get("started_at", 0) or 0),
            started_at_iso=str(data.get("started_at_iso", "") or ""),
            repository=str(data.get("repository", "") or ""),
            target_commit=str(data.get("target_commit", "") or ""),
            run_id=str(data.get("run_id", "") or ""),
            session_id=str(data.get("session_id", "") or ""),
            host=str(data.get("host", "") or ""),
            kind=str(data.get("kind", "") or ""),
            extra=dict(data.get("extra") or {}),
        )

    def describe(self) -> str:
        alive = "alive" if process_alive(self.pid) else "not running"
        age = ""
        if self.started_at:
            age = f", started {int(max(0, time.time() - self.started_at))}s ago"
        return (
            f"pid {self.pid} ({alive}{age}) on {self.host or 'this host'}, "
            f"repository {self.repository or '(unknown)'}, "
            f"target {self.target_commit[:12] or '(unknown)'}, "
            f"run {self.run_id or '(none)'}, session {self.session_id or '(none)'}"
        )


class LockHeld(Exception):
    """Another live owner holds this lock. The caller must not proceed."""

    def __init__(self, message: str, owner: LockRecord | None = None) -> None:
        super().__init__(message)
        self.owner = owner


class RepoLock:
    """A repo-local, non-blocking, exclusive lock backed by ``flock``.

    Not reentrant across processes by design: that is the whole point.
    """

    def __init__(
        self,
        repo: Path,
        name: str,
        *,
        kind: str = "",
        target_commit: str = "",
        run_id: str = "",
        session_id: str = "",
    ) -> None:
        self.repo = Path(repo)
        self.path = _git_dir(self.repo) / name
        self.kind = kind or name
        self.target_commit = target_commit
        self.run_id = run_id
        self.session_id = session_id or os.environ.get("NEYMA_SESSION_ID", "")
        self._fd: int | None = None
        self.record: LockRecord | None = None

    # -- inspection (never decides ownership) -----------------------------

    def read_record(self) -> LockRecord | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return LockRecord.from_dict(data)

    def held_by_other(self) -> LockRecord | None:
        """``LockRecord`` if a DIFFERENT live process holds the lock, else None.

        Probes with `flock` rather than reading the record: the record is
        descriptive, the kernel is authoritative.
        """
        if self._fd is not None:
            return None
        if not self.path.exists():
            return None
        try:
            fd = os.open(self.path, os.O_RDONLY)
        except OSError:
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None  # nobody holds it
        except OSError:
            return self.read_record() or LockRecord(kind=self.kind)
        finally:
            os.close(fd)

    # -- acquisition -------------------------------------------------------

    def acquire(self) -> LockRecord:
        """Take the lock, or raise :class:`LockHeld`. Never blocks, never steals."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            owner = self.read_record()
            os.close(fd)
            detail = owner.describe() if owner else "owner details unavailable"
            raise LockHeld(
                f"another {self.kind} already holds {self.path}: {detail}. "
                "The lock is authoritative: do not start a second one, and do not "
                "reclaim it because a log file is missing. Attach to the existing "
                "owner, wait for it, or stop.",
                owner,
            ) from exc

        now = time.time()
        record = LockRecord(
            pid=os.getpid(),
            started_at=now,
            started_at_iso=time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
            repository=str(self.repo),
            target_commit=self.target_commit,
            run_id=self.run_id,
            session_id=self.session_id,
            host=socket.gethostname(),
            kind=self.kind,
        )
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, json.dumps(record.to_dict(), indent=2).encode("utf-8"))
            os.fsync(fd)
        except OSError:
            # The record is descriptive; failing to write it must not silently
            # leave an unlabelled lock held.
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
            raise

        self._fd = fd
        self.record = record
        return record

    def release(self) -> None:
        """Release on every exit path, including failure. Safe to call twice."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            os.ftruncate(fd, 0)
        except OSError:
            pass
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def __enter__(self) -> LockRecord:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


@contextmanager
def finalizer_lock(
    repo: Path,
    *,
    target_commit: str = "",
    run_id: str = "",
    session_id: str = "",
) -> Generator[LockRecord, None, None]:
    """Exclusive right to finalize ``repo``.

    Must be held before ANY suite run, receipt deletion or status write. A second
    finalizer raises :class:`LockHeld` and must exit non-zero having modified
    nothing.
    """
    lock = RepoLock(
        repo,
        FINALIZER_LOCK_NAME,
        kind="finalizer",
        target_commit=target_commit,
        run_id=run_id,
        session_id=session_id,
    )
    record = lock.acquire()
    try:
        yield record
    finally:
        lock.release()


@contextmanager
def builder_worktree_lock(
    repo: Path,
    *,
    target_commit: str = "",
    run_id: str = "",
    session_id: str = "",
) -> Generator[LockRecord, None, None]:
    """Ownership of a product worktree by one builder.

    While held, ref movement and worktree-hiding operations are denied — see
    `command_guard`. The builder owns the materialized tree, so nothing else may
    move the branch out from under it or hide the files it is working on.
    """
    lock = RepoLock(
        repo,
        BUILDER_LOCK_NAME,
        kind="builder-worktree",
        target_commit=target_commit,
        run_id=run_id,
        session_id=session_id,
    )
    record = lock.acquire()
    try:
        yield record
    finally:
        lock.release()


def builder_owns_worktree(repo: Path) -> LockRecord | None:
    """Does a live builder currently own this worktree?"""
    return RepoLock(repo, BUILDER_LOCK_NAME, kind="builder-worktree").held_by_other()


def finalizer_running(repo: Path) -> LockRecord | None:
    """Is a live finalizer holding this repository?"""
    return RepoLock(repo, FINALIZER_LOCK_NAME, kind="finalizer").held_by_other()


def stale_lock_report(repo: Path) -> list[str]:
    """Human-readable notes about lock files nobody holds.

    A lock file whose owner has exited is NOT a problem — `flock` released it
    already and the next acquirer will take it cleanly. This exists so an
    operator can see the difference between "held" and "merely present".
    """
    notes: list[str] = []
    for name, kind in ((FINALIZER_LOCK_NAME, "finalizer"), (BUILDER_LOCK_NAME, "builder-worktree")):
        lock = RepoLock(repo, name, kind=kind)
        if not lock.path.exists():
            continue
        owner = lock.held_by_other()
        if owner is not None:
            notes.append(f"{kind}: HELD — {owner.describe()}")
        else:
            record = lock.read_record()
            notes.append(
                f"{kind}: lock file present but unheld (reclaimable){' — last ' + record.describe() if record else ''}"
            )
    return notes
