"""Read-only calibration: what does the Driver actually see in the target repo?

This is the first-supervised-run command. Before trusting the driver to work
unattended, you want to know that it reads the repository the way you do — that
it finds the same active unit, the same phase state, the same open risks, and
the same next step.

**Everything here is derived from the target repository.** No phase name, unit
id, risk id or gate name is written into this module. Whatever unit the
repository marks active is what calibration reports, and when the repository
advances to the next one calibration follows without a code change. A test
asserts this absence directly — it calibrates a synthetic repository whose
vocabulary appears nowhere in the implementation, and greps this file for the
real repository's current identifiers.

**It is strictly read-only.** It runs no Claude session, writes no file, and
executes only read-only git commands. It can be run against a mid-phase dirty
repository at any time without consequence.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .context import ContextResolutionError, RepositoryContextLoader
from .preservation import GitIdentity, capture_identity

# Where repositories in this family keep their registry and status. Discovered
# by glob rather than assumed, so an unusual layout still calibrates.
_REGISTRY_GLOBS = (
    "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
    "docs/implementation/*REGISTRY*.y*ml",
    "**/IMPLEMENTATION-REGISTRY.y*ml",
)
_STATUS_GLOBS = (
    "docs/implementation/BUILD-STATUS.yaml",
    "docs/implementation/*BUILD-STATUS*.y*ml",
    "**/BUILD-STATUS.y*ml",
)

_COMPLETE_STATES = {"COMPLETE", "COMPLETED", "DONE", "ACCEPTED", "CLOSED"}
_ACTIVE_STATES = {"READY", "ACTIVE", "IN_PROGRESS", "IN-PROGRESS", "WIP"}
_BLOCKED_STATES = {"BLOCKED", "WAITING", "ON_HOLD", "ON-HOLD"}


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=False, timeout=30
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _first_existing(repo: Path, globs: tuple[str, ...]) -> Path | None:
    for pattern in globs:
        for match in sorted(repo.glob(pattern)):
            if match.is_file():
                return match
    return None


def _load_yaml(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _units(registry: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the registry's units to a list of dicts with an id."""
    raw = registry.get("units")
    units: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                unit = dict(value)
                unit.setdefault("id", key)
                units.append(unit)
    elif isinstance(raw, list):
        for value in raw:
            if isinstance(value, dict):
                unit = dict(value)
                unit.setdefault("id", unit.get("unit_id") or unit.get("name") or "")
                units.append(unit)
    return units


def _status_of(unit: dict[str, Any]) -> str:
    return str(unit.get("status") or "").strip().upper()


def _phase_of(unit: dict[str, Any]) -> str:
    """The unit's phase, taken from an explicit field or from its id prefix.

    The id prefix is a *derivation*, not a hardcoded phase list: whatever token
    the repository uses becomes the phase name.
    """
    explicit = unit.get("phase") or unit.get("phase_id")
    if explicit:
        return str(explicit).strip()
    unit_id = str(unit.get("id") or "")
    match = re.match(r"^([A-Za-z]+-?\d+)", unit_id)
    return match.group(1) if match else ""


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


@dataclass
class PhaseState:
    """One phase, as the repository describes it."""

    phase: str
    total: int = 0
    complete: int = 0
    active: int = 0
    blocked: int = 0
    unit_ids: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        if self.total and self.complete == self.total:
            return "COMPLETE"
        if self.active:
            return "ACTIVE"
        if self.blocked and not self.active:
            return "BLOCKED"
        if self.complete:
            return "PARTIAL"
        return "NOT STARTED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase, "state": self.state, "total": self.total,
            "complete": self.complete, "active": self.active,
            "blocked": self.blocked, "unit_ids": list(self.unit_ids),
        }


