"""Three-layer context assembly.

    A. Stable founder context   — founder_context/*, versioned by content hash
    B. Current repository state  — re-read from Neyma before EVERY decision
    C. Immediate run evidence    — assembled by the caller

The repository is authoritative for the active READY unit, phase scope,
acceptance criteria, architecture, safety invariants and progress. This module
*reads* those; it never caches them across a repository change and never keeps a
second copy of them on disk.

The repository is followed as it exists now. Where it declares a unit registry,
that registry scopes the work; where it does not, the founder's task is the
authority and no unit is invented. :meth:`RepositoryContextLoader.resolve_active_unit`
still fails closed for callers that require a declared unit;
:meth:`~RepositoryContextLoader.resolve_active_unit_optional` — what the control
loop uses — returns a placeholder carrying the reason instead, so a repository
that has retired its registry is a repository the driver can still build in.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .models import redact, utcnow

FOUNDER_DIR_NAME = "founder_context"
OWNER_CONTEXT_FILE = "PRODUCT_OWNER_CONTEXT.md"
TASTE_RUBRIC_FILE = "PRODUCT_TASTE_RUBRIC.yaml"

REGISTRY_REL = "docs/implementation/IMPLEMENTATION-REGISTRY.yaml"
CURRENT_REL = "docs/implementation/CURRENT.md"
CLAUDE_REL = "CLAUDE.md"

# Documents consulted only when a topic makes them relevant.
TOPIC_DOCUMENTS: dict[str, tuple[str, ...]] = {
    "product": ("PRODUCT.md",),
    "architecture": ("ARCHITECTURE.md",),
    "acceptance": ("docs/specifications/acceptance/registry.md",),
    "status": ("docs/implementation/BUILD-STATUS.yaml",),
}


class ContextResolutionError(Exception):
    """Raised when current authority cannot be resolved. Always fails closed."""


# --------------------------------------------------------------------------
# Provenance — recorded on every decision
# --------------------------------------------------------------------------


class ContextProvenance(BaseModel):
    """Exactly what informed one evaluator decision."""

    assembled_at: str = Field(default_factory=utcnow)
    founder_context_version: str = ""
    founder_context_files: list[str] = Field(default_factory=list)
    repository_head: str = ""
    repository_branch: str = ""
    repository_dirty_files: int = 0
    active_unit_id: str = ""
    active_unit_status: str = ""
    active_unit_criteria: list[str] = Field(default_factory=list)
    repository_files_consulted: list[str] = Field(default_factory=list)
    evidence_files_consulted: list[str] = Field(default_factory=list)
    founder_feedback_count: int = 0
    prompt_chars: int = 0


# --------------------------------------------------------------------------
# Layer A — founder context
# --------------------------------------------------------------------------


@dataclass
class FounderContext:
    """Durable product direction. Versioned by content hash, not by mtime."""

    owner_context: str
    rubric: dict[str, Any]
    version: str
    files: list[str]
    owner_context_path: Path
    rubric_path: Path

    @property
    def minimum_confidence_for_fix(self) -> float:
        return float(self.rubric.get("confidence", {}).get("minimum_for_fix", 0.6))

    @property
    def minimum_confidence_for_customer_facing_fix(self) -> float:
        return float(
            self.rubric.get("confidence", {}).get("minimum_for_customer_facing_fix", 0.75)
        )

    @property
    def minimum_confidence_for_accept(self) -> float:
        return float(self.rubric.get("confidence", {}).get("minimum_for_accept", 0.6))

    @property
    def vague_phrases(self) -> list[str]:
        return [str(p).lower() for p in self.rubric.get("vague_correction_phrases", [])]

    @property
    def min_correction_chars(self) -> int:
        reqs = self.rubric.get("correction_prompt_requirements", {})
        return int(reqs.get("min_chars", 120))

    @property
    def category_ids(self) -> list[str]:
        return [str(c.get("id", "")) for c in self.rubric.get("categories", [])]

    def rubric_digest(self, max_chars: int = 9000) -> str:
        """Compact rendering of the rubric for the prompt."""
        lines: list[str] = []

        conf = self.rubric.get("confidence", {})
        lines.append("CONFIDENCE POLICY")
        lines.append(f"  minimum_for_fix: {conf.get('minimum_for_fix')}")
        lines.append(
            f"  minimum_for_customer_facing_fix: {conf.get('minimum_for_customer_facing_fix')}"
        )
        lines.append(f"  minimum_for_accept: {conf.get('minimum_for_accept')}")
        for rule in conf.get("rules", []):
            lines.append(f"  - {rule}")

        lines.append("\nASK_USER BOUNDARIES (escalate, do not decide alone)")
        for b in self.rubric.get("ask_user_boundaries", []):
            lines.append(f"  [{b.get('id')}] {str(b.get('description', '')).strip()}")

        lines.append("\nNEVER ACCEPTABLE")
        for n in self.rubric.get("never_acceptable", []):
            lines.append(f"  [{n.get('id')}] {str(n.get('description', '')).strip()}")

        lines.append("\nCATEGORIES")
        for c in self.rubric.get("categories", []):
            lines.append(
                f"  [{c.get('id')}] severity={c.get('severity')} "
                f"normally_maps_to={c.get('normally_maps_to')}"
                + ("  (repository is authoritative here)" if c.get("repository_authoritative") else "")
            )
            lines.append(f"      {str(c.get('description', '')).strip()}")
            for cond in c.get("pass_conditions", []):
                lines.append(f"      pass: {cond}")
            for fail in c.get("failure_examples", [])[:3]:
                lines.append(f"      fail: {fail}")

        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[:max_chars] + "\n...[rubric truncated]"


def founder_dir(driver_root: Path) -> Path:
    return Path(driver_root) / FOUNDER_DIR_NAME


def load_founder_context(driver_root: Path) -> FounderContext:
    """Load and hash the durable founder context. Raises if unusable."""
    base = founder_dir(driver_root)
    md_path = base / OWNER_CONTEXT_FILE
    yaml_path = base / TASTE_RUBRIC_FILE

    missing = [str(p) for p in (md_path, yaml_path) if not p.exists()]
    if missing:
        raise ContextResolutionError(
            "founder context missing: " + ", ".join(missing)
        )

    try:
        owner_context = md_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContextResolutionError(f"cannot read {md_path}: {exc}") from exc

    if not owner_context.strip():
        raise ContextResolutionError(f"{md_path} is empty")

    try:
        rubric = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ContextResolutionError(f"cannot parse {yaml_path}: {exc}") from exc

    if not isinstance(rubric, dict):
        raise ContextResolutionError(f"{yaml_path} must contain a mapping")
    if not rubric.get("categories"):
        raise ContextResolutionError(f"{yaml_path} defines no categories")

    digest = hashlib.sha256()
    digest.update(owner_context.encode("utf-8"))
    digest.update(yaml_path.read_bytes())

    return FounderContext(
        owner_context=owner_context,
        rubric=rubric,
        version=digest.hexdigest()[:16],
        files=[str(md_path), str(yaml_path)],
        owner_context_path=md_path,
        rubric_path=yaml_path,
    )


# --------------------------------------------------------------------------
# Layer B — repository context
# --------------------------------------------------------------------------


@dataclass
class ActiveUnit:
    """The single READY unit, as the Neyma registry currently defines it."""

    unit_id: str
    name: str = ""
    status: str = ""
    #: The registry's own record of how far the unit has got, where it keeps one.
    #: `status` is a selection ("this is the unit to work on"); this is progress
    #: ("and it is under way"). A phase can be READY and IN_PROGRESS at once, and
    #: reading the first as the second is how a phase in flight gets mistaken for
    #: a phase that has not started.
    execution_state: str = ""
    objective: str = ""
    acceptance_contract: str = ""
    acceptance_criteria: list[dict[str, Any]] = field(default_factory=list)
    allowed_scope: Any = None
    prohibited_scope: Any = None
    validation_blockers: Any = None
    dependencies: Any = None

    #: Why no unit could be resolved, when this is the standing-in placeholder
    #: rather than a unit the repository actually declares. Empty for a real
    #: unit. Carried rather than raised, so a repository that has retired its
    #: registry is a repository the driver can still build in.
    resolution_problem: str = ""

    @property
    def is_declared(self) -> bool:
        """True when the repository actually declares this unit."""
        return not self.resolution_problem and bool(self.unit_id)

    @classmethod
    def undeclared(cls, problem: str) -> "ActiveUnit":
        """The stand-in for a repository that names no active unit.

        The founder's task is the authority then. This is not a degraded mode to
        be apologised for: a repository is entitled to stop keeping a unit
        registry, and a driver that refuses to work without one is enforcing a
        convention its target has abandoned.
        """
        return cls(unit_id="", name="", status="", resolution_problem=problem)

    def criteria_labels(self) -> list[str]:
        out: list[str] = []
        for c in self.acceptance_criteria:
            if isinstance(c, dict):
                out.append(
                    f"{c.get('criterion')} (weight {c.get('weight')}): {c.get('result')}"
                )
            else:
                out.append(str(c))
        return out

    def render(self, max_chars: int = 6000) -> str:
        if not self.is_declared:
            return (
                "ACTIVE READY UNIT: none declared by the repository.\n"
                f"reason: {self.resolution_problem or 'the repository declares no unit registry'}\n"
                "The product owner's task is the authority for this run. Do not invent a "
                "unit id, a phase, or acceptance criteria the repository does not state."
            )
        parts = [
            f"ACTIVE READY UNIT: {self.unit_id} — {self.name}",
            f"status: {self.status}"
            + (f" (execution_state: {self.execution_state})" if self.execution_state else ""),
            f"objective: {self.objective}",
            f"acceptance_contract: {self.acceptance_contract}",
        ]
        if self.acceptance_criteria:
            parts.append("acceptance criteria (weighted; only PASS contributes):")
            parts += [f"  - {label}" for label in self.criteria_labels()]
        if self.allowed_scope:
            parts.append(f"allowed_scope: {_short(self.allowed_scope)}")
        if self.prohibited_scope:
            parts.append(f"prohibited_scope: {_short(self.prohibited_scope)}")
        if self.validation_blockers:
            parts.append(f"validation_blockers: {_short(self.validation_blockers)}")
        text = "\n".join(parts)
        return text if len(text) <= max_chars else text[:max_chars] + "\n...[truncated]"


def _short(value: Any, limit: int = 1200) -> str:
    text = value if isinstance(value, str) else yaml.safe_dump(value, default_flow_style=False)
    text = str(text).strip()
    return text if len(text) <= limit else text[:limit] + " ...[truncated]"


@dataclass
class RepositoryContext:
    """A freshly-read snapshot of Neyma's current authority."""

    head_commit: str
    branch: str
    dirty_file_count: int
    active_unit: ActiveUnit
    authority_excerpt: str
    current_excerpt: str
    topic_excerpts: dict[str, str]
    files_consulted: list[str]
    fingerprint: str

    def render(self) -> str:
        parts = [
            "=== NEYMA REPOSITORY — CURRENT AUTHORITY (authoritative) ===",
            f"branch: {self.branch}   HEAD: {self.head_commit}   dirty files: {self.dirty_file_count}",
            "",
            self.active_unit.render(),
            "",
            "--- CLAUDE.md (authority excerpt) ---",
            self.authority_excerpt,
            "",
            "--- CURRENT.md (status excerpt) ---",
            self.current_excerpt,
        ]
        for topic, text in self.topic_excerpts.items():
            if text.strip():
                parts += ["", f"--- relevant excerpt: {topic} ---", text]
        return "\n".join(parts)


