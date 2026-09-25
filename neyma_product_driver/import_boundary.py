"""Who may import a module, read from the repository's own guards — and what a probe may expect of it.

Run 20260925-043237 is the case this module exists for. Its coverage wave
admitted two generated scenarios that scanned the product's own package for
importers of a dark machine and expected the scan to print an EMPTY list:

    print('production importers of policy:', sorted(p.name for p, t in trees
          if p.name != 'policy.py' and 'policy' in own(t)))
    expect:    "production importers of policy: []"
    forbidden: "production importers of policy: ['"

"Ships dark" had once meant exactly that. It no longer did: the repository's
own guard had been REPLACED to fix the importer set to exactly one authorized
admission layer, by exact set equality both ways — a second importer is RED,
and so is the authorized one going missing. The scenarios could only pass
against a product that removed the wiring its authority requires, and the
builder was asked for that regression.

So this module reads the repository the way the refusal index does
(:mod:`~neyma_product_driver.refusal_semantics`), for one more closed question:

* **the import-boundary index.** A test function that enumerates a population
  of files, walks their ``Import`` / ``ImportFrom`` nodes, compares what it finds against exactly one production
  module ``M``, and holds the result to a literal collection of production
  module names — as the right side of a set difference, or one side of an
  equality — declares that collection the importers of ``M`` its authority
  permits. A guard that asserts emptiness with no such collection declares
  ``M`` has none. Guards that disagree about ``M`` establish nothing.
* **an importer scan in a probe.** A ``print('<label>:', <collection>)`` whose
  collection keeps the files of a resolvable population whose imports contain
  ``M`` — and applies NO other filter than excluding ``M`` itself — prints the
  importers of ``M`` in that population. Any other filter (a channel-capable
  subset, an allowlist subtracted, a population this reader cannot resolve)
  makes it a different question, and nothing is concluded from it.

Then one rule: a generated expectation that such a scan prints nothing — an
empty collection expected, or a non-empty one forbidden — is refused before
it runs when the repository's guards unambiguously authorize a NON-EMPTY
importer set for ``M`` that lies inside the scanned population. Expecting the
scan to print exactly the authorized set, expecting an unauthorized importer
to be absent, and expecting zero importers where the guards require zero all
remain lawful: this can only withhold admission, never pass anything.

### WHAT THIS DOES NOT DO. It reads no prose — the label is matched only as
the literal a print statement emits, never interpreted — and no product,
module, class or path name is written into it. Where the repository's guards
cannot establish the importer set, the generated expectation stands exactly
as written: an unknown boundary is never reinterpreted as a permitted one.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # pragma: no cover
    from .outcome_contract import RepositoryText

#: Attribute or name a program must reference to be reading import statements.
_IMPORT_NODES = frozenset({"Import", "ImportFrom"})
#: Calls that enumerate a population of files. A guard that parses one named
#: file is asking what THAT file imports, not who imports a module.
_POPULATION_CALLS = frozenset({"rglob", "glob", "iterdir"})
#: Calls that wrap a collection without filtering it.
_TRANSPARENT = frozenset({"sorted", "list", "set", "tuple", "frozenset"})
#: How an empty collection prints.
_EMPTY_RENDERINGS = frozenset({"[]", "set()", "()", "{}", "frozenset()"})
#: How a non-empty collection of names begins to print.
_NONEMPTY_OPENERS = tuple(o + q for o in "[{(" for q in "'\"")
#: Guard the cost of reading a repository: files larger than this are skipped.
_MAX_FILE_BYTES = 2_000_000


def module_stem(text: str) -> str:
    """``pkg.mod`` / ``mod.py`` / ``pkg/mod.py`` -> ``mod``. ``""`` if not a name."""
    clean = str(text or "").strip()
    if clean.endswith(".py"):
        clean = PurePosixPath(clean[:-3]).name
    clean = clean.rsplit(".", 1)[-1]
    return clean if clean.isidentifier() else ""


def _reads_imports(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in _IMPORT_NODES:
            return True
        if isinstance(child, ast.Name) and child.id in _IMPORT_NODES:
            return True
    return False


def _walks_population(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                if func.attr in _POPULATION_CALLS:
                    return True
                # `os.walk`, never `ast.walk`, which walks a tree, not a directory.
                if func.attr == "walk" and isinstance(func.value, ast.Name) and func.value.id == "os":
                    return True
    return False


def _string(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _string_collection(node: ast.AST | None) -> list[str] | None:
    """The members of a literal collection of strings, or ``None``."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in {"set", "frozenset", "list", "tuple"} and not node.keywords:
            if not node.args:
                return []
            if len(node.args) == 1:
                return _string_collection(node.args[0])
        return None
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        members = [_string(e) for e in node.elts]
        return None if any(m is None for m in members) else [m for m in members if m is not None]
    return None


