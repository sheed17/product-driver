"""Repository protocol resolution.

The completion auditor answers "is this claim true?". This module answers a
different question: "the implementation is right, so why can nothing be
finalized?" — and answers it with the causal chain rather than the word BLOCKED.

It reports, for one repository state:

    1. what is blocked
    2. which repository rule is being violated, quoted from its source
    3. what evidence proves it
    4. whether it is a code, evidence, environment, process, history or
       authority defect — kept separate, never merged into one vague failure
    5. whether the blockers form a circular dependency
    6. which repair options the repository's own rules authorize
    7. which is safest
    8. whether it needs human approval
    9. the exact commands or builder prompt to run after approval
   10. what must be re-run afterwards

Everything here is read-only. The driver proposes; a human approves; a fresh
builder session executes. This process never rewrites history, never writes
derived status, and never deletes evidence to make a graph look valid.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from .context import ContextResolutionError, RepositoryContextLoader
from .deadlock_detector import (
    Deadlock,
    DeadlockDetector,
    EnvironmentBlocker,
    GateObserver,
    GateState,
    ObservedGates,
)
from .git_topology import (
    CommitRole,
    GitCommitRole,
    GitTopology,
    GitTopologyAnalyzer,
    expected_graph,
)
from .models import utcnow
from .repo_state import RepositoryState
from .protocol_sources import (
    DiscoveredProtocol,
    ProtocolConflict,
    ProtocolRule,
    ProtocolViolation,
    RuleKind,
    ViolationType,
    discover_protocol,
)
from .remediation_planner import (
    ApprovalRecord,
    ApprovalStore,
    RemediationOption,
    RemediationPlanner,
    recommended,
)


class ProtocolStatus(str, Enum):
    CONSISTENT = "CONSISTENT"
    VIOLATION = "VIOLATION"
    DEADLOCK = "DEADLOCK"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"
    BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"
    BLOCKED_AUTHORITY = "BLOCKED_AUTHORITY"


# The seven buckets a blocker may fall into. They are never merged: a TLS
# failure and a commit-history deadlock are two findings, not one bad day.
class CauseCategory(str, Enum):
    PRODUCT_CODE = "product/code defect"
    EVIDENCE = "test or evidence defect"
    PROTOCOL = "repository protocol defect"
    HISTORY = "commit-history defect"
    ENVIRONMENT = "environmental defect"
    INDEPENDENT_REVIEW = "independent-review boundary"
    HUMAN_APPROVAL = "founder/human approval"


class RootCause(BaseModel):
    model_config = ConfigDict(extra="ignore")

    priority: str = "PRIMARY"  # PRIMARY | SECONDARY | REQUIRED_LATER
    category: CauseCategory = CauseCategory.PROTOCOL
    summary: str = ""
    evidence: list[str] = Field(default_factory=list)


class ProtocolResolution(BaseModel):
    """The resolver's verdict for one repository state."""

    model_config = ConfigDict(extra="ignore")

    status: ProtocolStatus
    current_graph: str = ""
    expected_graph: str = ""
    violations: list[ProtocolViolation] = Field(default_factory=list)
    deadlocks: list[Deadlock] = Field(default_factory=list)
    options: list[RemediationOption] = Field(default_factory=list)
    recommended_option: RemediationOption | None = None
    next_safe_action: str = ""
    approval_prompt: str = ""

    # Supporting observation, carried so a report never has to re-read the repo.
    topology: GitTopology | None = None
    gates: ObservedGates | None = None
    conflicts: list[ProtocolConflict] = Field(default_factory=list)
    environment_blockers: list[EnvironmentBlocker] = Field(default_factory=list)
    root_causes: list[RootCause] = Field(default_factory=list)
    rules_consulted: list[ProtocolRule] = Field(default_factory=list)
    sources_read: list[str] = Field(default_factory=list)
    unit_id: str = ""
    plan_hash: str = ""
    repo_path: str = ""
    resolved_at: str = Field(default_factory=utcnow)

    # -- views -------------------------------------------------------------

    @property
    def blocks_finalization(self) -> bool:
        return self.status is not ProtocolStatus.CONSISTENT

    @property
    def blocks_independent_review(self) -> bool:
        """A review of an invalid topology reviews the wrong thing."""
        return self.status in (
            ProtocolStatus.VIOLATION,
            ProtocolStatus.DEADLOCK,
            ProtocolStatus.REQUIRES_APPROVAL,
            ProtocolStatus.BLOCKED_AUTHORITY,
        )

    @property
    def requires_human_approval(self) -> bool:
        return self.status is ProtocolStatus.REQUIRES_APPROVAL

    def cause(self, priority: str) -> RootCause | None:
        for c in self.root_causes:
            if c.priority == priority:
                return c
        return None

    def blocker_chain(self) -> str:
        """The causal chain, when one exists."""
        if not self.deadlocks:
            return ""
        return self.deadlocks[0].render_cycle()

    def summary_block(self) -> str:
        primary = self.cause("PRIMARY")
        return "\n".join(
            [
                f"REPOSITORY PROTOCOL: {self.status.value}",
                f"PRIMARY ROOT CAUSE: {primary.summary if primary else 'none'}",
                f"VIOLATIONS: {len(self.violations)}   DEADLOCKS: {len(self.deadlocks)}   "
                f"OPTIONS: {len(self.options)}",
                f"NEXT SAFE ACTION: {self.next_safe_action}",
            ]
        )

    def render_report(self, run_id: str = "") -> str:
        """The concise terminal report."""
        lines: list[str] = [f"REPOSITORY PROTOCOL: {self.status.value}"]
        if self.deadlocks and self.status is not ProtocolStatus.DEADLOCK:
            lines[0] += f"  ({len(self.deadlocks)} deadlock(s))"

        primary = self.cause("PRIMARY")
        if primary:
            lines += ["", "PRIMARY ROOT CAUSE:", primary.summary]

        if self.conflicts:
            lines += ["", "PROTOCOL CONFLICT:"]
            lines += [c.render() for c in self.conflicts]

        if self.topology is not None and self.topology.state.determined:
            lines += ["", "REPOSITORY STATE (the repository's own state machine):",
                      f"  {self.topology.state.describe()}"]

        lines += ["", "CURRENT GRAPH:", self.current_graph or "(not determined)"]
        lines += ["", "EXPECTED GRAPH:", self.expected_graph or "(the repository states no rule)"]

        chain = self.blocker_chain()
        if chain:
            lines += ["", "WHY THE FINALIZER CANNOT PROCEED:", chain]

        if self.violations:
            lines += ["", f"VIOLATIONS ({len(self.violations)}):"]
            for i, v in enumerate(self.violations, 1):
                lines.append(f"  {i}. {v.render()}")

        for cause in self.root_causes:
            if cause.priority == "SECONDARY":
                lines += ["", "SECONDARY BLOCKER:", f"{cause.summary}  [{cause.category.value}]"]

        later = [c for c in self.root_causes if c.priority == "REQUIRED_LATER"]
        if later:
            lines += ["", "REQUIRED AFTER REPAIR:"]
            lines += [f"  - {c.summary}  [{c.category.value}]" for c in later]

        if self.options:
            lines += ["", "REMEDIATION OPTIONS:"]
            for option in self.options:
                lines.append(option.render(verbose=False))

        if self.recommended_option is not None:
            option = self.recommended_option
            lines += ["", "RECOMMENDED OPTION:", f"[{option.option_id}] {option.title}", option.description]
            if option.rerun_requirements:
                lines += ["", "GATES TO RERUN AFTER REPAIR:"]
                lines += [f"  - {r}" for r in option.rerun_requirements]

        if self.status is ProtocolStatus.REQUIRES_APPROVAL and self.recommended_option is not None:
            option = self.recommended_option
            lines += ["", "APPROVAL REQUIRED:", option.approval_phrase, ""]
            lines.append(
                "  python -m neyma_product_driver approve"
                + (f" --run {run_id}" if run_id else " --run <run-id>")
                + f" --option {option.option_id} --confirmation \"{option.approval_phrase}\""
            )

        lines += ["", f"NEXT SAFE ACTION: {self.next_safe_action}"]
        return "\n".join(lines)


