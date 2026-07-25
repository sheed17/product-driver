"""Governance deadlock detection.

A gate that fails is a problem. A set of gates that each require another one to
pass first, in a closed loop, is a *deadlock*: no amount of retrying any single
gate can clear it, because every path forward is gated by something downstream
of itself.

The distinction this module enforces:

    a failing gate        != a deadlocked gate
    retrying              != progress
    "BLOCKED"             != a named causal cycle
    an environment outage != a governance cycle

Edges are built only where repository evidence supports them: a rule the
repository states, a receipt that records a result, a guard file that exists.
The cycle is then *found* in the graph rather than pattern-matched, so a
repository whose rules do not close the loop produces no deadlock at all.

The shapes this reaches, given the right evidence:

  - the finalization cycle: an invalid topology fails the guard, the guard
    reddens the suite, the red suite stops the finalizer, and only the finalizer
    may produce the commit that would make the topology valid. This is also the
    general "an artifact a gate requires can only be produced by that same gate"
    case — the artifact here is the finalizer-owned metadata commit.
  - receipt freshness against a HEAD that committing the receipt itself moves.
  - a guard validating a baseline only the gate it blocks may update.

An environment outage (a gate that cannot run at all) is deliberately *not* a
cycle: nothing about it is circular, and calling it one would hide that the
repair is external.

Nothing here writes to the Neyma repository.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .git_topology import GitTopology
from .protocol_sources import (
    DiscoveredProtocol,
    ProtocolViolation,
    RuleKind,
    ViolationType,
)

# Failure text that means the environment could not do its job — not that the
# product is wrong.
_TLS_RE = re.compile(
    r"CERTIFICATE_VERIFY_FAILED|certificate verify failed|SSLError|SSLCertVerification|"
    r"\bTLS\b|self[- ]signed certificate|unable to get local issuer|SSL: ",
    re.I,
)
_NETWORK_RE = re.compile(
    r"Temporary failure in name resolution|Network is unreachable|Connection refused|"
    r"ReadTimeoutError|Max retries exceeded|Could not resolve host|ProxyError|"
    r"proxy (?:error|authentication required)",
    re.I,
)


class GateState(str, Enum):
    PASSING = "PASSING"
    FAILING = "FAILING"
    NOT_RUN = "NOT_RUN"
    BLOCKED = "BLOCKED"  # cannot run for a reason outside the product
    UNKNOWN = "UNKNOWN"


class EnvironmentBlocker(BaseModel):
    """Something the environment cannot do. Never a product failure."""

    model_config = ConfigDict(extra="ignore")

    blocker_id: str
    description: str
    blocks: str = ""
    evidence: list[str] = Field(default_factory=list)
    classification: ViolationType = ViolationType.ENVIRONMENT

    def render(self) -> str:
        return f"{self.description}" + (f" (blocks: {self.blocks})" if self.blocks else "")


class ObservedGates(BaseModel):
    """The current state of each gate, read from receipts and status surfaces."""

    model_config = ConfigDict(extra="ignore")

    canonical_suite: GateState = GateState.UNKNOWN
    canonical_suite_detail: str = ""
    status_reality: GateState = GateState.UNKNOWN
    status_reality_detail: str = ""
    status_reality_predicted: bool = False
    finalizer: GateState = GateState.UNKNOWN
    finalizer_detail: str = ""
    clean_clone: GateState = GateState.UNKNOWN
    clean_clone_detail: str = ""
    independent_review: GateState = GateState.UNKNOWN
    independent_review_detail: str = ""

    guard_in_canonical_suite: bool = False
    guard_path: str = ""
    failing_nodes: list[str] = Field(default_factory=list)
    environment_blockers: list[EnvironmentBlocker] = Field(default_factory=list)
    evidence_paths: list[str] = Field(default_factory=list)


class DeadlockNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    node_id: str
    label: str
    kind: str = "GATE"  # GATE | STATE | RULE
    state: str = "UNKNOWN"
    evidence: list[str] = Field(default_factory=list)


class DeadlockEdge(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str
    target: str
    relation: str = "blocks"
    rule_id: str = ""
    evidence: str = ""

    def render(self) -> str:
        cite = f"  [rule: {self.rule_id}]" if self.rule_id else ""
        return f"{self.source} --{self.relation}--> {self.target}{cite}"


class Deadlock(BaseModel):
    """A closed cycle of blocking dependencies."""

    model_config = ConfigDict(extra="ignore")

    nodes: list[DeadlockNode] = Field(default_factory=list)
    edges: list[DeadlockEdge] = Field(default_factory=list)
    cycle: list[str] = Field(default_factory=list)
    root_cause: str = ""
    evidence: list[str] = Field(default_factory=list)
    break_options: list[str] = Field(default_factory=list)
    requires_human_approval: bool = True

    deadlock_id: str = ""
    description: str = ""
    violation_type: ViolationType = ViolationType.UNKNOWN

    def render_cycle(self) -> str:
        """The causal chain, with each node's observed state, closing on itself."""
        by_id = {n.node_id: n for n in self.nodes}
        chain: list[str] = []
        for node_id in self.cycle:
            node = by_id.get(node_id)
            chain.append(f"{node.label} [{node.state}]" if node else node_id)
        if chain:
            chain.append(f"back to: {chain[0]}")
        return " → ".join(chain)


