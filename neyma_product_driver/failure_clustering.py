"""Grouping failures that plausibly share one cause.

Twenty-five scenarios failing because approval state is not durable is one
defect, not twenty-five. Sending the builder twenty-five corrections wastes the
iteration budget and buries the actual problem; sending one grounded correction
naming the shared domain is what a careful engineer would do.

The rule is deliberately conservative: two failures are linked only when they
share at least :data:`MIN_SHARED_SIGNALS` normalized signals **and** their risk
categories are in the same family. Anything less and they stay separate. The
cost of over-clustering is a real defect hidden behind another one's correction,
which is far worse than the cost of under-clustering — a slightly repetitive
correction. When uncertain, this module keeps failures apart.

Nothing here decides what to do about a cluster. It produces structured evidence;
the evaluator or the investigator forms the correction.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .models import ScenarioResult
from .scenario_plan import Priority, RiskCategory, neighbours

#: How many normalized signals two failures must share before they are treated
#: as one problem. Two is the smallest number that is not a coincidence.
MIN_SHARED_SIGNALS = 2


class FailureRecord(BaseModel):
    """One failed scenario, reduced to what clustering can reason about."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    scenario_name: str = ""
    origin: str = "generated"
    risk_category: RiskCategory | None = None
    priority: Priority = Priority.P2
    failed_assertions: list[str] = Field(default_factory=list)
    error: str = ""
    evidence_path: str = ""

    @classmethod
    def from_result(
        cls,
        result: ScenarioResult,
        *,
        scenario_id: str,
        origin: str = "generated",
        risk_category: RiskCategory | None = None,
        priority: Priority = Priority.P2,
        evidence_path: str = "",
    ) -> "FailureRecord":
        return cls(
            scenario_id=scenario_id,
            scenario_name=result.scenario_name,
            origin=origin,
            risk_category=risk_category,
            priority=priority,
            failed_assertions=[
                f"{a.kind}: {a.target} — {a.detail}".strip(" —")
                for a in result.failed_assertions()
            ],
            error=result.error or ("" if result.readiness_ok else result.readiness_detail),
            evidence_path=evidence_path,
        )


class FailureCluster(BaseModel):
    """Several failures the evidence suggests share one underlying cause."""

    model_config = ConfigDict(extra="forbid")

    cluster_id: str
    affected_scenarios: list[str] = Field(default_factory=list)
    shared_observations: list[str] = Field(default_factory=list)
    likely_failure_domain: str = ""
    evidence_paths: list[str] = Field(default_factory=list)
    highest_priority: Priority = Priority.P2
    risk_categories: list[str] = Field(default_factory=list)
    #: True when this cluster holds exactly one failure. A singleton is not a
    #: claim about a shared cause; it is simply a failure that matched nothing.
    singleton: bool = True

    def render(self) -> str:
        lines = [
            f"[{self.cluster_id}] {self.likely_failure_domain}"
            + ("" if self.singleton else f"  ({len(self.affected_scenarios)} scenarios)"),
            "  affected: " + ", ".join(self.affected_scenarios),
        ]
        if self.shared_observations:
            lines.append("  shared: " + "; ".join(self.shared_observations[:6]))
        if self.evidence_paths:
            lines.append("  evidence: " + ", ".join(self.evidence_paths[:6]))
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Signal extraction
# --------------------------------------------------------------------------

#: Values that differ between two runs of the same defect: ids, timestamps,
#: durations, ports, hex digests. Normalizing them away is what lets "invoice
#: INV-1 was paid twice" and "invoice INV-7 was paid twice" look alike.
_VOLATILE = (
    (re.compile(r"\b[0-9a-f]{7,40}\b", re.I), "<hex>"),
    # Case-insensitive on the date/time separator: signals are lowercased before
    # normalizing, so an ISO timestamp arrives as `...09t10:00:00`.
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[Tt ][\d:.]+"), "<timestamp>"),
    (re.compile(r"\b\d+(?:\.\d+)?s\b"), "<duration>"),
    (re.compile(r":\d{2,5}\b"), ":<port>"),
    (re.compile(r"\b\d+\b"), "<n>"),
)


def normalize_signal(text: str) -> str:
    """Reduce an observation to its shape, dropping run-specific values."""
    out = (text or "").strip().lower()
    for pattern, replacement in _VOLATILE:
        out = pattern.sub(replacement, out)
    return re.sub(r"\s+", " ", out).strip()


def signals_for(record: FailureRecord) -> set[str]:
    """Every normalized signal one failure exhibits."""
    signals = {normalize_signal(a) for a in record.failed_assertions}
    if record.error:
        signals.add(normalize_signal(record.error))
    return {s for s in signals if s}


# --------------------------------------------------------------------------
# Clustering
# --------------------------------------------------------------------------


def _same_family(a: FailureRecord, b: FailureRecord) -> bool:
    """Whether two failures are even eligible to share a cause.

    A permanent scenario has no risk category; it is eligible to join any
    cluster, because the reason it exists is that a human decided the behaviour
    matters, not that it belongs to a taxonomy.
    """
    if a.risk_category is None or b.risk_category is None:
        return True
    return b.risk_category in neighbours(a.risk_category)