@dataclass
class CalibrationReport:
    """Everything calibration observed. Serializable, and printable."""

    repo: str = ""
    repo_exists: bool = False
    problems: list[str] = field(default_factory=list)

    identity: GitIdentity | None = None
    registry_path: str = ""
    status_path: str = ""

    active_unit_id: str = ""
    active_unit_status: str = ""
    active_unit_objective: str = ""
    acceptance_criteria: list[dict[str, Any]] = field(default_factory=list)
    #: Where the acceptance contract lives, when the registry names a document
    #: instead of enumerating criteria inline.
    acceptance_contract: str = ""
    #: What the repository itself says is left before this unit completes.
    remaining_work: list[str] = field(default_factory=list)
    completion_evidence: list[str] = field(default_factory=list)
    active_unit_error: str = ""

    phases: list[PhaseState] = field(default_factory=list)
    next_eligible: list[dict[str, Any]] = field(default_factory=list)
    blocked_units: list[dict[str, Any]] = field(default_factory=list)
    open_risks: list[Any] = field(default_factory=list)
    dependencies: list[dict[str, Any]] = field(default_factory=list)

    checkpoint_state: dict[str, Any] = field(default_factory=dict)
    review_state: dict[str, Any] = field(default_factory=dict)

    founder_decision_required: str = ""

    @property
    def repo_state(self) -> str:
        if not self.identity:
            return "UNKNOWN"
        if self.identity.clean:
            return "CLEAN"
        return (f"DIRTY ({self.identity.tracked_dirty} tracked, "
                f"{self.identity.untracked} untracked)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repo,
            "repository_exists": self.repo_exists,
            "problems": list(self.problems),
            "git": self.identity.to_dict() if self.identity else None,
            "repository_state": self.repo_state,
            "registry_path": self.registry_path,
            "status_path": self.status_path,
            "active_unit": {
                "id": self.active_unit_id,
                "status": self.active_unit_status,
                "objective": self.active_unit_objective,
                "acceptance_criteria": list(self.acceptance_criteria),
                "acceptance_contract": self.acceptance_contract,
                "remaining_work": list(self.remaining_work),
                "completion_evidence": list(self.completion_evidence),
                "error": self.active_unit_error,
            },
            "phases": [p.to_dict() for p in self.phases],
            "next_eligible_units": list(self.next_eligible),
            "blocked_units": list(self.blocked_units),
            "open_risks": list(self.open_risks),
            "dependencies": list(self.dependencies),
            "checkpoint_state": dict(self.checkpoint_state),
            "review_state": dict(self.review_state),
            "founder_decision_required": self.founder_decision_required,
        }

    def render(self) -> str:
        i = self.identity
        lines = [
            "NEYMA PRODUCT DRIVER — CALIBRATION (read-only)",
            "",
            f"  repository       {self.repo}",
        ]
        if self.problems:
            lines.append("")
            for problem in self.problems:
                lines.append(f"  PROBLEM          {problem}")
            lines.append("")
            lines.append("Calibration cannot proceed until the repository path is correct.")
            return "\n".join(lines)

        lines += [
            f"  branch           {i.branch if i else '?'}",
            f"  HEAD             {(i.head if i else '') or '(none)'}",
            f"  tree             {(i.tree if i else '') or '(none)'}",
            f"  working tree     {self.repo_state}",
            f"  remotes          {', '.join(i.remotes) if i and i.remotes else '(none)'}",
            "",
            f"  registry         {self.registry_path or '(not found)'}",
            f"  status document  {self.status_path or '(not found)'}",
            "",
            "ACTIVE IMPLEMENTATION UNIT",
        ]
        if self.active_unit_error:
            lines.append(f"  UNRESOLVED       {self.active_unit_error}")
        else:
            lines.append(f"  unit             {self.active_unit_id} ({self.active_unit_status})")
            if self.active_unit_objective:
                lines.append(f"  objective        {self.active_unit_objective[:200]}")
        if self.acceptance_criteria:
            lines.append(f"  acceptance       {len(self.acceptance_criteria)} criteria")
            for criterion in self.acceptance_criteria:
                state = str(criterion.get("result") or criterion.get("status") or "PENDING")
                name = str(criterion.get("id") or criterion.get("name")
                           or criterion.get("criterion") or "")
                lines.append(f"                     [{state:<8}] {name[:110]}")
        elif self.acceptance_contract:
            lines.append(f"  acceptance       (contract) {self.acceptance_contract[:150]}")
        else:
            lines.append("  acceptance       (none recorded for this unit yet)")
        if self.remaining_work:
            lines.append(f"  remaining        {len(self.remaining_work)} item(s) before completion")
            for item in self.remaining_work:
                for line in _wrap_value(item, width=108, max_lines=1):
                    lines.append(f"                     - {line}")
        if self.completion_evidence:
            lines.append("  completion       evidence required:")
            for item in self.completion_evidence:
                for line in _wrap_value(item, width=108, max_lines=1):
                    lines.append(f"                     - {line}")

        lines += ["", "PHASE STATE"]
        if self.phases:
            for phase in self.phases:
                lines.append(
                    f"  {phase.phase:<10} {phase.state:<12} "
                    f"{phase.complete}/{phase.total} complete"
                    f"{f', {phase.active} active' if phase.active else ''}"
                    f"{f', {phase.blocked} blocked' if phase.blocked else ''}"
                )
        else:
            lines.append("  (no phases derivable from the registry)")

        lines += ["", "CHECKPOINT / REVIEW STATE"]
        for label, data in (("checkpoint", self.checkpoint_state), ("review", self.review_state)):
            if not data:
                lines.append(f"  {label}: (nothing recorded in the repository)")
                continue
            for key, value in data.items():
                lines.append(f"  {label}.{key}")
                for line in _wrap_value(value):
                    lines.append(f"      {line}")

        lines += ["", "OPEN RISKS"]
        if self.open_risks:
            for risk in self.open_risks:
                rendered = _render_risk(risk)
                lines.append(f"  - {rendered[:150]}")
                if len(rendered) > 150:
                    lines.append(f"    {rendered[150:300]}")
        else:
            lines.append("  (none recorded)")

        lines += ["", "DEPENDENCIES"]
        if self.dependencies:
            for dep in self.dependencies:
                lines.append(
                    f"  {dep['unit']:<10} depends on {dep['depends_on']:<10} "
                    f"[{dep['dependency_status'] or 'UNKNOWN'}]"
                    f"{'  <-- UNMET' if not dep['satisfied'] else ''}"
                )
        else:
            lines.append("  (none recorded)")

        lines += ["", "NEXT ELIGIBLE UNIT"]
        if self.next_eligible:
            for unit in self.next_eligible:
                lines.append(f"  {unit['id']:<10} ({unit['status']}) {unit.get('name', '')[:80]}")
        else:
            lines.append("  (none — every remaining unit is blocked or complete)")

        if self.blocked_units:
            lines += ["", "BLOCKED UNITS"]
            for unit in self.blocked_units:
                lines.append(
                    f"  {unit['id']:<10} blocked by {', '.join(unit['unmet']) or 'declared status'}"
                )

        lines += ["", "FOUNDER DECISION REQUIRED"]
        lines.append(f"  {self.founder_decision_required or 'none'}")
        lines += ["", "Nothing was written. No Claude session was used."]
        return "\n".join(lines)


