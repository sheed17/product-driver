"""A PRODUCT-VERIFICATION GAP, routed by name, answered by a guard, and nothing looser.

The loop this module exists to end. A run names a risk in prose — "UNKNOWN_OUTCOME
could be treated as a VERIFIED original effect and invent a compensating call" —
and the prose names one concrete artifact. :mod:`~neyma_product_driver.guard_coverage`
correctly refuses to check a guard against a risk that thin: with one shared token
any guard that mentions ``UNKNOWN_OUTCOME`` would discharge it, which is "any green
test near the words counts". So the risk stays open, the driver asks the builder
for verification, the builder adds exactly the guard asked for — and the risk is
STILL too thin, because the builder's guard cannot change the words the risk was
first written in. Nothing the builder can do closes it. Run 20260918-223447
(Neyma U8.4) was one correction away from exactly that loop, with three wordings
of the same obligation each blocking on its own.

What closes it is not a looser match. It is provenance:

1. **The driver routes the gap by identity.** The correction names the exact
   risk keys it is about (:attr:`IdentifiedRisk.key`), and that routing is
   persisted with the tree it was routed on. Wordings that name the same concrete
   subjects under the same category are one obligation — an exact equality of the
   artifacts each names, never a similarity score — and a risk that names no
   concrete subject is never grouped with anything.
2. **Only the answering change binds.** The tests whose expectations moved between
   the tree the gap was routed on and the next tree the driver judges are bound to
   that obligation, and to no other. A guard that already existed, one changed in
   answer to a different correction, or one changed later for another reason is
   not bound, however relevant it looks.
3. **The driver executes the bound tests itself,** by exact node id, on the judged
   tree, and records pass / fail / skip per node together with the tree. A
   builder's "this test covers it" is not an input anywhere here.
4. **An absence claim owes its control.** Every risk routed here is an
   acceptance-blocking "this must not happen", so a bound guard counts only with
   discrimination bound in the same answer: a passing control test that names the
   guard (it realises the forbidden behaviour and shows the guard going RED), or a
   passing mutation battery that names the guard's node id.
5. **Evidence belongs to one tree.** An execution from any other tree is recorded
   and never read as evidence about this one; a RED bound guard is a refutation.

The association is auditable end to end in run evidence: risk key → obligation →
correction (iteration, routed tree) → answering tree → bound test → exact command →
per-node result on the judged tree → discrimination.

No product, phase, unit or test name appears in this module.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .changed_verification import (
    DISCRIMINATION_NAME_MARKERS,
    VERIFICATION_FILE,
    _TEST_DEF,
    _matching,
    _read,
    _test_spans,
    changed_expectations,
)
from .guard_coverage import subjects
from .models import utcnow

# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def obligation_identity(risk: Any) -> tuple[str, tuple[str, ...]] | None:
    """What makes two risk wordings ONE obligation, or ``None`` if nothing can.

    The category and the exact set of concrete artifacts the wording names
    (:func:`~neyma_product_driver.guard_coverage.subjects`). Equality, not
    overlap: a wording that names one more artifact is a different subject. A
    wording that names none is its own obligation and never merges.
    """
    named = subjects(str(getattr(risk, "description", "") or ""))
    if not named:
        return None
    category = getattr(getattr(risk, "risk_category", ""), "value", None) or str(
        getattr(risk, "risk_category", "")
    )
    return category, tuple(sorted(named))


def _risk_key(risk: Any) -> str:
    return str(getattr(risk, "key", "") or "")


def group_by_obligation(risks: Sequence[Any]) -> list[list[Any]]:
    """Risks partitioned into obligations, in first-appearance order."""
    groups: list[list[Any]] = []
    index: dict[tuple[str, tuple[str, ...]], int] = {}
    for risk in risks:
        identity = obligation_identity(risk)
        if identity is None:
            groups.append([risk])
            continue
        if identity in index:
            groups[index[identity]].append(risk)
        else:
            index[identity] = len(groups)
            groups.append([risk])
    return groups


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


class GapCorrection(BaseModel):
    """One routing of an obligation to the builder, and what answered it."""

    model_config = ConfigDict(extra="ignore")

    routed_iteration: int = 0
    #: The whole working tree when the gap was routed. The answer is measured
    #: from here, so nothing that existed before the routing can be bound.
    routed_tree: str = ""
    routed_at: str = Field(default_factory=utcnow)
    #: The first different tree the driver judged after routing. Empty while
    #: the builder has not changed anything since.
    answered_tree: str = ""
    #: Collected test files the answer changed → the tests whose expectations
    #: it moved (every test, for a file it added).
    moved: dict[str, list[str]] = Field(default_factory=dict)
    #: Executable checks (probes, mutation batteries) the answer changed.
    scripts: list[str] = Field(default_factory=list)


class BoundExecution(BaseModel):
    """One execution, by this driver, of an obligation's bound tests on one tree."""

    model_config = ConfigDict(extra="ignore")

    tree: str
    command: str
    exit_code: int = -1
    passed: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    #: True when nothing reached an assertion: collection failed, timed out.
    infrastructure: bool = False
    detail: str = ""
    ran_at: str = Field(default_factory=utcnow)


