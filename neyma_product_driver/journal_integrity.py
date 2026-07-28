"""Run-journal evidence is acceptance evidence, so it must actually exist.

A run was accepted whose journal artifacts were zero bytes. Nothing failed,
because nothing checked: the files were present, the directory looked complete,
and "present" was treated as "captured". An empty `git-status.txt` is not a
record of a clean tree — it is a record of a capture that did not happen, and it
is indistinguishable from one unless emptiness is itself an error.

So: a required artifact that is missing, or present-but-empty, fails the run
closed. To keep that rule from firing on legitimately empty output, the writers
record an explicit marker instead of nothing (see `empty_marker`). A genuinely
clean tree therefore produces meaningful evidence, and zero bytes always means
the capture failed.

This module also owns the rule that a MISSING LOG PROVES NOTHING about a
process. `nohup` survives its parent and buffers output, so a log that has not
appeared yet is not a dead process — inferring death from an absent log is what
launched a second finalizer over the top of a live one. Liveness questions go to
`ownership.py`, where `flock` answers them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .run_journal import JOURNAL_FILE, SUMMARY_FILE

#: Per-iteration artifacts every completed iteration must carry. `commands.log`
#: is the command log PD-12 names: without it there is no record of what was
#: actually executed, only of what was decided.
REQUIRED_ITERATION_ARTIFACTS = (
    "git-status.txt",
    "git-diff-stat.txt",
    "commands.log",
    "record.json",
)

#: Run-level artifacts every completed run must carry.
REQUIRED_RUN_ARTIFACTS = (
    JOURNAL_FILE,
    SUMMARY_FILE,
)


def empty_marker(kind: str) -> str:
    """What to write when a capture legitimately produced no output.

    Never write nothing: zero bytes must keep meaning "the capture failed".
    """
    return f"(empty: {kind} produced no output at capture time)\n"


@dataclass
class ArtifactVerdict:
    path: str
    exists: bool = False
    size: int = 0
    ok: bool = False
    reason: str = ""


@dataclass
class JournalIntegrityResult:
    """Whether a run's evidence is admissible."""

    ok: bool = True
    verdicts: list[ArtifactVerdict] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "failures": list(self.failures),
            "artifacts": [
                {"path": v.path, "exists": v.exists, "size": v.size, "ok": v.ok, "reason": v.reason}
                for v in self.verdicts
            ],
        }

    def render(self) -> str:
        if self.ok:
            return f"run-journal evidence complete ({len(self.verdicts)} artifact(s) verified)"
        lines = ["run-journal evidence is NOT admissible:"]
        lines += [f"  - {f}" for f in self.failures]
        return "\n".join(lines)


def _check(path: Path, label: str) -> ArtifactVerdict:
    verdict = ArtifactVerdict(path=label)
    if not path.exists():
        verdict.reason = "missing — the run produced no such evidence"
        return verdict
    verdict.exists = True
    try:
        verdict.size = path.stat().st_size
    except OSError as exc:
        verdict.reason = f"unreadable: {exc}"
        return verdict
    if verdict.size == 0:
        verdict.reason = (
            "zero bytes — an empty artifact records a capture that did not happen, not a "
            "state that was empty"
        )
        return verdict
    verdict.ok = True
    return verdict


def iteration_dir(run_dir: Path, iteration: int) -> Path:
    """The directory for one iteration, whichever padding the store used.

    `EvidenceStore` writes `iteration-01`. Guessing a different padding here and
    then reporting the artifacts "missing" would fail a perfectly complete run
    on a naming difference — a false accusation is as bad as a missed one, so
    every layout the store has used is accepted.
    """
    run_dir = Path(run_dir)
    for name in (
        f"iteration-{iteration:02d}",
        f"iteration-{iteration:03d}",
        f"iteration-{iteration}",
    ):
        candidate = run_dir / name
        if candidate.exists():
            return candidate
    return run_dir / f"iteration-{iteration:02d}"


def verify_iteration_evidence(run_dir: Path, iteration: int) -> JournalIntegrityResult:
    """Every required artifact for one iteration exists and is non-empty."""
    base = iteration_dir(run_dir, iteration)

    result = JournalIntegrityResult()
    for name in REQUIRED_ITERATION_ARTIFACTS:
        verdict = _check(base / name, f"{base.name}/{name}")
        result.verdicts.append(verdict)
        if not verdict.ok:
            result.ok = False
            result.failures.append(f"{verdict.path}: {verdict.reason}")
    return result


def verify_run_evidence(
    run_dir: Path, iterations: list[int] | None = None
) -> JournalIntegrityResult:
    """Every required run-level and per-iteration artifact is real evidence.

    Fails closed: an unverifiable run is not an accepted run.
    """
    run_dir = Path(run_dir)
    result = JournalIntegrityResult()

    if not run_dir.exists():
        result.ok = False
        result.failures.append(f"{run_dir}: the run directory does not exist")
        return result

    for name in REQUIRED_RUN_ARTIFACTS:
        verdict = _check(run_dir / name, name)
        result.verdicts.append(verdict)
        if not verdict.ok:
            result.ok = False
            result.failures.append(f"{verdict.path}: {verdict.reason}")

    if iterations is None:
        iterations = []
        for child in sorted(run_dir.glob("iteration-*")):
            if not child.is_dir():
                continue
            suffix = child.name.split("-", 1)[-1]
            try:
                iterations.append(int(suffix))
            except ValueError:
                continue

    for iteration in iterations:
        sub = verify_iteration_evidence(run_dir, iteration)
        result.verdicts.extend(sub.verdicts)
        if not sub.ok:
            result.ok = False
            result.failures.extend(sub.failures)

    return result


class JournalEvidenceMissing(Exception):
    """Required run-journal evidence is absent or empty. The run does not pass."""


def require_run_evidence(run_dir: Path, iterations: list[int] | None = None) -> JournalIntegrityResult:
    """Verify, or raise. Use where acceptance is being decided."""
    result = verify_run_evidence(run_dir, iterations)
    if not result.ok:
        raise JournalEvidenceMissing(result.render())
    return result


# --------------------------------------------------------------------------
# PD-11: what a missing log does and does not prove
# --------------------------------------------------------------------------


def log_absence_proves_nothing(log_path: Path, pid: int | None = None) -> str:
    """Explain why an absent log is not evidence a process died.

    Returned as a string rather than a boolean because there is no boolean to
    return: the question "is it dead?" cannot be answered from this input at
    all. Ask `ownership.finalizer_running` instead.
    """
    detail = f"{log_path} is not present"
    if pid is not None:
        detail += f" for pid {pid}"
    return (
        f"{detail}. This proves nothing about whether the process is running: nohup "
        "survives its parent, output is buffered, and the file may simply not have been "
        "flushed yet. Do not start a replacement on this basis. The finalizer lock is "
        "authoritative — attach to or wait on the existing owner, or stop safely."
    )
