"""Operating policy: product safety versus engineering ceremony.

This module exists to *remove* precedence rules, not to add a layer. Before it,
every governance signal the driver could compute — a commit-topology
difference, a missing metadata commit, a finalizer receipt that had not been
regenerated, an independent-review criterion the registry still listed — sat
above the product evaluation in a fixed ordering, and any one of them could end
a run that had just demonstrated working behaviour. The founder then relayed the
finding to a builder by hand, and the loop the driver exists to run stopped
being autonomous.

The ordering is replaced by one question, asked of each signal:

    Does clearing this require an action only the founder may authorize —
    a history rewrite, a remote mutation, something destructive or external?

If yes, the run stops and asks. If no, the signal is recorded, reported and fed
to the investigator, and the product loop continues. That is the whole rule.

Two policies live here.

**Ceremony versus authority.** :func:`requires_founder_authority` reads a
protocol resolution and answers the question above from the remediation the
repository's own rules would need — ``rewrites_history``,
``affects_remote_history``, a destructive operation — rather than from the
category of the violation. A commit-topology difference the target repository no
longer treats as mandatory produces no violation at all (the resolver only
speaks where the repository states a rule); one it *does* still state is
reported as a diagnostic, and only blocks when repairing it would cross a
founder boundary.

**Review proportional to risk.** :func:`assess_change_risk` classifies what the
builder actually changed. Ordinary work needs no independent reviewer. A large
change may earn one when the run's own history suggests it would help. A change
touching a high-consequence surface — effect execution, money, approval
authority, authentication, authorization, tenant isolation, secrets, destructive
database operations, write-capable external integrations, outbound
communication, claims/legal/compliance behaviour, or a runtime safety invariant
— always gets one, automatically, without the founder spawning it.

The bias is deliberate and one-directional: surface matching is generous, so an
uncertain change earns a review rather than skipping one. A false positive costs
one bounded read-only session. A false negative ships an unreviewed change to
one of the surfaces on that list.

Nothing here writes anything, and nothing here can authorize an external effect:
the command guard is the boundary, and it is not consulted through this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

# --------------------------------------------------------------------------
# High-consequence surfaces
# --------------------------------------------------------------------------
#
# Matched against the changed file paths, and — only when there is a real diff —
# the task text, case-insensitively.
# These name *product* consequence — a customer, a payment, an authorization
# boundary, a tenant wall — never repository process. A pattern here says "if
# this is wrong, someone outside this machine is harmed", which is exactly the
# line the founder drew.

HIGH_CONSEQUENCE_SURFACES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "effect execution",
        re.compile(r"(?i)\beffects?\b|\beffect\s?(?:executor|runner|dispatch)\b"
                   r"|\bside\s?effects?\b|\bactuat\w*"),
    ),
    (
        "payment or banking",
        re.compile(r"(?i)\b(?:payment|payout|billing|banking|bank\s?account|ach|wire\s?transfer"
                   r"|stripe|remittance|settlement|refund|invoice\s?pay)\w*"),
    ),
    (
        "approval authority",
        re.compile(r"(?i)\b(?:approval|approver|approve|sign\s?off)\w*"
                   r"|\bauthoriz\w*\s?(?:policy|rule|matrix)\b"),
    ),
    (
        "authentication",
        re.compile(r"(?i)\b(?:auth|authn|login|logout|signin|session\s?token|password|passwd"
                   r"|oauth|oidc|saml|jwt|credential)\w*"),
    ),
    (
        "authorization",
        re.compile(r"(?i)\b(?:authz|permission|rbac|abac|access\s?control|policy\s?engine"
                   r"|entitlement)\w*|\bscopes?\s?check\w*|\brole\s?(?:check|guard|policy)\w*"),
    ),
    (
        "tenant isolation",
        re.compile(r"(?i)\b(?:tenant|tenancy|multi\s?tenant|org\s?id|organisation\s?id"
                   r"|organization\s?id|workspace\s?id|row\s?level\s?security|rls)\w*"),
    ),
    (
        "secrets",
        re.compile(r"(?i)\b(?:secret|vault|kms|private\s?key|api\s?key|token\s?store"
                   r"|keychain|signing\s?key)\w*"),
    ),
    (
        "destructive database operation",
        re.compile(r"(?i)\b(?:migrat|alembic|drop\s?(?:table|column|schema)|truncate"
                   r"|delete\s?all|purge|backfill|schema\s?change)\w*"),
    ),
    (
        "write-capable external integration",
        re.compile(r"(?i)\b(?:webhook|outbound|connector|integration|api\s?client|sdk\s?client"
                   r"|third\s?party|upstream\s?write|browser\s?(?:agent|automation))\w*"),
    ),
    (
        "outbound communication",
        re.compile(r"(?i)\b(?:email|smtp|sendgrid|mailer|sms|twilio|slack\s?(?:post|send)"
                   r"|notification\s?send|dispatch\s?message)\w*"),
    ),
    (
        "claims, legal or compliance behaviour",
        re.compile(r"(?i)\b(?:claim|dispute|liabilit|compliance|regulat|legal|retention\s?policy"
                   r"|audit\s?(?:log|trail)|gdpr|pii)\w*"),
    ),
    (
        "runtime safety invariant",
        re.compile(r"(?i)\b(?:kill\s?switch|circuit\s?breaker|safety\s?(?:guard|invariant|check)"
                   r"|invariant|guardrail|rate\s?limit|dark\s?launch|feature\s?gate)\w*"),
    ),
)


def _searchable(text: str) -> tuple[str, str]:
    """The raw string and a separator-normalized copy of it.

    ``_`` is a word character, so ``\btenant\b`` does not match
    ``app/db/tenant_scope.py`` — and file paths are written almost entirely in
    underscores and slashes. Every surface pattern is therefore matched against
    both forms: the raw text, and one where every non-alphanumeric run has become
    a single space. Missing a tenant-isolation change because of a regex word
    boundary is not a failure mode worth keeping.
    """
    return text, re.sub(r"[^A-Za-z0-9]+", " ", text)


class ChangeRisk(str, Enum):
    """How much independent scrutiny this change earns."""

    ORDINARY = "ORDINARY"
    MEANINGFUL = "MEANINGFUL"
    HIGH_CONSEQUENCE = "HIGH_CONSEQUENCE"


@dataclass
class RiskAssessment:
    """What the builder changed, and what review that warrants."""

    level: ChangeRisk = ChangeRisk.ORDINARY
    #: Human-readable reasons, each naming the evidence it came from.
    reasons: list[str] = field(default_factory=list)
    #: Which high-consequence surfaces were touched, by name.
    surfaces: list[str] = field(default_factory=list)
    changed_files: int = 0
    changed_lines: int = 0
    #: Findings from the authority module: a weakened or deleted mandatory
    #: control is high-consequence whatever else the change did.
    weakened_controls: list[str] = field(default_factory=list)

    @property
    def requires_independent_review(self) -> bool:
        """A focused independent review is mandatory, not optional."""
        return self.level is ChangeRisk.HIGH_CONSEQUENCE

    def warrants_independent_review(self, *, iterations: int, uncovered_risks: int) -> bool:
        """Whether a review would materially increase confidence in this run.

        High consequence always. A merely large change earns one only when the
        run's own history says the change was hard — it took more than one pass,
        or the acceptance gate is carrying risks nothing verified. A large change
        that worked first time with full coverage does not need a second opinion,
        and spending one on it is the ceremony this driver is removing.
        """
        if self.requires_independent_review:
            return True
        if self.level is ChangeRisk.MEANINGFUL:
            return iterations > 1 or uncovered_risks > 0
        return False

    def brief(self) -> str:
        head = f"{self.level.value}: {self.changed_files} file(s), ~{self.changed_lines} line(s)"
        if self.surfaces:
            head += f"; surfaces: {', '.join(self.surfaces)}"
        return head

    def summary_block(self) -> str:
        lines = [f"change risk: {self.brief()}"]
        lines += [f"  - {reason}" for reason in self.reasons]
        return "\n".join(lines)


def assess_change_risk(
    *,
    task: str = "",
    diff_files: Sequence[str] = (),
    diff_stat: str = "",
    authority_findings: Sequence[Any] = (),
    meaningful_files: int = 10,
    meaningful_lines: int = 400,
) -> RiskAssessment:
    """Classify a change by product consequence, then by size.

    ``diff_files`` and ``diff_stat`` come from the same read-only git inspection
    the loop already performs. ``authority_findings`` are the
    :mod:`~neyma_product_driver.authority` findings for this run; one that
    removes or weakens a mandatory control makes the change high-consequence on
    its own, because unblocking yourself by deleting the rule you are failing is
    precisely the move that must never pass unreviewed.
    """
    files = [str(f).strip() for f in diff_files if str(f).strip()]
    lines = _changed_lines(diff_stat)
    reasons: list[str] = []
    surfaces: list[str] = []

    # Consequence is a property of what was CHANGED. The task description is
    # read too, because a path like ``app/services/core.py`` does not reveal that
    # it now moves money — but only alongside a real diff. A run that changed
    # nothing has touched no surface, whatever its task said it would do, and
    # treating the description alone as evidence made every run whose task
    # mentioned approvals demand a review of an empty change.
    haystacks = list(files)
    if files and task:
        haystacks.append(task)
    for name, pattern in HIGH_CONSEQUENCE_SURFACES:
        hits = [h for h in haystacks if any(pattern.search(f) for f in _searchable(h))]
        if not hits:
            continue
        surfaces.append(name)
        where = "the task description" if hits[0] == task and hits[0] not in files else hits[0]
        reasons.append(f"touches {name} ({where})")

    weakened = [
        _finding_text(finding)
        for finding in authority_findings
        if _is_weakening(finding)
    ]
    for detail in weakened:
        reasons.append(f"weakens a mandatory control: {detail}")

    if surfaces or weakened:
        level = ChangeRisk.HIGH_CONSEQUENCE
    elif len(files) > meaningful_files or lines > meaningful_lines:
        level = ChangeRisk.MEANINGFUL
        reasons.append(
            f"large change: {len(files)} file(s), ~{lines} line(s) "
            f"(thresholds {meaningful_files} / {meaningful_lines})"
        )
    else:
        level = ChangeRisk.ORDINARY
        reasons.append("ordinary product work: no high-consequence surface, no unusual size")

    return RiskAssessment(
        level=level,
        reasons=reasons,
        surfaces=surfaces,
        changed_files=len(files),
        changed_lines=lines,
        weakened_controls=weakened,
    )


def _changed_lines(diff_stat: str) -> int:
    """Insertions plus deletions from a ``git diff --stat`` summary line."""
    total = 0
    for match in re.finditer(r"(\d+)\s+(?:insertion|deletion)", diff_stat or ""):
        total += int(match.group(1))
    if total:
        return total
    # No summary line (a --stat of a single file, say): count the per-file
    # change columns instead of reporting zero for a real diff.
    for match in re.finditer(r"\|\s+(\d+)\s", diff_stat or ""):
        total += int(match.group(1))
    return total


def _is_weakening(finding: Any) -> bool:
    kind = str(getattr(finding, "kind", "") or (finding.get("kind", "") if isinstance(finding, dict) else ""))
    return bool(re.search(r"(?i)weaken|remov|delet|downgrad|soften", kind))


def _finding_text(finding: Any) -> str:
    for attr in ("detail", "kind"):
        value = getattr(finding, attr, None)
        if value is None and isinstance(finding, dict):
            value = finding.get(attr)
        if value:
            return str(value)[:200]
    return str(finding)[:200]


# --------------------------------------------------------------------------
# Ceremony versus founder authority
# --------------------------------------------------------------------------


@dataclass
class AuthorityVerdict:
    """Whether a protocol finding is the founder's to decide, or a diagnostic."""

    requires_founder: bool = False
    reason: str = ""
    #: The operations that make it the founder's decision, quoted.
    operations: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.requires_founder