def _wrap_value(value: Any, width: int = 110, max_lines: int = 4) -> list[str]:
    """Render a status value compactly without hiding it behind an ellipsis."""
    if isinstance(value, list):
        items = [str(v) for v in value]
    elif isinstance(value, dict):
        items = [f"{k}: {v}" for k, v in value.items()]
    else:
        items = [str(value)]
    out: list[str] = []
    for item in items:
        text = " ".join(item.split())
        out.append(text[:width] + ("…" if len(text) > width else ""))
        if len(out) >= max_lines:
            remaining = len(items) - len(out)
            if remaining > 0:
                out.append(f"(+{remaining} more)")
            break
    return out


def _natural_key(name: str) -> tuple:
    """Sort P2 before P10 — lexicographic order misreports phase progression."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", name)
        if part
    )


def _render_risk(risk: Any) -> str:
    if isinstance(risk, dict):
        rid = risk.get("id") or risk.get("risk") or ""
        desc = risk.get("description") or risk.get("summary") or risk.get("title") or ""
        state = risk.get("status") or risk.get("state") or "OPEN"
        return f"{rid} [{state}] {str(desc)[:140]}".strip()
    return str(risk)[:180]


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


def calibrate(repo: Path) -> CalibrationReport:
    """Read the target repository and report what the driver derives from it.

    Read-only throughout: no file is written, no Claude session is started, and
    every git invocation is an inspection command.
    """
    repo = Path(repo)
    report = CalibrationReport(repo=str(repo))

    if not repo.exists():
        report.problems.append(
            f"repository not found: {repo} — set neyma_repo (or --repo) to the "
            "current path; the driver never falls back to a previously-used one"
        )
        return report
    if not (repo / ".git").exists():
        report.problems.append(f"not a git repository (no .git): {repo}")
        return report
    report.repo_exists = True
    report.identity = capture_identity(repo)

    registry_path = _first_existing(repo, _REGISTRY_GLOBS)
    status_path = _first_existing(repo, _STATUS_GLOBS)
    report.registry_path = str(registry_path.relative_to(repo)) if registry_path else ""
    report.status_path = str(status_path.relative_to(repo)) if status_path else ""

    registry = _load_yaml(registry_path)
    status = _load_yaml(status_path)
    units = _units(registry)

    # -- the canonical active unit, via the same loader the run itself uses --
    try:
        unit = RepositoryContextLoader(repo).resolve_active_unit()
        report.active_unit_id = unit.unit_id
        report.active_unit_status = unit.status
        report.active_unit_objective = unit.objective or ""
        report.acceptance_criteria = _criteria_of(unit, units)
        raw_unit = _unit_by_id(units, unit.unit_id)
        if raw_unit:
            report.acceptance_contract = str(raw_unit.get("acceptance_contract") or "")
            report.remaining_work = _string_list(_remaining_work(raw_unit))
            report.completion_evidence = _string_list(raw_unit.get("completion_evidence"))
    except ContextResolutionError as exc:
        report.active_unit_error = str(exc)
    except Exception as exc:  # pragma: no cover - defensive; calibration must not crash
        report.active_unit_error = f"{type(exc).__name__}: {exc}"

    # -- phases, derived from whatever the registry names --------------------
    by_phase: dict[str, PhaseState] = {}
    for unit_data in units:
        phase_name = _phase_of(unit_data)
        if not phase_name:
            continue
        state = by_phase.setdefault(phase_name, PhaseState(phase=phase_name))
        state.total += 1
        state.unit_ids.append(str(unit_data.get("id") or ""))
        status_value = _status_of(unit_data)
        if status_value in _COMPLETE_STATES:
            state.complete += 1
        elif status_value in _ACTIVE_STATES:
            state.active += 1
        elif status_value in _BLOCKED_STATES:
            state.blocked += 1
    report.phases = [by_phase[k] for k in sorted(by_phase, key=_natural_key)]

    # -- dependencies and the next eligible unit -----------------------------
    status_by_id = {str(u.get("id") or ""): _status_of(u) for u in units}
    for unit_data in units:
        unit_id = str(unit_data.get("id") or "")
        for dependency in _dependencies_of(unit_data):
            dep_status = status_by_id.get(dependency, "")
            report.dependencies.append({
                "unit": unit_id,
                "depends_on": dependency,
                "dependency_status": dep_status,
                "satisfied": dep_status in _COMPLETE_STATES,
            })

    for unit_data in units:
        unit_id = str(unit_data.get("id") or "")
        unit_status = _status_of(unit_data)
        if unit_status in _COMPLETE_STATES or unit_id == report.active_unit_id:
            continue
        unmet = [
            dependency for dependency in _dependencies_of(unit_data)
            if status_by_id.get(dependency, "") not in _COMPLETE_STATES
        ]
        entry = {
            "id": unit_id,
            "status": unit_status or "UNSPECIFIED",
            "name": str(unit_data.get("name") or unit_data.get("objective") or ""),
            "unmet": unmet,
        }
        if unmet or unit_status in _BLOCKED_STATES:
            report.blocked_units.append(entry)
        else:
            report.next_eligible.append(entry)

    # -- open risks ----------------------------------------------------------
    report.open_risks = _open_risks(status, registry)

    # -- checkpoint and review state, as recorded by the repository ----------
    report.checkpoint_state = _checkpoint_state(repo, status)
    report.review_state = _review_state(repo, status)

    # -- does this need the founder? -----------------------------------------
    report.founder_decision_required = _decision_required(report)
    return report


def _criteria_of(unit: Any, units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Acceptance criteria for the active unit, normalized to dicts."""
    raw = getattr(unit, "acceptance_criteria", None)
    if not raw:
        for candidate in units:
            if str(candidate.get("id") or "") == getattr(unit, "unit_id", ""):
                raw = candidate.get("acceptance_criteria") or candidate.get("acceptance")
                break
    out: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            entry = dict(value) if isinstance(value, dict) else {"criterion": value}
            entry.setdefault("id", key)
            out.append(entry)
    elif isinstance(raw, list):
        for value in raw:
            out.append(dict(value) if isinstance(value, dict) else {"criterion": str(value)})
    return out


