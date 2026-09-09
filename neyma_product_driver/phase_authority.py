"""Reading a phase's acceptance authority out of the target repository.

The one rule this module exists to hold: **Product Driver never supplies the
definition of done.** It reads what the repository states, reports what the
repository does not state, and stops. A phase that arrives at closure with no
explicit acceptance criteria is an ``AUTHORITY_GAP`` — a founder or architect
decision — and not an invitation to write a plausible list.

Everything is read generically. The registry the Neyma repository happens to
keep is one shape a repository might use, so every key this module looks for is
a list of synonyms and every path is configurable. A repository that names its
criteria ``criteria`` rather than ``acceptance_criteria``, or keeps them in a
different file, is read the same way. Where a fact is genuinely absent, the
resolution says so rather than inventing a default that looks like a fact.

Two defaults are deliberately strict, because the cost of the two directions is
not symmetric:

* a criterion that does not say whether it is required is treated as required;
* a residual that does not say whether it blocks is treated as NOT blocking,
  because "blocking" is a claim about consequence and inferring it from a
  severity word is precisely the inference the closure policy forbids.

Nothing here writes to the target repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import yaml

from .phase_acceptance import (
    AcceptanceCriterion,
    CheckpointRecord,
    CriterionSet,
    FindingClass,
    Residual,
    ResidualLedger,
    ResidualStatus,
)

# --------------------------------------------------------------------------
# Where to look, and what the keys might be called
# --------------------------------------------------------------------------

#: Files that may hold a unit registry. First one that parses into units wins.
DEFAULT_REGISTRY_PATHS: tuple[str, ...] = (
    "docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
    "docs/implementation/registry.yaml",
    "docs/implementation/UNITS.yaml",
    "IMPLEMENTATION-REGISTRY.yaml",
)

_UNITS_KEYS = ("units", "work_units", "phases")
_UNIT_ID_KEYS = ("unit_id", "id", "phase_id", "phase")
_CRITERIA_KEYS = ("acceptance_criteria", "criteria", "acceptance", "phase_acceptance_criteria")
_CHECKPOINT_KEYS = ("landed_checkpoints", "checkpoints", "landed", "increments")
_EXPECTED_CHECKPOINT_KEYS = (
    "expected_checkpoints",
    "planned_checkpoints",
    "checkpoint_count",
    "total_checkpoints",
)
_RESIDUAL_KEYS = (
    "residual_risks_carried_forward",
    "residuals",
    "carried_residuals",
    "residual_risks",
    "open_residuals",
)
_NEXT_KEYS = ("next_units_unlocked", "blocks", "unlocks", "next_units")
_PRODUCTION_KEYS = ("production_enabled", "enabled_in_production", "live", "shipped_to_production")

_CRITERION_ID_KEYS = ("id", "criterion_id", "ac_id", "key")
_CRITERION_NAME_KEYS = ("criterion", "name", "title")
_CRITERION_RESULT_KEYS = ("result", "status", "state", "verdict")
_CRITERION_REQUIREMENT_KEYS = ("requirement", "description", "statement", "demands", "criterion")
_CRITERION_AUTHORITY_KEYS = ("authority", "source", "reference", "citation")
_CRITERION_EVIDENCE_KEYS = (
    "adjudication_evidence",
    "evidence",
    "review_evidence",
    "verification_evidence",
)
_CRITERION_REQUIRED_KEYS = ("required", "mandatory", "must_pass")

_RESIDUAL_ID_KEYS = ("id", "residual_id", "ref")
_RESIDUAL_FINDING_KEYS = ("finding", "description", "issue", "what")
_RESIDUAL_DISPOSITION_KEYS = ("disposition", "resolution", "decision", "rationale")
_RESIDUAL_CLOSURE_KEYS = ("closure_condition", "closes_when", "closes_at", "closed_by", "closes")
_RESIDUAL_BLOCKING_KEYS = ("blocking", "blocks_phase_acceptance", "blocks_acceptance", "blocker")
_RESIDUAL_STATUS_KEYS = ("status", "state", "residual_status")
_RESIDUAL_CLOSURE_EVIDENCE_KEYS = ("closure", "closure_evidence", "closed_by_evidence", "closed_with")

_CHECKPOINT_ID_KEYS = ("id", "checkpoint_id", "ref")
_CHECKPOINT_STATE_KEYS = ("checkpoint_state", "state", "status")
_CHECKPOINT_COMMIT_KEYS = ("candidate_commit", "commit", "sha", "head")
_CHECKPOINT_TREE_KEYS = ("candidate_tree", "tree", "tree_sha")
_CHECKPOINT_REVIEW_KEYS = ("independent_review_report", "review_report", "review")

#: Unit states that mean the phase is already accepted, so a closure attempt has
#: nothing left to do but say so.
ACCEPTED_UNIT_STATES = ("COMPLETE", "ACCEPTED", "PHASE_ACCEPTANCE_COMPLETE")

#: Criterion names that name a *criterion only an independent session may award*.
#: Mirrors ``completion_auditor.INDEPENDENT_CRITERIA``.
INDEPENDENT_CRITERION_MARKERS = ("independent_review", "independent review", "final_adjudication",
                                 "final adjudication", "independent_phase_review",
                                 "non_builder", "non-builder")

#: Criterion names that are settled by the RESIDUAL LEDGER rather than by any
#: test — "the carried residuals are recorded and none of them blocks".
RESIDUAL_CRITERION_MARKERS = ("residual", "carried_debt", "carried debt", "open_risks",
                              "debt_recorded")

#: Criterion names that name an EXTERNAL verification gate — CI or equivalent.
EXTERNAL_CRITERION_MARKERS = ("ci_green", "ci green", "ci_", "continuous_integration",
                              "external_verification", "workflow_green", "pipeline_green",
                              "build_green")


def _first(mapping: Any, keys: Sequence[str], default: Any = None) -> Any:
    if not isinstance(mapping, dict):
        return default
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return default


def _text(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = yaml.safe_dump(value, default_flow_style=False)
        except Exception:  # pragma: no cover - yaml dumps anything we produce
            text = str(value)
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + " ...[truncated]"


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


#: How a repository writes "no" in a field whose value is prose. A registry that
#: answers "blocks_phase_acceptance" with a paragraph is still answering; the
#: first word is the answer and the rest is the reason.
_NEGATIVE_LEAD = re.compile(r"(?i)\A\W*(no|none|false|never|not\b|non-?blocking)\b")
_POSITIVE_LEAD = re.compile(r"(?i)\A\W*(yes|true|blocking|blocks)\b")


def _as_tristate(value: Any) -> tuple[bool | None, str]:
    """Read a yes/no field that may be a bool, a word, or a paragraph.

    Returns the answer and how it was read. ``None`` means the field was absent
    or said nothing either way, which callers must NOT read as ``False`` without
    saying so — the two are different, and the difference is whether a default
    was applied.
    """
    if value is None or value == "":
        return None, "absent"
    if isinstance(value, bool):
        return value, "stated as a boolean"
    text = str(value).strip()
    if _NEGATIVE_LEAD.match(text):
        return False, f"stated in prose beginning {text.split()[0]!r}"
    if _POSITIVE_LEAD.match(text):
        return True, f"stated in prose beginning {text.split()[0]!r}"
    return None, f"stated as prose that answers neither way: {text[:80]!r}"


#: Residual status words that mean the row is settled.
_CLOSED_STATUS = re.compile(r"(?i)\b(?:closed|resolved|discharged|done|complete)\b")


# --------------------------------------------------------------------------
# The resolution
# --------------------------------------------------------------------------


@dataclass
class PhaseAuthority:
    """Everything the repository states about one phase's acceptance.

    ``declared`` is the question every caller actually asks: does an explicit,
    readable acceptance criterion set exist for this phase? When it does not,
    ``problem`` says why in the repository's own terms and the controller stops
    at AUTHORITY_GAP.
    """

    phase_id: str = ""
    name: str = ""
    unit_status: str = ""
    execution_state: str = ""
    checkpoint_state: str = ""
    criteria: CriterionSet = field(default_factory=CriterionSet)
    checkpoints: list[CheckpointRecord] = field(default_factory=list)
    checkpoints_expected: int = 0
    residuals: ResidualLedger = field(default_factory=ResidualLedger)
    next_phase: str = ""
    production_enabled: bool = False
    acceptance_contract: str = ""
    source_paths: list[str] = field(default_factory=list)
    #: Statements about what could not be read, in the repository's own terms.
    problem: str = ""
    #: How each non-obvious fact above was arrived at.
    derivation: list[str] = field(default_factory=list)

    @property
    def declared(self) -> bool:
        return self.criteria.declared

    @property
    def already_accepted(self) -> bool:
        states = {
            str(self.unit_status or "").upper(),
            str(self.execution_state or "").upper(),
            str(self.checkpoint_state or "").upper(),
        }
        return any(s in states for s in ACCEPTED_UNIT_STATES)

    @property
    def independent_review_criteria(self) -> list[AcceptanceCriterion]:
        """Criteria that structurally cannot be awarded by the building session."""
        return [
            c
            for c in self.criteria.criteria
            if any(m in f"{c.criterion_id} {c.name}".lower() for m in INDEPENDENT_CRITERION_MARKERS)
        ]

    @property
    def external_criteria(self) -> list[AcceptanceCriterion]:
        """Criteria that can only be settled by an external verifier."""
        out: list[AcceptanceCriterion] = []
        for c in self.criteria.criteria:
            blob = f"{c.criterion_id} {c.name} {c.requirement}".lower()
            if any(m in blob for m in EXTERNAL_CRITERION_MARKERS):
                out.append(c)
        return out

    @property
    def residual_criteria(self) -> list[AcceptanceCriterion]:
        """Criteria the residual ledger settles, not a test.

        "Every carried residual is recorded, with a closure condition, and none
        of them blocks" is a statement about the ledger. Asking for a test node
        to establish it is asking the wrong question, and reporting it as
        unevidenced makes a well-run phase look unverified.
        """
        return [
            c
            for c in self.criteria.criteria
            if any(m in f"{c.criterion_id} {c.name}".lower() for m in RESIDUAL_CRITERION_MARKERS)
        ]

    def summary_block(self) -> str:
        lines = [
            f"PHASE: {self.phase_id or '(none resolved)'} — {self.name or 'unnamed'}",
            f"  status: {self.unit_status or 'unrecorded'}"
            + (f" / {self.execution_state}" if self.execution_state else "")
            + (f" / {self.checkpoint_state}" if self.checkpoint_state else ""),
        ]
        lines += self.criteria.summary_block().splitlines()
        lines.append(
            f"CHECKPOINTS: {len(self.checkpoints)} landed"
            + (f" of {self.checkpoints_expected} expected" if self.checkpoints_expected else "")
        )
        lines.append(f"RESIDUALS: {len(self.residuals)} recorded")
        if self.problem:
            lines.append(f"PROBLEM: {self.problem}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase_id": self.phase_id,
            "name": self.name,
            "unit_status": self.unit_status,
            "execution_state": self.execution_state,
            "checkpoint_state": self.checkpoint_state,
            "declared": self.declared,
            "already_accepted": self.already_accepted,
            "criteria": self.criteria.model_dump(mode="json"),
            "criteria_fingerprint": self.criteria.fingerprint(),
            "checkpoints": [c.model_dump(mode="json") for c in self.checkpoints],
            "checkpoints_expected": self.checkpoints_expected,
            "residuals": self.residuals.model_dump(mode="json"),
            "next_phase": self.next_phase,
            "production_enabled": self.production_enabled,
            "source_paths": list(self.source_paths),
            "problem": self.problem,
            "derivation": list(self.derivation),
        }


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def _load_units(repo: Path, registry_paths: Sequence[str]) -> tuple[list[dict], str, str]:
    """Every unit the repository declares, the file they came from, and a problem."""
    for rel in registry_paths:
        path = Path(repo) / rel
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, yaml.YAMLError) as exc:
            return [], rel, f"{rel} did not parse: {exc}"
        if not isinstance(data, dict):
            continue
        raw = _first(data, _UNITS_KEYS)
        if raw is None:
            continue
        units = list(raw.values()) if isinstance(raw, dict) else list(raw or [])
        units = [u for u in units if isinstance(u, dict)]
        if units:
            return units, rel, ""
    return (
        [],
        "",
        "the repository declares no readable unit registry at any of: "
        + ", ".join(registry_paths),
    )


def _criterion_from(raw: dict, index: int) -> AcceptanceCriterion:
    cid = str(_first(raw, _CRITERION_ID_KEYS, "") or "").strip()
    name = str(_first(raw, _CRITERION_NAME_KEYS, "") or "").strip()
    if not cid:
        # A criterion with no id of its own is still a criterion. Name it by its
        # position so it can be referred to, and say so in the requirement.
        cid = name or f"criterion-{index + 1}"
    required_raw = _first(raw, _CRITERION_REQUIRED_KEYS, None)
    # Strict default: a criterion that does not say is required. A phase whose
    # bar is unclear is not a phase with a lower bar.
    required = True if required_raw is None else bool(required_raw)
    requirement = _text(_first(raw, _CRITERION_REQUIREMENT_KEYS, ""))
    if requirement == name:
        # The registry uses one field for both. Keep the name as the requirement
        # rather than reporting an empty demand.
        requirement = name
    return AcceptanceCriterion(
        criterion_id=cid,
        name=name,
        required=required,
        weight=_as_float(_first(raw, ("weight", "points", "value"), 0)),
        result=str(_first(raw, _CRITERION_RESULT_KEYS, "PENDING") or "PENDING"),
        requirement=requirement,
        authority=_text(_first(raw, _CRITERION_AUTHORITY_KEYS, ""), limit=600),
        recorded_evidence=_text(_first(raw, _CRITERION_EVIDENCE_KEYS, ""), limit=2000),
    )


def _checkpoint_from(raw: dict, index: int) -> CheckpointRecord:
    return CheckpointRecord(
        checkpoint_id=str(_first(raw, _CHECKPOINT_ID_KEYS, f"checkpoint-{index + 1}")),
        name=str(_first(raw, ("name", "title", "summary"), "") or "")[:200],
        state=str(_first(raw, _CHECKPOINT_STATE_KEYS, "") or ""),
        candidate_commit=str(_first(raw, _CHECKPOINT_COMMIT_KEYS, "") or ""),
        candidate_tree=str(_first(raw, _CHECKPOINT_TREE_KEYS, "") or ""),
        independent_review_report=str(_first(raw, _CHECKPOINT_REVIEW_KEYS, "") or ""),
    )


def _residual_from(raw: Any, index: int, source: str) -> Residual:
    if not isinstance(raw, dict):
        return Residual(
            residual_id=f"residual-{index + 1}",
            finding=_text(raw, limit=600),
            classification=FindingClass.NONBLOCKING_DEBT,
            source=source,
        )
    blocking, _how = _as_tristate(_first(raw, _RESIDUAL_BLOCKING_KEYS, None))
    status_text = str(_first(raw, _RESIDUAL_STATUS_KEYS, "") or "")
    closure_evidence = _text(_first(raw, _RESIDUAL_CLOSURE_EVIDENCE_KEYS, ""), limit=600)
    residual = Residual(
        residual_id=str(_first(raw, _RESIDUAL_ID_KEYS, f"residual-{index + 1}")),
        finding=_text(_first(raw, _RESIDUAL_FINDING_KEYS, ""), limit=1200),
        disposition=_text(_first(raw, _RESIDUAL_DISPOSITION_KEYS, ""), limit=1200),
        severity=str(_first(raw, ("severity", "sev", "priority"), "") or ""),
        # Never inferred from severity. A repository that wants a residual to
        # block says so; one that records HIGH severity has described impact,
        # not a gate. An unanswered field is read as non-blocking, because
        # "blocking" is a claim and an absent claim has not been made.
        blocks_phase_acceptance=bool(blocking) if blocking is not None else False,
        closure_condition=_text(_first(raw, _RESIDUAL_CLOSURE_KEYS, ""), limit=600),
        classification=FindingClass.NONBLOCKING_DEBT,
        source=source,
    )
    # The repository's own record of whether this row is settled outranks
    # anything derived: a residual the repository closed, citing what closed it,
    # is closed. Reading only the `blocks` field turned a discharged row back
    # into a blocker at every subsequent acceptance.
    if _CLOSED_STATUS.search(status_text):
        residual.status = ResidualStatus.CLOSED
        for line in (status_text, closure_evidence):
            if line and line not in residual.evidence:
                residual.evidence.append(line)
    elif closure_evidence:
        residual.evidence.append(closure_evidence)
    return residual


def resolve_phase_authority(
    repo: Path,
    phase_id: str = "",
    *,
    registry_paths: Sequence[str] = DEFAULT_REGISTRY_PATHS,
) -> PhaseAuthority:
    """Read one phase's acceptance authority. Never raises, never invents.

    ``phase_id`` empty means "whichever unit the repository currently selects",
    which is the unit whose status is READY or IN_PROGRESS. When more than one
    qualifies the repository is contradictory, and that is reported rather than
    resolved by picking.
    """
    repo = Path(repo)
    units, source, problem = _load_units(repo, registry_paths)
    authority = PhaseAuthority(phase_id=phase_id, source_paths=[source] if source else [])

    if problem:
        authority.problem = problem
        authority.criteria = CriterionSet(phase_id=phase_id, resolution_problem=problem)
        return authority

    def unit_id_of(unit: dict) -> str:
        return str(_first(unit, _UNIT_ID_KEYS, "") or "").strip()

    chosen: dict | None = None
    if phase_id:
        for unit in units:
            if unit_id_of(unit).upper() == phase_id.upper():
                chosen = unit
                break
        if chosen is None:
            authority.problem = (
                f"the repository's registry ({source}) declares no unit {phase_id}"
            )
            authority.criteria = CriterionSet(
                phase_id=phase_id, source_paths=[source], resolution_problem=authority.problem
            )
            return authority
    else:
        selected = [
            u
            for u in units
            if str(_first(u, ("status",), "") or "").upper() in ("READY", "IN_PROGRESS")
        ]
        if len(selected) == 1:
            chosen = selected[0]
            authority.derivation.append(
                f"phase taken from the single selected unit in {source} "
                f"(status {_first(selected[0], ('status',), '')})"
            )
        elif not selected:
            authority.problem = (
                f"the repository's registry ({source}) selects no unit to work on, so there "
                "is no phase to close"
            )
            authority.criteria = CriterionSet(
                source_paths=[source], resolution_problem=authority.problem
            )
            return authority
        else:
            ids = ", ".join(unit_id_of(u) for u in selected)
            authority.problem = (
                f"the repository's registry ({source}) selects more than one unit ({ids}); "
                "which phase is being closed is not something Product Driver may decide"
            )
            authority.criteria = CriterionSet(
                source_paths=[source], resolution_problem=authority.problem
            )
            return authority

    authority.phase_id = unit_id_of(chosen) or phase_id
    authority.name = str(_first(chosen, ("name", "title"), "") or "")
    authority.unit_status = str(_first(chosen, ("status",), "") or "")
    authority.execution_state = str(_first(chosen, ("execution_state",), "") or "")
    authority.checkpoint_state = str(_first(chosen, ("checkpoint_state",), "") or "")
    authority.acceptance_contract = _text(_first(chosen, ("acceptance_contract",), ""), limit=3000)

    # -- criteria ---------------------------------------------------------
    raw_criteria = _first(chosen, _CRITERIA_KEYS, None)
    rows = (
        list(raw_criteria.values())
        if isinstance(raw_criteria, dict)
        else list(raw_criteria or [])
    )
    rows = [r for r in rows if isinstance(r, dict)]
    if rows:
        authority.criteria = CriterionSet(
            phase_id=authority.phase_id,
            criteria=[_criterion_from(r, i) for i, r in enumerate(rows)],
            source_paths=[source],
        )
    else:
        gap = (
            f"{authority.phase_id} declares no explicit acceptance criterion set in {source}. "
            "Product Driver will not manufacture one: what makes this phase done is a "
            "founder or architect decision."
        )
        authority.problem = gap
        authority.criteria = CriterionSet(
            phase_id=authority.phase_id, source_paths=[source], resolution_problem=gap
        )

    # -- checkpoints ------------------------------------------------------
    raw_cps = _first(chosen, _CHECKPOINT_KEYS, None)
    cp_rows = list(raw_cps.values()) if isinstance(raw_cps, dict) else list(raw_cps or [])
    authority.checkpoints = [
        _checkpoint_from(r, i) for i, r in enumerate(cp_rows) if isinstance(r, dict)
    ]
    expected = _first(chosen, _EXPECTED_CHECKPOINT_KEYS, None)
    if expected is not None:
        try:
            authority.checkpoints_expected = int(expected)
        except (TypeError, ValueError):
            authority.checkpoints_expected = len(authority.checkpoints)
    else:
        authority.checkpoints_expected = len(authority.checkpoints)
        if authority.checkpoints:
            authority.derivation.append(
                "the repository declares no expected checkpoint count, so 'expected' is "
                "reported as the number that landed; this cannot detect a missing checkpoint"
            )

    # -- residuals --------------------------------------------------------
    raw_res = _first(chosen, _RESIDUAL_KEYS, None)
    res_rows = list(raw_res.values()) if isinstance(raw_res, dict) else list(raw_res or [])
    ledger = ResidualLedger()
    for index, raw in enumerate(res_rows):
        ledger.add(_residual_from(raw, index, source))
    authority.residuals = ledger

    # -- surroundings -----------------------------------------------------
    nxt = _first(chosen, _NEXT_KEYS, None)
    if isinstance(nxt, (list, tuple)) and nxt:
        authority.next_phase = str(nxt[0])
    elif isinstance(nxt, str):
        authority.next_phase = nxt
    authority.production_enabled = bool(_first(chosen, _PRODUCTION_KEYS, False))

    return authority