#: Rule families whose repair is inherently a founder decision. Read from the
#: repository's own discovered rules, so a repository that stops declaring one
#: stops producing it.
_FOUNDER_RULE_KINDS = ("HISTORY_REWRITE_APPROVAL",)


def requires_founder_authority(resolution: Any) -> AuthorityVerdict:
    """Is clearing this protocol state something only the founder may authorize?

    True when the repair the repository's own rules point at would rewrite
    history, touch a remote, or perform a destructive operation — the boundaries
    that stay hard however autonomous the rest of the loop becomes. False for
    everything else, including every commit-topology, metadata-commit,
    finalizer-ownership and receipt-freshness finding, which are reported as
    diagnostics and handed to the investigator rather than ending the run.

    "The repair the rules point at" means the *recommended* option, not any
    option. A repository that offers both a safe repair and a destructive one has
    a safe repair, and demanding founder approval because a dangerous
    alternative also exists would stop runs that never needed stopping. Where
    there is no recommendation, the question becomes whether *every* option still
    on the table is destructive — which is the same question, asked of a set.

    Reads through ``getattr`` so a resolution from any version of the resolver —
    or a fake in a test — is handled without a type dependency.
    """
    if resolution is None:
        return AuthorityVerdict()

    for violation in getattr(resolution, "violations", None) or []:
        rule_id = str(getattr(violation, "rule_id", "") or "")
        if any(kind in rule_id.upper() for kind in _FOUNDER_RULE_KINDS):
            return AuthorityVerdict(
                True,
                "the repository states that this requires explicit history-rewrite approval",
                [str(getattr(violation, "detail", "") or getattr(violation, "observed_state", ""))],
            )

    best = getattr(resolution, "recommended_option", None)
    if best is not None:
        return _option_verdict(best)

    viable = [
        option
        for option in (getattr(resolution, "options", None) or [])
        if not getattr(option, "disqualified", False)
    ]
    if not viable:
        return AuthorityVerdict()
    verdicts = [_option_verdict(option) for option in viable]
    if all(verdict.requires_founder for verdict in verdicts):
        return AuthorityVerdict(
            True,
            "every repair the repository's rules allow crosses a boundary you own: "
            + verdicts[0].reason,
            [op for verdict in verdicts for op in verdict.operations][:8],
        )
    return AuthorityVerdict()


