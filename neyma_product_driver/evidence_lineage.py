"""Which earlier scenario evidence is still evidence about the tree under test.

A run executes its suite once per iteration, and after the first pass it
narrows: failed scenarios, their risk neighbours, new cases and the permanent
set. The acceptance gate, however, judged only the iteration it was handed, so
every scenario a narrowed pass did not select arrived as "no result recorded"
and every risk only those scenarios exercised arrived as an uncovered gap. Run
20260916-070036 is the case: iteration 3 ran the whole generated suite green on
``a8dc6a5``; iteration 4's builder changed one verification script; the
narrowed iteration 4 pass did not re-select the service_unavailable, stale_state
and concurrency scenarios, and the run blocked as though that same-tree evidence
had never been taken.

The opposite mistake is worse. Carrying a pass forward from a tree that has
since changed in a way that could move it is how a regression is accepted on
old evidence. So evidence is inherited only under an explicit validity rule,
checked per scenario, and everything else is re-executed:

1. **Same run, same measurement.** The earlier outcome belongs to this run, it
   PASSED with verified evidence, and the scenario it measured is the one the
   suite would run now (same compiled definition; for a record written before
   definitions were digested, the same executed commands and no
   re-materialization since).
2. **The artifact still resolves.** Its evidence directory is inside this run,
   its record still names this scenario, this run and the iteration that wrote
   it, and every assertion it recorded passed.
3. **The tree it was taken on is known.** The worktree was clean and identical
   before and after that execution, so the evidence names one git tree.
4. **Nothing since touches its subject.** Every path that differs between that
   tree and the current worktree is checked against the scenario's blast
   radius: the files its commands name, the Python modules they import,
   transitively, the files and directories those modules name in string
   literals, the package directories they live in, and pytest's conftest
   files. A change inside that radius invalidates exactly this evidence; a
   change outside it does not. A root-level configuration file, any conftest,
   or a module that imports by computed name makes the radius unbounded, and
   any change then invalidates.

The rule is deliberately conservative where it cannot see: a non-Python file is
in the radius when a subject module names it or sits beside it; installed
packages outside the repository are not seen at all (see the module's
limitations below). Anything invalidated is re-executed in the same iteration,
never silently dropped, and the final ledger states which risks were verified
fresh, which by inherited evidence, which were invalidated and re-exercised, and
which remain uncovered.

LIMITATION: dependencies outside the repository (a virtualenv, a system
library) are not part of any git tree, so a change there does not invalidate
inherited evidence. The same was already true of every narrowed rerun.
"""

from __future__ import annotations

import ast
import hashlib
import json
import posixpath
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover
    from .scenario_suite import ScenarioOutcome, ScenarioSuite, SuiteResult
    from .scenarios import Scenario

#: Root-level files whose change can move any execution in the repository.
_GLOBAL_ROOT_SUFFIXES = frozenset({".toml", ".cfg", ".ini", ".lock", ".pth"})
_GLOBAL_ROOT_NAMES = frozenset(
    {"conftest.py", "sitecustomize.py", "usercustomize.py", ".python-version", "Makefile"}
)
_DYNAMIC_IMPORTS = frozenset({"import_module", "__import__", "spec_from_file_location", "run_path", "run_module", "load_source"})
_PATHLIKE = re.compile(r"^[\w.\-]+(?:/[\w.\-]+)*/?$")


# --------------------------------------------------------------------------
# Tree identity
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, proc.stdout


@dataclass(frozen=True)
class TreeIdentity:
    """The committed tree at HEAD, and every path the worktree differs from it in."""

    tree: str = ""
    dirty: tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        return bool(self.tree)

    @property
    def exact(self) -> str:
        """The single git tree this worktree IS, or ``""`` when it is dirty/unknown."""
        return self.tree if self.tree and not self.dirty else ""