# --------------------------------------------------------------------------
# Gate observation
# --------------------------------------------------------------------------


class GateObserver:
    """Reads the current state of every gate from repository evidence."""

    def __init__(self, repo: Path, protocol: DiscoveredProtocol, topology: GitTopology) -> None:
        self.repo = Path(repo)
        self.protocol = protocol
        self.topology = topology

    def _read_json(self, rel: str) -> dict[str, Any] | None:
        path = self.repo / rel
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _receipt_rel(self, *needles: str) -> str:
        for rel in self.protocol.sources.by_category.get("receipts", []):
            name = rel.split("/")[-1].upper()
            if any(n.upper() in name for n in needles):
                return rel
        return ""

    def _build_status(self) -> dict[str, Any]:
        rel = self.protocol.sources.first("build_status")
        if not rel:
            return {}
        try:
            data = yaml.safe_load((self.repo / rel).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def observe(
        self,
        *,
        topology_violated: bool = False,
        run_commands: Iterable[Any] = (),
        independent_review_done: bool | None = None,
    ) -> ObservedGates:
        gates = ObservedGates()
        evidence_paths: list[str] = []

        # -- canonical suite, and the status-reality guard inside it -------
        suite_rel = self._receipt_rel("SUITE")
        suite = self._read_json(suite_rel) if suite_rel else None
        if suite is not None:
            evidence_paths.append(suite_rel)
            # A receipt is written by another program and may say anything at
            # all. An unreadable count is not a pass.
            failed = _as_int(suite.get("failed"), default=1)
            exit_status = _as_int(suite.get("exit_status"), default=1)
            gates.canonical_suite = (
                GateState.PASSING if (failed == 0 and exit_status == 0) else GateState.FAILING
            )
            gates.failing_nodes = [str(n) for n in (suite.get("failed_nodes") or suite.get("failures") or [])]
            gates.canonical_suite_detail = (
                f"{suite_rel}: exit_status={exit_status}, passed={suite.get('passed')}, failed={failed}"
            )
        else:
            gates.canonical_suite = GateState.NOT_RUN
            gates.canonical_suite_detail = "no canonical-suite receipt exists"

        gates.guard_path = self.protocol.sources.first("status_guard")
        guard_rule = self.protocol.effective(RuleKind.STATUS_REALITY_GUARD)
        guard_named_failing = [n for n in gates.failing_nodes if re.search(r"status[-_ ]?realit", n, re.I)]

        if guard_named_failing:
            gates.status_reality = GateState.FAILING
            gates.status_reality_detail = f"the canonical-suite receipt records failing node(s): {', '.join(guard_named_failing[:3])}"
        elif gates.canonical_suite is GateState.PASSING:
            gates.status_reality = GateState.PASSING
            gates.status_reality_detail = "the canonical suite is green, so its guards passed"
        elif (gates.guard_path or guard_rule) and topology_violated:
            # Not observed, inferred: the guard exists and the state it checks is
            # invalid. Recorded as a prediction so no report can present it as
            # an executed result.
            gates.status_reality = GateState.FAILING
            gates.status_reality_predicted = True
            gates.status_reality_detail = (
                f"predicted from the observed topology violation and the guard at "
                f"{gates.guard_path or (guard_rule.cite() if guard_rule else 'the repository')}; "
                "not an executed result"
            )
        else:
            gates.status_reality = GateState.UNKNOWN
            gates.status_reality_detail = "no evidence either way"

        canonical_rel = self.protocol.sources.first("canonical_suite")
        if gates.guard_path and canonical_rel:
            try:
                config = (self.repo / canonical_rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                config = ""
            guard_dir = gates.guard_path.split("/")[0]
            gates.guard_in_canonical_suite = bool(
                guard_dir and guard_dir in config
            ) or gates.guard_path in config
        if guard_named_failing:
            gates.guard_in_canonical_suite = True

        # -- finalizer ------------------------------------------------------
        build = self._build_status()
        snapshot = build.get("snapshot", {}) if isinstance(build.get("snapshot"), dict) else {}
        derived = build.get("derived", {}) if isinstance(build.get("derived"), dict) else {}
        reported = str(snapshot.get("finalizer_result", "") or "")
        if self.protocol.sources.first("build_status"):
            evidence_paths.append(self.protocol.sources.first("build_status"))

        if re.search(r"REFUS|DECLINE|WILL NOT", reported, re.I):
            gates.finalizer = GateState.BLOCKED
        elif re.search(r"NOT\s+EXECUTED|NOT\s+RUN|PENDING", reported, re.I) or not reported:
            gates.finalizer = GateState.NOT_RUN
        elif re.search(r"FAIL|ERROR", reported, re.I):
            gates.finalizer = GateState.FAILING
        else:
            gates.finalizer = GateState.PASSING
        gates.finalizer_detail = f"finalizer_result={reported[:100]!r}" if reported else "no finalizer result recorded"

        # A finalizer that recorded success for a different tree has not run for
        # this work, whatever it says.
        recorded_commit = str(derived.get("content_commit", "") or "")
        if gates.finalizer is GateState.PASSING and recorded_commit:
            if not any(
                c.commit_sha.startswith(recorded_commit[:12]) for c in self.topology.commits
            ) and not self.topology.head_commit.startswith(recorded_commit[:12]):
                gates.finalizer = GateState.NOT_RUN
                gates.finalizer_detail += (
                    f"; the recorded content_commit {recorded_commit[:12]} is not in the "
                    "current range, so it does not validate this work"
                )

        # -- clean clone ----------------------------------------------------
        gate_rel = self._receipt_rel("GATE", "CLONE")
        gate = self._read_json(gate_rel) if gate_rel else None
        gate_text = json.dumps(gate) if gate else ""
        clean_clone_reported = str(snapshot.get("clean_clone_result", "") or "")
        haystack = f"{gate_text}\n{clean_clone_reported}\n" + "\n".join(
            f"{getattr(c, 'stdout', '')}\n{getattr(c, 'stderr', '')}" for c in run_commands
        )

        if gate is not None:
            evidence_paths.append(gate_rel)
            gates.clean_clone = GateState.PASSING if gate.get("passed") else GateState.FAILING
            gates.clean_clone_detail = f"{gate_rel}: passed={bool(gate.get('passed'))}"
        elif clean_clone_reported:
            gates.clean_clone = (
                GateState.NOT_RUN
                if re.search(r"NOT\s+EXECUTED|NOT\s+RUN|PENDING", clean_clone_reported, re.I)
                else GateState.UNKNOWN
            )
            gates.clean_clone_detail = f"clean_clone_result={clean_clone_reported[:100]!r}"
        else:
            gates.clean_clone = GateState.NOT_RUN
            gates.clean_clone_detail = "no clean-clone receipt exists"

        # A gate that recorded a pass is not overridden by text found elsewhere:
        # a certificate error in an unrelated log does not un-run a gate that ran.
        tls = _TLS_RE.search(haystack) if gates.clean_clone is not GateState.PASSING else None
        network = _NETWORK_RE.search(haystack) if gates.clean_clone is not GateState.PASSING else None
        if tls or network:
            reason = "TLS/certificate verification" if tls else "network reachability"
            snippet = (tls or network).group(0)[:80]
            gates.clean_clone = GateState.BLOCKED
            gates.clean_clone_detail = (
                f"dependency installation cannot complete: {reason} failure ({snippet!r})"
            )
            gates.environment_blockers.append(
                EnvironmentBlocker(
                    blocker_id="clean-clone-environment",
                    description=(
                        f"the clean-clone gate cannot run: dependency installation fails on "
                        f"{reason}"
                    ),
                    blocks="clean_clone",
                    evidence=[e for e in (gate_rel, clean_clone_reported[:120]) if e],
                )
            )

        # -- independent review --------------------------------------------
        if independent_review_done is None:
            gates.independent_review = GateState.NOT_RUN
            gates.independent_review_detail = "no independent review recorded for this state"
        else:
            gates.independent_review = GateState.PASSING if independent_review_done else GateState.NOT_RUN
            gates.independent_review_detail = (
                "an independent review is recorded" if independent_review_done else "not yet performed"
            )

        gates.evidence_paths = [p for p in evidence_paths if p]
        return gates


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------

_NODES: dict[str, tuple[str, str]] = {
    "commit_topology": ("commit topology since the authorized baseline", "STATE"),
    "status_reality": ("the status-reality guard", "GATE"),
    "canonical_suite": ("the canonical suite", "GATE"),
    "finalizer": ("the finalizer", "GATE"),
    "derived_status": ("derived status (finalizer-owned)", "STATE"),
    "receipt_freshness": ("receipt freshness against final HEAD/tree", "STATE"),
    "head_position": ("HEAD/tree position", "STATE"),
    "baseline_value": ("the recorded baseline the guard validates against", "STATE"),
    "clean_clone": ("the clean-clone gate", "GATE"),
    "independent_review": ("independent review", "GATE"),
}


class DeadlockDetector:
    """Builds the dependency graph and finds cycles in it."""

    def __init__(self, protocol: DiscoveredProtocol) -> None:
        self.protocol = protocol

    # -- graph -------------------------------------------------------------

    def build_graph(
        self,
        topology: GitTopology,
        gates: ObservedGates,
        violations: list[ProtocolViolation],
    ) -> tuple[list[DeadlockNode], list[DeadlockEdge]]:
        """Emit only the edges the repository's own rules and evidence support."""
        p = self.protocol
        guard_rule = p.effective(RuleKind.STATUS_REALITY_GUARD)
        suite_rule = p.effective(RuleKind.CANONICAL_SUITE_GATE)
        owner_rule = p.effective(RuleKind.FINALIZER_OWNERSHIP)
        receipt_rule = p.states(RuleKind.RECEIPT_FRESHNESS, "must_match_head")

        history_violations = [v for v in violations if v.violation_type is ViolationType.HISTORY]
        edges: list[DeadlockEdge] = []
        used: set[str] = set()

        def add(source: str, target: str, relation: str, rule_id: str, evidence: str) -> None:
            edges.append(
                DeadlockEdge(source=source, target=target, relation=relation, rule_id=rule_id, evidence=evidence)
            )
            used.update({source, target})

        # 1. An invalid topology is what the status-reality guard checks.
        if history_violations and (guard_rule is not None or gates.guard_path):
            add(
                "commit_topology",
                "status_reality",
                "invalidates",
                guard_rule.rule_id if guard_rule else "",
                f"{len(history_violations)} topology violation(s) observed; the guard at "
                f"{gates.guard_path or (guard_rule.cite() if guard_rule else 'the repository')} "
                "validates exactly this state",
            )

        # 2. The guard is part of the canonical suite, so its failure reddens it.
        if gates.status_reality is GateState.FAILING and gates.guard_in_canonical_suite:
            add(
                "status_reality",
                "canonical_suite",
                "reddens",
                guard_rule.rule_id if guard_rule else "",
                gates.status_reality_detail,
            )

        # 3. The repository gates finalization on a green canonical suite.
        #    A suite that merely exists gates nothing: the repository has to say
        #    that finalization waits on it.
        gating_rule = p.states(RuleKind.CANONICAL_SUITE_GATE, "gates", "finalizer") or p.states(
            RuleKind.CANONICAL_SUITE_GATE, "requires_green"
        )
        if gates.canonical_suite in (GateState.FAILING, GateState.NOT_RUN) and gating_rule is not None:
            add(
                "canonical_suite",
                "finalizer",
                "blocks",
                gating_rule.rule_id,
                f"{gates.canonical_suite_detail}; {gating_rule.description[:120]}",
            )

        # 4. Only the finalizer may write derived status.
        if gates.finalizer in (GateState.BLOCKED, GateState.NOT_RUN, GateState.FAILING) and (
            owner_rule is not None
        ):
            add(
                "finalizer",
                "derived_status",
                "blocks",
                owner_rule.rule_id,
                f"finalizer state={gates.finalizer.value} ({gates.finalizer_detail}); "
                f"{owner_rule.description[:120]}",
            )

        # 5. The missing finalizer-owned commit is what makes the topology
        #    invalid — and no one else may create it. This is the edge that
        #    closes the loop, so it is emitted only when the repository really
        #    does forbid every other actor from writing status.
        exclusive_rule = p.states(RuleKind.FINALIZER_OWNERSHIP, "exclusive")
        ban_rule = p.states(RuleKind.MANUAL_STATUS_PROHIBITED, "prohibited")
        exclusive = exclusive_rule is not None
        banned = ban_rule is not None
        authorized_escape = p.states(RuleKind.MANUAL_FINALIZATION_ALLOWED, "explicitly_authorized") is not None
        needs_metadata_commit = (
            p.states(RuleKind.COMMIT_TOPOLOGY, "requires_metadata_commit") is not None
        )
        if (
            history_violations
            and needs_metadata_commit
            and (exclusive or banned)
            and not authorized_escape
            and gates.finalizer is not GateState.PASSING
        ):
            add(
                "derived_status",
                "commit_topology",
                "blocks",
                (ban_rule or exclusive_rule).rule_id if (ban_rule or exclusive_rule) else "",
                "the topology can only become valid once the finalizer-owned metadata/status "
                "commit exists, and the repository forbids any other actor from writing it",
            )

        # 6. Receipt freshness against a HEAD that the receipt itself moves.
        stale_committed = [
            r
            for r in topology.receipts
            if r.exists and r.committed_in and (r.self_referential or not r.matches_head)
        ]
        if receipt_rule is not None and stale_committed:
            r = stale_committed[0]
            add(
                "receipt_freshness",
                "head_position",
                "requires-change-to",
                receipt_rule.rule_id,
                f"{r.path} must name the final HEAD/tree, so it must be rewritten and committed",
            )
            add(
                "head_position",
                "receipt_freshness",
                "invalidates",
                receipt_rule.rule_id,
                f"committing {r.path} moves HEAD/tree, so the value it just recorded is stale "
                f"({r.detail})",
            )

        # 7. A guard validating a baseline that the gate it blocks is the only
        #    thing allowed to update. Requires evidence that the guard actually
        #    reads the baseline — a baseline merely recorded in a status file is
        #    an ordinary arrangement, not a cycle.
        if (
            gates.status_reality is GateState.FAILING
            and topology.baseline_source.startswith("recorded in")
            and _guard_reads_baseline(self.protocol)
        ):
            add(
                "baseline_value",
                "status_reality",
                "invalidates",
                guard_rule.rule_id if guard_rule else "",
                f"the baseline is read from {topology.baseline_source}, a surface the guard "
                "itself validates",
            )
            add(
                "status_reality",
                "finalizer",
                "blocks",
                suite_rule.rule_id if suite_rule else "",
                "the guard's failure prevents the finalizer from running",
            )
            add(
                "finalizer",
                "baseline_value",
                "blocks",
                owner_rule.rule_id if owner_rule else "",
                "only the finalizer may update the recorded baseline",
            )

        nodes = [
            DeadlockNode(
                node_id=node_id,
                label=_NODES[node_id][0],
                kind=_NODES[node_id][1],
                state=_node_state(node_id, gates, bool(history_violations)),
                evidence=_node_evidence(node_id, gates, topology, history_violations),
            )
            for node_id in _NODES
            if node_id in used
        ]
        return nodes, edges

    # -- detection ---------------------------------------------------------

    def detect(
        self,
        topology: GitTopology,
        gates: ObservedGates,
        violations: list[ProtocolViolation],
    ) -> list[Deadlock]:
        """Return every distinct blocking cycle in the dependency graph."""
        nodes, edges = self.build_graph(topology, gates, violations)
        if not edges:
            return []

        cycles = find_cycles([(e.source, e.target) for e in edges])
        by_id = {n.node_id: n for n in nodes}

        deadlocks: list[Deadlock] = []
        for cycle in cycles:
            in_cycle = set(cycle)
            cycle_edges = [
                e
                for e in edges
                if e.source in in_cycle
                and e.target in in_cycle
                and _adjacent_in_cycle(cycle, e.source, e.target)
            ]
            cycle_nodes = [by_id[n] for n in cycle if n in by_id]
            deadlocks.append(
                Deadlock(
                    deadlock_id="+".join(cycle),
                    nodes=cycle_nodes,
                    edges=cycle_edges,
                    cycle=list(cycle),
                    root_cause=_root_cause(cycle, violations, gates),
                    evidence=[e.evidence for e in cycle_edges if e.evidence],
                    break_options=_break_options(cycle, self.protocol, topology),
                    requires_human_approval=True,
                    description=_describe(cycle, by_id),
                    violation_type=_cycle_violation_type(cycle, violations),
                )
            )

        # A commit-history cycle is reported first: it is the one that explains
        # why every other gate is stuck.
        priority = {ViolationType.HISTORY: 0, ViolationType.EVIDENCE: 1}
        deadlocks.sort(key=lambda d: (priority.get(d.violation_type, 2), -len(d.cycle)))
        return deadlocks


# --------------------------------------------------------------------------
# Graph helpers
# --------------------------------------------------------------------------


def find_cycles(edges: list[tuple[str, str]]) -> list[list[str]]:
    """Every distinct simple cycle, each normalized to start at its smallest node."""
    adjacency: dict[str, list[str]] = {}
    for source, target in edges:
        adjacency.setdefault(source, []).append(target)
        adjacency.setdefault(target, [])

    found: dict[tuple[str, ...], list[str]] = {}
    stack: list[str] = []
    on_stack: set[str] = set()

    def walk(node: str) -> None:
        stack.append(node)
        on_stack.add(node)
        for nxt in adjacency.get(node, []):
            if nxt in on_stack:
                cycle = stack[stack.index(nxt) :]
                key = _canonical_cycle(cycle)
                found.setdefault(key, list(key))
            elif len(stack) < 24:
                walk(nxt)
        stack.pop()
        on_stack.discard(node)

    for node in sorted(adjacency):
        walk(node)

    return [_rotate_for_reading(found[k]) for k in sorted(found)]


# Where a cycle reads best from: the state that causes it, not the gate that
# happens to sort first.
_PREFERRED_START = ("commit_topology", "receipt_freshness", "baseline_value")


def _rotate_for_reading(cycle: list[str]) -> list[str]:
    for preferred in _PREFERRED_START:
        if preferred in cycle:
            i = cycle.index(preferred)
            return cycle[i:] + cycle[:i]
    return cycle


def _as_int(value: Any, default: int) -> int:
    """Read a number out of a receipt. Anything unreadable takes the default.

    The default at every call site is the failing value: a receipt the driver
    cannot parse has not demonstrated a pass.
    """
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _guard_reads_baseline(protocol: DiscoveredProtocol) -> bool:
    """Does the status-reality guard actually validate against a recorded baseline?"""
    rel = protocol.sources.first("status_guard")
    if not rel:
        return False
    try:
        text = (Path(protocol.repo) / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(r"\bbaseline\b", text, re.I))


def _canonical_cycle(cycle: list[str]) -> tuple[str, ...]:
    """Rotate a cycle so the same loop always yields the same key."""
    if not cycle:
        return ()
    start = cycle.index(min(cycle))
    return tuple(cycle[start:] + cycle[:start])


def _adjacent_in_cycle(cycle: list[str], source: str, target: str) -> bool:
    if source not in cycle or target not in cycle:
        return False
    i = cycle.index(source)
    return cycle[(i + 1) % len(cycle)] == target


def _node_state(node_id: str, gates: ObservedGates, topology_violated: bool) -> str:
    if node_id == "commit_topology":
        return "VIOLATED" if topology_violated else "OK"
    if node_id == "status_reality":
        return gates.status_reality.value
    if node_id == "canonical_suite":
        return gates.canonical_suite.value
    if node_id == "finalizer":
        return gates.finalizer.value
    if node_id == "clean_clone":
        return gates.clean_clone.value
    if node_id == "independent_review":
        return gates.independent_review.value
    if node_id == "derived_status":
        return "STALE" if gates.finalizer is not GateState.PASSING else "CURRENT"
    return "UNKNOWN"


def _node_evidence(
    node_id: str,
    gates: ObservedGates,
    topology: GitTopology,
    history_violations: list[ProtocolViolation],
) -> list[str]:
    if node_id == "commit_topology":
        return [v.observed_state for v in history_violations[:3]] or [topology.render_graph()]
    if node_id == "status_reality":
        return [gates.status_reality_detail]
    if node_id == "canonical_suite":
        return [gates.canonical_suite_detail]
    if node_id == "finalizer":
        return [gates.finalizer_detail]
    if node_id == "clean_clone":
        return [gates.clean_clone_detail]
    if node_id == "receipt_freshness":
        return [r.detail for r in topology.receipts if r.exists][:3]
    return []


def _root_cause(cycle: list[str], violations: list[ProtocolViolation], gates: ObservedGates) -> str:
    if "commit_topology" in cycle:
        history = [v for v in violations if v.violation_type is ViolationType.HISTORY]
        if history:
            return (
                f"commit-history finalization deadlock: {history[0].observed_state}, where "
                f"{history[0].expected_state}"
            )
        return "commit-history finalization deadlock"
    if "receipt_freshness" in cycle:
        return (
            "receipt-freshness deadlock: the receipt must name the final HEAD/tree, but "
            "committing the receipt is itself what moves HEAD/tree"
        )
    if "baseline_value" in cycle:
        return (
            "moving-baseline deadlock: the guard validates against a baseline that only the "
            "gate it blocks is permitted to update"
        )
    return "circular gate dependency"


def _cycle_violation_type(cycle: list[str], violations: list[ProtocolViolation]) -> ViolationType:
    if "commit_topology" in cycle:
        return ViolationType.HISTORY
    if "receipt_freshness" in cycle:
        return ViolationType.EVIDENCE
    return ViolationType.PROCESS


def _describe(cycle: list[str], by_id: dict[str, DeadlockNode]) -> str:
    labels = [by_id[n].label if n in by_id else n for n in cycle]
    return " → ".join(labels + [labels[0]]) if labels else ""


def _break_options(cycle: list[str], protocol: DiscoveredProtocol, topology: GitTopology) -> list[str]:
    """Which edge could be cut, and what cutting it would cost."""
    options: list[str] = []
    if "commit_topology" in cycle:
        where = "local" if not topology.is_shared else "shared/pushed"
        options.append(
            f"make the topology satisfy the repository's rule by normalizing {where} history "
            "into the permitted shape (rewrites history — requires human approval)"
        )
        options.append(
            "add a repository-authorized mechanism that accepts the observed topology "
            "(changes canonical protocol — requires human approval)"
        )
        if protocol.effective(RuleKind.MANUAL_FINALIZATION_ALLOWED) is not None:
            options.append(
                "perform the manual finalization the repository explicitly authorizes"
            )
        else:
            options.append(
                "manual finalization is NOT available: the repository does not authorize it"
            )
    if "receipt_freshness" in cycle:
        options.append(
            "record the receipt against the content commit rather than against the commit "
            "that carries the receipt (a protocol clarification, not a history rewrite)"
        )
    if "baseline_value" in cycle:
        options.append(
            "record the authorized baseline in a source the guarded gate does not own"
        )
    return options