def _unit_by_id(units: list[dict[str, Any]], unit_id: str) -> dict[str, Any] | None:
    for unit in units:
        if str(unit.get("id") or "") == unit_id:
            return unit
    return None


def _remaining_work(unit: dict[str, Any]) -> Any:
    """What the repository says is left, whatever it chose to call the key.

    Repositories name this key after the unit it belongs to, so it is matched by
    shape (a ``remaining*`` prefix) rather than by an enumerated key list —
    which is what keeps this module free of any particular phase name.
    """
    for key, value in unit.items():
        lowered = str(key).lower()
        if lowered.startswith("remaining") or lowered.endswith("_remaining"):
            return value
    return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, dict):
        return [f"{k}: {v}" for k, v in value.items()]
    return [str(value)]


def _dependencies_of(unit: dict[str, Any]) -> list[str]:
    raw = unit.get("dependencies") or unit.get("depends_on") or []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item.get("id") if isinstance(item, dict) else item) for item in raw]
    return []


def _sections(status: dict[str, Any]) -> list[dict[str, Any]]:
    """The status document itself plus its conventional sub-sections.

    Repositories in this family nest most of the interesting state under
    ``snapshot:`` (human-maintained) and ``derived:`` (finalizer-owned). Looking
    only at the top level silently reports "nothing recorded" for a document
    that records a great deal, which is the worst possible calibration answer.
    """
    out = [status]
    for key in ("snapshot", "derived", "state", "current"):
        value = status.get(key)
        if isinstance(value, dict):
            out.append(value)
    return out