# --------------------------------------------------------------------------
# The resolver
# --------------------------------------------------------------------------


def _ratified_by_finalizer(content_commit: GitCommitRole, topology: GitTopology) -> bool:
    """Is this content commit closed by a finalizer metadata commit on top of it?

    True only for the legal two-commit pair: the very next commit on the
    first-parent path is FINALIZER_GENERATED (finalizer-owned files only, with
    the ownership stamp) and its first parent is this content commit — so the
    finalizer certified exactly this content commit's tree.
    """
    commits = topology.commits
    try:
        idx = commits.index(content_commit)
    except ValueError:
        return False
    follower = commits[idx + 1] if idx + 1 < len(commits) else None
    return (
        follower is not None
        and follower.role is CommitRole.FINALIZER_GENERATED
        and content_commit.commit_sha in follower.parents
    )


class ProtocolResolver:
    """Diagnoses repository-governance state. Read-only."""

    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)
        self._loader = RepositoryContextLoader(self.repo)

    # -- unit --------------------------------------------------------------

    def _unit_id(self) -> str:
        """The active unit, when the registry names one. Never fatal here.

        The completion auditor fails closed on an unresolvable unit; this
        resolver does not, because diagnosing a repository whose registry is
        itself in trouble is exactly when it is most useful.
        """
        try:
            return self._loader.resolve_active_unit().unit_id
        except ContextResolutionError:
            return ""
        except Exception:
            return ""

    # -- violations --------------------------------------------------------

    def topology_violations(
        self, protocol: DiscoveredProtocol, topology: GitTopology
    ) -> list[ProtocolViolation]:
        """Compare the observed graph against the rule the repository states.

        A rule the repository does not state produces no violation: the driver
        has no standing to invent a convention and then enforce it.
        """
        violations: list[ProtocolViolation] = []
        rule = protocol.states(RuleKind.COMMIT_TOPOLOGY, "max_content_commits")
        owner_rule = protocol.states(RuleKind.FINALIZER_OWNERSHIP, "exclusive")
        manual_ban = protocol.states(RuleKind.MANUAL_STATUS_PROHIBITED, "prohibited")
        needs_metadata = protocol.states(RuleKind.COMMIT_TOPOLOGY, "requires_metadata_commit")

        if rule is not None:
            allowed = int(rule.parameters["max_content_commits"])
            content = topology.content_commits
            if len(content) > allowed:
                violations.append(
                    ProtocolViolation(
                        rule_id=rule.rule_id,
                        observed_state=(
                            f"{len(content)} content commit(s) since the authorized baseline: "
                            + ", ".join(f"{c.short} ({c.role.value})" for c in content)
                        ),
                        expected_state=(
                            rule.allowed_states[0]
                            if rule.allowed_states
                            else f"at most {allowed} content commit(s)"
                        ),
                        evidence_paths=sorted({f for c in content for f in c.files})[:12],
                        severity="blocker",
                        violation_type=ViolationType.HISTORY,
                        detail=(
                            f"current authority permits {allowed} content commit(s) per unit; "
                            f"{len(content)} exist"
                        ),
                        rule_citation=rule.cite(),
                    )
                )

            # A commit that changes runtime content and derived status together
            # cannot be either kind cleanly — UNLESS the finalizer metadata
            # commit that immediately follows it rebinds derived status and
            # receipts to it. In that legal two-commit pair the content commit's
            # status edits are the adjudicated content the finalizer then
            # certified (its tree is the one the receipts validate), not a
            # self-certifying green. Only an UNRATIFIED mixed commit is a defect.
            mixed = [
                c
                for c in content
                if c.status_files and not _ratified_by_finalizer(c, topology)
            ]
            if mixed and needs_metadata is not None:
                violations.append(
                    ProtocolViolation(
                        rule_id=rule.rule_id,
                        observed_state=(
                            f"commit {mixed[0].short} changes both runtime content and derived "
                            f"status ({', '.join(mixed[0].status_files[:3])})"
                        ),
                        expected_state="content and derived status live in separate commits",
                        evidence_paths=mixed[0].files[:12],
                        severity="blocker",
                        violation_type=ViolationType.HISTORY,
                        detail="a content commit also carries derived status",
                        rule_citation=rule.cite(),
                    )
                )

            # Status before content: the status commit describes a tree that did
            # not exist yet.
            #
            # This check may only speak where the REPOSITORY'S OWN state machine
            # has already found the arrangement illegal. The repository defines
            # three legal states (FINALIZED / PRODUCING / BASELINE) and PRODUCING
            # legitimately places a finalizer metadata commit before the next
            # content commit — the certified pair the new content was built on.
            # Enforcing a private "status must never precede content" ordering on
            # top of that is a stronger separation rule than the repository
            # states, and it condemned a legal PRODUCING repository (a certified
            # pair plus one fresh content commit) as corrupt, then recommended
            # rewriting the certified history to "repair" it. The driver has no
            # standing to invent a convention and then enforce it.
            first_status = next((i for i, c in enumerate(topology.commits) if c.is_status), None)
            first_content = next((i for i, c in enumerate(topology.commits) if c.is_content), None)
            if (
                first_status is not None
                and first_content is not None
                and first_status < first_content
                and not topology.state.legal
            ):
                violations.append(
                    ProtocolViolation(
                        rule_id=rule.rule_id,
                        observed_state=(
                            f"a status commit ({topology.commits[first_status].short}) precedes the "
                            f"content commit ({topology.commits[first_content].short})"
                        ),
                        expected_state="the metadata/status commit follows the content commit",
                        evidence_paths=topology.commits[first_status].files[:8],
                        severity="blocker",
                        violation_type=ViolationType.HISTORY,
                        detail="status was recorded for a tree that did not exist yet",
                        rule_citation=rule.cite(),
                    )
                )

        # A status commit no gate produced, where the repository says only the
        # finalizer may write derived status.
        if owner_rule is not None or manual_ban is not None:
            authority_rule = manual_ban or owner_rule
            for commit in topology.hand_edited_status_commits:
                violations.append(
                    ProtocolViolation(
                        rule_id=authority_rule.rule_id if authority_rule else "",
                        observed_state=(
                            f"commit {commit.short} writes derived status "
                            f"({', '.join(commit.status_files[:3])}) with no finalizer marker in "
                            "its diff"
                        ),
                        expected_state="derived status is written only by the finalizer",
                        evidence_paths=commit.status_files[:8],
                        severity="blocker",
                        violation_type=ViolationType.HISTORY,
                        detail="a hand-edited derived-status commit is present in the history",
                        rule_citation=authority_rule.cite() if authority_rule else "",
                    )
                )

        # Receipts bound to the wrong state, or to themselves.
        receipt_rule = protocol.effective(RuleKind.RECEIPT_FRESHNESS)
        for binding in topology.receipts:
            if not binding.exists:
                continue
            if binding.self_referential:
                violations.append(
                    ProtocolViolation(
                        rule_id=receipt_rule.rule_id if receipt_rule else "",
                        observed_state=(
                            f"{binding.path} names commit {binding.recorded_commit[:12]}, which is "
                            "the very commit that introduced the receipt"
                        ),
                        expected_state=(
                            "a receipt names the state it validated, which must already exist "
                            "when the receipt is written"
                        ),
                        evidence_paths=[binding.path],
                        severity="blocker",
                        violation_type=ViolationType.EVIDENCE,
                        detail="the receipt could not have known the sha it records",
                        rule_citation=receipt_rule.cite() if receipt_rule else "",
                    )
                )
            elif not binding.matches_head and not binding.matches_known_commit:
                violations.append(
                    ProtocolViolation(
                        rule_id=receipt_rule.rule_id if receipt_rule else "",
                        observed_state=(
                            f"{binding.path} names commit {binding.recorded_commit[:12] or '(none)'} / "
                            f"tree {binding.recorded_tree[:12] or '(none)'}, which is not HEAD "
                            f"({topology.head_commit[:12]}) nor any commit since the baseline"
                        ),
                        expected_state="a receipt validates the tree it is presented for",
                        evidence_paths=[binding.path],
                        severity="blocker",
                        violation_type=ViolationType.EVIDENCE,
                        detail="stale receipt",
                        rule_citation=receipt_rule.cite() if receipt_rule else "",
                    )
                )

        # The repository's own state machine is the authority on whether the
        # arrangement of recorded status and checked-out commit is legal. When it
        # says the state is illegal, that is a real violation reported in the
        # repository's own words — not the driver's paraphrase of it.
        #
        # FINALIZED / PRODUCING / BASELINE are *commit-topology* states: each one
        # is defined by a status-metadata commit sitting a known distance above a
        # content commit. A repository that states no commit topology has no such
        # arrangement to be in, legally or otherwise, and reporting one as a
        # blocker would be this driver enforcing a convention its target retired
        # — while citing no rule at all, because there is none to cite. An
        # unattributable blocker is worse than a missing one: it cannot be
        # answered, and run 20260820-204803 spent eight iterations proving it.
        if topology.state.state is RepositoryState.ILLEGAL and rule is not None:
            violations.append(
                ProtocolViolation(
                    rule_id=rule.rule_id if rule is not None else "",
                    observed_state=topology.state.reason,
                    expected_state=(
                        "the repository is in one of its three legal states: FINALIZED "
                        "(recorded == HEAD^), PRODUCING (recorded == HEAD^^ with a pure "
                        "status-metadata HEAD^), or BASELINE (recorded == HEAD)"
                    ),
                    evidence_paths=(
                        [topology.state.source_path] if topology.state.source_path else []
                    )
                    + topology.state.stray_files[:8],
                    severity="blocker",
                    violation_type=ViolationType.HISTORY,
                    detail="the repository is not in any state its own status-reality guard allows",
                    rule_citation=rule.cite() if rule is not None else "",
                )
            )

        if not topology.baseline_commit:
            violations.append(
                ProtocolViolation(
                    rule_id="",
                    observed_state="no authorized baseline could be determined",
                    expected_state=(
                        "the repository records the baseline each unit's commits are measured from"
                    ),
                    evidence_paths=[],
                    severity="blocker",
                    violation_type=ViolationType.AUTHORITY,
                    detail="topology cannot be judged without an authorized baseline",
                )
            )

        return violations

    # -- root causes -------------------------------------------------------

    def classify_causes(
        self,
        *,
        conflicts: list[ProtocolConflict],
        violations: list[ProtocolViolation],
        deadlocks: list[Deadlock],
        gates: ObservedGates,
        protocol: DiscoveredProtocol,
        requires_approval: bool,
    ) -> list[RootCause]:
        """Separate blockers. Unrelated problems never merge into one failure."""
        causes: list[RootCause] = []

        if conflicts:
            causes.append(
                RootCause(
                    priority="PRIMARY",
                    category=CauseCategory.PROTOCOL,
                    summary=(
                        "the repository states protocol definitions that cannot both hold, so no "
                        "rule can be applied without choosing between them"
                    ),
                    evidence=[c.description for c in conflicts],
                )
            )

        history_deadlock = next(
            (d for d in deadlocks if d.violation_type is ViolationType.HISTORY), None
        )
        if history_deadlock is not None:
            causes.append(
                RootCause(
                    priority="PRIMARY" if not causes else "SECONDARY",
                    category=CauseCategory.HISTORY,
                    summary=history_deadlock.root_cause,
                    evidence=history_deadlock.evidence[:4],
                )
            )
        elif deadlocks:
            causes.append(
                RootCause(
                    priority="PRIMARY" if not causes else "SECONDARY",
                    category=CauseCategory.PROTOCOL,
                    summary=deadlocks[0].root_cause,
                    evidence=deadlocks[0].evidence[:4],
                )
            )
        else:
            history = [v for v in violations if v.violation_type is ViolationType.HISTORY]
            if history:
                causes.append(
                    RootCause(
                        priority="PRIMARY" if not causes else "SECONDARY",
                        category=CauseCategory.HISTORY,
                        summary=history[0].detail or history[0].observed_state,
                        evidence=[history[0].observed_state],
                    )
                )

        evidence_defects = [v for v in violations if v.violation_type is ViolationType.EVIDENCE]
        if evidence_defects:
            causes.append(
                RootCause(
                    priority="PRIMARY" if not causes else "SECONDARY",
                    category=CauseCategory.EVIDENCE,
                    summary=f"{evidence_defects[0].detail}: {evidence_defects[0].observed_state}",
                    evidence=[v.observed_state for v in evidence_defects[:3]],
                )
            )

        for blocker in gates.environment_blockers:
            causes.append(
                RootCause(
                    priority="PRIMARY" if not causes else "SECONDARY",
                    category=CauseCategory.ENVIRONMENT,
                    summary=blocker.description,
                    evidence=blocker.evidence,
                )
            )

        review_rule = protocol.effective(RuleKind.INDEPENDENT_REVIEW)
        if review_rule is not None and gates.independent_review is not GateState.PASSING:
            causes.append(
                RootCause(
                    priority="REQUIRED_LATER",
                    category=CauseCategory.INDEPENDENT_REVIEW,
                    summary=(
                        "a fresh independent review is still required, and it must not run "
                        "against a topology the repository forbids"
                    ),
                    evidence=[review_rule.cite()],
                )
            )

        if requires_approval:
            causes.append(
                RootCause(
                    priority="REQUIRED_LATER",
                    category=CauseCategory.HUMAN_APPROVAL,
                    summary="the recommended repair rewrites history and needs explicit approval",
                    evidence=[],
                )
            )

        return causes

    # -- resolution --------------------------------------------------------

    def resolve(
        self,
        *,
        baseline: str | None = None,
        run_commands: Iterable[Any] = (),
        independent_review_done: bool | None = None,
    ) -> ProtocolResolution:
        """Read the repository and diagnose it. Never raises; never writes."""
        protocol = discover_protocol(self.repo)
        analyzer = GitTopologyAnalyzer(self.repo, protocol)
        topology = analyzer.analyze(baseline=baseline)
        unit_id = self._unit_id()

        violations = self.topology_violations(protocol, topology)
        topology_violated = any(v.violation_type is ViolationType.HISTORY for v in violations)

        gates = GateObserver(self.repo, protocol, topology).observe(
            topology_violated=topology_violated,
            run_commands=list(run_commands),
            independent_review_done=independent_review_done,
        )
        for blocker in gates.environment_blockers:
            violations.append(
                ProtocolViolation(
                    rule_id="",
                    observed_state=blocker.description,
                    expected_state="the gate runs and produces a receipt",
                    evidence_paths=blocker.evidence,
                    severity="environmental",
                    violation_type=ViolationType.ENVIRONMENT,
                    detail="an environment failure, not a product failure — and not a PASS",
                )
            )

        deadlocks = DeadlockDetector(protocol).detect(topology, gates, violations)

        planner = RemediationPlanner(protocol, unit_id=unit_id)
        options = planner.plan(topology, violations, deadlocks, gates)
        best = recommended(options)

        conflicts = protocol.unresolved_conflicts
        status = self._status_for(conflicts, violations, deadlocks, options, best, gates)
        requires_approval = status is ProtocolStatus.REQUIRES_APPROVAL

        causes = self.classify_causes(
            conflicts=conflicts,
            violations=violations,
            deadlocks=deadlocks,
            gates=gates,
            protocol=protocol,
            requires_approval=requires_approval,
        )

        resolution = ProtocolResolution(
            status=status,
            current_graph=topology.render_graph(),
            expected_graph=expected_graph(protocol, topology.baseline_commit),
            violations=violations,
            deadlocks=deadlocks,
            options=options,
            recommended_option=best,
            topology=topology,
            gates=gates,
            conflicts=conflicts,
            environment_blockers=list(gates.environment_blockers),
            root_causes=causes,
            rules_consulted=protocol.rules,
            sources_read=protocol.sources.all_paths(),
            unit_id=unit_id,
            plan_hash=best.plan_hash if best else "",
            repo_path=str(self.repo),
        )
        resolution.next_safe_action = self._next_safe_action(resolution)
        resolution.approval_prompt = build_approval_prompt(resolution)
        return resolution

    def _status_for(
        self,
        conflicts: list[ProtocolConflict],
        violations: list[ProtocolViolation],
        deadlocks: list[Deadlock],
        options: list[RemediationOption],
        best: RemediationOption | None,
        gates: ObservedGates,
    ) -> ProtocolStatus:
        """Decision precedence. Authority first; a green suite never outranks it."""
        if conflicts:
            return ProtocolStatus.BLOCKED_AUTHORITY
        if any(v.violation_type is ViolationType.AUTHORITY for v in violations):
            return ProtocolStatus.BLOCKED_AUTHORITY

        blocking = [
            v
            for v in violations
            if v.severity == "blocker" and v.violation_type is not ViolationType.ENVIRONMENT
        ]

        if deadlocks or blocking:
            if best is not None and best.requires_human_approval:
                return ProtocolStatus.REQUIRES_APPROVAL
            if deadlocks:
                return ProtocolStatus.DEADLOCK
            return ProtocolStatus.VIOLATION

        if gates.environment_blockers:
            return ProtocolStatus.BLOCKED_ENVIRONMENT
        return ProtocolStatus.CONSISTENT

    def _next_safe_action(self, resolution: ProtocolResolution) -> str:
        if resolution.status is ProtocolStatus.BLOCKED_AUTHORITY:
            if resolution.conflicts:
                return (
                    "resolve the contradictory protocol definitions; the driver has no standing "
                    "to choose which one governs"
                )
            return "record the authorized baseline this unit's commits are measured from"

        if resolution.status is ProtocolStatus.REQUIRES_APPROVAL and resolution.recommended_option:
            option = resolution.recommended_option
            return (
                f"review option {option.option_id} and, if you approve it, reply with the exact "
                f"phrase: {option.approval_phrase!r}"
            )

        if resolution.status is ProtocolStatus.DEADLOCK:
            return (
                "stop re-running the blocked gate: the cycle above cannot be cleared by any "
                "single gate. Choose how to break it."
            )

        if resolution.status is ProtocolStatus.VIOLATION:
            first = resolution.violations[0] if resolution.violations else None
            return (
                f"correct the state that violates {first.rule_id or 'the stated rule'}"
                if first
                else "correct the observed violation"
            )

        if resolution.status is ProtocolStatus.BLOCKED_ENVIRONMENT:
            return (
                "repair the environment and re-run the blocked gate; do not record its result "
                "as PASS in the meantime"
            )

        return "proceed: topology and authority are consistent"

    # -- gating other actions ---------------------------------------------

    def review_block_reason(self, resolution: ProtocolResolution) -> str:
        """Why an independent review must not be launched yet, if it must not."""
        if not resolution.blocks_independent_review:
            return ""
        primary = resolution.cause("PRIMARY")
        return (
            f"repository topology/authority is not valid ({resolution.status.value}): "
            f"{primary.summary if primary else 'see the protocol report'}. A review of an "
            "invalid topology reviews the wrong thing."
        )

    def verify_after_remediation(
        self, option: RemediationOption, baseline: str | None = None
    ) -> tuple[bool, list[str]]:
        """Re-read the repository and check the builder followed the approved plan."""
        from .remediation_planner import verify_executed_plan

        protocol = discover_protocol(self.repo)
        after = GitTopologyAnalyzer(self.repo, protocol).analyze(baseline=baseline)
        ok, deviations = verify_executed_plan(option, after)
        return ok, [f"{d.what}: expected {d.expected}, observed {d.observed}" for d in deviations]