class VerificationGapObligation(BaseModel):
    """One routed obligation, the risk identities it speaks for, and its evidence."""

    model_config = ConfigDict(extra="ignore")

    obligation_id: str
    category: str = ""
    subjects: list[str] = Field(default_factory=list)
    #: The exact risk identities this obligation may discharge. Nothing else.
    risk_keys: list[str] = Field(default_factory=list)
    risk_ids: list[str] = Field(default_factory=list)
    descriptions: list[str] = Field(default_factory=list)
    corrections: list[GapCorrection] = Field(default_factory=list)
    executions: list[BoundExecution] = Field(default_factory=list)

    def bound_tests(self) -> dict[str, list[str]]:
        """Every test an answering change moved, per file, across its corrections."""
        bound: dict[str, list[str]] = {}
        for correction in self.corrections:
            if not correction.answered_tree:
                continue
            for path, names in correction.moved.items():
                kept = bound.setdefault(path, [])
                kept += [n for n in names if n not in kept]
        return bound

    def bound_scripts(self) -> list[str]:
        out: list[str] = []
        for correction in self.corrections:
            if correction.answered_tree:
                out += [s for s in correction.scripts if s not in out]
        return out

    def matches(self, risk: Any) -> bool:
        return _risk_key(risk) in self.risk_keys

    def label(self) -> str:
        return f"{self.obligation_id} ({', '.join(self.risk_ids or self.risk_keys)})"


def load(raw: Sequence[Any]) -> list[VerificationGapObligation]:
    out: list[VerificationGapObligation] = []
    for item in raw or []:
        try:
            out.append(
                item
                if isinstance(item, VerificationGapObligation)
                else VerificationGapObligation.model_validate(item)
            )
        except Exception:  # an unreadable record binds nothing
            continue
    return out


def dump(obligations: Sequence[VerificationGapObligation]) -> list[dict[str, Any]]:
    return [o.model_dump(mode="json") for o in obligations]


def find_obligation(
    obligations: Sequence[VerificationGapObligation], risk: Any
) -> VerificationGapObligation | None:
    for obligation in obligations:
        if obligation.matches(risk):
            return obligation
    return None


# --------------------------------------------------------------------------
# Routing, joining and answering
# --------------------------------------------------------------------------


def obligation_group(risk: Any, register: Sequence[Any]) -> list[Any]:
    """``risk`` and every register entry that is the same obligation."""
    identity = obligation_identity(risk)
    if identity is None:
        return [risk]
    group = [r for r in register if obligation_identity(r) == identity]
    return group if any(_risk_key(r) == _risk_key(risk) for r in group) else [risk, *group]


