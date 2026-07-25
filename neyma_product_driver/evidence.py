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

from .models import (
    IterationRecord,
    RunState,
    looks_like_env_dump,
    redact,
    redact_obj,
)

STOP_SENTINEL = "STOP"
STATE_FILE = "state.json"

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
        d = self.run_dir / f"iteration-{iteration:02d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

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
            self.write_text(rel / "git-status.txt", record.git.status_porcelain)
            self.write_text(rel / "git-diff-stat.txt", record.git.diff_stat)

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

        if record.context_provenance:
            self.write_json(rel / "context-provenance.json", record.context_provenance)

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

    def save_independent_review(self, iteration: int, review: dict[str, Any]) -> Path:
        """Persist an independent reviewer's findings."""
        rel = self.iteration_dir(iteration).relative_to(self.run_dir)
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


def sanitize_filename(name: str) -> str:
    """Make an arbitrary label safe for use as a filename component."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return cleaned[:80] or "unnamed"
