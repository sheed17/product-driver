"""Run-artifact storage.

Everything the driver observes lands under ``runs/<run-id>/``. Every string is
passed through :func:`~neyma_product_driver.models.redact` on the way in, and
anything resembling a full environment dump is dropped rather than stored.

Layout::

    runs/<run-id>/
        state.json                 RunState, rewritten after every change
        STOP                       sentinel file; presence requests a stop
        iteration-01/
            record.json            IterationRecord
            builder-summary.md
            git-status.txt
            git-diff-stat.txt
            commands.log
            scenario.json
            decision.json
            correction-prompt.md
            screenshots/*.png
            trace.zip
        accepted/                  copy of the accepted iteration's evidence
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .journal_integrity import empty_marker
from .models import (
    IterationRecord,
    RunState,
    looks_like_env_dump,
    redact,
    redact_obj,
)

STOP_SENTINEL = "STOP"
STATE_FILE = "state.json"

#: How many consecutive numbers :meth:`EvidenceStore.allocate_iteration` will
#: try before refusing. It only ever walks past directories that already exist,
#: so reaching the end means something is wrong with the run directory itself —
#: and failing closed there is correct, because the alternative is writing over
#: an iteration that already happened.
_ALLOCATION_ATTEMPTS = 1000


class IterationAllocationError(RuntimeError):
    """No unused iteration number could be claimed. Never overwrite; refuse."""

# Cap on any single stored text blob. Prevents a runaway log from filling disk
# and keeps evidence readable.
MAX_BLOB_CHARS = 400_000


def new_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%d-%H%M%S")


def _safe_text(text: str | None) -> str:
    """Redact, drop env dumps, and truncate."""
    if not text:
        return ""
    cleaned = redact(str(text))
    if looks_like_env_dump(cleaned):
        return "[REDACTED:environment-dump omitted by policy]"
    if len(cleaned) > MAX_BLOB_CHARS:
        head = cleaned[: MAX_BLOB_CHARS // 2]
        tail = cleaned[-MAX_BLOB_CHARS // 2 :]
        return f"{head}\n\n...[truncated {len(cleaned) - MAX_BLOB_CHARS} chars]...\n\n{tail}"
    return cleaned


class EvidenceStore:
    """Filesystem-backed evidence and state store for one run."""

    def __init__(self, runs_dir: Path, run_id: str) -> None:
        self.runs_dir = Path(runs_dir)
        self.run_id = run_id
        self.run_dir = self.runs_dir / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

    # -- paths ------------------------------------------------------------

    @property
    def state_path(self) -> Path:
        return self.run_dir / STATE_FILE

    @property
    def stop_path(self) -> Path:
        return self.run_dir / STOP_SENTINEL

    def iteration_dir(self, iteration: int) -> Path:
        """The directory for ONE iteration number, created if absent.

        Addressing, never allocation: it says where iteration *n* lives and is
        how everything that writes into the CURRENT iteration — and everything
        that reads a past one — finds it. Which number a NEW iteration may take
        is :meth:`allocate_iteration`, and nothing else may decide it.
        """
        d = self.run_dir / f"iteration-{iteration:02d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def existing_iterations(self) -> list[int]:
        """Every iteration number this run already has a directory for, ascending.

        Read off the filesystem, because the filesystem is what a resume
        actually has: a new process holds no memory of the last one, and the
        state file it loads is a claim that can be stale, truncated or older
        than the directories beside it.

        Padding is not assumed. The store writes ``iteration-01``, but a
        directory written by another version, or by a hand-run command, still
        occupies its number and must still be counted — the point is to find
        what is TAKEN, and a number this cannot see is a number this would
        hand out twice.
        """
        found: set[int] = set()
        for child in self.run_dir.glob("iteration-*"):
            if not child.is_dir():
                continue
            suffix = child.name.split("-", 1)[-1]
            try:
                number = int(suffix)
            except ValueError:
                continue
            if number > 0:
                found.add(number)
        return sorted(found)

    def next_iteration(self, state: "RunState | None" = None) -> int:
        """The lowest number no iteration has ever used here. One calculation.

        ### WHY THIS EXISTS AT ALL. The control loop numbered its iterations
        ``range(1, max_iterations + 1)`` — from ONE, every time, including on a
        resume. So the second process wrote ``iteration-01/`` over the first
        process's ``iteration-01/``: its record, its decision, its suite
        result, its evaluator prompt and its per-scenario evidence, replaced in
        place by a different iteration's. Run 20260903-065810 is what that
        cost — its ``iteration-01/record.json`` holds the resumed iteration,
        and the original survives only inside ``state.json``'s own copy.
        Historical evidence is the one thing a verification harness may never
        rewrite: everything it later reports rests on it.

        **The maximum of every source, never the minimum.** Directories on
        disk, the highest iteration in the state's own records, and the
        state's current pointer are three claims about the same fact, and they
        disagree exactly when something went wrong — a crash between writing
        evidence and saving state, a truncated state file, an evidence
        directory restored from a copy. Taking the maximum and adding one is
        the conservative reading of ANY disagreement: at worst it skips a
        number, which costs nothing, and it cannot be talked into a number that
        is already taken. Taking a minimum, or trusting one source, is how the
        old bug destroyed evidence.
        """
        highest = max(self.existing_iterations(), default=0)
        if state is not None:
            highest = max(
                highest,
                int(getattr(state, "iteration", 0) or 0),
                max(
                    (int(getattr(r, "iteration", 0) or 0) for r in getattr(state, "iterations", [])),
                    default=0,
                ),
            )
        return highest + 1

    def allocate_iteration(self, state: "RunState | None" = None) -> int:
        """Claim the next unused iteration number, by creating its directory.

        Returns the number claimed. The claim IS the directory: ``mkdir``
        without ``exist_ok`` is atomic, so a number can be handed out only to
        whoever created it, and an existing directory can never be handed out
        at all. Where one is found — a stale state pointer, a gap filled by
        something else, two processes on one run — the answer is the NEXT free
        number, never the existing directory: reallocation, never replacement.

        A crash between this and the first artifact leaves an empty iteration
        directory. That is the same shape as a crash part-way through any
        iteration, which already leaves an incomplete one; both are visible as
        an iteration missing its required artifacts, and neither destroys
        anything that was written before.
        """
        candidate = self.next_iteration(state)
        for number in range(candidate, candidate + _ALLOCATION_ATTEMPTS):
            try:
                (self.run_dir / f"iteration-{number:02d}").mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            return number
        raise IterationAllocationError(
            f"no free iteration number for run {self.run_id} in "
            f"{candidate}..{candidate + _ALLOCATION_ATTEMPTS - 1}; refusing to reuse one "
            "rather than write over an existing iteration's evidence"
        )

    def screenshots_dir(self, iteration: int) -> Path:
        d = self.iteration_dir(iteration) / "screenshots"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- writing ----------------------------------------------------------

    def write_text(self, relative: str | Path, text: str) -> Path:
        path = self.run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_safe_text(text), encoding="utf-8")
        return path

    def write_json(self, relative: str | Path, data: Any) -> Path:
        path = self.run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = redact_obj(data)
        path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        return path

    def append_log(self, relative: str | Path, text: str) -> Path:
        path = self.run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(_safe_text(text).rstrip() + "\n")
        return path

    # -- state ------------------------------------------------------------

    def save_state(self, state: RunState) -> Path:
        state.touch()
        return self.write_json(STATE_FILE, state.model_dump(mode="json"))

    def load_state(self) -> RunState | None:
        if not self.state_path.exists():
            return None
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        try:
            return RunState.model_validate(raw)
        except Exception:
            return None

    # -- stop signalling --------------------------------------------------

    def request_stop(self, reason: str = "") -> Path:
        return self.write_text(STOP_SENTINEL, reason or "stop requested")

    def stop_requested(self) -> bool:
        return self.stop_path.exists()

    def clear_stop(self) -> None:
        self.stop_path.unlink(missing_ok=True)

    # -- iteration persistence -------------------------------------------

    def save_iteration(self, record: IterationRecord) -> Path:
        """Persist one iteration in both machine and human readable form."""
        d = self.iteration_dir(record.iteration)
        rel = d.relative_to(self.run_dir)

        self.write_json(rel / "record.json", record.model_dump(mode="json"))

        if record.builder_summary:
            self.write_text(rel / "builder-summary.md", record.builder_summary)

        if record.git:
            # Never write zero bytes: an empty file must always mean the capture
            # failed, so a legitimately clean tree records that explicitly
            # instead. See journal_integrity.
            self.write_text(
                rel / "git-status.txt",
                record.git.status_porcelain or empty_marker("git status --porcelain"),
            )
            self.write_text(
                rel / "git-diff-stat.txt",
                record.git.diff_stat or empty_marker("git diff --stat"),
            )

        if record.scenario:
            self.write_json(rel / "scenario.json", record.scenario.model_dump(mode="json"))
            lines: list[str] = []
            for group, results in (
                ("setup", record.scenario.setup),
                ("commands", record.scenario.commands),
                ("teardown", record.scenario.teardown),
            ):
                for res in results:
                    lines.append(f"--- {group} ---")
                    lines.append(res.brief())
            if lines:
                self.write_text(rel / "commands.log", "\n".join(lines))

        if record.decision:
            self.write_json(rel / "decision.json", record.decision.model_dump(mode="json"))

        if record.correction_prompt_sent:
            self.write_text(rel / "correction-prompt.md", record.correction_prompt_sent)

        if record.raw_decision is not None:
            self.write_json(
                rel / "rejected-decision.json",
                {
                    "reasons": record.rejected_reasons,
                    "decision": record.raw_decision.model_dump(mode="json"),
                },
            )

        if record.suite:
            # The aggregate. Per-scenario artifacts already live under
            # iteration-NN/scenarios/<scenario-id>/, and each outcome in here
            # carries that path, so evidence and scenario ids stay linked.
            self.write_json(rel / "suite-result.json", record.suite)

        if record.context_provenance:
            self.write_json(rel / "context-provenance.json", record.context_provenance)

        if record.task_scope:
            self.write_json(rel / "task-scope.json", record.task_scope)

        if record.scoped_completion:
            self.write_json(rel / "scoped-completion.json", record.scoped_completion)

        if record.completion_audit:
            self.write_json(rel / "completion-audit.json", record.completion_audit)

        if record.protocol_resolution:
            self.write_json(rel / "protocol-resolution.json", record.protocol_resolution)

        if record.investigation:
            self.write_json(rel / "investigation-result.json", record.investigation)

        if record.independent_review:
            self.write_json(rel / "independent-review.json", record.independent_review)

        return d

    def save_completion_audit(self, iteration: int, audit: dict[str, Any]) -> Path:
        """Persist the completion audit for one iteration."""
        rel = self.iteration_dir(iteration).relative_to(self.run_dir)
        return self.write_json(rel / "completion-audit.json", audit)

    def save_protocol_resolution(
        self, resolution: dict[str, Any], iteration: int | None = None
    ) -> Path:
        """Persist a protocol resolution, per iteration and at the run root.

        The run-root copy is what ``approve`` compares its plan hash against, so
        a plan that changed since it was reported cannot be approved by mistake.
        """
        if iteration is not None:
            rel = self.iteration_dir(iteration).relative_to(self.run_dir)
            self.write_json(rel / "protocol-resolution.json", resolution)
        return self.write_json("protocol-resolution.json", resolution)

    def load_protocol_resolution(self) -> dict[str, Any] | None:
        path = self.run_dir / "protocol-resolution.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def save_phase_closure(self, record: dict[str, Any], iteration: int | None = None) -> Path:
        """Persist the phase-closure attempt, at the run root and per iteration.

        The run-root copy is the resume unit: ``phase close`` and
        ``phase external-evidence`` read it back, exactly as ``approve`` reads
        back the protocol resolution. The per-iteration copy is history.
        """
        from .phase_closure import CLOSURE_FILE

        if iteration is not None:
            rel = self.iteration_dir(iteration).relative_to(self.run_dir)
            self.write_json(rel / CLOSURE_FILE, record)
        return self.write_json(CLOSURE_FILE, record)

    def load_phase_closure(self) -> dict[str, Any] | None:
        from .phase_closure import CLOSURE_FILE

        path = self.run_dir / CLOSURE_FILE
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def save_independent_review(self, iteration: int, review: dict[str, Any]) -> Path:
        """Persist an independent reviewer's findings, without replacing an earlier one.

        A review is a verdict about one exact tree, reached by a session whose
        independence cannot be recovered once spent. Reviewing the same
        iteration again — the ``review`` command run a second time, a
        re-review after a remediation — used to write over the first verdict,
        so what a reviewer actually said stopped existing the moment anyone
        asked again. The current verdict keeps the name every reader already
        looks for; the one it supersedes is moved aside first, under the next
        free ``independent-review-NN.json``, and stays exactly as it was.
        """
        d = self.iteration_dir(iteration)
        current = d / "independent-review.json"
        if current.exists():
            for number in range(2, _ALLOCATION_ATTEMPTS):
                superseded = d / f"independent-review-{number:02d}.json"
                if not superseded.exists():
                    current.rename(superseded)
                    break
            else:  # pragma: no cover - a run with a thousand reviews of one tree
                raise IterationAllocationError(
                    f"iteration {iteration} of run {self.run_id} already holds every "
                    "numbered independent review; refusing to write over one"
                )
        rel = d.relative_to(self.run_dir)
        return self.write_json(rel / "independent-review.json", review)

    def save_prompt_manifest(
        self, iteration: int, provenance: dict[str, Any], prompt: str
    ) -> Path:
        """Persist the assembled evaluator prompt and what informed it.

        Both are redacted on write, like every other artifact.
        """
        d = self.iteration_dir(iteration)
        rel = d.relative_to(self.run_dir)
        self.write_json(rel / "prompt-manifest.json", provenance)
        return self.write_text(rel / "evaluator-prompt.md", prompt)

    def save_accepted(self, iteration: int) -> Path:
        """Copy the accepted iteration's evidence to ``accepted/``."""
        src = self.iteration_dir(iteration)
        dst = self.run_dir / "accepted"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        return dst

    # -- discovery --------------------------------------------------------

    @classmethod
    def latest_run(cls, runs_dir: Path) -> "EvidenceStore | None":
        runs_dir = Path(runs_dir)
        if not runs_dir.exists():
            return None
        candidates = [
            p for p in runs_dir.iterdir() if p.is_dir() and (p / STATE_FILE).exists()
        ]
        if not candidates:
            return None
        latest = max(candidates, key=lambda p: (p / STATE_FILE).stat().st_mtime)
        return cls(runs_dir, latest.name)

    @classmethod
    def open_run(cls, runs_dir: Path, run_id: str) -> "EvidenceStore":
        store = cls(runs_dir, run_id)
        return store