def cluster_failures(records: Sequence[FailureRecord]) -> list[FailureCluster]:
    """Group failures into clusters, keeping uncertain ones apart.

    Union-find over the "shares enough signals and is in the same risk family"
    relation. Deterministic: input order decides cluster numbering, and nothing
    consults a model.
    """
    parent = list(range(len(records)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    signals = [signals_for(r) for r in records]
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            if not _same_family(records[i], records[j]):
                continue
            if len(signals[i] & signals[j]) >= MIN_SHARED_SIGNALS:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for index in range(len(records)):
        groups.setdefault(find(index), []).append(index)

    clusters: list[FailureCluster] = []
    for number, (_root, members) in enumerate(sorted(groups.items()), start=1):
        group = [records[i] for i in members]
        shared = set.intersection(*(signals[i] for i in members)) if members else set()
        categories = sorted(
            {r.risk_category.value for r in group if r.risk_category is not None}
        )
        clusters.append(
            FailureCluster(
                cluster_id=f"C{number:02d}",
                affected_scenarios=[r.scenario_id for r in group],
                shared_observations=sorted(shared)[:12],
                likely_failure_domain=_describe_domain(group, sorted(shared)),
                evidence_paths=[r.evidence_path for r in group if r.evidence_path],
                highest_priority=min((r.priority for r in group), key=lambda p: p.rank),
                risk_categories=categories,
                singleton=len(group) == 1,
            )
        )
    # Most blocking first, then largest — the order a correction should read in.
    clusters.sort(key=lambda c: (c.highest_priority.rank, -len(c.affected_scenarios)))
    return clusters


def _describe_domain(group: Sequence[FailureRecord], shared: Sequence[str]) -> str:
    """A short, honest name for what this cluster appears to be about.

    Derived from the risk categories present, never invented. A singleton is
    described by its own failure rather than by a claim about a shared cause.
    """
    if len(group) == 1:
        record = group[0]
        if record.error:
            return f"{record.scenario_name}: {record.error[:160]}"
        first = record.failed_assertions[0] if record.failed_assertions else "failed"
        return f"{record.scenario_name}: {first[:160]}"

    categories = [r.risk_category for r in group if r.risk_category is not None]
    if categories:
        family = neighbours(categories[0])
        label = _FAMILY_LABELS.get(frozenset(family), "shared behaviour")
    else:
        label = "shared behaviour"
    detail = shared[0][:120] if shared else ""
    return f"{label} ({len(group)} scenarios)" + (f" — shared observation: {detail}" if detail else "")


_FAMILY_LABELS: dict[frozenset[RiskCategory], str] = {
    frozenset(
        {
            RiskCategory.IDEMPOTENCY,
            RiskCategory.REPEATED_REQUEST,
            RiskCategory.CONCURRENCY,
            RiskCategory.RETRY_SAFETY,
            RiskCategory.TIMEOUT_AFTER_EFFECT,
            RiskCategory.AMBIGUOUS_EXTERNAL_EFFECT,
        }
    ): "effect duplication / idempotency under repetition, races and retries",
    frozenset(
        {
            RiskCategory.PERSISTENCE_FAILURE,
            RiskCategory.RESTART_RECOVERY,
            RiskCategory.CRASH_MID_WORKFLOW,
            RiskCategory.STALE_STATE,
            RiskCategory.UI_BACKEND_DISAGREEMENT,
            RiskCategory.UNEXPECTED_TRANSITION,
        }
    ): "state durability — transitions that do not survive a read, a refresh or a restart",
    frozenset(
        {
            RiskCategory.AUTHORIZATION,
            RiskCategory.CROSS_TENANT,
            RiskCategory.APPROVAL_REQUIRED,
            RiskCategory.SAFETY_INVARIANT,
        }
    ): "authorization and approval boundaries",
    frozenset(
        {
            RiskCategory.MISSING_DATA,
            RiskCategory.MALFORMED_INPUT,
            RiskCategory.BOUNDARY,
            RiskCategory.CONFLICTING_EVIDENCE,
        }
    ): "input and evidence handling at the edges",
    frozenset(
        {
            RiskCategory.SERVICE_UNAVAILABLE,
            RiskCategory.DEPENDENCY_FAILURE,
            RiskCategory.PARTIAL_FAILURE,
            RiskCategory.TIMEOUT_BEFORE_EFFECT,
            RiskCategory.BROWSER_NETWORK_ERROR,
        }
    ): "degraded dependencies and partial failure",
}


def render_clusters(clusters: Iterable[FailureCluster]) -> str:
    listed = list(clusters)
    if not listed:
        return "no failures to cluster"
    grouped = [c for c in listed if not c.singleton]
    lines = [
        f"{len(listed)} failure cluster(s); {len(grouped)} group several scenarios "
        "under one likely cause"
    ]
    lines += [c.render() for c in listed]
    return "\n".join(lines)


__all__ = [
    "FailureCluster",
    "FailureRecord",
    "MIN_SHARED_SIGNALS",
    "cluster_failures",
    "normalize_signal",
    "render_clusters",
    "signals_for",
]
