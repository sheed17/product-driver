"""When a scoped task requires independent review, and what that review binds to.

Three separate questions live here, and keeping them separate is the whole
design:

    1. *Does this task require an independent review?*  Asked of the repository,
       about the **scoped task** — never inferred from the phase around it.
    2. *Which implementation was reviewed?*  A review is evidence about one exact
       repository state. Change the state and the review is evidence about
       something that no longer exists.
    3. *Where does this verdict go?*  A defect goes back to the builder. A
       verification gap does not. A decision goes to the founder. Routing all
       three to "send it to the builder" is how a review loop stops converging.

WHY (1) IS SCOPED
-----------------

A phase's acceptance contract lists an ``independent_review`` criterion because
the *phase* will need one before the phase is accepted. A run asked to build one
unit inside that phase is not the phase. Reading the phase's criterion as this
task's requirement makes every unit in a thirteen-unit phase demand a phase
review it cannot possibly satisfy — and reading it the other way, as no
requirement at all, ships an unreviewed effect boundary. So the requirement is
resolved from what the repository states about *this unit*: an active
``INDEPENDENT_REVIEW`` rule in its protocol binds every unit under it; a phase
acceptance criterion binds only a run that actually claims the phase.

WHY (2) IS A FINGERPRINT
------------------------

"The reviewer supported this" is only true of the tree the reviewer read. The
builder then corrects something, and the same sentence is now false — but it is
still sitting in the run's evidence, and nothing in it says which tree it was
about. So every review is bound to a
:class:`TreeFingerprint`: HEAD, the HEAD tree, and a digest of the *working
tree* on top of it, because the implementation under review is very often
uncommitted. A review satisfies the requirement only for its own fingerprint,
and :meth:`ReviewLedger.invalidate_stale` records the moment a later change
retired it. There is no path anywhere that lets a review of one tree stand in
for a review of another.

WHY (3) IS A CLASSIFICATION
---------------------------

``NOT_SUPPORTED`` and ``INSUFFICIENT_EVIDENCE`` are not two shades of the same
answer. The first says the product is wrong; the second says the review could
not tell. Sending the second back to the builder as a correction asks it to
change working code to fix a measurement problem, the measurement problem
survives, and the loop runs until its budget is gone. :func:`route_review`
separates them, and separates both from the two answers that are nobody's defect
at all: the harness cannot verify this, or a human has to decide it.

Nothing in this module writes to the target repository, and nothing here can
mark a criterion, a status file or a phase.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

# --------------------------------------------------------------------------
# 1. The requirement
# --------------------------------------------------------------------------


class ReviewTrigger(str, Enum):
    """Why a review is owed. Recorded so the founder summary can say which."""

    #: The repository's own protocol states an independent-review rule.
    REPOSITORY_AUTHORITY = "REPOSITORY_AUTHORITY"
    #: The phase's acceptance contract lists a criterion only an independent
    #: session may award — and this run actually claims the phase.
    PHASE_ACCEPTANCE_CRITERION = "PHASE_ACCEPTANCE_CRITERION"
    #: What the builder changed touches a high-consequence product surface.
    CHANGE_RISK = "CHANGE_RISK"
    #: The completion auditor found the claim needs a session other than the
    #: implementing one.
    COMPLETION_AUDIT = "COMPLETION_AUDIT"


#: Criterion names that structurally cannot be awarded by the session that built
#: the thing. Mirrors ``completion_auditor.INDEPENDENT_CRITERIA``; kept as a
#: separate constant so this module does not import the auditor.
_INDEPENDENT_CRITERIA = ("independent_review", "final_adjudication")


@dataclass
class ReviewRequirement:
    """Whether the scoped task owes an independent review, and on whose say-so."""

    required: bool = False
    #: The unit the review is *about* — the scoped task, not the phase.
    scope_id: str = ""
    parent_phase_id: str = ""
    triggers: list[ReviewTrigger] = field(default_factory=list)
    #: One human-readable sentence per trigger, each naming its evidence.
    reasons: list[str] = field(default_factory=list)
    #: Repository paths the requirement was read from.
    sources: list[str] = field(default_factory=list)
    #: True when the repository says the review must come from a session other
    #: than the implementing one. Defaults true: that is what "independent"
    #: means, and a repository that states the rule without the adjective has
    #: not thereby permitted a self-review.
    fresh_session_required: bool = True

    def add(self, trigger: ReviewTrigger, reason: str, source: str = "") -> None:
        self.required = True
        if trigger not in self.triggers:
            self.triggers.append(trigger)
        if reason and reason not in self.reasons:
            self.reasons.append(reason)
        if source and source not in self.sources:
            self.sources.append(source)

    @property
    def from_repository_authority(self) -> bool:
        return ReviewTrigger.REPOSITORY_AUTHORITY in self.triggers

    def brief(self) -> str:
        if not self.required:
            return "no independent review is required for this task"
        who = ", ".join(t.value for t in self.triggers)
        return f"{self.scope_id or 'this task'} requires an independent review ({who})"

    def summary_block(self) -> str:
        lines = [f"INDEPENDENT REVIEW REQUIRED: {'yes' if self.required else 'no'}"]
        if self.scope_id:
            lines.append(f"  scope: {self.scope_id}")
        lines += [f"  - {reason}" for reason in self.reasons]
        if self.sources:
            lines.append(f"  sources: {', '.join(self.sources[:6])}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "scope_id": self.scope_id,
            "parent_phase_id": self.parent_phase_id,
            "triggers": [t.value for t in self.triggers],
            "reasons": list(self.reasons),
            "sources": list(self.sources),
            "fresh_session_required": self.fresh_session_required,
        }


def resolve_review_requirement(
    repo: Path,
    scope: Any,
    *,
    unit: Any = None,
    risk: Any = None,
    audit_requires_review: bool = False,
    protocol: Any = None,
) -> ReviewRequirement:
    """What review this run owes, read fresh from the repository. Never raises.

    ``scope`` is the :class:`~neyma_product_driver.task_scope.TaskScope` the run
    resolved from the product owner's task. ``unit`` is the repository's active
    unit, supplying the phase's acceptance criteria. ``risk`` is this run's own
    change classification.

    The four triggers are independent and additive: any one of them makes a
    review owed, and each is recorded, so a run can say *why* it reviewed.
    """
    requirement = ReviewRequirement(
        scope_id=str(getattr(scope, "scope_id", "") or ""),
        parent_phase_id=str(getattr(scope, "parent_phase_id", "") or ""),
    )

    # -- the repository's own protocol, applied TO THE SCOPED UNIT.
    #
    #    The scoping is the whole point of this branch and it is easy to get
    #    wrong in both directions. A repository sentence like "independent review
    #    must be performed by a fresh session" states how review happens; on its
    #    own it does not say that any particular piece of work owes one, and
    #    reading it as though it did makes every task in that repository — a
    #    typo fix included — demand a reviewer. So the rule binds where the
    #    task-scope machinery says a unit is being built: a nested unit inside a
    #    phase is exactly the thing a phase's review rule is about, and it is the
    #    same test `CompletionAuditor._task_review_outstanding` applies when it
    #    decides the unit still owes one.
    #
    #    A phase-scope run is not left uncovered: it is caught by the phase's own
    #    acceptance criterion below, which is where a phase's review requirement
    #    is actually written down.
    if bool(getattr(scope, "is_nested", False)):
        for rule in _independent_review_rules(repo, protocol):
            requirement.add(
                ReviewTrigger.REPOSITORY_AUTHORITY,
                (
                    f"the repository states an independent-review rule "
                    f"({_quote(rule)}), and this run is building the unit "
                    f"{requirement.scope_id or 'it applies to'}"
                ),
                source=str(getattr(rule, "source_path", "") or ""),
            )
            if not bool(
                (getattr(rule, "parameters", None) or {}).get("requires_fresh_session", True)
            ):
                # Recorded, never obeyed downward: a rule that omits the
                # adjective has not authorized a self-review, it just did not
                # spell it out.
                requirement.reasons.append(
                    f"{_quote(rule)} does not spell out 'a session that did not write it'; "
                    "the review is still taken from a fresh session"
                )

    # -- the phase's acceptance contract. ONLY when the task ACTUALLY asked for
    #    the phase to be completed or accepted.
    #
    #    Two narrowings, both load-bearing. A nested unit inherits none of this:
    #    the criterion describes what the phase owes at phase acceptance, and a
    #    unit inside it is not that. And a run that merely failed to name a unit
    #    inherits none of it either — `claims_phase_completion` is the strict
    #    default for *evidence*, not a statement that this run is at phase
    #    acceptance, and reading it as one demands a review of twelve units that
    #    have not been written. A run that has actually reached phase acceptance
    #    is caught here; one that only claims it in its report is caught by the
    #    completion auditor below.
    if bool(getattr(scope, "phase_completion_requested", False)):
        for name in _pending_independent_criteria(unit):
            requirement.add(
                ReviewTrigger.PHASE_ACCEPTANCE_CRITERION,
                (
                    f"this run claims {requirement.parent_phase_id or 'the phase'} itself, and "
                    f"its acceptance criterion {name!r} may only be awarded by a session "
                    "other than the implementing one"
                ),
                source="docs/implementation/IMPLEMENTATION-REGISTRY.yaml",
            )

    # -- what the builder actually changed.
    if risk is not None and bool(getattr(risk, "requires_independent_review", False)):
        surfaces = ", ".join(list(getattr(risk, "surfaces", []) or [])) or "a high-consequence surface"
        requirement.add(
            ReviewTrigger.CHANGE_RISK,
            f"the change touches {surfaces}, which always earns one focused review",
        )

    # -- the completion auditor, which reads claims rather than diffs.
    if audit_requires_review:
        requirement.add(
            ReviewTrigger.COMPLETION_AUDIT,
            "the completion audit found a claim that only a session other than the "
            "implementing one can support",
        )

    return requirement


def _independent_review_rules(repo: Path, protocol: Any = None) -> list[Any]:
    """Active INDEPENDENT_REVIEW rules the repository states right now.

    ``INDEPENDENT_REVIEW`` is not in ``RETIRABLE_KINDS`` — it protects something
    rather than produces an artifact — so no sentence anywhere can retire it, and
    a historical document that mentions it is still excluded by ``is_active``.
    """
    try:
        from .protocol_sources import RuleKind, discover_protocol

        discovered = protocol if protocol is not None else discover_protocol(Path(repo))
        return list(discovered.of_kind(RuleKind.INDEPENDENT_REVIEW))
    except Exception:  # discovery must never break a run
        return []


def _pending_independent_criteria(unit: Any) -> list[str]:
    """Acceptance criteria that are not passing and need an independent session."""
    names: list[str] = []
    for raw in getattr(unit, "acceptance_criteria", None) or []:
        if not isinstance(raw, dict):
            continue
        criterion = str(raw.get("criterion", "") or "")
        result = str(raw.get("result", "PENDING") or "PENDING").upper()
        if result in {"PASS", "PASSED", "COMPLETE", "DONE"}:
            continue
        if any(key in criterion.lower() for key in _INDEPENDENT_CRITERIA):
            names.append(criterion)
    return names


def _quote(rule: Any) -> str:
    text = str(getattr(rule, "quote", "") or getattr(rule, "description", "") or "")
    collapsed = " ".join(text.split())
    where = str(getattr(rule, "source_path", "") or "")
    body = collapsed[:160] + ("…" if len(collapsed) > 160 else "")
    return f"{where}: {body}" if where else body


# --------------------------------------------------------------------------
# 2. What a review is evidence about
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TreeFingerprint:
    """The exact repository state a review was performed against.

    Three parts, because two of them are routinely wrong on their own:

    * ``head`` — the commit. Unchanged across an entire run that never commits.
    * ``tree`` — HEAD's tree. Same problem.
    * ``dirty_digest`` — a digest over the working tree's own diff and its
      untracked file list. This is the part that moves when a builder corrects
      something without committing, which is the normal case.
    """

    head: str = ""
    tree: str = ""
    dirty_digest: str = ""
    tracked_dirty: int = 0
    untracked: int = 0

    @property
    def identity(self) -> str:
        return f"{self.head[:12] or '-'}/{self.tree[:12] or '-'}/{self.dirty_digest[:12] or '-'}"

    def matches(self, other: "TreeFingerprint | None") -> bool:
        """Same commit, same tree and the same working-tree contents on top."""
        if other is None:
            return False
        return (
            self.head == other.head
            and self.tree == other.tree
            and self.dirty_digest == other.dirty_digest
        )

    def describe(self) -> str:
        return (
            f"HEAD {self.head[:12] or '(none)'}  tree {self.tree[:12] or '(none)'}  "
            f"working tree {self.tracked_dirty} modified / {self.untracked} untracked "
            f"(digest {self.dirty_digest[:12] or '(clean)'})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "head": self.head,
            "tree": self.tree,
            "dirty_digest": self.dirty_digest,
            "tracked_dirty": self.tracked_dirty,
            "untracked": self.untracked,
            "identity": self.identity,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TreeFingerprint":
        if not isinstance(data, dict):
            return cls()
        return cls(
            head=str(data.get("head", "") or ""),
            tree=str(data.get("tree", "") or ""),
            dirty_digest=str(data.get("dirty_digest", "") or ""),
            tracked_dirty=int(data.get("tracked_dirty", 0) or 0),
            untracked=int(data.get("untracked", 0) or 0),
        )


#: A working tree can be enormous; the digest is over the diff, and a diff that
#: large is not something a reviewer was reading anyway.
_MAX_DIFF_CHARS = 4_000_000

#: Per-file and total budgets for hashing UNTRACKED content. Untracked files have
#: no blob in the object store, so the only way to notice a change inside one is
#: to read it — and a unit being built new is very often entirely untracked, which
#: makes this the difference between a review that binds to the implementation and
#: one that binds to a filename.
_MAX_UNTRACKED_FILE_BYTES = 2_000_000
_MAX_UNTRACKED_TOTAL_BYTES = 64_000_000
_MAX_UNTRACKED_FILES = 5_000


def capture_fingerprint(repo: Path) -> TreeFingerprint:
    """Read the repository's current identity. Read-only; never raises."""
    repo = Path(repo)

    def git(*args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            return proc.stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    head = git("rev-parse", "HEAD").strip()
    tree = git("rev-parse", "HEAD^{tree}").strip()
    # `-uall` so an untracked *directory* expands into its files. Collapsed, a
    # whole new package reads as one line that never changes again.
    status = [ln for ln in git("status", "--porcelain", "-uall").splitlines() if ln.strip()]
    untracked = sorted(ln[3:].strip().strip('"') for ln in status if ln.startswith("??"))

    digest = hashlib.sha256()
    digest.update(git("diff", "HEAD")[:_MAX_DIFF_CHARS].encode("utf-8", "replace"))
    _hash_untracked(repo, untracked, digest)

    return TreeFingerprint(
        head=head,
        tree=tree,
        # A clean tree has no dirty component at all, so its identity is just
        # HEAD/tree — and two clean checkouts of the same commit compare equal.
        dirty_digest=digest.hexdigest() if status else "",
        tracked_dirty=len([ln for ln in status if not ln.startswith("??")]),
        untracked=len(untracked),
    )


def _hash_untracked(repo: Path, paths: Sequence[str], digest: Any) -> None:
    """Fold untracked file *contents* into the digest, within budgets.

    A tracked file's change is already in ``git diff HEAD``. An untracked one is
    not in the object store at all, so nothing but reading it can tell that its
    body moved — and "the unit being built is untracked" is the normal case, not
    an edge case. A fingerprint that hashed only untracked *names* would let a
    supported review of one implementation stand over a completely rewritten one
    with the same filename.

    Budgets exist so a stray database or log dump cannot make this expensive.
    A file past them contributes its path and size instead of its bytes: still
    enough to notice it appearing, growing or vanishing, and the one residual
    gap — a change inside a very large untracked file that leaves its size
    identical — is recorded in the README's limitations rather than pretended
    away.
    """
    spent = 0
    for index, rel in enumerate(paths):
        digest.update(b"\x00")
        digest.update(rel.encode("utf-8", "replace"))
        if index >= _MAX_UNTRACKED_FILES:
            continue
        path = repo / rel
        try:
            if not path.is_file() or path.is_symlink():
                continue
            size = path.stat().st_size
        except OSError:
            continue
        digest.update(str(size).encode("ascii"))
        if size > _MAX_UNTRACKED_FILE_BYTES or spent + size > _MAX_UNTRACKED_TOTAL_BYTES:
            continue
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
        spent += size


@dataclass
class ReviewRecord:
    """One review, bound to the state it was performed against."""

    review: Any
    fingerprint: TreeFingerprint
    iteration: int = 0
    scope_id: str = ""
    builder_session_id: str = ""
    #: The fingerprint identity of the change that retired this review, if one
    #: has. Set by :meth:`ReviewLedger.invalidate_stale`; never cleared.
    superseded_by: str = ""

    @property
    def verdict(self) -> str:
        return str(getattr(self.review, "verdict", "") or "")

    @property
    def reviewer_session_id(self) -> str:
        return str(getattr(self.review, "reviewer_session_id", "") or "")

    @property
    def independent(self) -> bool:
        """True when this review did not come from the session that built the work.

        The real guarantee is structural and lives in
        :class:`~neyma_product_driver.reviewer.IndependentReviewerSession`: it is
        constructed with ``resume=None``, ``continue_conversation=False`` and
        ``fork_session=False``, so it *cannot* be the builder's conversation.
        There is no configuration that relaxes that.

        This property is the **check** on top of it, and it catches the one
        failure it can see from the artifact: a review whose session id is the
        builder's. When both ids are present they must differ. When the reviewer
        never reported an id — the SDK not surfacing one is an environment
        quirk, not an independence failure — the structural guarantee stands on
        its own, expressed as ``inherited_builder_context``, which is ``False``
        by construction and is never set to anything else.
        """
        if bool(getattr(self.review, "inherited_builder_context", False)):
            return False
        reviewer = self.reviewer_session_id
        builder = self.builder_session_id
        if reviewer and builder:
            return reviewer != builder
        return True

    @property
    def stale(self) -> bool:
        return bool(self.superseded_by)

    def satisfies(self, current: TreeFingerprint) -> bool:
        """Whether this review can stand as the review of ``current``."""
        return (
            self.verdict == "SUPPORTED"
            and not self.stale
            and self.independent
            and self.fingerprint.matches(current)
        )

    def to_dict(self) -> dict[str, Any]:
        review = self.review
        dump = (
            review.model_dump(mode="json")
            if hasattr(review, "model_dump")
            else dict(review or {})
        )
        return {
            "iteration": self.iteration,
            "scope_id": self.scope_id,
            "verdict": self.verdict,
            "reviewer_session_id": self.reviewer_session_id,
            "builder_session_id": self.builder_session_id,
            "independent": self.independent,
            "reviewed": self.fingerprint.to_dict(),
            "superseded_by": self.superseded_by,
            "review": dump,
        }


class ReviewLedger:
    """Every review this run launched, in order, each bound to its own tree.

    The ledger is the thing that makes "reviewer supported an older tree" unable
    to satisfy the current one: :meth:`satisfying` only ever returns a record
    whose fingerprint matches, and :meth:`invalidate_stale` writes down the
    moment a record stopped matching so the evidence says so out loud.
    """

    def __init__(self) -> None:
        self.records: list[ReviewRecord] = []
        #: One line per invalidation, for the run journal.
        self.invalidations: list[str] = []

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self):
        return iter(self.records)

    @property
    def reviews(self) -> list[Any]:
        return [r.review for r in self.records]

    def record(
        self,
        review: Any,
        fingerprint: TreeFingerprint,
        *,
        iteration: int = 0,
        scope_id: str = "",
        builder_session_id: str = "",
    ) -> ReviewRecord:
        entry = ReviewRecord(
            review=review,
            fingerprint=fingerprint,
            iteration=iteration,
            scope_id=scope_id,
            builder_session_id=builder_session_id,
        )
        self.records.append(entry)
        return entry

    def invalidate_stale(self, current: TreeFingerprint) -> list[ReviewRecord]:
        """Retire every review that is no longer about the current state.

        Called before the requirement is consulted, so a review can never be
        read as satisfying a tree it was not performed against — and so the run
        evidence carries the sentence "this review stopped applying here".
        """
        newly: list[ReviewRecord] = []
        for record in self.records:
            if record.stale or record.fingerprint.matches(current):
                continue
            record.superseded_by = current.identity
            newly.append(record)
            self.invalidations.append(
                f"the review of {record.fingerprint.identity} "
                f"({record.verdict or 'no verdict'}) no longer describes the "
                f"implementation: it is now {current.identity}"
            )
        return newly

    def satisfying(self, current: TreeFingerprint) -> ReviewRecord | None:
        """A supported, independent, non-stale review OF THIS EXACT STATE."""
        for record in reversed(self.records):
            if record.satisfies(current):
                return record
        return None

    def refusals(self) -> list[ReviewRecord]:
        return [r for r in self.records if r.verdict and r.verdict != "SUPPORTED"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviews": [r.to_dict() for r in self.records],
            "invalidations": list(self.invalidations),
        }