def worktree_identity(repo: Path | None) -> TreeIdentity:
    if repo is None:
        return TreeIdentity()
    code, tree = _git(Path(repo), "rev-parse", "HEAD^{tree}")
    if code != 0 or not tree.strip():
        return TreeIdentity()
    code, status = _git(Path(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if code != 0:
        return TreeIdentity()
    dirty: set[str] = set()
    entries = status.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        dirty.add(entry[3:])
        if entry[0] in "RC":  # a rename carries its source as the next entry
            if index < len(entries) and entries[index]:
                dirty.add(entries[index])
            index += 1
    return TreeIdentity(tree=tree.strip(), dirty=tuple(sorted(dirty)))


def tree_of_commit(repo: Path | None, commit: str) -> str:
    if repo is None or not commit:
        return ""
    code, out = _git(Path(repo), "rev-parse", "--verify", "--quiet", f"{commit}^{{tree}}")
    return out.strip() if code == 0 else ""


def changed_paths(repo: Path | None, from_tree: str, current: TreeIdentity) -> set[str] | None:
    """Every path that differs between ``from_tree`` and the current worktree.

    ``None`` when that cannot be established — which invalidates, never blesses.
    """
    if repo is None or not from_tree or not current.known:
        return None
    changed = set(current.dirty)
    if from_tree != current.tree:
        code, out = _git(Path(repo), "diff", "--name-only", "-z", "--no-renames", from_tree, current.tree)
        if code != 0:
            return None
        changed |= {p for p in out.split("\0") if p}
    return changed


# --------------------------------------------------------------------------
# Scenario identity
# --------------------------------------------------------------------------


def scenario_digest(scenario: "Scenario") -> str:
    """Identity of the MEASUREMENT: what runs and what is asserted, never prose."""
    payload = json.dumps(
        scenario.model_dump(mode="json", exclude={"description"}), sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def scenario_commands(scenario: "Scenario") -> list[str]:
    """Every command string a scenario can run, in declaration order."""
    out: list[str] = [*scenario.setup]
    out += [spec.run for spec in scenario.commands]
    out += [check.command for check in scenario.expect_state]
    for step in scenario.steps:
        if step.command is not None:
            out.append(step.command.run)
        if step.state_check is not None:
            out.append(step.state_check.command)
    out += list(scenario.teardown)
    return [c for c in out if c]


def _norm(command: str) -> str:
    return " ".join(str(command or "").split())


# --------------------------------------------------------------------------
# Blast radius
# --------------------------------------------------------------------------


def _repo_relative(literal: str) -> str:
    """``./a/b/`` -> ``a/b``. The repository root itself names nothing specific."""
    text = str(literal or "").strip()
    if not text or text.startswith("/"):
        return ""
    clean = posixpath.normpath(text)
    return "" if clean in {".", ".."} or clean.startswith("../") else clean


@dataclass
class _ModuleFacts:
    imports: set[str] = field(default_factory=set)
    literals: set[str] = field(default_factory=set)
    dynamic: bool = False


def _module_facts(tree: ast.AST, package: str) -> _ModuleFacts:
    facts = _ModuleFacts()
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            facts.imports |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join([*parts, *([base] if base else [])])
            if base:
                facts.imports.add(base)
                facts.imports |= {f"{base}.{alias.name}" for alias in node.names}
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings and len(node.value) < 400:
                facts.literals.add(node.value.strip())
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _DYNAMIC_IMPORTS:
                args = list(node.args)
                target = args[-1] if name == "spec_from_file_location" and len(args) > 1 else (args[0] if args else None)
                if isinstance(target, ast.Constant) and isinstance(target.value, str):
                    facts.literals.add(target.value)
                    if name in {"import_module", "__import__", "run_module"}:
                        facts.imports.add(target.value)
                else:
                    facts.dynamic = True
    return facts


@dataclass
class Subject:
    """What one scenario can observe of the repository."""

    files: set[str] = field(default_factory=set)
    literals: set[str] = field(default_factory=set)
    unbounded: str = ""

    def touched_by(self, path: str) -> str:
        """Why a change to ``path`` could move this scenario's result, or ``""``."""
        posix = PurePosixPath(path)
        if len(posix.parts) == 1 and (
            posix.name in _GLOBAL_ROOT_NAMES
            or posix.suffix in _GLOBAL_ROOT_SUFFIXES
            or posix.name.startswith("requirements")
        ):
            return f"{path} is repository-wide configuration"
        if posix.name == "conftest.py":
            return f"{path} is pytest configuration"
        if self.unbounded and posix.suffix == ".py":
            return f"{path} is Python and this scenario's reach is unbounded ({self.unbounded})"
        if path in self.files:
            return f"{path} is part of what this scenario executes or imports"
        for literal in self.literals:
            clean = _repo_relative(literal)
            if not clean:
                continue
            if clean == path or path.startswith(clean + "/"):
                return f"{path} is named by the scenario's subject ({literal!r})"
            if "." in posix.name and (clean == posix.name or clean.endswith("/" + posix.name)):
                return f"{path} is named by the scenario's subject ({literal!r})"
        if posix.suffix != ".py":
            parent = str(posix.parent)
            if any(str(PurePosixPath(f).parent) == parent for f in self.files):
                return f"{path} sits beside a module this scenario imports"
        return ""


class SubjectIndex:
    """Computes, and caches, the blast radius of each scenario over one repository."""

    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)
        self._facts: dict[str, _ModuleFacts | None] = {}
        roots = [PurePosixPath(".")]
        try:
            roots += [
                PurePosixPath(child.name)
                for child in sorted(self.repo.iterdir())
                if child.is_dir() and not child.name.startswith(".")
            ]
        except OSError:
            pass
        self._roots = roots

    def _exists(self, rel: str) -> bool:
        try:
            return (self.repo / rel).exists()
        except OSError:
            return False

    def _resolve_module(self, dotted: str) -> list[str]:
        """Repository files importing ``dotted`` executes (packages included)."""
        parts = dotted.split(".")
        out: list[str] = []
        for root in self._roots:
            base = PurePosixPath(*root.parts) if root.parts else PurePosixPath()
            found: list[str] = []
            for depth in range(1, len(parts) + 1):
                stem = base.joinpath(*parts[:depth])
                init = str(stem / "__init__.py")
                mod = str(stem) + ".py"
                if self._exists(init):
                    found.append(init)
                elif self._exists(mod):
                    found.append(mod)
                    break
                else:
                    break
            if found:
                out += found
        return out

    def _facts_for(self, rel: str) -> _ModuleFacts | None:
        if rel not in self._facts:
            facts: _ModuleFacts | None = None
            try:
                source = (self.repo / rel).read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
                tree = None
            if tree is not None:
                package = ".".join(PurePosixPath(rel).with_suffix("").parts[:-1])
                facts = _module_facts(tree, package)
            self._facts[rel] = facts
        return self._facts[rel]

    def _seed(self, command: str, subject: Subject) -> list[str]:
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        pending: list[str] = []
        for index, token in enumerate(tokens):
            if index > 0 and tokens[index - 1] == "-c":
                try:
                    program = ast.parse(token)
                except SyntaxError:
                    subject.unbounded = subject.unbounded or "an inline program that does not parse"
                    continue
                facts = _module_facts(program, "")
                subject.literals |= facts.literals
                if facts.dynamic:
                    subject.unbounded = subject.unbounded or "an inline program imports by computed name"
                for dotted in facts.imports:
                    pending += self._resolve_module(dotted)
                continue
            if index > 0 and tokens[index - 1] == "-m":
                pending += self._resolve_module(token)
                continue
            for piece in [token, *token.split("=", 1)[1:]]:
                clean = piece.split("::", 1)[0]
                if _PATHLIKE.match(clean) and self._exists(clean):
                    subject.literals.add(clean)
                    if clean.endswith(".py"):
                        pending.append(_repo_relative(clean))
        return pending

    def subject(self, scenario: "Scenario") -> Subject:
        subject = Subject()
        pending: list[str] = []
        for command in scenario_commands(scenario):
            pending += self._seed(command, subject)
        while pending:
            rel = pending.pop()
            if rel in subject.files:
                continue
            subject.files.add(rel)
            # pytest executes every conftest between the rootdir and a test.
            for parent in PurePosixPath(rel).parents:
                conftest = str(parent / "conftest.py") if str(parent) != "." else "conftest.py"
                if self._exists(conftest) and conftest not in subject.files:
                    pending.append(conftest)
            facts = self._facts_for(rel)
            if facts is None:
                continue
            if facts.dynamic:
                subject.unbounded = subject.unbounded or f"{rel} imports by computed name"
            subject.literals |= {lit for lit in facts.literals if _PATHLIKE.match(lit)}
            for dotted in facts.imports:
                pending += self._resolve_module(dotted)
            for literal in facts.literals:
                clean = _repo_relative(literal)
                if clean.endswith(".py") and _PATHLIKE.match(clean) and self._exists(clean):
                    pending.append(clean)
        return subject


# --------------------------------------------------------------------------
# The ledger
# --------------------------------------------------------------------------


class LineageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    risk_category: str = ""
    #: The iteration whose execution is the evidence.
    evidence_iteration: int = 0
    evidence_tree: str = ""
    evidence_path: str = ""
    reason: str = ""


class EvidenceLineage(BaseModel):
    """Where each scenario's evidence in one iteration's verdict came from."""

    model_config = ConfigDict(extra="forbid")

    iteration: int = 0
    tree: str = ""
    #: Executed in this iteration.
    fresh: list[str] = Field(default_factory=list)
    #: Not executed in this iteration; their earlier same-subject evidence stands.
    inherited: list[LineageEntry] = Field(default_factory=list)
    #: Earlier passing evidence that a change since invalidated. Each was
    #: re-executed in this iteration; ``reason`` says what invalidated it.
    invalidated: list[LineageEntry] = Field(default_factory=list)

    def inherited_ids(self) -> set[str]:
        return {e.scenario_id for e in self.inherited}

    def invalidated_ids(self) -> set[str]:
        return {e.scenario_id for e in self.invalidated}

    def lines(self) -> list[str]:
        out = [
            f"EVIDENCE LINEAGE (iteration {self.iteration}, tree {self.tree[:12] or 'unknown'}): "
            f"{len(self.fresh)} executed fresh, {len(self.inherited)} inherited from valid "
            f"earlier same-subject evidence, {len(self.invalidated)} invalidated and re-executed"
        ]
        for entry in self.inherited:
            out.append(
                f"  inherited {entry.scenario_id} from iteration {entry.evidence_iteration} "
                f"(tree {entry.evidence_tree[:12]}): {entry.reason}"
            )
            if entry.evidence_path:
                out.append(f"      evidence: {entry.evidence_path}")
        for entry in self.invalidated:
            out.append(
                f"  invalidated {entry.scenario_id} (evidence from iteration "
                f"{entry.evidence_iteration}) and re-executed: {entry.reason}"
            )
        return out


@dataclass
class Assessment:
    """Per-scenario verdict on earlier evidence, before this iteration executes."""

    valid: dict[str, "ScenarioOutcome"] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    #: Earlier PASSED evidence rejected — the ones that are "invalidated".
    invalidated: dict[str, LineageEntry] = field(default_factory=dict)
    current: TreeIdentity = field(default_factory=TreeIdentity)

    def must_run(self, suite: "ScenarioSuite") -> list[str]:
        return [e.scenario_id for e in suite.entries if e.scenario_id not in self.valid]


def _rebase(path: str, run_dir: Path, run_id: str) -> Path | None:
    """The evidence directory inside THIS run directory, or ``None``."""
    if not path:
        return None
    candidate = Path(path)
    try:
        candidate.resolve().relative_to(run_dir.resolve())
        return candidate
    except (ValueError, OSError):
        pass
    parts = candidate.parts
    if run_id and run_id in parts:
        rebased = run_dir.joinpath(*parts[parts.index(run_id) + 1:])
        try:
            rebased.resolve().relative_to(run_dir.resolve())
        except (ValueError, OSError):
            return None
        return rebased
    return None


def _record_problem(directory: Path, outcome: "ScenarioOutcome", run_id: str) -> tuple[str, dict[str, Any]]:
    from .scenario_suite import CASE_RECORD_FILENAME, verify_case_evidence

    problem = verify_case_evidence(
        str(directory),
        scenario_id=outcome.scenario_id,
        run_id=run_id,
        iteration=outcome.evidence_iteration,
    )
    if problem:
        return problem, {}
    try:
        record = json.loads((directory / CASE_RECORD_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"the evidence record could not be re-read ({type(exc).__name__})", {}
    assertions = record.get("assertions") or []
    if not assertions or not all(a.get("passed") for a in assertions if isinstance(a, dict)):
        return "the evidence record does not show every assertion passing", {}
    if record.get("error") or record.get("infrastructure_failure"):
        return "the evidence record carries an execution error", {}
    return "", record


def assess(
    prior: "SuiteResult | None",
    suite: "ScenarioSuite",
    *,
    repo: Path | None,
    run_dir: Path | None,
    run_id: str,
    current: TreeIdentity | None = None,
    forced: Iterable[str] = (),
    index: SubjectIndex | None = None,
) -> Assessment:
    """Decide, per suite entry, whether earlier evidence may stand this iteration."""
    identity = current if current is not None else worktree_identity(repo)
    result = Assessment(current=identity)
    if prior is None or run_dir is None:
        return result
    from .scenario_suite import Outcome

    forced_ids = {str(i) for i in forced}
    subjects = index or (SubjectIndex(repo) if repo is not None else None)
    changes: dict[str, set[str] | None] = {}

    for entry in suite.entries:
        outcome = prior.by_id(entry.scenario_id)
        if outcome is None:
            continue

        def refuse(
            reason: str, *, invalidates: bool = True, earlier: "ScenarioOutcome" = outcome
        ) -> None:
            result.reasons[earlier.scenario_id] = reason
            if invalidates:
                result.invalidated[earlier.scenario_id] = LineageEntry(
                    scenario_id=earlier.scenario_id,
                    risk_category=earlier.risk_category,
                    evidence_iteration=earlier.evidence_iteration,
                    evidence_tree=earlier.evidence_tree,
                    evidence_path=earlier.evidence_path,
                    reason=reason,
                )

        if outcome.outcome is not Outcome.PASSED or not outcome.evidence_verified:
            refuse(f"the earlier result was {outcome.outcome.value}", invalidates=False)
            continue
        if entry.scenario_id in forced_ids:
            refuse("the scenario was re-materialized since, so its earlier measurement is superseded")
            continue
        if not outcome.evidence_iteration or not outcome.evidence_tree:
            refuse("the earlier evidence does not name the single clean tree it was taken on")
            continue
        directory = _rebase(outcome.evidence_path, run_dir, run_id)
        if directory is None:
            refuse("the earlier evidence is not inside this run's directory")
            continue
        problem, record = _record_problem(directory, outcome, run_id)
        if problem:
            refuse(f"the earlier evidence no longer resolves: {problem}")
            continue
        if outcome.scenario_digest:
            if outcome.scenario_digest != scenario_digest(entry.scenario):
                refuse("the scenario's definition changed since that evidence was taken")
                continue
        else:
            generated = entry.generated
            if generated is not None and (generated.rebound_on_resume or generated.replaces):
                refuse("the scenario was re-materialized since that evidence was taken")
                continue
            # Written before definitions were digested: the measurement is
            # identified by exactly what it executed.
            executed = {
                _norm(c.get("command", ""))
                for c in (record.get("setup") or []) + (record.get("commands") or [])
                if isinstance(c, dict)
            }
            teardown = {_norm(c) for c in entry.scenario.teardown}
            wanted = {_norm(c) for c in scenario_commands(entry.scenario)} - teardown
            if not executed or executed != wanted:
                refuse("the commands that evidence executed are not the commands this scenario runs now")
                continue
        if outcome.evidence_tree not in changes:
            changes[outcome.evidence_tree] = changed_paths(repo, outcome.evidence_tree, identity)
        changed = changes[outcome.evidence_tree]
        if changed is None:
            refuse("what changed since that evidence cannot be established")
            continue
        why = ""
        if changed:
            if subjects is None:
                why = "the repository cannot be read to bound what the change touches"
            else:
                subject = subjects.subject(entry.scenario)
                for path in sorted(changed):
                    why = subject.touched_by(path)
                    if why:
                        break
        if why:
            refuse(f"changed since iteration {outcome.evidence_iteration}: {why}")
            continue
        result.valid[entry.scenario_id] = outcome.model_copy(
            update={"evidence_path": str(directory)}
        )
        result.reasons[entry.scenario_id] = (
            "same tree" if not changed else
            f"{len(changed)} path(s) changed since, none inside its subject: "
            + ", ".join(sorted(changed)[:4])
        )
    return result


def stamp(result: "SuiteResult", suite: "ScenarioSuite", *, iteration: int, before: TreeIdentity, after: TreeIdentity) -> None:
    """Record, on every outcome this execution produced, what it is evidence OF."""
    tree = before.exact if before.exact and before == after else ""
    result.tree = tree or before.tree
    for outcome in result.outcomes:
        if outcome.inherited_from_iteration:
            continue
        outcome.evidence_iteration = iteration
        outcome.evidence_tree = tree
        entry = suite.by_id(outcome.scenario_id)
        outcome.scenario_digest = scenario_digest(entry.scenario) if entry is not None else ""


def inherit(result: "SuiteResult", suite: "ScenarioSuite", assessment: Assessment, *, iteration: int) -> "SuiteResult":
    """Complete a narrowed execution with the earlier evidence that still stands."""
    executed = {o.scenario_id for o in result.outcomes}
    lineage = EvidenceLineage(
        iteration=iteration,
        tree=assessment.current.tree,
        fresh=sorted(executed),
        invalidated=[
            e for sid, e in sorted(assessment.invalidated.items()) if sid in executed
        ],
    )
    outcomes = list(result.outcomes)
    for entry in suite.entries:
        if entry.scenario_id in executed:
            continue
        earlier = assessment.valid.get(entry.scenario_id)
        if earlier is None:
            continue
        carried = earlier.model_copy(
            update={
                "inherited_from_iteration": earlier.evidence_iteration,
                "required": entry.required,
                "priority": entry.priority,
            }
        )
        outcomes.append(carried)
        lineage.inherited.append(
            LineageEntry(
                scenario_id=carried.scenario_id,
                risk_category=carried.risk_category,
                evidence_iteration=carried.evidence_iteration,
                evidence_tree=carried.evidence_tree,
                evidence_path=carried.evidence_path,
                reason=assessment.reasons.get(carried.scenario_id, ""),
            )
        )
    recorded = {o.scenario_id for o in outcomes}
    return result.model_copy(
        update={
            "outcomes": outcomes,
            "full_run": all(e.scenario_id in recorded for e in suite.entries),
            "lineage": lineage,
            "selection_reason": (
                result.selection_reason
                + (
                    f"; {len(lineage.inherited)} further scenario(s) carried by valid "
                    "earlier same-subject evidence"
                    if lineage.inherited
                    else ""
                )
            ),
        }
    )


def accumulated_result(run_dir: Path, repo: Path | None, *, before_iteration: int | None = None) -> "SuiteResult | None":
    """The run's evidence so far: each scenario's LATEST recorded outcome.

    Read from the persisted iteration records, so a resumed process continues
    the lineage a previous process built. A record written before outcomes were
    stamped is stamped here from its own iteration record, and only when that
    record shows a clean worktree — otherwise its tree is unknown and nothing
    of it can be inherited.
    """
    from .scenario_suite import Outcome, SuiteResult

    merged: dict[str, Any] = {}
    latest: SuiteResult | None = None
    dirs = []
    for path in run_dir.glob("iteration-*/suite-result.json"):
        match = re.fullmatch(r"iteration-(\d+)", path.parent.name)
        if match:
            dirs.append((int(match.group(1)), path))
    for number, path in sorted(dirs):
        if before_iteration is not None and number >= before_iteration:
            continue
        try:
            result = SuiteResult.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        legacy_tree = ""
        record_path = path.parent / "record.json"
        try:
            git = json.loads(record_path.read_text(encoding="utf-8")).get("git") or {}
        except (OSError, json.JSONDecodeError, AttributeError):
            git = {}
        if isinstance(git, dict) and not str(git.get("status_porcelain", "x")).strip():
            legacy_tree = tree_of_commit(repo, str(git.get("head_commit", "")))
        for outcome in result.outcomes:
            if outcome.outcome is Outcome.SKIPPED:
                continue
            if not outcome.evidence_iteration and not outcome.inherited_from_iteration:
                outcome = outcome.model_copy(
                    update={"evidence_iteration": number, "evidence_tree": legacy_tree}
                )
            merged[outcome.scenario_id] = outcome
        latest = result
    if latest is None:
        return None
    return latest.model_copy(update={"outcomes": list(merged.values()), "lineage": None})


__all__ = [
    "Assessment",
    "EvidenceLineage",
    "LineageEntry",
    "Subject",
    "SubjectIndex",
    "TreeIdentity",
    "accumulated_result",
    "assess",
    "changed_paths",
    "inherit",
    "scenario_commands",
    "scenario_digest",
    "stamp",
    "tree_of_commit",
    "worktree_identity",
]