def route(
    obligations: list[VerificationGapObligation],
    group: Sequence[Any],
    *,
    iteration: int,
    tree: str,
) -> VerificationGapObligation:
    """Record that ``group`` was routed to the builder now, on ``tree``."""
    keys = [_risk_key(r) for r in group if _risk_key(r)]
    existing = next((o for o in obligations if set(keys) & set(o.risk_keys)), None)
    if existing is None:
        identity = obligation_identity(group[0])
        category, named = identity if identity is not None else (
            getattr(getattr(group[0], "risk_category", ""), "value", ""),
            (),
        )
        digest = hashlib.sha256("|".join(sorted(keys)).encode("utf-8")).hexdigest()[:10]
        existing = VerificationGapObligation(
            obligation_id=f"VG-{digest}",
            category=str(category),
            subjects=list(named),
        )
        obligations.append(existing)
    for risk in group:
        key = _risk_key(risk)
        if key and key not in existing.risk_keys:
            existing.risk_keys.append(key)
            existing.risk_ids.append(str(getattr(risk, "id", "") or key))
            existing.descriptions.append(str(getattr(risk, "description", "")))
    last = existing.corrections[-1] if existing.corrections else None
    if last is not None and not last.answered_tree and last.routed_tree == tree:
        # Routed again on the same tree with nothing answered in between: the
        # same open request, not a new correction window.
        return existing
    existing.corrections.append(GapCorrection(routed_iteration=iteration, routed_tree=tree))
    return existing


def adopt_wordings(
    obligations: Sequence[VerificationGapObligation], register: Sequence[Any]
) -> list[str]:
    """Join a later wording of an already-routed obligation to it. Returns notes.

    Only an exact identity match joins, so a wording that names a different
    artifact, or none, stays its own obligation.
    """
    notes: list[str] = []
    for obligation in obligations:
        if not obligation.subjects:
            continue
        identity = (obligation.category, tuple(sorted(obligation.subjects)))
        for risk in register:
            key = _risk_key(risk)
            if key and key not in obligation.risk_keys and obligation_identity(risk) == identity:
                obligation.risk_keys.append(key)
                obligation.risk_ids.append(str(getattr(risk, "id", "") or key))
                obligation.descriptions.append(str(getattr(risk, "description", "")))
                notes.append(
                    f"{getattr(risk, 'id', key)} names the same subjects under the same "
                    f"category as {obligation.obligation_id}; joined as one obligation"
                )
    return notes


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=60, check=False
    )


def answer(
    obligations: Sequence[VerificationGapObligation], repo: Path, tree: str
) -> list[str]:
    """Bind what the builder changed since each open routing. Returns notes.

    A routing is answered by the first different tree the driver judges after
    it. The diff is taken between the two whole-tree objects, so an uncommitted
    edit counts and nothing that existed at routing time can be bound.
    """
    notes: list[str] = []
    repo = Path(repo)
    if not tree:
        return notes
    for obligation in obligations:
        for correction in obligation.corrections:
            if correction.answered_tree or not correction.routed_tree:
                continue
            if correction.routed_tree == tree:
                continue
            names = _git(repo, "diff", "--name-only", correction.routed_tree, tree)
            if names.returncode != 0:
                notes.append(
                    f"{obligation.obligation_id}: could not diff the routed tree "
                    f"{correction.routed_tree[:12]} against {tree[:12]}; nothing bound"
                )
                correction.answered_tree = tree
                continue
            for rel in [ln.strip() for ln in names.stdout.splitlines() if ln.strip()]:
                if not VERIFICATION_FILE.search(rel) or not (repo / rel).is_file():
                    continue
                text = _read(repo / rel)
                added = _git(repo, "cat-file", "-e", f"{correction.routed_tree}:{rel}").returncode != 0
                diff = _git(repo, "diff", correction.routed_tree, tree, "--", rel).stdout
                if _TEST_DEF.search(text):
                    moved = changed_expectations(text, diff, whole_file=added)
                    if moved:
                        correction.moved[rel] = moved
                else:
                    correction.scripts.append(rel)
            correction.answered_tree = tree
            bound = sum(len(v) for v in correction.moved.values())
            notes.append(
                f"{obligation.obligation_id}: the change answering its routing at iteration "
                f"{correction.routed_iteration} bound {bound} test(s)"
                + (f" and {len(correction.scripts)} executable check(s)" if correction.scripts else "")
            )
    return notes


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

_NODE_RESULT = re.compile(r"^(\S+::\S+?)\s+(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\b", re.M)
_INFRA = re.compile(
    r"\b(?:no tests ran|error(?:s)? during collection|ImportError|ModuleNotFoundError|"
    r"INTERNALERROR|unrecognized arguments|file or directory not found|not found:)\b",
    re.I,
)