# --------------------------------------------------------------------------
# The approval contract
# --------------------------------------------------------------------------


def build_approval_prompt(resolution: ProtocolResolution) -> str:
    """Everything a human needs before authorizing a consequential change."""
    option = resolution.recommended_option
    if option is None or resolution.status is not ProtocolStatus.REQUIRES_APPROVAL:
        return ""

    primary = resolution.cause("PRIMARY")
    lines = [
        "=== APPROVAL REQUIRED ===",
        "",
        "CURRENT GRAPH",
        resolution.current_graph,
        "",
        "EXPECTED GRAPH",
        option.expected_commit_graph,
        "",
        "ROOT CAUSE",
        primary.summary if primary else "(not classified)",
    ]
    chain = resolution.blocker_chain()
    if chain:
        lines += ["", "CAUSAL CYCLE", chain]

    lines += ["", "PROPOSED OPERATIONS"]
    lines += [f"  {i}. {op}" for i, op in enumerate(option.operations, 1)]

    lines += [
        "",
        "EVIDENCE PRESERVED",
        ("yes — " + ", ".join(option.archival_refs)) if option.archival_refs else
        ("yes" if option.evidence_preserved else "NO"),
    ]

    lines += ["", "RISKS"]
    lines += [f"  - {r}" for r in option.risks] or ["  (none recorded)"]

    lines += [
        "",
        "REMOTE IMPACT",
        (
            "YES — one or more affected commits exist on a remote; other clones have this history"
            if option.affects_remote_history
            else "none — the affected commits are local and unpushed"
        ),
    ]

    lines += ["", "COMMANDS TO BE EXECUTED"]
    lines += [f"  {op}" for op in option.operations]

    lines += ["", "GATES TO RERUN"]
    lines += [f"  - {r}" for r in option.rerun_requirements]

    lines += [
        "",
        f"PLAN HASH: {option.plan_hash}  (an approval applies to this plan only; any change to "
        "the repository expires it)",
        "",
        "TO APPROVE, reply with exactly:",
        f"    {option.approval_phrase}",
        "",
        "A general agreement is not an approval for a history rewrite.",
    ]
    return "\n".join(lines)