def _is_empty_literal(node: ast.AST) -> bool:
    return _string_collection(node) == [] or (isinstance(node, ast.Dict) and not node.keys)


# --------------------------------------------------------------------------
# What the repository's guards say may import each module
# --------------------------------------------------------------------------


@dataclass
class Boundary:
    """One guard's declaration about the importers of one module."""

    allowed: frozenset[str]
    where: str


@dataclass
class ImportBoundaryIndex:
    """Per production module stem, what the repository's guards say may import it."""

    boundaries: dict[str, list[Boundary]] = field(default_factory=dict)
    #: Tracked, non-test ``.py`` paths by module stem.
    production: dict[str, list[str]] = field(default_factory=dict)
    files_read: int = 0

    def authorized(self, stem: str) -> Boundary | None:
        """The importer set the guards unambiguously authorize for ``stem``, or ``None``."""
        declared = self.boundaries.get(stem, [])
        if not declared or len({b.allowed for b in declared}) != 1:
            return None
        return declared[0]

    def covered(self, allowed: Iterable[str], root: str) -> list[str]:
        """Paths of the ``allowed`` modules that lie inside population ``root``."""
        prefix = root.strip().strip("/")
        prefix = "" if prefix in {"", "."} else prefix + "/"
        return sorted(
            path
            for stem in allowed
            for path in self.production.get(stem, [])
            if path.startswith(prefix)
        )

    @classmethod
    def build(cls, repository: "RepositoryText") -> "ImportBoundaryIndex":
        from .outcome_contract import _is_test_path

        index = cls()
        if repository.repo is None:
            return index
        guards: list[str] = []
        for path in sorted(repository.tracked()):
            if PurePosixPath(path).suffix != ".py":
                continue
            if _is_test_path(path):
                guards.append(path)
            else:
                stem = module_stem(path)
                if stem:
                    index.production.setdefault(stem, []).append(path)
        for path in guards:
            try:
                full = repository.repo / path
                if full.stat().st_size > _MAX_FILE_BYTES:
                    continue
                tree = ast.parse(full.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError, RecursionError):
                continue
            index.files_read += 1
            module_names = _assignments(tree.body)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                    found = _guard_boundary(node, module_names, index.production)
                    if found is not None:
                        stem, allowed = found
                        index.boundaries.setdefault(stem, []).append(
                            Boundary(frozenset(allowed), f"{path}:{node.lineno}")
                        )
        return index


def _assignments(body: Iterable[ast.AST]) -> dict[str, ast.AST]:
    """Names bound exactly once by a plain assignment in ``body``."""
    seen: dict[str, list[ast.AST]] = {}
    for statement in body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    seen.setdefault(target.id, []).append(statement.value)
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            if statement.value is not None:
                seen.setdefault(statement.target.id, []).append(statement.value)
    return {name: values[0] for name, values in seen.items() if len(values) == 1}


def _guard_boundary(
    function: ast.AST,
    module_names: dict[str, ast.AST],
    production: dict[str, list[str]],
) -> tuple[str, set[str]] | None:
    """``(module stem, permitted importer stems)`` if ``function`` is an importer guard."""
    if not _reads_imports(function) or not _walks_population(function):
        return None
    local = {**module_names, **_assignments(ast.walk(function))}

    def collection(node: ast.AST) -> list[str] | None:
        if isinstance(node, ast.Name) and node.id in local:
            return _string_collection(local[node.id])
        return _string_collection(node)

    allowlists: list[list[str]] = []
    for node in ast.walk(function):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
            members = collection(node.right)
            if members:
                allowlists.append(members)
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            for side in (node.left, node.comparators[0]):
                members = collection(side)
                if members:
                    allowlists.append(members)
    allowed: set[str] = set()
    for members in allowlists:
        stems = {module_stem(m) for m in members}
        if "" in stems or not stems <= production.keys():
            return None
        allowed |= stems

    targets: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Compare):
            continue
        for side in (node.left, *node.comparators):
            stem = module_stem(_string(side) or "")
            if stem and stem in production and stem not in allowed:
                targets.add(stem)
    if len(targets) != 1:
        return None
    target = next(iter(targets))
    if target in allowed:
        return None
    if allowed:
        return target, allowed
    # No allowlist: only an assertion of emptiness declares "none may import it".
    for node in ast.walk(function):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            return target, set()
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
            if _is_empty_literal(test.comparators[0]) or _is_empty_literal(test.left):
                return target, set()
    return None


# --------------------------------------------------------------------------
# Importer scans inside a probe
# --------------------------------------------------------------------------


@dataclass
class ImporterScan:
    """One print statement that emits the importers of ``module`` found under ``root``."""

    label: str
    module: str
    root: str
    counted: bool
    source: str


