"""Remediation planning and the approval contract.

The driver may *propose* a repair. It never performs one. Every option here is
generated, ranked and printed; the git operations are executed by a human, or by
a fresh builder session working from an approved plan — never by this process.

The distinctions this module enforces:

    proposing a rewrite        != performing a rewrite
    "go ahead"                 != approving a specific plan
    an approval                != an approval that survives the repo changing
    a graph that looks valid   != a graph that preserves the evidence
    deleting evidence          => never an option, however green it would look

An approval binds to one option *and* one plan hash. The hash covers the
repository state the plan was computed against, so any change to HEAD, the
baseline, the commit range or the rules themselves expires it.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .deadlock_detector import Deadlock, ObservedGates
from .git_topology import CommitRole, GitTopology
from .models import utcnow
from .protocol_sources import (
    DiscoveredProtocol,
    ProtocolViolation,
    RuleKind,
    ViolationType,
)

# Phrases that are agreement, not authorization. A destructive, irreversible or
# outward-facing operation needs the second kind.
VAGUE_CONFIRMATIONS = (
    "go ahead",
    "go ahead with whatever",
    "whatever",
    "whatever you think",
    "sure",
    "yes",
    "yep",
    "ok",
    "okay",
    "do it",
    "fine",
    "proceed",
    "sounds good",
    "up to you",
    "your call",
    "lgtm",
    "approve",
    "approved",
)

# Operations that must never appear in a plan the driver generates.
NEVER_PROPOSE = (
    "git stash",
    "git clean",
    "git checkout --",
    "git restore",
    "reset --hard",
    "push --force",
    "push -f",
    "branch -D",
    "tag -d",
    "filter-branch",
    "reflog expire",
    "gc --prune",
)


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    SEVERE = "SEVERE"


class ProtocolCompliance(BaseModel):
    model_config = ConfigDict(extra="ignore")

    satisfies: list[str] = Field(default_factory=list)
    violates: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def compliant(self) -> bool:
        return not self.violates


class RemediationOption(BaseModel):
    """One materially valid way out, stated precisely enough to approve."""

    model_config = ConfigDict(extra="ignore")

    option_id: str
    title: str
    description: str = ""
    preconditions: list[str] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)
    destructive_operations: list[str] = Field(default_factory=list)
    evidence_preserved: bool = True
    risks: list[str] = Field(default_factory=list)
    protocol_compliance: ProtocolCompliance = Field(default_factory=ProtocolCompliance)
    expected_commit_graph: str = ""
    rerun_requirements: list[str] = Field(default_factory=list)
    requires_human_approval: bool = True
    recommendation_rank: int = 0

    # Approval and execution metadata.
    rewrites_history: bool = False
    affects_remote_history: bool = False
    risk_level: RiskLevel = RiskLevel.MEDIUM
    disqualified: bool = False
    disqualification_reason: str = ""
    approval_phrase: str = ""
    verification_commands: list[str] = Field(default_factory=list)
    prohibited_operations: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    archival_refs: list[str] = Field(default_factory=list)
    expected_tree: str = ""
    plan_hash: str = ""

    def hashable(self) -> dict[str, Any]:
        """The parts of the option an approval is actually approving."""
        return {
            "option_id": self.option_id,
            "title": self.title,
            "operations": self.operations,
            "destructive_operations": self.destructive_operations,
            "archival_refs": self.archival_refs,
            "expected_tree": self.expected_tree,
            "expected_commit_graph": self.expected_commit_graph,
        }

    def render(self, verbose: bool = True) -> str:
        lines = [
            f"[{self.option_id}] {self.title}"
            + (f"   (rank {self.recommendation_rank})" if self.recommendation_rank else ""),
            f"    risk: {self.risk_level.value}   rewrites history: {self.rewrites_history}   "
            f"remote impact: {self.affects_remote_history}   evidence preserved: {self.evidence_preserved}",
        ]
        if self.disqualified:
            lines.append(f"    DISQUALIFIED: {self.disqualification_reason}")
        if not verbose:
            return "\n".join(lines)
        if self.description:
            lines.append(f"    {self.description}")
        if self.protocol_compliance.satisfies:
            lines.append(f"    satisfies: {', '.join(self.protocol_compliance.satisfies)}")
        if self.protocol_compliance.violates:
            lines.append(f"    VIOLATES:  {', '.join(self.protocol_compliance.violates)}")
        for risk in self.risks:
            lines.append(f"    risk: {risk}")
        return "\n".join(lines)


class ApprovalRecord(BaseModel):
    """One human approval, bound to one option and one plan hash."""

    model_config = ConfigDict(extra="ignore")

    option_id: str
    plan_hash: str
    confirmation: str
    approval_phrase: str
    approved_at: str = Field(default_factory=utcnow)
    run_id: str = ""
    remote_impact: bool = False
    notes: list[str] = Field(default_factory=list)


class ApprovalStore:
    """Persists approvals beside the run they belong to."""

    FILENAME = "protocol-approvals.json"

    def __init__(self, run_dir: Path) -> None:
        self.path = Path(run_dir) / self.FILENAME

    def load(self) -> list[ApprovalRecord]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out: list[ApprovalRecord] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                out.append(ApprovalRecord.model_validate(item))
            except Exception:
                continue
        return out

    def add(self, record: ApprovalRecord) -> ApprovalRecord:
        records = self.load()
        records.append(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([r.model_dump(mode="json") for r in records], indent=2), encoding="utf-8"
        )
        return record

    def active(self, option_id: str, plan_hash: str) -> ApprovalRecord | None:
        """An approval only counts for the exact plan it approved."""
        for record in reversed(self.load()):
            if record.option_id == option_id and record.plan_hash == plan_hash:
                return record
        return None

    def expired(self, plan_hash: str) -> list[ApprovalRecord]:
        return [r for r in self.load() if r.plan_hash != plan_hash]


# --------------------------------------------------------------------------
# Plan identity
# --------------------------------------------------------------------------


def repo_fingerprint(topology: GitTopology, protocol: DiscoveredProtocol) -> dict[str, Any]:
    """Everything a plan depends on. Any change to it expires an approval."""
    return {
        "head": topology.head_commit,
        "head_tree": topology.head_tree,
        "baseline": topology.baseline_commit,
        "branch": topology.branch,
        "commits": [[c.commit_sha, c.tree_sha, c.role.value] for c in topology.commits],
        "dirty": topology.dirty_file_count,
        "untracked": topology.untracked_count,
        "pushed": sorted(topology.pushed_commits),
        "rules": sorted(r.rule_id for r in protocol.rules),
    }


def compute_plan_hash(fingerprint: dict[str, Any], option: RemediationOption) -> str:
    payload = json.dumps(
        {"repo": fingerprint, "option": option.hashable()}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def validate_confirmation(text: str, option: RemediationOption) -> list[str]:
    """Reasons a confirmation is not an approval. Empty means it is one."""
    reasons: list[str] = []
    raw = (text or "").strip()
    if not raw:
        return ["no confirmation was given"]

    normalized = re.sub(r"\s+", " ", raw).strip().upper()
    required = re.sub(r"\s+", " ", option.approval_phrase).strip().upper()

    if required and required not in normalized:
        reasons.append(
            f"the confirmation does not contain the exact required phrase: {option.approval_phrase!r}"
        )
    if re.sub(r"[^A-Z ]", "", normalized).strip().lower() in VAGUE_CONFIRMATIONS:
        reasons.append(
            "a general agreement is not an approval for a history or authority change; "
            f"the exact phrase {option.approval_phrase!r} is required"
        )
    if option.affects_remote_history and "REMOTE" not in normalized:
        reasons.append(
            "this plan affects history that exists on a remote; the confirmation must "
            "acknowledge the remote impact explicitly"
        )
    return reasons


# --------------------------------------------------------------------------
# The planner
# --------------------------------------------------------------------------


class RemediationPlanner:
    """Generates every materially valid option, then ranks them."""

    def __init__(self, protocol: DiscoveredProtocol, unit_id: str = "") -> None:
        self.protocol = protocol
        self.unit_id = unit_id or "unit"

    # -- naming ------------------------------------------------------------

    def _slug(self) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "-", self.unit_id).strip("-").lower() or "unit"

    def _phrase(self, action: str, shared: bool) -> str:
        scope = "SHARED HISTORY" if shared else "LOCAL HISTORY"
        unit = re.sub(r"[^A-Za-z0-9]+", " ", self.unit_id).strip().upper() or "UNIT"
        suffix = " INCLUDING REMOTE IMPACT" if shared else ""
        return f"APPROVE {unit} {scope} {action}{suffix}".strip()

    # -- shared pieces -----------------------------------------------------

    def _rerun_requirements(self) -> list[str]:
        """What the repository itself says must run again after a repair."""
        out: list[str] = []
        if self.protocol.effective(RuleKind.STATUS_REALITY_GUARD) is not None:
            out.append("the status-reality guard")
        if self.protocol.effective(RuleKind.CANONICAL_SUITE_GATE) is not None:
            out.append("the canonical suite")
        if self.protocol.effective(RuleKind.FINALIZER_OWNERSHIP) is not None:
            out.append("the finalizer (which alone may write derived status)")
        if self.protocol.effective(RuleKind.CLEAN_CLONE_GATE) is not None:
            out.append("the clean-clone gate")
        if self.protocol.effective(RuleKind.INDEPENDENT_REVIEW) is not None:
            out.append("a fresh independent review (a session other than the implementing one)")
        out.append("this protocol resolver, to confirm the topology is now valid")
        return out

    def _archival_names(self, topology: GitTopology) -> list[tuple[str, str, str]]:
        """(ref name, target sha, why) for every state worth preserving."""
        slug = self._slug()
        refs: list[tuple[str, str, str]] = []
        for commit in topology.content_commits:
            label = "content" if commit.role is CommitRole.CONTENT else "remediation"
            refs.append(
                (
                    f"archive/{slug}/{label}-{commit.short[:8]}",
                    commit.commit_sha,
                    f"{commit.role.value} commit {commit.short}",
                )
            )
        if topology.head_commit and topology.head_commit not in {c.commit_sha for c in topology.content_commits}:
            refs.append(
                (
                    f"archive/{slug}/pre-normalization-head-{topology.head_commit[:8]}",
                    topology.head_commit,
                    "HEAD before any repair",
                )
            )
        return refs

    def _target_tree(self, topology: GitTopology) -> tuple[str, str]:
        """The tree the consolidated commit must reproduce, byte for byte."""
        content = topology.content_commits
        if content:
            return content[-1].tree_sha, f"the tree of the last content commit {content[-1].short}"
        return topology.head_tree, "the current HEAD tree"

    # -- options -----------------------------------------------------------

    def _option_consolidate(self, topology: GitTopology) -> RemediationOption:
        """Rebuild one content commit from the baseline, proving tree equality first."""
        shared = topology.is_shared
        baseline = topology.baseline_commit
        target_tree, tree_reason = self._target_tree(topology)
        archives = self._archival_names(topology)
        # A detached HEAD has no branch ref to move; update HEAD itself instead
        # of inventing a branch name that does not exist.
        branch = topology.branch or "(detached HEAD)"
        ref = f"refs/heads/{topology.branch}" if topology.branch else "HEAD"

        operations = [f"git branch {name} {sha}" for name, sha, _ in archives]
        operations += [
            f"NEW=$(git commit-tree {target_tree} -p {baseline} "
            f"-m '{self.unit_id}: consolidated content commit')",
            "# nothing has moved yet: commit-tree writes an object, not a ref",
            f"git diff --stat {target_tree} $NEW^{{tree}}    # must print nothing",
            f"test \"$(git rev-parse $NEW^{{tree}})\" = \"{target_tree}\"",
            f"git update-ref {ref} $NEW {topology.head_commit}",
            "git status --porcelain     # the working tree must be unchanged",
        ]

        risks = [
            "the branch ref moves: any reference to the previous commits by sha resolves only "
            "through the archival refs created in step 1",
        ]
        if topology.status_commits:
            risks.append(
                "status commits made after the last content commit are dropped by design; "
                "their content is finalizer-owned and must be regenerated, never re-committed by hand"
            )
        if topology.dirty_file_count or topology.untracked_count:
            risks.append(
                f"{topology.dirty_file_count} modified and {topology.untracked_count} untracked "
                "file(s) exist; they are untouched by these operations but will be compared "
                "against a different HEAD afterwards"
            )
        if shared:
            risks.append(
                "one or more of these commits exists on a remote: replacing them changes history "
                "other clones have already fetched"
            )

        topo_rule = self.protocol.effective(RuleKind.COMMIT_TOPOLOGY)
        history_rule = self.protocol.effective(RuleKind.HISTORY_REWRITE_APPROVAL)

        return RemediationOption(
            option_id="A",
            title=(
                "Normalize history into one content commit, preserving both existing states "
                "as archival refs"
            ),
            description=(
                f"Create archival refs for every reviewed state, rebuild a single content commit "
                f"on the authorized baseline {baseline[:12]} whose tree is {tree_reason} "
                f"({target_tree[:12]}), prove the tree is byte-identical before any ref moves, "
                f"then move {branch} with a compare-and-swap. Status stays PENDING until the "
                "finalizer and an independent review succeed."
            ),
            preconditions=[
                "every archival ref above is created and verified first",
                (
                    "all commits in the range are local and unpushed"
                    if not shared
                    else "the remote impact of replacing pushed commits is explicitly accepted"
                ),
                "no other worktree, branch or tag depends on the commits being replaced",
                f"the authorized baseline is {baseline[:12]} ({topology.baseline_source})",
            ],
            operations=operations,
            destructive_operations=[
                f"git update-ref refs/heads/{branch} (replaces the branch tip; history rewrite)"
            ],
            evidence_preserved=True,
            risks=risks,
            protocol_compliance=ProtocolCompliance(
                satisfies=[topo_rule.rule_id] if topo_rule else [],
                violates=[],
                notes=[
                    "no evidence is deleted: both prior states remain reachable through their "
                    "archival refs",
                    "derived status is not written by this plan; the finalizer still owns it",
                ]
                + ([f"history rewriting requires approval per {history_rule.rule_id}"] if history_rule else []),
            ),
            expected_commit_graph="\n".join(
                [
                    f"{baseline[:12]}  BASELINE",
                    f"  → <new consolidated content commit>  tree == {target_tree[:12]}",
                    "  → <finalizer-owned metadata/status commit>  (PENDING; written by the finalizer)",
                ]
            ),
            rerun_requirements=self._rerun_requirements(),
            requires_human_approval=True,
            rewrites_history=True,
            affects_remote_history=shared,
            risk_level=RiskLevel.SEVERE if shared else RiskLevel.MEDIUM,
            approval_phrase=self._phrase("NORMALIZATION", shared),
            verification_commands=[
                f"git rev-parse HEAD^{{tree}}      # must equal {target_tree}",
                f"git rev-list --count {baseline}..HEAD    # must print 1",
                f"git log --oneline {baseline}..HEAD",
                f"git rev-parse {archives[0][0] if archives else 'archive/...'}   # archival ref must resolve",
                "git status --porcelain",
            ],
            prohibited_operations=list(NEVER_PROPOSE),
            stop_conditions=[
                f"the reconstructed tree is not exactly {target_tree}",
                "any archival ref fails to create or fails to resolve afterwards",
                "the working tree gains or loses a file during the operation",
                "any operation not listed in this plan becomes necessary",
            ],
            archival_refs=[name for name, _, _ in archives],
            expected_tree=target_tree,
        )

    def _option_amend(self, topology: GitTopology) -> RemediationOption:
        """Fold the later content into the reviewed commit, in place."""
        content = topology.content_commits
        first = content[0] if content else None
        shared = topology.is_shared
        archives = self._archival_names(topology)
        target_tree, _ = self._target_tree(topology)
        topo_rule = self.protocol.effective(RuleKind.COMMIT_TOPOLOGY)

        origin = first.short if first else "the reviewed commit"
        operations = [f"git branch {name} {sha}" for name, sha, _ in archives]
        operations += [
            f"git reset --soft {topology.baseline_commit}   # index keeps the current tree",
            f"git commit -m '{self.unit_id}: content (amended from {origin})'",
            f"git rev-parse HEAD^{{tree}}    # must equal {target_tree}",
        ]

        return RemediationOption(
            option_id="B",
            title="Amend the reviewed content commit in place, preserving archival refs",
            description=(
                "Move the branch back to the authorized baseline with a soft reset so the index "
                "retains the current tree, then re-commit once. Offered only because nothing was "
                "committed after the last content commit, so the index already holds the target "
                "tree. Reaches the same end state as option A, but the refs move before the tree "
                "is proven, so a mistake is caught later rather than earlier."
            ),
            preconditions=[
                "every archival ref above is created first",
                (
                    "uncommitted working-tree changes stay uncommitted: the soft reset keeps the "
                    "index, and it is the index that is re-committed"
                ),
                "the amended commit has not been reviewed by anyone relying on its sha",
            ],
            operations=operations,
            destructive_operations=[
                "git reset --soft (moves the branch tip)",
                "amend of a previously reviewed commit",
            ],
            evidence_preserved=True,
            risks=[
                "the branch moves before the resulting tree is verified",
                "a soft reset also un-commits any status commit in the range, which is easy to "
                "re-commit by hand — and hand-writing derived status is forbidden",
            ]
            + (
                ["one or more of these commits exists on a remote"] if shared else []
            ),
            protocol_compliance=ProtocolCompliance(
                satisfies=[topo_rule.rule_id] if topo_rule else [],
                violates=[],
                notes=["evidence is preserved only because the archival refs are created first"],
            ),
            expected_commit_graph="\n".join(
                [
                    f"{topology.baseline_commit[:12]}  BASELINE",
                    f"  → <amended content commit>  tree == {target_tree[:12]}",
                    "  → <finalizer-owned metadata/status commit>  (PENDING)",
                ]
            ),
            rerun_requirements=self._rerun_requirements(),
            requires_human_approval=True,
            rewrites_history=True,
            affects_remote_history=shared,
            risk_level=RiskLevel.SEVERE if shared else RiskLevel.HIGH,
            approval_phrase=self._phrase("AMEND", shared),
            verification_commands=[
                f"git rev-parse HEAD^{{tree}}   # must equal {target_tree}",
                f"git rev-list --count {topology.baseline_commit}..HEAD   # must print 1",
            ],
            prohibited_operations=list(NEVER_PROPOSE),
            stop_conditions=[
                f"the resulting tree is not exactly {target_tree}",
                "the amend produces a different file set than the archival refs record",
            ],
            archival_refs=[name for name, _, _ in archives],
            expected_tree=target_tree,
        )

    def _option_protocol_exception(self, topology: GitTopology) -> RemediationOption:
        """Change the rule instead of the history — honestly labelled."""
        topo_rule = self.protocol.effective(RuleKind.COMMIT_TOPOLOGY)
        guard_rule = self.protocol.effective(RuleKind.STATUS_REALITY_GUARD)
        source = topo_rule.source_path if topo_rule else "the commit/finalization protocol"

        return RemediationOption(
            option_id="C",
            title="Add a repository-authorized exception to the commit-topology rule",
            description=(
                f"Amend {source} so the observed topology is permitted, then update the guard "
                "that enforces it, under the repository's own change process. No history is "
                "rewritten and no evidence moves — but a guard changed to accept the state it "
                "was written to reject is a defect with a passing status, so this is only "
                "appropriate when the rule itself is wrong."
            ),
            preconditions=[
                "the founder agrees the current rule, not the current history, is what is wrong",
                "the change is made to the protocol document first, and to the guard second",
                "the change is reviewed independently, like any other content change",
            ],
            operations=[
                f"edit {source} to state the topology that is actually intended",
                f"update the guard at {guard_rule.source_path if guard_rule else '(the status-reality guard)'} "
                "to enforce the amended rule",
                "run the canonical suite, then the finalizer, then the clean-clone gate",
            ],
            destructive_operations=[],
            evidence_preserved=True,
            risks=[
                "weakening a guard to obtain a green result hides the condition the guard existed "
                "to catch",
                "the amended rule applies to every future unit, not only this one",
            ],
            protocol_compliance=ProtocolCompliance(
                satisfies=[],
                violates=[],
                notes=[
                    "this option changes the rule rather than the state; it is legitimate only "
                    "when the repository's authority agrees the rule is wrong"
                ],
            ),
            expected_commit_graph=(
                "unchanged history; the rule is amended so the observed graph becomes permitted"
            ),
            rerun_requirements=self._rerun_requirements(),
            requires_human_approval=True,
            rewrites_history=False,
            affects_remote_history=False,
            risk_level=RiskLevel.HIGH,
            approval_phrase=f"APPROVE {self.unit_id.upper()} PROTOCOL AMENDMENT",
            verification_commands=[
                "git diff --stat    # the protocol and guard changes only",
            ],
            prohibited_operations=list(NEVER_PROPOSE),
            stop_conditions=[
                "the guard change would make the guard accept states beyond the one intended",
            ],
            expected_tree="",
        )

    def _option_manual_finalization(self, topology: GitTopology) -> RemediationOption:
        """Only ever offered when the repository authorizes it in writing."""
        allowed = self.protocol.effective(RuleKind.MANUAL_FINALIZATION_ALLOWED)
        ban = self.protocol.effective(RuleKind.MANUAL_STATUS_PROHIBITED)
        owner = self.protocol.effective(RuleKind.FINALIZER_OWNERSHIP)
        authorized = bool(allowed and allowed.parameters.get("explicitly_authorized"))

        blocking = [r.rule_id for r in (ban, owner) if r is not None]
        return RemediationOption(
            option_id="D",
            title=(
                "Perform the manual finalization the repository authorizes"
                if authorized
                else "Write the derived status by hand — NOT authorized by this repository"
            ),
            description=(
                f"Write the derived status by hand under the authorization at {allowed.cite()}."
                if authorized
                else "Write the derived status by hand. The repository does not authorize this."
            ),
            preconditions=(
                [f"the authorization at {allowed.cite()} applies to this exact situation"]
                if authorized
                else ["a repository rule authorizing manual finalization — none exists"]
            ),
            operations=(
                ["apply the authorized manual finalization procedure exactly as the repository states it"]
                if authorized
                else []
            ),
            destructive_operations=[],
            evidence_preserved=True,
            risks=[
                "hand-written derived status cannot be distinguished later from generated status",
                "a status surface that no gate produced cannot prove itself",
            ],
            protocol_compliance=ProtocolCompliance(
                satisfies=[allowed.rule_id] if authorized and allowed else [],
                violates=[] if authorized else blocking,
                notes=[]
                if authorized
                else ["the repository prohibits hand-edited derived status; this option cannot be taken"],
            ),
            expected_commit_graph="unchanged history; derived status written outside the finalizer",
            rerun_requirements=self._rerun_requirements(),
            requires_human_approval=True,
            rewrites_history=False,
            affects_remote_history=False,
            risk_level=RiskLevel.HIGH if authorized else RiskLevel.SEVERE,
            disqualified=not authorized,
            disqualification_reason=(
                ""
                if authorized
                else "the repository does not authorize manual finalization; "
                + (f"see {ban.cite()}" if ban else "derived status is finalizer-owned")
            ),
            approval_phrase=f"APPROVE {self.unit_id.upper()} MANUAL FINALIZATION",
            prohibited_operations=list(NEVER_PROPOSE),
            stop_conditions=["any doubt that the authorization covers this exact situation"],
            expected_tree="",
        )

    def _option_fix_protocol_conflict(self) -> RemediationOption:
        conflicts = self.protocol.unresolved_conflicts
        return RemediationOption(
            option_id="E",
            title="Resolve the contradictory protocol definitions before anything else",
            description=(
                "The repository states rules that cannot both hold. Until the founder decides "
                "which one governs, no option below it can be evaluated: any repair would be "
                "compliant with one rule and non-compliant with another."
            ),
            preconditions=["the founder decides which statement governs"],
            operations=[
                f"reconcile: {c.description}" for c in conflicts
            ]
            or ["reconcile the conflicting protocol statements"],
            destructive_operations=[],
            evidence_preserved=True,
            risks=["choosing the wrong governing rule silently legitimizes the wrong history"],
            protocol_compliance=ProtocolCompliance(
                satisfies=[],
                violates=[],
                notes=[c.render() for c in conflicts],
            ),
            expected_commit_graph="unchanged history",
            rerun_requirements=["this protocol resolver, once the conflict is resolved"],
            requires_human_approval=True,
            rewrites_history=False,
            risk_level=RiskLevel.MEDIUM,
            approval_phrase=f"APPROVE {self.unit_id.upper()} PROTOCOL CONFLICT RESOLUTION",
            prohibited_operations=list(NEVER_PROPOSE),
            stop_conditions=["the conflict is still unresolved"],
            expected_tree="",
        )

    # -- generation --------------------------------------------------------

    def plan(
        self,
        topology: GitTopology,
        violations: list[ProtocolViolation],
        deadlocks: list[Deadlock],
        gates: ObservedGates | None = None,
    ) -> list[RemediationOption]:
        """Every materially valid option for the observed state, ranked."""
        options: list[RemediationOption] = []

        if self.protocol.unresolved_conflicts:
            options.append(self._option_fix_protocol_conflict())

        history_trouble = bool(
            deadlocks or [v for v in violations if v.violation_type is ViolationType.HISTORY]
        )
        if history_trouble and topology.baseline_commit and topology.commits:
            # Consolidation rebuilds a *content* commit from a content tree.
            # With no content commit in the range there is nothing to
            # consolidate, and packaging a stray status edit as content would be
            # a worse state than the one being repaired.
            if topology.content_commits:
                options.append(self._option_consolidate(topology))
                # A soft reset re-commits the index, which holds the *current*
                # HEAD tree. That equals the target tree only when nothing was
                # committed after the last content commit; otherwise the amend
                # would silently absorb whatever came after — including derived
                # status no one is allowed to write by hand.
                if topology.commits[-1] is topology.content_commits[-1]:
                    options.append(self._option_amend(topology))
            options.append(self._option_protocol_exception(topology))
            options.append(self._option_manual_finalization(topology))

        fingerprint = repo_fingerprint(topology, self.protocol)
        for option in options:
            option.plan_hash = compute_plan_hash(fingerprint, option)
            _assert_never_proposes_destruction(option)

        return rank_options(options)


def rank_options(options: list[RemediationOption]) -> list[RemediationOption]:
    """Safest first. A disqualified option is always last, whatever it scores."""

    def score(option: RemediationOption) -> tuple[int, int]:
        value = 0
        value += 3 if option.protocol_compliance.compliant else -6
        value += 2 if option.evidence_preserved else -4
        value += 2 if not option.affects_remote_history else -3
        value += 1 if not option.destructive_operations else 0
        value += {RiskLevel.LOW: 2, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: -1, RiskLevel.SEVERE: -3}[
            option.risk_level
        ]
        if option.disqualified:
            value -= 20
        # Ties resolve toward the option that leaves the repository's own rules
        # unchanged only when it is not also a history rewrite.
        return (value, 0 if option.rewrites_history else 1)

    ordered = sorted(options, key=lambda o: (-score(o)[0], -score(o)[1], o.option_id))
    for i, option in enumerate(ordered, start=1):
        option.recommendation_rank = i
    return ordered


def recommended(options: list[RemediationOption]) -> RemediationOption | None:
    for option in options:
        if not option.disqualified:
            return option
    return None


def _assert_never_proposes_destruction(option: RemediationOption) -> None:
    """A plan that would destroy evidence is a bug, not an option."""
    blob = " ".join(option.operations).lower()
    for forbidden in NEVER_PROPOSE:
        if forbidden.lower() in blob:
            raise AssertionError(
                f"remediation option {option.option_id} proposes {forbidden!r}, which would "
                "destroy evidence or shared history"
            )


# --------------------------------------------------------------------------
# Handing an approved plan to a fresh builder
# --------------------------------------------------------------------------


def remediation_builder_prompt(
    *,
    option: RemediationOption,
    topology: GitTopology,
    approval: ApprovalRecord,
    unit_id: str = "",
) -> str:
    """The exact prompt a fresh builder session may execute after approval.

    It contains the plan hash it is bound to, the state it was computed against,
    and the conditions under which it must stop rather than improvise.
    """
    lines = [
        "=== APPROVED REPOSITORY-HISTORY REMEDIATION ===",
        "",
        f"approved plan: {option.option_id} — {option.title}",
        f"plan hash:     {option.plan_hash}   (approved {approval.approved_at})",
        f"confirmation:  {approval.approval_phrase!r}",
        f"unit:          {unit_id or '(not resolved)'}",
        "",
        "You are executing an approved plan. You are not designing one. If the repository",
        "state does not match what is recorded below, STOP and report — the approval was",
        "given for a different state and no longer applies.",
        "",
        "--- STATE THIS PLAN WAS COMPUTED AGAINST ---",
        f"branch:            {topology.branch}",
        f"HEAD:              {topology.head_commit}",
        f"HEAD tree:         {topology.head_tree}",
        f"authorized baseline: {topology.baseline_commit}  ({topology.baseline_source})",
        f"commits in range:  {len(topology.commits)}",
    ]
    for commit in topology.commits:
        lines.append(f"    {commit.describe()}")

    lines += ["", "--- CURRENT GRAPH ---", topology.render_graph(), "", "--- EXPECTED GRAPH AFTER THE PLAN ---", option.expected_commit_graph]

    if option.archival_refs:
        lines += ["", "--- ARCHIVAL REFS TO CREATE FIRST (every one, before anything moves) ---"]
        lines += [f"    {ref}" for ref in option.archival_refs]

    lines += ["", "--- OPERATIONS, IN THIS ORDER ---"]
    lines += [f"    {i}. {op}" for i, op in enumerate(option.operations, start=1)]

    lines += ["", "--- PROHIBITED, WITHOUT EXCEPTION ---"]
    lines += [f"    - {op}" for op in option.prohibited_operations]
    lines += [
        "    - any operation not listed above",
        "    - writing, editing or 'fixing' any derived status file by hand",
        "    - deleting, weakening or skipping any guard, test or receipt to obtain a green result",
        "    - pushing anything",
    ]

    lines += ["", "--- VERIFICATION (run all of these, report the output verbatim) ---"]
    lines += [f"    {cmd}" for cmd in option.verification_commands]
    if option.expected_tree:
        lines.append(f"    the resulting tree MUST be exactly {option.expected_tree}")

    lines += ["", "--- STOP CONDITIONS (stop and report; do not improvise) ---"]
    lines += [f"    - {cond}" for cond in option.stop_conditions]

    lines += ["", "--- AFTER THE PLAN SUCCEEDS ---"]
    lines += [f"    - {req}" for req in option.rerun_requirements]
    lines += [
        "",
        "Leave every acceptance criterion PENDING. This plan repairs commit topology; it",
        "proves nothing about the product, and it awards no criterion.",
        "",
        "Report: the exact commands you ran, their output, the resulting graph, and the",
        "resulting tree sha.",
    ]
    return "\n".join(lines)


class PlanDeviation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    what: str
    expected: str
    observed: str


def verify_executed_plan(
    option: RemediationOption, after: GitTopology
) -> tuple[bool, list[PlanDeviation]]:
    """Inspect the resulting graph and tree. Any deviation stops the run."""
    deviations: list[PlanDeviation] = []

    if option.expected_tree and after.head_tree and not after.head_tree.startswith(
        option.expected_tree[:12]
    ):
        deviations.append(
            PlanDeviation(
                what="the resulting tree does not match the approved plan",
                expected=option.expected_tree,
                observed=after.head_tree,
            )
        )

    if option.rewrites_history:
        content = after.content_commits
        if len(content) != 1:
            deviations.append(
                PlanDeviation(
                    what="the resulting graph does not contain exactly one content commit",
                    expected="1 content commit since the authorized baseline",
                    observed=f"{len(content)} content commit(s)",
                )
            )

    existing = set(after.archival_refs) | set(after.tags)
    for ref in option.archival_refs:
        short = ref.split("/")[-1]
        if not any(short in candidate or ref in candidate for candidate in existing):
            deviations.append(
                PlanDeviation(
                    what="an archival ref required by the plan does not exist",
                    expected=ref,
                    observed="not found among local refs or tags",
                )
            )

    return (not deviations), deviations