# --------------------------------------------------------------------------
# 3. Where a verdict goes
# --------------------------------------------------------------------------


class ReviewRoute(str, Enum):
    """Who owns what the reviewer just said."""

    #: The requirement is satisfied; the loop may continue toward acceptance.
    SATISFIED = "SATISFIED"
    #: A grounded product defect. Back to the same builder as a correction.
    CORRECT_PRODUCT = "CORRECT_PRODUCT"
    #: The reviewer keeps refusing after correction. That is a decision.
    FOUNDER_DECISION = "FOUNDER_DECISION"
    #: A named external action — push, CI, credentials, partner data — the
    #: driver may not perform. Stop at the boundary and say exactly what.
    EXTERNAL_ACTION = "EXTERNAL_ACTION"
    #: The reviewer could not tell, and no allowed command could resolve it.
    #: Fail closed with the exact reason; never a product change.
    UNRESOLVED = "UNRESOLVED"
    #: The reviewer could not tell, has not yet used the verification capability
    #: it was given, and has budget left. Re-ask once, with the vocabulary.
    RETRY_WITH_EXECUTION = "RETRY_WITH_EXECUTION"


class BlockerKind(str, Enum):
    """What kind of thing stopped a review from concluding.

    Named so the four owners of a stuck review stay distinguishable. Collapsing
    them is what turns "the harness cannot measure this" into an endless series
    of product corrections that fix nothing.
    """

    NONE = "NONE"
    PRODUCT_DEFECT = "PRODUCT_DEFECT"
    VERIFICATION_HARNESS = "VERIFICATION_HARNESS"
    REVIEWER_CAPABILITY = "REVIEWER_CAPABILITY"
    REPOSITORY_AUTHORITY = "REPOSITORY_AUTHORITY"
    EXTERNAL_ACTION = "EXTERNAL_ACTION"

    @classmethod
    def parse(cls, value: Any) -> "BlockerKind":
        text = str(getattr(value, "value", value) or "").strip().upper()
        try:
            return cls(text)
        except ValueError:
            return cls.NONE