def _unwrap(node: ast.AST) -> tuple[ast.AST, bool]:
    counted = False
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1 and not node.keywords:
        if node.func.id in _TRANSPARENT:
            node = node.args[0]
        elif node.func.id == "len" and not counted:
            counted = True
            node = node.args[0]
        else:
            break
    return node, counted


def _population_root(node: ast.AST, names: dict[str, ast.AST], depth: int = 0) -> str | None:
    """The directory an unfiltered ``rglob('*.py')`` population is drawn from, or ``None``."""
    if depth > 8:
        return None
    node, counted = _unwrap(node)
    if counted:
        return None
    if isinstance(node, ast.Name):
        return _population_root(names[node.id], names, depth + 1) if node.id in names else None
    if isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
        if len(node.generators) != 1 or node.generators[0].ifs:
            return None
        return _population_root(node.generators[0].iter, names, depth + 1)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "rglob"
        and len(node.args) == 1
        and _string(node.args[0]) == "*.py"
    ):
        return _path_constant(node.func.value, names, depth + 1)
    return None


def _path_constant(node: ast.AST, names: dict[str, ast.AST], depth: int) -> str | None:
    if depth > 8:
        return None
    if isinstance(node, ast.Name):
        return _path_constant(names[node.id], names, depth + 1) if node.id in names else None
    if isinstance(node, ast.Call) and len(node.args) == 1 and not node.keywords:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in {"Path", "PurePath", "PosixPath", "PurePosixPath"}:
            return _string(node.args[0])
    return None


def _loop_names(target: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}


def _uses(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))


def _called_definitions(node: ast.AST, callables: dict[str, ast.AST]) -> list[ast.AST]:
    return [
        callables[n.func.id]
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in callables
    ]


def _membership_module(
    conjunct: ast.AST, loop: set[str], callables: dict[str, ast.AST]
) -> str | None:
    """``M`` when ``conjunct`` asks "does this file import M?", else ``None``."""
    if not _uses(conjunct, loop):
        return None
    if not (_reads_imports(conjunct) or any(_reads_imports(d) for d in _called_definitions(conjunct, callables))):
        return None
    # `'M' in imports(file)`
    if (
        isinstance(conjunct, ast.Compare)
        and len(conjunct.ops) == 1
        and isinstance(conjunct.ops[0], ast.In)
        and _string(conjunct.left) is not None
    ):
        return module_stem(_string(conjunct.left) or "") or None
    # `any(<import node> ... == 'M' ... for n in ast.walk(tree))`
    if (
        isinstance(conjunct, ast.Call)
        and isinstance(conjunct.func, ast.Name)
        and conjunct.func.id == "any"
        and len(conjunct.args) == 1
        and isinstance(conjunct.args[0], ast.GeneratorExp)
    ):
        if any(isinstance(n, (ast.UnaryOp, ast.BoolOp)) and isinstance(n.op, (ast.Not, ast.Or)) for n in ast.walk(conjunct)):
            return None
        stems = {
            module_stem(_string(side) or "")
            for n in ast.walk(conjunct)
            if isinstance(n, ast.Compare)
            for side in (n.left, *n.comparators)
            if _string(side) is not None
        }
        stems.discard("")
        return next(iter(stems)) if len(stems) == 1 else None
    return None


def _scan_of(expr: ast.AST, names: dict[str, ast.AST], callables: dict[str, ast.AST]) -> tuple[str, str, bool] | None:
    """``(module, population root, counted)`` when ``expr`` is a pure importer scan."""
    node, counted = _unwrap(expr)
    # A scan bound once to a name and printed by that name is the same scan.
    for _hop in range(8):
        if not (isinstance(node, ast.Name) and node.id in names):
            break
        node, again = _unwrap(names[node.id])
        if again and counted:
            return None
        counted = counted or again
    if not isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)) or len(node.generators) != 1:
        return None
    generator = node.generators[0]
    root = _population_root(generator.iter, names)
    if root is None:
        return None
    loop = _loop_names(generator.target)
    conjuncts: list[ast.AST] = []
    for condition in generator.ifs:
        if isinstance(condition, ast.BoolOp) and isinstance(condition.op, ast.And):
            conjuncts += condition.values
        else:
            conjuncts.append(condition)
    module = ""
    excluded: set[str] = set()
    for conjunct in conjuncts:
        if (
            isinstance(conjunct, ast.Compare)
            and len(conjunct.ops) == 1
            and isinstance(conjunct.ops[0], ast.NotEq)
            and _uses(conjunct, loop)
        ):
            constant = _string(conjunct.comparators[0]) or _string(conjunct.left)
            stem = module_stem(constant or "")
            if not stem:
                return None
            excluded.add(stem)
            continue
        stem = _membership_module(conjunct, loop, callables)
        if not stem or (module and stem != module):
            return None
        module = stem
    if not module or not excluded <= {module}:
        return None
    return module, root, counted