def _option_verdict(option: Any) -> AuthorityVerdict:
    """Whether performing one remediation option would need founder authority."""
    operations = [str(op) for op in (getattr(option, "destructive_operations", None) or [])]
    label = f"option {getattr(option, 'option_id', '?')}"
    if getattr(option, "rewrites_history", False):
        return AuthorityVerdict(
            True,
            "clearing this would rewrite local history, which is yours to authorize",
            operations or [f"{label} rewrites history"],
        )
    if getattr(option, "affects_remote_history", False):
        return AuthorityVerdict(
            True,
            "clearing this would change history that has been pushed or shared",
            operations or [f"{label} affects remote history"],
        )
    if operations:
        return AuthorityVerdict(
            True, "the only repair the repository's rules allow is destructive", operations
        )
    return AuthorityVerdict()


def protocol_diagnostic_notes(resolution: Any) -> list[str]:
    """The findings, as notes for the record and for the investigator.

    Deliberately flat prose. These are observations about the repository, not
    instructions to a builder: a builder told to go and satisfy a rule the
    repository may no longer intend is how the ceremony loop restarted itself
    every time it was cut back.
    """
    if resolution is None:
        return []
    notes: list[str] = []
    status = str(getattr(getattr(resolution, "status", None), "value", "") or "")
    if status and status != "CONSISTENT":
        notes.append(f"repository protocol reports {status} (recorded, not blocking)")
    for conflict in getattr(resolution, "conflicts", None) or []:
        notes.append(f"protocol conflict: {getattr(conflict, 'description', '')}")
    for violation in getattr(resolution, "violations", None) or []:
        detail = getattr(violation, "detail", "") or getattr(violation, "observed_state", "")
        if detail:
            notes.append(f"protocol: {detail}")
    for deadlock in getattr(resolution, "deadlocks", None) or []:
        root = getattr(deadlock, "root_cause", "")
        if root:
            notes.append(f"protocol deadlock: {root}")
    for blocker in getattr(resolution, "environment_blockers", None) or []:
        description = getattr(blocker, "description", "")
        if description:
            notes.append(f"environment: {description}")
    return notes