class RepositoryContextLoader:
    """Reads Neyma's current authority. Re-reads on every load.

    A cache exists only to avoid re-parsing identical bytes: it is keyed on the
    repository fingerprint (HEAD plus the size and mtime of every file consulted)
    and is discarded the moment anything changes. Stale phase context can never
    be served.
    """

    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)
        self._cache: tuple[str, RepositoryContext] | None = None
        self.loads = 0
        self.cache_hits = 0

    # -- git ---------------------------------------------------------------

    def _git(self, *args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(self.repo), capture_output=True, text=True, timeout=30
            )
            return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def _fingerprint(self, paths: list[Path]) -> str:
        digest = hashlib.sha256()
        digest.update(self._git("rev-parse", "HEAD").encode())
        digest.update(self._git("status", "--porcelain").encode())
        for p in sorted(paths):
            try:
                st = p.stat()
                digest.update(f"{p}:{st.st_size}:{st.st_mtime_ns}".encode())
            except OSError:
                digest.update(f"{p}:missing".encode())
        return digest.hexdigest()[:16]

    # -- active unit -------------------------------------------------------

    def resolve_active_unit(self) -> ActiveUnit:
        """Resolve the single READY unit. Fails closed on 0 or >1."""
        registry_path = self.repo / REGISTRY_REL
        if not registry_path.exists():
            raise ContextResolutionError(
                f"cannot resolve current authority: {REGISTRY_REL} not found in {self.repo}"
            )
        try:
            data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ContextResolutionError(
                f"cannot resolve current authority: {REGISTRY_REL} did not parse: {exc}"
            ) from exc

        if not isinstance(data, dict) or "units" not in data:
            raise ContextResolutionError(
                f"cannot resolve current authority: {REGISTRY_REL} has no 'units'"
            )

        raw_units = data["units"]
        units = list(raw_units.values()) if isinstance(raw_units, dict) else list(raw_units or [])
        ready = [u for u in units if isinstance(u, dict) and u.get("status") == "READY"]

        if len(ready) == 0:
            raise ContextResolutionError(
                "no READY unit in the Neyma registry — the repository cannot identify "
                "the next approved unit, so the driver must not invent work"
            )
        if len(ready) > 1:
            ids = ", ".join(str(u.get("unit_id")) for u in ready)
            raise ContextResolutionError(
                f"more than one READY unit in the Neyma registry ({ids}) — exactly one "
                "unit may be READY; the repository is in a contradictory state"
            )

        u = ready[0]
        unit_id = str(u.get("unit_id") or "").strip()
        if not unit_id:
            raise ContextResolutionError("the READY unit has no unit_id")

        criteria = u.get("acceptance_criteria") or []
        return ActiveUnit(
            unit_id=unit_id,
            name=str(u.get("name") or ""),
            status=str(u.get("status") or ""),
            execution_state=str(u.get("execution_state") or ""),
            objective=str(u.get("objective") or ""),
            acceptance_contract=_short(u.get("acceptance_contract") or ""),
            acceptance_criteria=[c for c in criteria if isinstance(c, dict)],
            allowed_scope=u.get("allowed_scope"),
            prohibited_scope=u.get("prohibited_scope"),
            validation_blockers=u.get("validation_blockers"),
            dependencies=u.get("dependencies"),
        )

    def resolve_active_unit_optional(self) -> ActiveUnit:
        """Resolve the active unit, or stand in for a repository that has none.

        :meth:`resolve_active_unit` fails closed, and that was right while a unit
        registry was a mandatory fixture of the target repository. It is not a
        property of software development. A repository that retires its registry —
        or that has not written one yet — is not in an error state, and a driver
        that refuses to build anything until one appears has made a repository
        convention outrank the product work it exists to do.

        The distinction that still matters is kept: an *ambiguous* registry (two
        units both marked READY, a file that will not parse) is a genuine
        contradiction in the repository, and it is reported as one — as a
        diagnostic the loop hands to the investigator, not as a full stop. What
        is never done is inventing a unit the repository does not state.
        """
        try:
            return self.resolve_active_unit()
        except ContextResolutionError as exc:
            return ActiveUnit.undeclared(str(exc))

    # -- loading -----------------------------------------------------------

    def load(self, topics: list[str] | None = None) -> RepositoryContext:
        """Re-read repository authority. Never serves context from a stale tree."""
        topics = topics or []
        consulted = [self.repo / REGISTRY_REL, self.repo / CURRENT_REL, self.repo / CLAUDE_REL]
        for topic in topics:
            for rel in TOPIC_DOCUMENTS.get(topic, ()):
                consulted.append(self.repo / rel)

        fingerprint = self._fingerprint(consulted)
        if self._cache is not None and self._cache[0] == fingerprint:
            self.cache_hits += 1
            return self._cache[1]

        self.loads += 1
        active_unit = self.resolve_active_unit_optional()

        authority = _read(self.repo / CLAUDE_REL)
        current = _read(self.repo / CURRENT_REL)

        topic_excerpts: dict[str, str] = {}
        for topic in topics:
            for rel in TOPIC_DOCUMENTS.get(topic, ()):
                path = self.repo / rel
                text = _read(path)
                if text:
                    topic_excerpts[rel] = select_sections(
                        text, [x for x in [topic, active_unit.unit_id] if x], 4000
                    )

        ctx = RepositoryContext(
            head_commit=self._git("rev-parse", "--short", "HEAD"),
            branch=self._git("branch", "--show-current"),
            dirty_file_count=len(
                [ln for ln in self._git("status", "--porcelain").splitlines() if ln.strip()]
            ),
            active_unit=active_unit,
            authority_excerpt=select_sections(
                authority,
                ["authority", "non-negotiable", "stop", "work-unit", "definition of done", "must not"],
                7000,
            ),
            current_excerpt=select_sections(
                current,
                [t for t in ["status", "next", active_unit.unit_id, "ready"] if t],
                5000,
            ),
            topic_excerpts=topic_excerpts,
            files_consulted=[str(p) for p in consulted if p.exists()],
            fingerprint=fingerprint,
        )
        self._cache = (fingerprint, ctx)
        return ctx


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