def importer_scans(text: str) -> list[ImporterScan]:
    """Every print in ``text`` that emits a pure importer scan."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    names = _assignments(tree.body)
    callables: dict[str, ast.AST] = {
        name: value for name, value in names.items() if isinstance(value, ast.Lambda)
    }
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            callables[statement.name] = statement
    scans: list[ImporterScan] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"):
            continue
        if node.keywords:
            continue
        label: str | None = None
        expr: ast.AST | None = None
        if len(node.args) == 2 and _string(node.args[0]) is not None:
            label, expr = (_string(node.args[0]) or "") + " ", node.args[1]
        elif len(node.args) == 1 and isinstance(node.args[0], ast.JoinedStr):
            values = node.args[0].values
            if (
                len(values) == 2
                and _string(values[0]) is not None
                and isinstance(values[1], ast.FormattedValue)
                and values[1].format_spec is None
                and values[1].conversion in (-1, ord("s"))
            ):
                label, expr = _string(values[0]), values[1].value
        if not label or not label.strip() or expr is None:
            continue
        found = _scan_of(expr, names, callables)
        if found is None:
            continue
        module, root, counted = found
        scans.append(
            ImporterScan(
                label=label,
                module=module,
                root=root,
                counted=counted,
                source=(ast.get_source_segment(text, node) or label)[:240],
            )
        )
    return scans


def _requires_none(scan: ImporterScan, literal: str, *, forbidden: bool, allowed: frozenset[str]) -> bool:
    """Does this expectation of ``scan``'s output demand that it find no importer?"""
    if not literal.startswith(scan.label):
        return False
    rest = literal[len(scan.label):].strip()
    if not forbidden:
        return rest == "0" if scan.counted else rest in _EMPTY_RENDERINGS
    if scan.counted:
        return False
    if rest.startswith(_NONEMPTY_OPENERS):
        try:
            value = ast.literal_eval(rest)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            # A prefix — `['` — forbids every non-empty result.
            return True
        # A complete rendering forbids only itself: unlawful when what it
        # forbids is some of the authorized importers and nothing else.
        if isinstance(value, (list, tuple, set, frozenset)) and value:
            stems = {module_stem(v) for v in value if isinstance(v, str)}
            return len(stems) == len(value) and "" not in stems and stems <= allowed
    return False


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------


def boundary_problems(generated: object, repository: "RepositoryText") -> list[str]:
    """Every expectation of ``generated`` that forbids an importer its repository authorizes."""
    from .outcome_contract import command_actions
    from .refusal_semantics import command_program

    if repository.repo is None:
        return []
    index = repository.import_boundaries()
    scenario_expected = [str(x) for x in getattr(generated, "expected_observations", []) or []]
    scenario_forbidden = [str(x) for x in getattr(generated, "forbidden_observations", []) or []]
    problems: list[str] = []
    for action_index, action in command_actions(generated):
        program = command_program(str(getattr(action, "command", "") or ""), repository)
        if program is None:
            continue
        expected = [str(x) for x in getattr(action, "expect_contains", []) or []] + scenario_expected
        for scan in importer_scans(program[1]):
            boundary = index.authorized(scan.module)
            if boundary is None or not boundary.allowed:
                continue
            inside = index.covered(boundary.allowed, scan.root)
            if not inside:
                continue
            offending = [
                (literal, False) for literal in expected
                if _requires_none(scan, literal, forbidden=False, allowed=boundary.allowed)
            ] + [
                (literal, True) for literal in scenario_forbidden
                if _requires_none(scan, literal, forbidden=True, allowed=boundary.allowed)
            ]
            for literal, forbidden in dict.fromkeys(offending):
                verb = "forbids" if forbidden else "expects"
                problems.append(
                    f"actions[{action_index}] {verb} {literal!r} of `{scan.source}`, which lists the "
                    f"importers of `{scan.module}` under {scan.root!r}: that demands `{scan.module}` "
                    f"have NO importer there, but this repository's own guard at {boundary.where} "
                    f"authorizes exactly {sorted(boundary.allowed)} to import it ({', '.join(inside)}). "
                    "Repository authority outranks the generated expectation: the scenario could "
                    "only pass against a product that removed wiring its authority requires. "
                    "Expect the scan to print exactly the authorized importers, or assert that no "
                    "importer OUTSIDE that set exists"
                )
    return problems


__all__ = [
    "Boundary",
    "ImportBoundaryIndex",
    "ImporterScan",
    "boundary_problems",
    "importer_scans",
    "module_stem",
]