def protocol_warrants_investigation(resolution: Any) -> tuple[bool, str]:
    """Whether this resolution is the investigator's problem rather than nobody's.

    An environmental blocker, a deadlock, or contradictory protocol authority are
    exactly the machine-debugging the founder does not want to perform by hand.
    They no longer stop the run, so something has to pick them up; this is what
    routes them.
    """
    if resolution is None:
        return False, ""
    status = str(getattr(getattr(resolution, "status", None), "value", "") or "")
    if getattr(resolution, "environment_blockers", None):
        return True, "the repository reports an environmental blocker; confirm it with a direct probe"
    if status == "BLOCKED_ENVIRONMENT":
        return True, "a repository gate could not run for environmental reasons"
    if getattr(resolution, "deadlocks", None):
        return True, "the repository's own rules form a circular blocker; diagnose the chain"
    if status == "BLOCKED_AUTHORITY" or getattr(resolution, "conflicts", None):
        return True, "the repository states contradictory protocol authority"
    return False, ""


# --------------------------------------------------------------------------
# Ceremony the auditor should stop requiring when the repository stops stating it
# --------------------------------------------------------------------------


def declared_rule_kinds(protocol: Any) -> frozenset[str]:
    """Every rule family the target repository currently states, by name.

    The auditor uses this to decide which receipts and gates it is entitled to
    require. A repository that deletes its finalizer protocol stops producing a
    FINALIZER_OWNERSHIP rule, and the auditor stops asking for a finalizer
    receipt — with no change here and no code that ever knew the rule's name.
    """
    kinds: set[str] = set()
    for rule in getattr(protocol, "rules", None) or []:
        kind = getattr(getattr(rule, "kind", None), "value", None) or getattr(rule, "kind", "")
        if kind:
            kinds.add(str(kind))
    return frozenset(kinds)