@dataclass
class ReviewRouting:
    """The decision about one review, with the sentence that justifies it."""

    route: ReviewRoute
    reason: str = ""
    #: The precise thing the founder is being asked to do, when the route is
    #: EXTERNAL_ACTION or FOUNDER_DECISION. Never invented here — copied from
    #: what the reviewer wrote.
    requested_action: str = ""
    #: Findings that are grounded enough to send to a builder.
    grounded_findings: list[Any] = field(default_factory=list)
    blocker: BlockerKind = BlockerKind.NONE

    @property
    def satisfied(self) -> bool:
        return self.route is ReviewRoute.SATISFIED


def grounded_findings(review: Any) -> list[Any]:
    """Findings a builder could actually act on.

    A correction has to name a place. A finding with no evidence path is an
    opinion about the change, and turning an opinion into an automatic code
    change is exactly what requirement "do not turn a reviewer disagreement into
    an automatic code change" forbids. Blockers first; majors only if there are
    no blockers, so a review with one real blocker does not drown it in minors.
    """
    findings = list(getattr(review, "findings", []) or [])
    grounded = [f for f in findings if str(getattr(f, "evidence_path", "") or "").strip()]
    blockers = [f for f in grounded if str(getattr(f, "severity", "")) == "blocker"]
    return blockers or [f for f in grounded if str(getattr(f, "severity", "")) == "major"]