def approve_option(
    *,
    resolution: ProtocolResolution,
    option_id: str,
    confirmation: str,
    store: ApprovalStore,
    run_id: str = "",
) -> tuple[ApprovalRecord | None, list[str]]:
    """Record an approval, or explain precisely why it is not one."""
    from .remediation_planner import validate_confirmation

    option = next((o for o in resolution.options if o.option_id == option_id), None)
    if option is None:
        available = ", ".join(o.option_id for o in resolution.options) or "(none)"
        return None, [f"no option {option_id!r} in this resolution; available: {available}"]

    if option.disqualified:
        return None, [
            f"option {option_id} is disqualified and cannot be approved: "
            f"{option.disqualification_reason}"
        ]

    reasons = validate_confirmation(confirmation, option)
    if reasons:
        return None, reasons

    from .remediation_planner import working_state_fingerprint
    from .worktree_state import capture_worktree_state

    topology = resolution.topology
    # What is being approved is stated explicitly on the record: the baseline,
    # the exact ref transition, and the stable topology it was computed against.
    # The working-tree state is recorded as ADVISORY only — it is re-verified
    # before execution, but ordinary editing must not expire the approval.
    advisory: dict = {}
    if resolution.repo_path:
        try:
            advisory = working_state_fingerprint(
                capture_worktree_state(Path(resolution.repo_path))
            )
        except Exception:
            advisory = {}

    record = ApprovalRecord(
        option_id=option.option_id,
        plan_hash=option.plan_hash,
        confirmation=confirmation.strip(),
        approval_phrase=option.approval_phrase,
        run_id=run_id,
        remote_impact=option.affects_remote_history,
        approved_baseline=option.baseline_commit or (topology.baseline_commit if topology else ""),
        ref_transition=option.ref_transition(),
        topology_fingerprint=dict(option.topology_fingerprint),
        working_state=advisory,
    )
    store.add(record)
    return record, []