# --------------------------------------------------------------------------
# Compact section selection
# --------------------------------------------------------------------------


def split_sections(markdown: str) -> list[tuple[str, str]]:
    """Split markdown into (heading, body) pairs."""
    lines = markdown.splitlines()
    sections: list[tuple[str, list[str]]] = [("(preamble)", [])]
    for line in lines:
        if re.match(r"^#{1,4}\s+\S", line):
            sections.append((line.strip("# ").strip(), []))
        else:
            sections[-1][1].append(line)
    return [(h, "\n".join(b).strip()) for h, b in sections]


def select_sections(markdown: str, topics: list[str], max_chars: int) -> str:
    """Pick the sections most relevant to ``topics``, bounded by ``max_chars``.

    Keeps whole sections so the excerpt stays readable, and always returns
    something when the document is non-empty.
    """
    if not markdown.strip():
        return ""
    if len(markdown) <= max_chars:
        return markdown.strip()

    needles = [t.lower() for t in topics if t]
    scored: list[tuple[int, int, str, str]] = []
    for idx, (heading, body) in enumerate(split_sections(markdown)):
        if not body and not heading:
            continue
        blob = f"{heading}\n{body}".lower()
        score = 0
        for needle in needles:
            score += 5 * heading.lower().count(needle)
            score += blob.count(needle)
        scored.append((score, -idx, heading, body))

    scored.sort(reverse=True)

    chosen: list[tuple[int, str]] = []
    used = 0
    for score, neg_idx, heading, body in scored:
        chunk = f"## {heading}\n{body}".strip()
        if score <= 0 and chosen:
            continue
        if used + len(chunk) > max_chars:
            if chosen:
                continue
            chunk = chunk[:max_chars]
        chosen.append((-neg_idx, chunk))
        used += len(chunk)
        if used >= max_chars:
            break

    if not chosen:
        return markdown[:max_chars].strip()

    chosen.sort()  # restore document order
    return "\n\n".join(chunk for _idx, chunk in chosen)