def route_review(
    review: Any,
    *,
    refusals_so_far: int,
    correction_budget: int,
    execution_available: bool,
    execution_used: bool,
    retried_with_execution: bool = False,
) -> ReviewRouting:
    """Decide who owns this verdict. The only place that decision is made.

    ``refusals_so_far`` counts refusing reviews INCLUDING this one, and
    ``correction_budget`` is ``review.max_automatic_reviews``: once the budget is
    spent, a reviewer that still refuses is describing a decision rather than a
    defect, and the founder owns decisions.
    """
    verdict = str(getattr(review, "verdict", "") or "").upper()
    blocker = BlockerKind.parse(
        getattr(getattr(review, "blocked_on", None), "kind", BlockerKind.NONE)
    )
    requested = str(
        getattr(getattr(review, "blocked_on", None), "requested_action", "") or ""
    ).strip()
    detail = str(
        getattr(getattr(review, "blocked_on", None), "detail", "") or ""
    ).strip()
    summary = str(getattr(review, "summary", "") or "").strip()

    if verdict == "SUPPORTED":
        return ReviewRouting(ReviewRoute.SATISFIED, reason=summary[:400])

    # An external action is an external action whatever the verdict says. A
    # reviewer that cannot finish because CI has to run after a push is naming
    # a boundary, and the driver reports the boundary rather than crossing it or
    # manufacturing the receipt that would be on the other side of it.
    if blocker is BlockerKind.EXTERNAL_ACTION:
        return ReviewRouting(
            ReviewRoute.EXTERNAL_ACTION,
            reason=detail or summary[:400],
            requested_action=requested,
            blocker=blocker,
        )

    if blocker is BlockerKind.REPOSITORY_AUTHORITY:
        return ReviewRouting(
            ReviewRoute.FOUNDER_DECISION,
            reason=(
                "the review turns on a question the repository's own authority does not "
                "answer, which is a product decision rather than a defect: "
                + (detail or summary[:300])
            ),
            requested_action=requested,
            blocker=blocker,
        )

    grounded = grounded_findings(review)

    if verdict == "NOT_SUPPORTED":
        if not grounded:
            # A refusal with nothing a builder could act on is a disagreement,
            # not a defect. Sending it anyway produces a correction the
            # prompt-quality contract would reject and a builder could not act
            # on, so it goes where disagreements go.
            return ReviewRouting(
                ReviewRoute.FOUNDER_DECISION,
                reason=(
                    "the reviewer refused without a finding that cites evidence, so there "
                    "is nothing grounded to send back to the builder: " + summary[:300]
                ),
                blocker=blocker,
            )
        if refusals_so_far > max(0, correction_budget):
            return ReviewRouting(
                ReviewRoute.FOUNDER_DECISION,
                reason=(
                    "independent review refused this change again after the builder "
                    "corrected it, so this is a product or authority decision rather than "
                    "a defect the builder can be sent back to fix"
                ),
                grounded_findings=grounded,
                blocker=blocker,
            )
        return ReviewRouting(
            ReviewRoute.CORRECT_PRODUCT,
            reason=summary[:400],
            grounded_findings=grounded,
            blocker=blocker or BlockerKind.PRODUCT_DEFECT,
        )

    # INSUFFICIENT_EVIDENCE, and anything unrecognized, which degrades the same
    # way for the same reason: an answer nobody can classify is not a pass.
    if blocker is BlockerKind.PRODUCT_DEFECT and grounded:
        if refusals_so_far > max(0, correction_budget):
            return ReviewRouting(
                ReviewRoute.FOUNDER_DECISION,
                reason="the reviewer still cannot support this after correction",
                grounded_findings=grounded,
                blocker=blocker,
            )
        return ReviewRouting(
            ReviewRoute.CORRECT_PRODUCT,
            reason=summary[:400],
            grounded_findings=grounded,
            blocker=blocker,
        )

    if execution_available and not execution_used and not retried_with_execution:
        # It has the capability and did not use it. That is worth exactly one
        # more ask, with the vocabulary spelled out — and never more than one,
        # because a second identical answer is the answer.
        return ReviewRouting(
            ReviewRoute.RETRY_WITH_EXECUTION,
            reason=(
                "the reviewer reported insufficient evidence without running any of the "
                "deterministic verification it was permitted to run"
            ),
            blocker=blocker or BlockerKind.REVIEWER_CAPABILITY,
        )

    why = detail or summary or "the reviewer did not say what was missing"
    if blocker is BlockerKind.VERIFICATION_HARNESS:
        why = (
            "Product Driver cannot produce the evidence this review needs — this is the "
            "driver's verification gap, not a defect in the product: " + why
        )
    elif blocker is BlockerKind.REVIEWER_CAPABILITY:
        why = (
            "the reviewer could not reach the evidence within the read-only boundary it "
            "is held to: " + why
        )
    return ReviewRouting(
        ReviewRoute.UNRESOLVED,
        reason=why[:600],
        requested_action=requested,
        blocker=blocker or BlockerKind.REVIEWER_CAPABILITY,
    )