def bound_nodes(obligation: VerificationGapObligation, repo: Path) -> list[str]:
    """The bound tests that still exist in the tree, as exact node ids."""
    nodes: list[str] = []
    for path, names in obligation.bound_tests().items():
        present = set(_TEST_DEF.findall(_read(Path(repo) / path)))
        nodes += [f"{path}::{n}" for n in names if n in present]
    return nodes


def execute(
    obligation: VerificationGapObligation,
    repo: Path,
    *,
    tree: str,
    runner: str,
    timeout_s: int = 900,
) -> BoundExecution | None:
    """Run the obligation's bound tests, by node id, on ``tree``. Once per tree.

    Read-only on the repository: no cache provider, no bytecode. ``None`` when
    nothing is bound or no runner is known — which leaves the obligation open.
    """
    from .command_guard import classify_command

    nodes = bound_nodes(obligation, repo)
    if not nodes or not runner or not tree:
        return None
    command = f"{runner} {' '.join(nodes)} -v -p no:cacheprovider"
    for previous in obligation.executions:
        if previous.tree == tree and previous.command == command:
            return previous
    refusal = classify_command(command)
    if refusal:
        execution = BoundExecution(
            tree=tree, command=command, infrastructure=True,
            detail=f"the command guard refuses this command: {refusal}",
        )
        obligation.executions.append(execution)
        return execution
    import shlex

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        proc = subprocess.run(
            shlex.split(command), cwd=str(repo), capture_output=True, text=True,
            timeout=timeout_s, check=False, env=env,
        )
        output = f"{proc.stdout}\n{proc.stderr}"
        results: dict[str, str] = {}
        for node, verdict in _NODE_RESULT.findall(proc.stdout):
            results[node] = verdict
        execution = BoundExecution(
            tree=tree,
            command=command,
            exit_code=proc.returncode,
            passed=[n for n in nodes if results.get(n) == "PASSED"],
            failed=[n for n in nodes if results.get(n) in ("FAILED", "ERROR", "XPASS")],
            skipped=[n for n in nodes if results.get(n) in ("SKIPPED", "XFAIL")],
            infrastructure=(proc.returncode != 0 and not results and bool(_INFRA.search(output))),
            detail="\n".join(ln for ln in output.splitlines() if ln.strip())[-1500:],
        )
    except subprocess.TimeoutExpired:
        execution = BoundExecution(
            tree=tree, command=command, infrastructure=True, detail=f"timed out after {timeout_s}s"
        )
    except (OSError, ValueError) as exc:
        execution = BoundExecution(
            tree=tree, command=command, infrastructure=True, detail=f"{type(exc).__name__}: {exc}"
        )
    obligation.executions.append(execution)
    return execution


# --------------------------------------------------------------------------
# What the evidence is worth
# --------------------------------------------------------------------------


class BoundVerdict(BaseModel):
    """Whether an obligation's bound, executed guard discharges it on one tree."""

    model_config = ConfigDict(extra="forbid")

    obligation_id: str = ""
    discharges: bool = False
    #: A bound guard ran on the judged tree and REFUSED: the risk is realised.
    refuted: bool = False
    reason: str = ""
    #: The bound guards that carry the discharge, as node ids.
    guards: list[str] = Field(default_factory=list)
    #: The discrimination evidence observed for them.
    discrimination: list[str] = Field(default_factory=list)
    command: str = ""
    tree: str = ""

    def citation(self) -> str:
        return (
            f"{', '.join(self.guards)} via `{self.command}` on tree {self.tree[:12]}"
            + (f" (discrimination: {', '.join(self.discrimination)})" if self.discrimination else "")
        )


def _body(text: str, name: str) -> str:
    lines = (text or "").splitlines()
    for test, start, end in _test_spans(text):
        if test == name:
            return "\n".join(lines[start:end])  # the body, without the def line
    return ""