def check_writable(path: Path) -> tuple[bool, str]:
    """Used by ``doctor`` to verify the run-artifact directory is usable."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, str(path)
    except OSError as exc:
        return False, f"{path}: {exc}"


#: Longest filename component this produces. Well inside every filesystem limit
#: the driver runs on, and long enough that the readable prefix stays readable.
FILENAME_LIMIT = 80
#: Hex characters of the digest appended when a name has to be shortened.
_DIGEST_CHARS = 12


def shorten_preserving_identity(value: str, limit: int) -> str:
    """Shorten ``value`` to ``limit`` characters without merging distinct inputs.

    Plain truncation is not a shortening, it is a collision: two labels sharing a
    long prefix become one label, and whatever the second one referred to stops
    existing. That is exactly how a generated scenario disappeared — the suite,
    the evidence directory and the acceptance gate all agreed there had only ever
    been one.

    The result is a readable prefix plus a digest of the *whole* input, so it is
    still recognisable to a human, still deterministic for resume and
    aggregation, and distinct whenever the inputs are distinct. It is auditable
    rather than reversible: callers that need the original keep it (see
    ``GeneratedScenario.proposed_id``).
    """
    if len(value) <= limit:
        return value
    from hashlib import sha256

    digest = sha256(value.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
    keep = max(1, limit - _DIGEST_CHARS - 1)
    return f"{value[:keep]}-{digest}"


def sanitize_filename(name: str) -> str:
    """Make an arbitrary label safe for use as a filename component.

    Distinct labels produce distinct filenames *as the filesystem compares
    them*: two scenarios sharing an 80-character prefix must not share one
    evidence directory, or each would overwrite the other's record and only one
    of them could ever be proven.

    Two things could previously merge two labels into one path, and the digest
    could not separate either, because it was taken over the already-cleaned
    string rather than over the input:

    * **folding.** ``approve twice`` and ``approve-twice`` both clean to
      ``approve-twice``; every label made only of punctuation cleaned to
      ``unnamed``.
    * **case.** ``gen-AUTH-01`` and ``gen-auth-01`` are two strings and one
      directory on APFS and NTFS.

    So the digest is taken over the *original* input and appended whenever
    cleaning changed the string, or whenever the cleaned string is not already
    case-folded. A label that is already lowercase and already filename-safe —
    which every generated scenario id and every shipped scenario name is — is
    returned unchanged.
    """
    from hashlib import sha256

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "unnamed"
    if cleaned != name or cleaned != cleaned.casefold():
        digest = sha256(name.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
        cleaned = f"{cleaned}-{digest}"
    return shorten_preserving_identity(cleaned, FILENAME_LIMIT)