# --------------------------------------------------------------------------
# Rendering, for the prompts and the record
# --------------------------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")


def correction_lines(review: Any, findings: Sequence[Any], requirement: Any = None) -> list[str]:
    """The reviewer's findings, as a builder-facing block. Nothing invented."""
    lines = [
        "INDEPENDENT REVIEW — a fresh session that did not write this implementation "
        "reviewed it and did not support it.",
        "",
        f"verdict: {getattr(review, 'verdict', '?')} "
        f"(confidence {float(getattr(review, 'confidence', 0.0) or 0.0):.2f})",
    ]
    if requirement is not None and getattr(requirement, "reasons", None):
        lines.append(f"why this review happened: {requirement.reasons[0]}")
    # What the review's evidence actually rests on, in its own words. Never
    # inferred here: `evidence_basis` is computed from the harness's own record
    # of what each command produced and whether its named expectation held, so a
    # builder is never told a finding was measured when it was read.
    basis = getattr(review, "evidence_basis", None)
    lines.append(f"evidence: {basis() if callable(basis) else 'not recorded'}")
    for line in (getattr(review, "evidence_lines", None) or (lambda **_: []))(limit=6):
        lines.append(f"  {line}")
    lines += ["", _WHITESPACE.sub(" ", str(getattr(review, "summary", "") or "")).strip(), ""]
    lines.append("FINDINGS — each cites evidence the reviewer actually read or produced:")
    for finding in findings:
        lines.append(f"  [{getattr(finding, 'severity', 'major')}] {getattr(finding, 'finding', '')}")
        lines.append(f"      evidence:  {getattr(finding, 'evidence_path', '')}")
        lines.append(f"      reasoning: {getattr(finding, 'reasoning', '')}")
    upheld = [
        a
        for a in (getattr(review, "adjudications", []) or [])
        if str(getattr(a, "ruling", "")) == "UPHELD"
    ]
    if upheld:
        lines += ["", "DISCREPANCIES THE REVIEWER UPHELD:"]
        lines += [
            f"  - {getattr(a, 'discrepancy', '')}\n      basis: {getattr(a, 'basis', '')}"
            for a in upheld
        ]
    lines += [
        "",
        "Address the findings above. Do not delete, weaken or disable a test, guard or "
        "control to clear one — a finding cleared that way is a defect with a passing "
        "status. If a finding rests on a product decision the repository has not made, "
        "say so plainly rather than choosing one.",
        "",
        "A NEW independent reviewer will look at whatever you produce. The review above "
        "describes the tree as it is right now and stops applying the moment you change "
        "it, so there is nothing to be gained by leaving a finding unaddressed.",
    ]
    return lines