def bound_evidence(
    obligation: VerificationGapObligation,
    repo: Path,
    *,
    judged_tree: str,
    changed_verification: Any = None,
) -> BoundVerdict:
    """The whole rule, and every "no" it can give, each as its own sentence."""
    verdict = BoundVerdict(obligation_id=obligation.obligation_id, tree=judged_tree)
    repo = Path(repo)
    routed = [c for c in obligation.corrections]
    if not any(c.answered_tree for c in routed):
        verdict.reason = (
            f"verification-gap obligation {obligation.obligation_id} was routed to the builder "
            "and no change has answered it yet"
        )
        return verdict
    bound = obligation.bound_tests()
    if not bound:
        verdict.reason = (
            f"the change answering verification-gap obligation {obligation.obligation_id} moved "
            "no test, so no guard is bound to it"
        )
        return verdict
    if not judged_tree:
        verdict.reason = "the judged tree is unknown, so no execution can be evidence about it"
        return verdict
    here = [e for e in obligation.executions if e.tree == judged_tree]
    if not here:
        elsewhere = [e.tree[:12] for e in obligation.executions]
        verdict.reason = (
            f"the guard bound to {obligation.obligation_id} has not been executed by this "
            f"driver on the judged tree {judged_tree[:12]}"
            + (
                f"; its only executions were on tree(s) {', '.join(elsewhere)}, which are not "
                "evidence about this one"
                if elsewhere
                else ""
            )
        )
        return verdict
    execution = here[-1]
    verdict.command = execution.command
    nodes = bound_nodes(obligation, repo)
    if execution.infrastructure:
        verdict.reason = (
            f"the guard bound to {obligation.obligation_id} could not be executed here: "
            f"{execution.detail[-200:]}"
        )
        return verdict
    if execution.failed:
        verdict.refuted = True
        verdict.reason = (
            f"the guard bound to {obligation.obligation_id} REFUSED on the judged tree: "
            + ", ".join(execution.failed[:4])
        )
        return verdict
    unreported = [n for n in nodes if n not in execution.passed]
    if unreported:
        verdict.reason = (
            f"bound test(s) of {obligation.obligation_id} did not pass on the judged tree "
            "(skipped, or never reported): " + ", ".join(unreported[:4])
        )
        return verdict

    by_file: dict[str, str] = {path: _read(repo / path) for path in bound}
    controls = {
        n for n in nodes if _matching([n.split("::", 1)[1]], DISCRIMINATION_NAME_MARKERS)
    }
    guards = [n for n in nodes if n not in controls]
    if not guards:
        verdict.reason = (
            f"the change answering {obligation.obligation_id} bound only control cases and no "
            "guard"
        )
        return verdict

    # A battery bound in the same answer, executed by this driver on this tree
    # through its declared invocation, that passed and names the guard's node.
    batteries: list[tuple[str, str]] = []
    if changed_verification is not None and getattr(changed_verification, "tree", "") == judged_tree:
        passed_scripts = {
            r.target.path
            for r in getattr(changed_verification, "results", None) or []
            if r.passed and not r.infrastructure
        }
        for script in obligation.bound_scripts():
            if script in passed_scripts:
                batteries.append((script, _read(repo / script)))

    for guard in guards:
        path, name = guard.split("::", 1)
        evidence: list[str] = []
        for control in sorted(controls):
            c_path, c_name = control.split("::", 1)
            if c_path == path and re.search(rf"\b{re.escape(name)}\b", _body(by_file[path], c_name)):
                evidence.append(control)
        for script, text in batteries:
            if guard in text:
                evidence.append(f"{script} (mutation battery naming {guard})")
        if evidence:
            verdict.guards.append(guard)
            verdict.discrimination += [e for e in evidence if e not in verdict.discrimination]
    if not verdict.guards:
        verdict.reason = (
            f"the guard(s) bound to {obligation.obligation_id} pass on the judged tree, but no "
            "control bound in the same answer was observed to realise the forbidden behaviour "
            "against them — a control test that invokes the guard, or a mutation battery naming "
            "its node id — so a green here is not yet discriminating: "
            + ", ".join(guards[:3])
        )
        return verdict
    verdict.discharges = True
    return verdict


__all__ = [
    "BoundExecution",
    "BoundVerdict",
    "GapCorrection",
    "VerificationGapObligation",
    "adopt_wordings",
    "answer",
    "bound_evidence",
    "bound_nodes",
    "dump",
    "execute",
    "find_obligation",
    "group_by_obligation",
    "load",
    "obligation_group",
    "obligation_identity",
    "route",
]