# --------------------------------------------------------------------------
# Founder feedback — per-run, explicitly not permanent
# --------------------------------------------------------------------------


class FounderFeedback(BaseModel):
    """One piece of explicit founder direction, scoped to a single run."""

    timestamp: str = Field(default_factory=utcnow)
    message: str
    iteration: int | None = None
    promoted: bool = False


class FounderFeedbackStore:
    """Stores founder feedback for one run. Never writes to durable context."""

    FILENAME = "founder-feedback.json"

    def __init__(self, run_dir: Path) -> None:
        self.path = Path(run_dir) / self.FILENAME

    def load(self) -> list[FounderFeedback]:
        if not self.path.exists():
            return []
        import json

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out: list[FounderFeedback] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                out.append(FounderFeedback.model_validate(item))
            except Exception:
                continue
        return out

    def add(self, message: str, iteration: int | None = None) -> FounderFeedback:
        import json

        entries = self.load()
        entry = FounderFeedback(message=redact(message), iteration=iteration)
        entries.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([e.model_dump(mode="json") for e in entries], indent=2),
            encoding="utf-8",
        )
        return entry

    def render(self) -> str:
        entries = self.load()
        if not entries:
            return ""
        lines = [
            "=== FOUNDER DIRECTION FOR THIS RUN (HIGHEST-PRIORITY PRODUCT INPUT) ===",
            "The founder's explicit product feedback overrides evaluator taste.",
            "It does NOT override the Neyma repository's authority on the active unit,",
            "phase scope, acceptance criteria or safety invariants.",
            "",
        ]
        for i, e in enumerate(entries, 1):
            when = f" (given at iteration {e.iteration})" if e.iteration else ""
            lines.append(f"{i}.{when} {e.message}")
        return "\n".join(lines)