def _open_risks(status: dict[str, Any], registry: dict[str, Any]) -> list[Any]:
    for source in (*_sections(status), registry):
        for key in ("open_program_risks", "open_risks", "risks", "program_risks"):
            value = source.get(key)
            if not value:
                continue
            if isinstance(value, list):
                return [
                    risk for risk in value
                    if not (isinstance(risk, dict)
                            and str(risk.get("status") or risk.get("state") or "OPEN").upper()
                            in _COMPLETE_STATES | {"CONTAINED", "CLOSED", "RESOLVED"})
                ]
            if isinstance(value, dict):
                return [
                    {"id": k, **(v if isinstance(v, dict) else {"description": v})}
                    for k, v in value.items()
                ]
    return []


_CHECKPOINT_KEYS = (
    "finalizer_result", "finalizer", "last_finalized", "clean_clone_result",
    "suite_result", "current_phase_percent", "checkpoint", "checkpoints",
    "last_checkpoint", "phase_state", "completed_criteria", "pending_criteria",
    "last_verified_test_evidence", "active_work_unit", "next_approved_unit",
)

_REVIEW_KEYS = (
    "independent_review", "independent_review_status", "review", "reviews",
    "adjudication", "final_adjudication", "verdict",
)

_BLOCKER_KEY_RE = re.compile(r"(?i)^blocker")


def _collect(status: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for section in _sections(status):
        for key in keys:
            if key in section and key not in out:
                out[key] = section[key]
    return out


def _checkpoint_state(repo: Path, status: dict[str, Any]) -> dict[str, Any]:
    """Whatever the repository records about checkpoints and finalization."""
    out = _collect(status, _CHECKPOINT_KEYS)
    # Blocker keys are named after the unit they block (`blockers_to_p4`), so
    # they cannot be enumerated in advance — match the prefix instead.
    for section in _sections(status):
        for key, value in section.items():
            if _BLOCKER_KEY_RE.match(str(key)):
                out.setdefault(key, value)
    head_subject = _git(repo, "log", "-1", "--pretty=%s")
    if head_subject:
        out["head_commit_subject"] = head_subject[:140]
    return out


def _review_state(repo: Path, status: dict[str, Any]) -> dict[str, Any]:
    """Whatever the repository records about independent review."""
    out = _collect(status, _REVIEW_KEYS)
    artifacts = sorted(
        str(p.relative_to(repo))
        for p in repo.glob("docs/implementation/*review*.md")
        if p.is_file()
    )
    if artifacts:
        out["review_artifacts"] = artifacts[:10]
    return out


def _decision_required(report: CalibrationReport) -> str:
    """Whether calibration found something only the founder can settle."""
    if report.active_unit_error:
        return (
            "The repository does not name exactly one active unit "
            f"({report.active_unit_error}). Resolve the registry before an "
            "unattended run — the driver will not choose between candidates."
        )
    if not report.registry_path:
        return ("No implementation registry was found, so no unit, phase or "
                "acceptance criteria could be derived.")
    if report.open_risks and not report.next_eligible and not report.active_unit_id:
        return "Open risks remain and no unit is eligible; the next step is a founder call."
    return ""
