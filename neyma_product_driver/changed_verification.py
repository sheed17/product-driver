"""Operating the verification surface a change CHANGED.

Two different questions look alike and are not.
:mod:`~neyma_product_driver.repo_verification` asks "does the repository's own
standing verification still hold about the KIND of surface this change touched" —
a guard the change did not write, run to catch collateral damage. This module
asks the other one: **when the thing that changed IS a guard, was that guard ever
operated?**

The failure it exists for. A task's whole deliverable was two boundary guards: a
corrected oracle, and a new reconciliation guard over a status document. The
builder changed exactly those two files and reported both green. Product Driver
executed one permanent regression scenario for an unrelated unit, the evaluator
correctly refused to accept a builder's self-report as an observation, and the
run stopped and asked the founder to relay two measurements that were a single
command each, already present in the repository, and passing. Nothing was wrong
with the product, the evaluator, or the gate. The driver had simply never asked
the changed guard to run.

The rules that follow from that, and that this module holds:

* **a changed guard is a risk surface.** Verification is matched to what the diff
  changed, never to what a permanent scenario happens to cover. An unrelated
  passing scenario is not an observation of a changed guard, however green;
* **self-report is not observation.** The only thing that counts here is a
  command this driver ran and read the exit status of;
* **the repository's own measurement is preferred to a generated one.** A guard
  already in the tree is executed as it is. Nothing here writes, generates or
  approximates a test — a driver that replaces a real guard with its own
  approximation has stopped measuring the repository;
* **a negative guard needs discrimination evidence when its semantics changed.** A
  guard that asserts absence passes vacuously when it stops looking. When the
  diff changes what such a guard asserts, this looks for the repository's own
  anti-vacuity case — a test that proves the guard can still fire — and reports
  its absence as a gap rather than pretending the green is meaningful;
* **a gap is not a pass and not a defect.** A guard that cannot be executed here
  blocks the CLAIM that the change was verified. It is routed as the smallest
  verification work, to the builder, under the repository's own authority;
* **it does not run the suite.** Only the changed guards themselves, plus the few
  guards the repository already keeps over the other files the diff touched, and
  both are capped. A driver that runs everything has discovered nothing.

Nothing here knows the name of a product, a phase, a unit, a criterion or a
test. Every signal is read from the diff and from the repository's own file
names, and nothing is written into the repository being verified.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .guard_coverage import MIN_TOKEN, normalise, subjects
from .models import utcnow
from .repo_verification import (
    _VENDOR,
    VerificationResult,
    VerificationTarget,
    detect_test_runner,
    discover_record_guards,
    run_verification,
)

# --------------------------------------------------------------------------
# What counts as a verification file
# --------------------------------------------------------------------------

#: A file whose job is to measure something. The first alternative is the
#: universal test-file convention; the rest are the names repositories give the
#: executable checks that are not collected as tests — probes, oracles, guards,
#: mutation batteries. Read from the path only, so a module is never classified
#: by what a task said about it.
VERIFICATION_FILE = re.compile(
    r"(?:^|/)(?:test_[^/]+|[^/]+_test)\.py$"
    r"|(?:^|/)(?:probe|oracle|guard|check|checks|verify|verification|mutate|mutation|"
    r"assert|invariant)[_-][^/]*\.py$"
    r"|(?:^|/)[^/]*[_-](?:probe|probes|oracle|oracles|guard|guards|check|checks|"
    r"verification|mutation|invariants?)\.py$",
    re.I,
)

#: A test NAME that asserts something must NOT be there. These are the guards
#: that pass vacuously the moment they stop looking, which is why a change to
#: what one of them asserts earns a second question the positive ones do not.
NEGATIVE_NAME_MARKERS: tuple[str, ...] = (
    "no_",
    "not_",
    "never",
    "refus",
    "reject",
    "forbid",
    "denied",
    "deny",
    "cannot",
    "must_not",
    "absent",
    "empty",
    "dark",
    "drift",
    "unchanged",
    "does_not",
    "blocked",
    "without",
    "outside",
    "second_authority",
)

#: A test NAME that is itself the anti-vacuity control: it proves the guard can
#: still fire, that the population it reads is not empty, that the detector
#: catches the thing it is there to catch. This is the repository saying "and
#: here is why the green above means something".
DISCRIMINATION_NAME_MARKERS: tuple[str, ...] = (
    "catch",
    "catches",
    "can_fire",
    "fires",
    "detector",
    "detects",
    "non_empty",
    "nonempty",
    "populated",
    "discriminat",
    "mutant",
    "mutation",
    "mutate",
    "negative_control",
    "anti_vacuity",
    "antivacuity",
    "would_fail",
    "turns_red",
    "sanity",
    "control",
    "forced",
    "and_catches",
)

#: A line that states an expectation. Used on the DIFF, to answer "did this
#: change what the guard asserts?" — which is a different question from "did it
#: touch the file", and the only one that earns a discrimination demand.
ASSERTION_LINE = re.compile(
    r"\bassert\b|\bpytest\.raises\b|\braises\s*\(|\bassert(?:Equal|True|False|Raises|In|Is)\b"
    r"|\bexpect\w*\s*\(|\bfail\s*\(|\bmust_\w+|\bshould_\w+",
    re.I,
)

_TEST_DEF = re.compile(r"def\s+(test_\w+)")


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


#: The structural property a test measures when it walks import statements.
IMPORT_REACHABILITY = "import_reachability"
_IMPORT_NODES = frozenset({"Import", "ImportFrom"})
_MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


class GuardTestMeasure(BaseModel):
    """What ONE test this change moved measures, read from its own body.

    Per test, not per file: a file whose one test scans imports and whose
    other test names a module has not thereby measured that module's imports.
    """

    model_config = ConfigDict(extra="ignore")

    name: str
    #: Concrete artifacts the test body names (its docstring and name excluded).
    subjects: list[str] = Field(default_factory=list)
    #: Structural properties its body measures, e.g. :data:`IMPORT_REACHABILITY`.
    properties: list[str] = Field(default_factory=list)
    #: Whether the test asserts that its own scan FOUND something — the control
    #: that separates "nothing is there" from "the scan stopped looking".
    positive_control: bool = False


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _mentions_import_nodes(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in _IMPORT_NODES:
            return True
        if isinstance(child, ast.Name) and child.id in _IMPORT_NODES:
            return True
    return False


def measure_test(function: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> GuardTestMeasure:
    """Read one test function's own body for what it structurally measures."""
    body = list(function.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
        body = body[1:]  # the docstring is intention, not measurement
    text = "\n".join(ast.get_source_segment(source, statement) or "" for statement in body)
    measure = GuardTestMeasure(name=function.name, subjects=subjects(text))
    # Names the import scan populates: anything appended/added/assigned under a
    # branch or comprehension that inspects import nodes.
    scanned: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, (ast.If, ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            if not _mentions_import_nodes(node):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                    if inner.func.attr in {"append", "add", "extend", "update"} and isinstance(
                        inner.func.value, ast.Name
                    ):
                        scanned.add(inner.func.value.id)
        if isinstance(node, ast.Assign) and _mentions_import_nodes(node.value):
            for target in node.targets:
                scanned |= _names_in(target)
    if _mentions_import_nodes(function):
        measure.properties.append(IMPORT_REACHABILITY)
        # An import scan names the modules it looks for as bare strings —
        # "rule", "pkg.rule", "rule.py" — which are exactly its subjects.
        for node in ast.walk(function):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.strip()
                if _MODULE_NAME.fullmatch(text):
                    name = normalise(text[:-3] if text.endswith(".py") else text.rsplit(".", 1)[-1])
                    if len(name) >= MIN_TOKEN and name not in measure.subjects:
                        measure.subjects.append(name)
    for node in ast.walk(function):
        if not isinstance(node, ast.Assert) or not isinstance(node.test, ast.Compare):
            continue
        compare = node.test
        if any(isinstance(op, ast.In) for op in compare.ops):
            if any(_names_in(c) & scanned for c in compare.comparators):
                measure.positive_control = True
        if (
            isinstance(compare.left, ast.Call)
            and getattr(compare.left.func, "id", "") == "len"
            and _names_in(compare.left) & scanned
            and any(isinstance(op, (ast.Gt, ast.GtE)) for op in compare.ops)
        ):
            measure.positive_control = True
    return measure


def measure_tests(text: str, names: Sequence[str]) -> list[GuardTestMeasure]:
    """Per-test measurements for ``names`` in one verification file."""
    wanted = set(names or ())
    if not wanted:
        return []
    try:
        tree = ast.parse(text or "")
    except (SyntaxError, ValueError, RecursionError):
        return []
    out: list[GuardTestMeasure] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            if node.name in seen:
                continue
            seen.add(node.name)
            out.append(measure_test(node, text))
    return out


class ChangedGuard(BaseModel):
    """One piece of verification this diff changed, and what it is.

    ``relation`` separates the two ways a guard earns a run. ``changed`` means
    the diff edited or added this file — the deliverable itself. ``related``
    means the repository already keeps this guard over another file the diff
    changed, which is the narrow collateral set, not the suite.
    """

    model_config = ConfigDict(extra="ignore")

    target: VerificationTarget
    relation: str = "changed"
    #: The file did not exist before this change.
    added: bool = False
    #: Every test the file declares, by name, as it stands now.
    test_names: list[str] = Field(default_factory=list)
    #: The subset that asserts something must NOT be present.
    negative_names: list[str] = Field(default_factory=list)
    #: The subset that proves the guard can still fire.
    discrimination_names: list[str] = Field(default_factory=list)
    #: The tests whose own expectations this diff moved. Per test, not per file:
    #: adding a case to a suite that happens to contain an unrelated
    #: absence-asserting test does not change what that test asserts, and a
    #: file-level answer demanded discrimination work nobody owed.
    changed_expectation_names: list[str] = Field(default_factory=list)
    #: The concrete repository artifacts the assertions THIS CHANGE MOVED were
    #: read to operate on — paths, declared constants, field and type names,
    #: taken from the source of those tests and not from any name. This is what
    #: lets a risk-coverage ledger ask "does this guard measure that risk?"
    #: without guessing, and it is recorded here so the answer survives a resume
    #: rather than being re-derived from a tree that has since moved on. See
    #: :mod:`~neyma_product_driver.guard_coverage`.
    measured_subjects: list[str] = Field(default_factory=list)
    #: The same question asked per moved test, with the structural property each
    #: one measures and whether it carries its own positive control. See
    #: :mod:`~neyma_product_driver.risk_grounding`.
    test_measures: list[GuardTestMeasure] = Field(default_factory=list)
    #: Where the command came from when this is an executable script rather
    #: than a collected test file: the exact invocation a human declared for it,
    #: and the declaration's own location. Empty for a test file, which runs
    #: under the repository's test runner. Never composed by this driver.
    declared_by: str = ""
    #: The declared invocation's own time allowance, when it states one.
    declared_timeout_s: int = 0

    @property
    def path(self) -> str:
        return self.target.path

    @property
    def semantics_changed(self) -> bool:
        """Did this change move what the file asserts, rather than how it reads?"""
        return bool(self.changed_expectation_names)

    @property
    def is_negative_guard(self) -> bool:
        return bool(self.negative_names)

    @property
    def changed_negative_names(self) -> list[str]:
        """Absence-asserting tests this change actually rewrote."""
        moved = set(self.changed_expectation_names)
        return [n for n in self.negative_names if n in moved]

    @property
    def needs_discrimination(self) -> bool:
        """A negative guard whose own semantics this change moved.

        Both halves are required. A negative guard nobody touched is the
        repository's standing business; a positive assertion that changed proves
        itself by passing. It is the combination — "this change altered what an
        absence-asserting guard asserts" — that a green run cannot speak for.
        """
        return bool(self.changed_negative_names)

    def brief(self) -> str:
        bits = [f"{self.path} ({self.relation}"]
        if self.added:
            bits.append(", new")
        if self.semantics_changed:
            bits.append(", assertions changed")
        bits.append(")")
        return "".join(bits)


class ChangedSurfaceVerification(BaseModel):
    """Whether the verification this change changed was actually operated."""

    model_config = ConfigDict(extra="ignore")

    #: Verification files the diff changed. Empty means this did not apply,
    #: which is the ordinary case and is not a gap.
    changed_paths: list[str] = Field(default_factory=list)
    #: Other files the diff changed, for which the repository's own guards were
    #: consulted. Recorded so "why did that run" is answerable.
    related_paths: list[str] = Field(default_factory=list)
    guards: list[ChangedGuard] = Field(default_factory=list)
    results: list[VerificationResult] = Field(default_factory=list)
    #: Changed negative guards whose semantics moved and for which the
    #: repository declares no case proving they can still fire.
    discrimination_gaps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    commit: str = ""
    ran_at: str = Field(default_factory=utcnow)
    #: The driver's own structural reachability scan of every product module
    #: the diff changed, on :attr:`commit`. Independent of whether any guard
    #: changed, so it is recorded even when :attr:`applicable` is False. See
    #: :func:`~neyma_product_driver.risk_grounding.measure_reachability`.
    module_reachability: list[Any] = Field(default_factory=list)
    #: The product modules the TASK changed, from the run's own base: what a
    #: risk is grounded against. Stable across iterations — a later commit that
    #: only touches verification does not remove a module the task built —
    #: while :attr:`module_reachability` is always a measurement of
    #: :attr:`commit`. Subject identity from the task; result from this tree.
    task_modules: list[str] = Field(default_factory=list)
    #: The whole working tree these results were read from, untracked files
    #: included (:func:`~neyma_product_driver.worktree_state.worktree_tree_hash`).
    #: A commit is not enough: an uncommitted edit leaves it unchanged. A result
    #: carries forward only onto the identical tree, and never onto another.
    tree: str = ""
    #: The changed-file set the selection was made from, so a resumed run can
    #: tell "the same tree and the same diff" from anything else.
    diff_fingerprint: str = ""
    #: Changed verification files this driver will not execute, each with why:
    #: an executable script for which no exact invocation is declared anywhere
    #: this driver may read. A gap, stated as one — never a pass, never a
    #: composed command.
    undeclared: dict[str, str] = Field(default_factory=dict)
    #: Changed verification files there is nothing to operate for — a deleted
    #: guard. Not a gap and not an observation.
    not_applicable: dict[str, str] = Field(default_factory=dict)
    #: The paths each bounded pass executed, in order. ``max_changed_guards`` is
    #: a bound on ONE pass, so a diff with more changed verification than that
    #: is operated across several, and this is how that is audited.
    passes: list[list[str]] = Field(default_factory=list)

    # -- what happened ----------------------------------------------------

    @property
    def applicable(self) -> bool:
        return bool(self.changed_paths)

    @property
    def executed_paths(self) -> list[str]:
        return [r.target.path for r in self.results]

    @property
    def product_failures(self) -> list[VerificationResult]:
        """Guards that ran and refused. A finding about the work, every time."""
        return [r for r in self.results if not r.passed and not r.infrastructure]

    @property
    def unexecutable(self) -> list[VerificationResult]:
        """Guards that never reached an assertion. A fact about this environment."""
        return [r for r in self.results if r.infrastructure]

    @property
    def pending(self) -> list[ChangedGuard]:
        """Selected guards with no result yet — nothing has observed these."""
        done = set(self.executed_paths)
        return [g for g in self.guards if g.path not in done]

    @property
    def unobserved_changed_paths(self) -> list[str]:
        """Changed verification files this run holds no direct observation of."""
        passed = {r.target.path for r in self.results}
        return [
            p for p in self.changed_paths if p not in passed and p not in self.not_applicable
        ]

    def path_status(self) -> dict[str, str]:
        """Every changed verification path, and which of the four things it is.

        ``OBSERVED`` — a command this driver ran reached an assertion (pass or
        refusal); ``UNEXECUTABLE`` — it could not be run here, or no invocation
        for it is declared; ``NOT_APPLICABLE`` — there is nothing to operate;
        ``PENDING`` — selected and not yet reached by a bounded pass. Only the
        last one is a state the obligation may not rest in.
        """
        observed = {r.target.path for r in self.results if not r.infrastructure}
        broken = {r.target.path for r in self.results if r.infrastructure}
        status: dict[str, str] = {}
        for path in self.changed_paths:
            if path in observed:
                status[path] = "OBSERVED"
            elif path in self.not_applicable:
                status[path] = "NOT_APPLICABLE"
            elif path in broken or path in self.undeclared:
                status[path] = "UNEXECUTABLE"
            else:
                status[path] = "PENDING"
        return status

    @property
    def blocks_acceptance(self) -> bool:
        """Only a real refusal refuses the work."""
        return bool(self.product_failures)

    @property
    def blocks_claim(self) -> bool:
        """Whether "the changed verification was observed" may be said at all.

        A failure, a guard that could not be run, a changed guard nothing ran,
        and a changed negative guard with no anti-vacuity case all land here.
        They are different sentences and the report prints them as different
        sentences, but none of them supports the claim.
        """
        return bool(
            self.product_failures
            or self.unexecutable
            or self.pending
            or self.unobserved_changed_paths
            or self.discrimination_gaps
        )

    @property
    def gap_reasons(self) -> list[str]:
        """Why the claim cannot be made, when no guard actually failed."""
        reasons: list[str] = []
        for result in self.unexecutable:
            reasons.append(
                f"{result.target.path} could not be executed here: {result.detail[:200]}"
            )
        for guard in self.pending:
            reasons.append(f"{guard.path} was selected for verification and never ran")
        for path, why in self.undeclared.items():
            if path in self.changed_paths:
                reasons.append(why)
        for path in self.unobserved_changed_paths:
            if path in self.undeclared:
                continue
            if path not in [g.path for g in self.pending] and path not in [
                r.target.path for r in self.results
            ]:
                reasons.append(
                    f"{path} is a verification file this change edited and no command "
                    "this driver ran exercised it"
                )
        reasons += list(self.discrimination_gaps)
        return reasons

    # -- how it reads -----------------------------------------------------

    def direct_observations(self) -> list[str]:
        """The observations, in the only form that counts: commands that ran."""
        lines: list[str] = []
        for result in self.results:
            guard = next((g for g in self.guards if g.path == result.target.path), None)
            verdict = "PASS" if result.passed else ("COULD NOT RUN" if result.infrastructure else "FAIL")
            line = f"{result.target.path}: {verdict} via `{result.target.command}`"
            if guard is not None and guard.discrimination_names:
                line += (
                    " — including its own discrimination case(s): "
                    + ", ".join(guard.discrimination_names[:4])
                )
            if not result.passed:
                line += f" — {result.detail[:300]}"
            lines.append(line)
        return lines

    def headline(self) -> str:
        if not self.applicable:
            return "not applicable — this change edited no verification file"
        if self.product_failures:
            first = self.product_failures[0]
            return (
                f"verification this change CHANGED fails on this tree: "
                f"{first.target.path} ({first.detail[:160]})"
            )
        if self.unexecutable:
            first = self.unexecutable[0]
            return (
                f"verification this change CHANGED could not be executed here: "
                f"{first.target.path} ({first.detail[:160]})"
            )
        if self.pending or self.unobserved_changed_paths:
            missing = self.pending and [g.path for g in self.pending] or self.unobserved_changed_paths
            if not self.pending and all(p in self.undeclared for p in missing):
                return (
                    "verification this change CHANGED has no declared invocation, so this "
                    "driver cannot operate it: " + ", ".join(missing[:4])
                )
            return (
                "verification this change CHANGED has not been operated: "
                + ", ".join(missing[:4])
            )
        if self.discrimination_gaps:
            return (
                f"{len(self.results)} changed guard(s) pass, and "
                f"{len(self.discrimination_gaps)} changed negative guard(s) declare no case "
                "proving they can still fire"
            )
        return (
            f"{len(self.results)} guard(s) over the verification this change changed "
            "pass on this tree, directly observed"
        )

    def summary_block(self) -> str:
        lines = [f"CHANGED-VERIFICATION: {self.headline()}"]
        for guard in self.guards:
            lines.append(f"  selected: {guard.brief()} — {'; '.join(guard.target.why[:2])}")
        for observation in self.direct_observations():
            lines.append(f"  observed: {observation}")
        for gap in self.discrimination_gaps:
            lines.append(f"  gap: {gap}")
        for why in self.undeclared.values():
            lines.append(f"  gap: {why}")
        for number, executed in enumerate(self.passes, start=1):
            lines.append(f"  pass {number}: {', '.join(executed) or 'nothing new to run'}")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Reading the diff
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str, timeout: int = 60) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _is_tracked(repo: Path, rel: str) -> bool:
    return bool(_git(repo, "ls-files", "--error-unmatch", "--", rel).strip())


def path_diff(repo: Path, rel: str, base_commit: str = "") -> str:
    """The change to one file, as added and removed lines.

    ``base_commit`` matters. A builder that commits its work leaves a clean
    tree, so diffing the tree alone would report that a file the run rewrote was
    never touched. An untracked file has no diff at all, so its content IS the
    addition.
    """
    repo = Path(repo)
    if not _is_tracked(repo, rel):
        text = ""
        try:
            path = repo / rel
            if path.is_file():
                text = path.read_text(errors="replace")
        except OSError:
            text = ""
        return "\n".join(f"+{line}" for line in text.splitlines())
    attempts: list[tuple[str, ...]] = []
    if base_commit:
        attempts.append(("diff", base_commit, "--", rel))
    attempts += [("diff", "HEAD", "--", rel), ("diff", "--", rel)]
    for args in attempts:
        out = _git(repo, *args)
        if out.strip():
            return out
    return ""


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _expectation_lines(diff_text: str) -> list[int]:
    """Line numbers, in the file as it now stands, where an expectation moved.

    Only added and removed lines are read, and the file headers are skipped, so
    a rename or a moved import is not mistaken for a moved expectation. A removed
    line has no line number of its own in the new file; it is attributed to the
    position the hunk had reached, which is where its replacement sits.
    """
    lines: list[int] = []
    cursor = 0
    for raw in (diff_text or "").splitlines():
        hunk = _HUNK.match(raw)
        if hunk:
            cursor = int(hunk.group(1))
            continue
        if raw.startswith(("+++", "---", "diff ", "index ")):
            continue
        if not raw:
            continue
        mark, body = raw[0], raw[1:]
        if mark == "+":
            if ASSERTION_LINE.search(body):
                lines.append(cursor)
            cursor += 1
        elif mark == "-":
            if ASSERTION_LINE.search(body):
                lines.append(cursor)
        else:
            cursor += 1
    return lines


def _test_spans(text: str) -> list[tuple[str, int, int]]:
    """Each test's name and the line range it occupies, as the file now stands.

    Crude on purpose — a definition runs until the next definition or class at
    the same indentation. Attribution has to be conservative rather than clever:
    the question it answers is "did this change touch what THIS test asserts",
    and a wrong answer either demands work nobody owes or excuses work someone
    does.
    """
    lines = (text or "").splitlines()
    starts: list[tuple[str, int, int]] = []
    for number, line in enumerate(lines, start=1):
        match = re.match(r"^(\s*)def\s+(test_\w+)", line)
        if match:
            starts.append((match.group(2), number, len(match.group(1))))
    spans: list[tuple[str, int, int]] = []
    for index, (name, start, indent) in enumerate(starts):
        end = len(lines)
        for number, line in enumerate(lines[start:], start=start + 1):
            if not line.strip():
                continue
            leading = len(line) - len(line.lstrip())
            if leading <= indent and re.match(r"^\s*(?:def|class|@)\s*\w", line):
                end = number - 1
                break
        if index + 1 < len(starts):
            end = min(end, starts[index + 1][1] - 1)
        spans.append((name, start, end))
    return spans


def changed_expectations(text: str, diff_text: str, *, whole_file: bool = False) -> list[str]:
    """Which tests in this file had what they assert changed by this diff.

    ``whole_file`` is for a file the diff ADDED: every test in it is new, so
    every one of them states something that was not stated before.
    """
    names = _TEST_DEF.findall(text or "")
    if whole_file:
        return names
    touched = _expectation_lines(diff_text)
    if not touched:
        return []
    changed: list[str] = []
    for name, start, end in _test_spans(text):
        if any(start <= line <= end for line in touched) and name not in changed:
            changed.append(name)
    if not changed and touched:
        # An expectation moved somewhere this crude attribution could not place —
        # a module-level helper every test reads, say. The file's semantics moved;
        # which test's, it will not guess.
        return names
    return changed


_MODULE_DOCSTRING = re.compile(r"\A(?:#[^\n]*\n|\s*\n)*(?:[rubRUB]{0,2})('''|\"\"\")(?:.|\n)*?\1")
_DECLARATION = re.compile(r"^\s*(?:@|def\s|class\s|async\s+def\s)")


def assertion_span(text: str, changed_names: Sequence[str]) -> str:
    """The source THIS CHANGE's own assertions read, and nothing else in the file.

    What a guard measures is a question about code, and this is the code that
    question is entitled to be asked about:

    * the bodies of the tests whose expectations this diff moved, and the
      module-level constants and helpers every one of them reads. A guard file
      often carries tests this change never touched, and letting those speak
      would let a diff borrow the subject matter of assertions it did not write;
    * **not** the module docstring. Prose about what a guard is for is an
      author's intention, and intention is exactly the thing this path refuses
      to accept in place of a measurement;
    * **not** ``def``, ``class`` or decorator lines. A test's NAME is a label.
      A rule that read names would be a rule about naming conventions, which is
      the mistake this whole module is the correction for.
    """
    body = _MODULE_DOCSTRING.sub("", text or "", count=1)
    wanted = set(changed_names or ())
    skip: set[int] = set()
    for name, start, end in _test_spans(body):
        if name not in wanted:
            skip.update(range(start, end + 1))
    kept = [
        line
        for number, line in enumerate(body.splitlines(), start=1)
        if number not in skip and not _DECLARATION.match(line)
    ]
    return "\n".join(kept)


def _read(path: Path, limit: int = 400_000) -> str:
    try:
        if not path.is_file():
            return ""
        return path.read_text(errors="replace")[:limit]
    except OSError:
        return ""


def _matching(names: Sequence[str], markers: Sequence[str]) -> list[str]:
    lowered = [(n, n.lower()) for n in names]
    return [n for n, low in lowered if any(m in low for m in markers)]


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def changed_verification_paths(diff_files: Sequence[str]) -> list[str]:
    """The verification files in a diff, in the diff's own order.

    Path-only, and deliberately so: whether a file measures something is a fact
    about the repository's own naming, not about what a task description claimed
    the change was for.
    """
    seen: list[str] = []
    for raw in diff_files:
        rel = str(raw).strip()
        if not rel or _VENDOR.search(rel):
            continue
        if VERIFICATION_FILE.search(rel) and rel not in seen:
            seen.append(rel)
    return seen


def analyse_guard(
    repo: Path,
    rel: str,
    *,
    base_commit: str = "",
    runner: str,
    relation: str = "changed",
) -> ChangedGuard | None:
    """What one changed verification file is, and how the repository runs it.

    ``None`` when the file is gone (a deleted guard is not run) or declares no
    test this runner would collect — a probe script is executed by the
    repository's own entry point, not by guessing one.
    """
    repo = Path(repo)
    text = _read(repo / rel)
    if not text:
        return None
    names = _TEST_DEF.findall(text)
    if not names:
        return None
    diff_text = path_diff(repo, rel, base_commit) if relation == "changed" else ""
    added = relation == "changed" and not _is_tracked(repo, rel)
    negative = _matching(names, NEGATIVE_NAME_MARKERS)
    discrimination = _matching(names, DISCRIMINATION_NAME_MARKERS)
    moved = (
        changed_expectations(text, diff_text, whole_file=added)
        if relation == "changed"
        else []
    )
    why: list[str] = []
    if relation == "changed":
        why.append(
            "this change "
            + ("added" if added else "edited")
            + " this verification file; it is the deliverable, not collateral"
        )
    if negative:
        why.append(
            f"{len(negative)} of {len(names)} tests here assert an absence: "
            + ", ".join(negative[:3])
        )
    return ChangedGuard(
        target=VerificationTarget(
            path=rel,
            # `-rf` so a refusal's own message lands in the tail the result is
            # read from, and `-p no:cacheprovider` so reading the repository's
            # answer never writes a cache directory into the tree being read.
            command=f"{runner} {rel} -q -rf -p no:cacheprovider",
            surface="the verification this change changed",
            why=why,
            score=len(names),
        ),
        relation=relation,
        added=added,
        test_names=names,
        negative_names=negative,
        discrimination_names=discrimination,
        changed_expectation_names=moved,
        # The guard's own path is barred from its side of the comparison. A
        # driver that accepted `test_status_reconciliation.py` as evidence about
        # "status reconciliation" would be reading a label, which is the one
        # thing every rule here refuses.
        measured_subjects=subjects(assertion_span(text, moved), exclude=[rel]),
        test_measures=measure_tests(text, moved),
    )


# --------------------------------------------------------------------------
# Declared invocations: how a changed SCRIPT may be operated at all
# --------------------------------------------------------------------------


class DeclaredInvocation(BaseModel):
    """One exact command a human already wrote down for one verification script.

    The only way a probe or battery that declares no collected test is ever run
    here. Its arguments are the declaration's, character for character; the one
    thing ever substituted is a CI line's bare ``python`` for the repository's
    own interpreter, which is the environment the line names and not an argument.
    """

    model_config = ConfigDict(extra="ignore")

    command: str
    #: The script the command runs, repository-relative.
    script: str
    #: The exit status the declaration says this invocation must produce.
    expect_exit_code: int = 0
    timeout_s: int = 0
    #: Where it was declared, for the record: a scenario step, or a CI file.
    source: str = ""

    @property
    def bare(self) -> bool:
        """Runs the script with no arguments — its own whole-run default."""
        tokens = _tokens(self.command)
        return bool(tokens) and tokens[-1] == self.script


#: Shell syntax a declared command may not carry to be run here. Such a line is
#: a pipeline or a redirection, and its exit status is not the script's.
_SHELL = re.compile(r"[|;&<>`$\\]")


def _tokens(command: str) -> list[str]:
    import shlex

    try:
        return shlex.split(command)
    except ValueError:
        return []


def _script_of(command: str) -> str:
    """The ``*.py`` a single-program command runs, or ``""``.

    Only ``<interpreter> path/to/script.py [args...]`` qualifies. ``-m``,
    ``-c``, a pipeline, a redirection or a chained command does not: in each of
    those the exit status read back is not the script's own.
    """
    if not command or _SHELL.search(command):
        return ""
    tokens = _tokens(command)
    if len(tokens) < 2 or "python" not in Path(tokens[0]).name:
        return ""
    script = tokens[1]
    if not script.endswith(".py") or script.startswith("-"):
        return ""
    return script[2:] if script.startswith("./") else script


def declared_invocations_from_scenarios(scenarios: Sequence[Any]) -> list[DeclaredInvocation]:
    """Every single-program invocation a scenario file states an exit contract for."""
    out: list[DeclaredInvocation] = []
    for scenario in scenarios:
        name = str(getattr(scenario, "name", "") or "")
        specs = list(getattr(scenario, "commands", None) or [])
        specs += [
            step.command
            for step in (getattr(scenario, "steps", None) or [])
            if getattr(step, "command", None) is not None
        ]
        for spec in specs:
            command = str(getattr(spec, "run", "") or "")
            script = _script_of(command)
            if not script:
                continue
            out.append(
                DeclaredInvocation(
                    command=command,
                    script=script,
                    expect_exit_code=int(getattr(spec, "expect_exit_code", 0) or 0),
                    timeout_s=int(getattr(spec, "timeout_s", 0) or 0),
                    source=f"scenario {name}: {getattr(spec, 'name', '') or command}",
                )
            )
    return out


def declared_invocations_from_ci(repo: Path, interpreter: str) -> list[DeclaredInvocation]:
    """Every single-program line the repository's own CI runs.

    A CI step that runs a line is the repository declaring that the line must
    exit 0. Multi-line ``run:`` blocks are read line by line and only lines that
    are one program with no shell syntax count. A bare ``python``/``python3`` is
    the CI image's interpreter; here it is the repository's own, which is the
    one detail of the line that describes the machine rather than the check.
    """
    import yaml

    repo = Path(repo)
    found: list[DeclaredInvocation] = []
    workflows = repo / ".github" / "workflows"
    if not interpreter or not workflows.is_dir():
        return found

    def runs(node: Any):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "run" and isinstance(value, str):
                    yield value
                else:
                    yield from runs(value)
        elif isinstance(node, list):
            for item in node:
                yield from runs(item)

    for path in sorted(workflows.glob("*.y*ml")):
        try:
            document = yaml.safe_load(path.read_text(errors="replace"))
        except (OSError, yaml.YAMLError):
            continue
        for block in runs(document):
            for line in block.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                tokens = _tokens(line)
                if tokens and tokens[0] in ("python", "python3"):
                    line = " ".join([interpreter, *line.split(None, 1)[1:]])
                script = _script_of(line)
                if not script:
                    continue
                found.append(
                    DeclaredInvocation(
                        command=line,
                        script=script,
                        source=f"{path.relative_to(repo)}",
                    )
                )
    return found


def choose_invocation(
    rel: str, declared: Sequence[DeclaredInvocation]
) -> DeclaredInvocation | None:
    """The one declared invocation this driver runs for ``rel``, or ``None``.

    Only declarations that run exactly this script and state exit 0 are
    candidates — a refusal control proves the program's input is closed, not
    that its checks hold. Among them: the bare invocation (the script's own
    whole-run default) first; otherwise the one declared with the largest time
    allowance, which is the declaration a human sized as the whole run; ties go
    to the first written. Every choice is a string a human wrote.
    """
    candidates = [d for d in declared if d.script == rel and d.expect_exit_code == 0]
    if not candidates:
        return None
    bare = [d for d in candidates if d.bare]
    if bare:
        return bare[0]
    best = candidates[0]
    for candidate in candidates[1:]:
        if candidate.timeout_s > best.timeout_s:
            best = candidate
    return best


def analyse_script(
    repo: Path, rel: str, declared: Sequence[DeclaredInvocation]
) -> tuple[ChangedGuard | None, str]:
    """A changed script that collects no test: its declared invocation, or why none.

    Returns ``(guard, "")`` when a human declared an exact invocation for it, and
    ``(None, reason)`` otherwise. The reason is the gap, and it says what closes
    it: a declaration, not a wrapper written to satisfy this driver.
    """
    invocation = choose_invocation(rel, declared)
    if invocation is None:
        return None, (
            f"{rel} changed and is an executable check that collects no test, and no exact "
            "invocation for it is declared — not in this driver's approved scenario "
            "vocabulary and not in the repository's CI — so it has not been operated. This "
            "driver does not compose arguments for it; declare the exact command the "
            "repository runs it with (for example as a CI step) and it will be run"
        )
    return (
        ChangedGuard(
            target=VerificationTarget(
                path=rel,
                command=invocation.command,
                surface="the verification this change changed",
                why=[
                    "this change edited this executable check; it is the deliverable, not "
                    "collateral",
                    f"run exactly as declared by {invocation.source}",
                ],
            ),
            relation="changed",
            added=not _is_tracked(Path(repo), rel),
            declared_by=invocation.source,
            declared_timeout_s=invocation.timeout_s,
        ),
        "",
    )


def select_changed_verification(
    repo: Path,
    diff_files: Sequence[str],
    *,
    base_commit: str = "",
    max_related: int = 2,
    runner: str = "",
    declared: Sequence[DeclaredInvocation] = (),
    undeclared: dict[str, str] | None = None,
    not_applicable: dict[str, str] | None = None,
) -> tuple[list[ChangedGuard], list[str], list[str], list[str]]:
    """The guards this diff earns: the ones it changed, plus their narrow collateral.

    Returns ``(guards, changed_paths, related_paths, notes)``.

    Two sources, and neither of them "the suite":

    * EVERY verification file the diff changed. This is the deliverable, so all
      of it is selected, in the diff's order. How much of it runs at once is a
      separate question with a separate bound — see :func:`verify_changed_surface`.
      Selecting only the first few made that bound a lifetime cap: on an
      unchanged tree the same few were selected, observed and re-run forever,
      and everything after them stayed "not operated" for as long as the run
      lasted (Neyma U8.4, run 20260918-223447: 11 changed, the same 4 run on
      each of 11 iterations);
    * the repository's own guards over the OTHER files the diff changed. A guard
      file usually exists because some non-test file has a property worth
      keeping, and that file is normally in the same diff. This is the part that
      catches "the document you reconciled is read by a guard you did not run",
      and it is capped hard, because it is collateral rather than deliverable.

    A changed script that collects no test is selected only through an exact
    invocation in ``declared``; otherwise it lands in ``undeclared`` with the
    reason. A changed file that no longer exists lands in ``not_applicable``.
    """
    repo = Path(repo)
    notes: list[str] = []
    changed_paths = changed_verification_paths(diff_files)
    if not changed_paths:
        return [], [], [], notes
    runner = runner or detect_test_runner(repo)
    if not runner:
        notes.append(
            "the repository declares no Python test runner this driver could identify, so "
            "the verification this change changed could not be executed"
        )
        return [], changed_paths, [], notes

    guards: list[ChangedGuard] = []
    for rel in changed_paths:
        if not (repo / rel).is_file():
            if not_applicable is not None:
                not_applicable[rel] = f"{rel} no longer exists; a deleted guard is not run"
            continue
        guard = analyse_guard(repo, rel, base_commit=base_commit, runner=runner)
        if guard is not None:
            guards.append(guard)
            continue
        script, why = analyse_script(repo, rel, declared)
        if script is not None:
            guards.append(script)
        else:
            if undeclared is not None:
                undeclared[rel] = why
            notes.append(
                f"{rel} changed and declares no test this runner collects, and no exact "
                "invocation is declared for it, so this driver did not invent an entry point"
            )

    # The other files the diff changed. A changed test file is never its own
    # collateral, and the whole point of the cap is that a test edit must not
    # turn into a repository-wide run.
    other = [
        str(f).strip()
        for f in diff_files
        if str(f).strip()
        and str(f).strip() not in changed_paths
        and not _VENDOR.search(str(f).strip())
    ]
    related_paths: list[str] = []
    if other and max_related > 0:
        targets, related_notes = discover_record_guards(
            repo, other, max_targets=max_related, runner=runner
        )
        notes += [n for n in related_notes if "keeps no test that reads" not in n]
        chosen = {g.path for g in guards}
        for target in targets:
            if target.path in chosen:
                continue
            guard = analyse_guard(
                repo, target.path, base_commit=base_commit, runner=runner, relation="related"
            )
            if guard is None:
                continue
            guard.target.why = list(target.why) + [
                "the repository already keeps this guard over a file this change touched"
            ]
            guard.target.surface = "the repository's own guard over what this change touched"
            guards.append(guard)
            related_paths.append(target.path)
    return guards, changed_paths, related_paths, notes


def discrimination_gaps(repo: Path, guards: Sequence[ChangedGuard]) -> list[str]:
    """Changed negative guards with no case proving they can still fire.

    Looked for in the guard's own file first, then in any other guard selected
    here that names it. A gap is stated as what is missing, never as a defect:
    the guard may be perfectly correct and simply unaccompanied, and the answer
    is one small case rather than a founder decision.
    """
    repo = Path(repo)
    gaps: list[str] = []
    for guard in guards:
        if guard.relation != "changed" or not guard.needs_discrimination:
            continue
        if guard.discrimination_names:
            continue
        elsewhere = ""
        for other in guards:
            if other.path == guard.path or not other.discrimination_names:
                continue
            base = guard.path.rsplit("/", 1)[-1]
            if base and base in _read(repo / other.path):
                elsewhere = other.path
                break
        if elsewhere:
            continue
        gaps.append(
            f"{guard.path} asserts an absence ("
            + ", ".join(guard.changed_negative_names[:2])
            + ") and this change altered what it asserts, but neither it nor any guard "
            "selected here declares a case proving it still fires when the forbidden "
            "state is realised; its green is not yet discriminating"
        )
    return gaps


# --------------------------------------------------------------------------
# The whole path, in one call
# --------------------------------------------------------------------------


def structural_measurements(repo: Path, diff_files: Sequence[str], *, commit: str = "") -> list[Any]:
    """The driver's own reachability scan of the product modules a diff changed.

    Never raises: a scan that could not be taken is simply absent, and an
    absent measurement discharges nothing.
    """
    from .risk_grounding import measure_reachability, product_modules

    modules = product_modules(diff_files)
    if not modules:
        return []
    try:
        return list(measure_reachability(Path(repo), modules, tree=commit))
    except Exception:  # pragma: no cover - defensive
        return []


def verify_changed_surface(
    repo: Path,
    diff_files: Sequence[str],
    *,
    base_commit: str = "",
    commit: str = "",
    max_changed: int = 4,
    max_related: int = 2,
    timeout_s: int = 900,
    only: Sequence[str] = (),
    declared: Sequence[DeclaredInvocation] = (),
    tree: str = "",
    prior: ChangedSurfaceVerification | None = None,
) -> ChangedSurfaceVerification:
    """Select the verification this change changed, and operate ONE bounded pass of it.

    ``max_changed`` bounds how many changed guards one pass executes; it is not
    a bound on how many ever will. Each pass takes the next ones still owed —
    never re-running one this same tree already answered — so a diff with more
    changed verification than the bound is operated across passes, and every
    changed path ends OBSERVED, UNEXECUTABLE with its reason, or NOT_APPLICABLE.

    ``prior`` is an earlier record of this obligation. Its results are carried
    only when it was read from the identical working tree (``tree``) and the
    identical commit; a result from any other tree is never evidence here. A
    guard that could not be run is not carried — it is tried again — because
    "the environment could not run it" is not a fact about the tree.

    ``only`` restricts execution to named paths.
    """
    from .risk_grounding import product_modules
    from .worktree_state import worktree_tree_hash

    repo = Path(repo)
    if not tree:
        tree, _error = worktree_tree_hash(repo)
    reachability = structural_measurements(repo, diff_files, commit=commit)
    undeclared: dict[str, str] = {}
    not_applicable: dict[str, str] = {}
    guards, changed_paths, related_paths, notes = select_changed_verification(
        repo,
        diff_files,
        base_commit=base_commit,
        max_related=max_related,
        declared=declared,
        undeclared=undeclared,
        not_applicable=not_applicable,
    )
    record = ChangedSurfaceVerification(
        changed_paths=changed_paths,
        related_paths=related_paths,
        guards=guards,
        notes=notes,
        commit=commit,
        module_reachability=reachability,
        task_modules=product_modules(diff_files),
        tree=tree,
        diff_fingerprint="\n".join(sorted(str(f) for f in diff_files)),
        undeclared=undeclared,
        not_applicable=not_applicable,
    )
    if not guards:
        return record
    record.discrimination_gaps = discrimination_gaps(repo, guards)

    # What this same tree already answered, command for command.
    carried: list[VerificationResult] = []
    if (
        prior is not None
        and tree
        and prior.tree == tree
        and prior.commit == commit
    ):
        commands = {g.path: g.target.command for g in guards}
        carried = [
            r
            for r in prior.results
            if not r.infrastructure and commands.get(r.target.path) == r.target.command
        ]
        record.passes = [list(p) for p in prior.passes]
    done = {r.target.path for r in carried}

    wanted = [
        g for g in guards if g.path not in done and (not only or g.path in set(only))
    ]
    batch = [g for g in wanted if g.relation == "changed"][: max(1, max_changed)]
    batch += [g for g in wanted if g.relation != "changed"][: max(0, max_related)]
    remaining = len([g for g in wanted if g.relation == "changed"]) - len(
        [g for g in batch if g.relation == "changed"]
    )
    if remaining > 0:
        record.notes.append(
            f"{len(changed_paths)} verification files changed; this pass executed "
            f"{len([g for g in batch if g.relation == 'changed'])} and {remaining} are still "
            "owed to a later pass. The bound is per pass, not per run."
        )

    executed: list[VerificationResult] = []
    for guard in batch:
        # A declared script runs under its declaration's own time allowance
        # when that is the longer one; a guard is never cut short by a
        # stopwatch tighter than the one a human wrote beside it.
        executed += run_verification(
            repo,
            [guard.target],
            timeout_s=max(timeout_s, guard.declared_timeout_s),
            commit=commit,
        ).results
    record.results = carried + executed
    if executed or not carried:
        # A pass that found nothing still owed on a tree already answered is
        # not a pass; recording it would only pad the audit trail.
        record.passes.append([r.target.path for r in executed])
    return record
