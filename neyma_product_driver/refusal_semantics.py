"""The semantics of each OPERATION inside a probe, read against the repository's own guards.

The outcome contract (:mod:`~neyma_product_driver.outcome_contract`) gives a
command one expected outcome — ``permitted`` or ``refused`` — and checks that
expectation against the authority the scenario CITES. Run 20260917-063502 is
what that left open. Its generated P8-S11 was a compound probe: a dozen
structural assertions that must succeed, and a final line

    GateRegistry({}, policy_version='pv1').gate_for('raise_invoice').gate.value

whose correct behaviour, since F-20, is to REFUSE. The scenario declared
``permitted`` and cited nothing. With no citation there was nothing for the
contract to contradict, so the correct refusal reached the gate as a product
failure, and the builder was asked for a product that would stop refusing.

The repository had already said what that operation does. Four of its own
guards run that exact shape inside ``except UnclassifiedActionClass`` and treat
the refusal as their pass condition. Repository authority beats a generated
expectation, and a generator that does not cite that authority must not be
able to outvote it by staying silent.

So this module reads a probe the way it will run — operation by operation —
and asks the repository's own verification what each operation IS:

* **the refusal index.** Every call a tracked repository file makes inside a
  guard that catches a TYPED refusal (an exception class the repository's own
  non-test source defines) — ``try/except``, ``pytest.raises``,
  ``assertRaises`` — is recorded by its closed SHAPE. A call a test function
  makes UNGUARDED is recorded too, as proof that shape is permitted.
* **refusal-only.** A shape the repository exercises only as a refusal, and
  never as a permitted call inside a test, is one its authority says refuses.
  A shape exercised both ways is ambiguous and is never used to refuse
  anything here.
* **closed shapes only.** A shape is the call with module aliases dropped and
  literal values abstracted by type, and it is recorded only when nothing in
  it depends on a variable. ``GateRegistry({}).gate_for('x')`` is fully
  determined by its text; ``registry.gate_for(name)`` is not, and nothing is
  concluded from it.

Then, per command action:

* **permitted + an unguarded refusal-only operation** is the P8-S11 shape. It
  is refused before execution, and a scenario already admitted is HELD on
  resume, never executed again to ask the product to stop refusing.
* **permitted + the refusal caught inside the probe** is lawful, but only when
  the action asserts text that only the refusal branch prints. Otherwise a
  product that silently defaulted would exit 0 through the same probe and
  pass.
* **refused + an assertion printed only after the refusing operation** can
  never be observed, and is refused as unsatisfiable.

### WHAT THIS DOES NOT DO. It never passes anything. Knowledge taken from the
repository's guards can only WITHHOLD a verdict — refuse a scenario, hold it.
Declaring ``refused`` still needs the documentary citation and the refusal
evidence the outcome contract demands. Nothing here reads prose, and no
product, class, method or path name is written into it.
"""

from __future__ import annotations

import ast
import builtins
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Iterable, Sequence

if TYPE_CHECKING:  # pragma: no cover
    from .outcome_contract import RepositoryText

#: Names a probe may call without importing them.
_BUILTIN_NAMES = frozenset(dir(builtins))
_BUILTIN_EXCEPTIONS = frozenset(
    name
    for name, value in vars(builtins).items()
    if isinstance(value, type) and issubclass(value, BaseException)
)

#: Context managers whose body is expected to raise the class they name.
_RAISING_CONTEXTS = frozenset({"raises", "assertRaises", "assertRaisesRegex", "suppress"})

#: The shortest fragment that can tie an assertion to a refusal branch. Mirrors
#: the outcome contract's refusal-evidence minimum.
MIN_FRAGMENT = 6

#: Guard the cost of reading a repository: files larger than this are skipped.
_MAX_FILE_BYTES = 2_000_000

_PYTHON_BASENAMES = ("python", "python3", "pypy", "pypy3")


# --------------------------------------------------------------------------
# Shapes
# --------------------------------------------------------------------------


def _imported_names(tree: ast.AST) -> set[str]:
    """Every name an ``import`` binds anywhere in ``tree``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
    return names


def _callee(node: ast.AST, imported: set[str]) -> str | None:
    """The spelling-independent name of what is called, or ``None`` if open."""
    if isinstance(node, ast.Name):
        if node.id in imported or node.id in _BUILTIN_NAMES:
            return node.id
        return None
    if isinstance(node, ast.Attribute):
        base = node.value
        # A dotted chain rooted in an imported module is the module's member:
        # `ck.GateRegistry` and `GateRegistry` are one thing.
        root = base
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id in imported and not _has_call(base):
            return node.attr
        inner = _shape(base, imported)
        return None if inner is None else f"{inner}.{node.attr}"
    return _shape(node, imported)


def _has_call(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) for n in ast.walk(node))


def _shape(node: ast.AST, imported: set[str]) -> str | None:
    """A closed, literal-abstracted spelling of an expression, or ``None``."""
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or value is None:
            return repr(value)
        if isinstance(value, (int, float, complex)):
            return "<num>"
        if isinstance(value, bytes):
            return "<bytes>"
        if isinstance(value, str):
            return "<str>"
        return None
    if isinstance(node, ast.JoinedStr):
        return "<str>" if all(isinstance(v, ast.Constant) for v in node.values) else None
    if isinstance(node, ast.Call):
        callee = _callee(node.func, imported)
        if callee is None:
            return None
        parts: list[str] = []
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                return None
            inner = _shape(arg, imported)
            if inner is None:
                return None
            parts.append(inner)
        for keyword in node.keywords:
            if keyword.arg is None:
                return None
            inner = _shape(keyword.value, imported)
            if inner is None:
                return None
            parts.append(f"{keyword.arg}={inner}")
        return f"{callee}({', '.join(parts)})"
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _callee(node, imported)
    if isinstance(node, ast.Dict):
        if not node.keys:
            return "{}"
        items: list[str] = []
        for key, value in zip(node.keys, node.values):
            if key is None:
                return None
            left, right = _shape(key, imported), _shape(value, imported)
            if left is None or right is None:
                return None
            items.append(f"{left}: {right}")
        return "{" + ", ".join(items) + "}"
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        opener, closer = {ast.List: "[]", ast.Tuple: "()", ast.Set: "{}"}[type(node)]
        items = []
        for element in node.elts:
            inner = _shape(element, imported)
            if inner is None:
                return None
            items.append(inner)
        return opener + ", ".join(items) + closer
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant):
        return _shape(node.operand, imported)
    return None


def _product_rooted(imported: set[str], node: ast.Call) -> bool:
    """Is this call's receiver chain rooted in something the file imported?"""
    root: ast.AST = node.func
    while True:
        if isinstance(root, ast.Attribute):
            root = root.value
        elif isinstance(root, ast.Call):
            root = root.func
        elif isinstance(root, ast.Subscript):
            root = root.value
        else:
            break
    return isinstance(root, ast.Name) and root.id in imported


def _strip_to_call(node: ast.AST) -> ast.AST:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node


def _class_names(node: ast.AST | None) -> list[str]:
    if node is None:
        return ["*"]
    if isinstance(node, ast.Tuple):
        out: list[str] = []
        for element in node.elts:
            out += _class_names(element)
        return out
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    return []


def _string_constants(node: ast.AST) -> list[str]:
    out: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            text = child.value.strip()
            if len(text) >= MIN_FRAGMENT:
                out.append(text)
    return out


# --------------------------------------------------------------------------
# Operations inside one program
# --------------------------------------------------------------------------


@dataclass
class Operation:
    """One product call inside a program, and how the program treats its failure."""

    shape: str
    source: str
    lineno: int
    #: The top-level statement the call belongs to, in program order.
    statement: int
    #: True when the top-level statement is a definition, so its order means nothing.
    deferred: bool
    #: Exception classes a guard around this call catches. Empty: unguarded.
    #: ``"*"``: a bare ``except``.
    guard_classes: list[str] = field(default_factory=list)
    #: Text only the refusal branch of that guard can produce.
    handler_literals: list[str] = field(default_factory=list)
    #: Whether the call sits inside a ``test_*`` function.
    in_test: bool = False
    #: Whether the call runs only when a lambda or helper is called, so its
    #: position says nothing about whether it is guarded.
    callable_scope: bool = False

    @property
    def guarded(self) -> bool:
        return bool(self.guard_classes)


@dataclass
class ProgramAnalysis:
    """A parsed program: its operations, and which statement prints what."""

    operations: list[Operation]
    #: String constants per top-level statement, excluding refusal branches.
    statement_literals: dict[int, list[str]]
    deferred_statements: set[int]
    #: Every string constant outside a refusal branch.
    ordinary_literals: list[str]


def analyse_program(text: str) -> ProgramAnalysis | None:
    """Every closed product operation ``text`` performs. ``None`` if unparseable."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    imported = _imported_names(tree)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    top: dict[ast.AST, int] = {}
    deferred: set[int] = set()
    for index, statement in enumerate(getattr(tree, "body", [])):
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            deferred.add(index)
        for node in ast.walk(statement):
            top[node] = index

    intermediates: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            inner = _strip_to_call(node.func)
            if isinstance(inner, ast.Call):
                intermediates.add(inner)

    handler_nodes: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                for inner in ast.walk(handler):
                    handler_nodes.add(inner)

    operations: list[Operation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node in intermediates:
            continue
        shape = _shape(node, imported)
        if shape is None or not _product_rooted(imported, node):
            continue
        classes: list[str] = []
        literals: list[str] = []
        in_test = False
        # Only what runs HERE can be judged here. A call inside a lambda or a
        # helper function runs wherever that callable is called, possibly under
        # a guard this position cannot see, so it is never read as unguarded.
        callable_scope = False
        nested = False
        at_statement = False
        cursor: ast.AST = node
        while cursor in parents:
            parent = parents[cursor]
            if isinstance(parent, ast.stmt):
                at_statement = True
            if (
                not at_statement
                and isinstance(parent, ast.Call)
                and cursor is not parent.func
                and _product_rooted(imported, parent)
            ):
                # An argument of another product call: the outer call is the
                # operation, and what it does with this value is its business.
                nested = True
            if isinstance(parent, ast.Lambda):
                callable_scope = True
                break
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if parent.name.startswith("test"):
                    in_test = True
                else:
                    callable_scope = True
                break
            if isinstance(parent, ast.Try) and cursor in parent.body and parent.handlers:
                for handler in parent.handlers:
                    classes += _class_names(handler.type)
                    for statement in handler.body:
                        literals += _string_constants(statement)
            if isinstance(parent, (ast.With, ast.AsyncWith)) and cursor in parent.body:
                for item in parent.items:
                    context = item.context_expr
                    if isinstance(context, ast.Call):
                        func = context.func
                        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                        if name in _RAISING_CONTEXTS:
                            for arg in context.args[:1]:
                                classes += _class_names(arg)
            cursor = parent
        if nested:
            continue
        statement = top.get(node, -1)
        operations.append(
            Operation(
                shape=shape,
                source=(ast.get_source_segment(text, node) or shape)[:240],
                lineno=getattr(node, "lineno", 0),
                statement=statement,
                deferred=statement in deferred,
                guard_classes=sorted(set(classes)),
                handler_literals=literals,
                in_test=in_test,
                callable_scope=callable_scope,
            )
        )

    statement_literals: dict[int, list[str]] = {}
    ordinary: list[str] = []
    for node in ast.walk(tree):
        if node in handler_nodes:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip()
            if len(value) < MIN_FRAGMENT:
                continue
            ordinary.append(value)
            index = top.get(node, -1)
            statement_literals.setdefault(index, []).append(value)
    return ProgramAnalysis(operations, statement_literals, deferred, ordinary)


# --------------------------------------------------------------------------
# What the repository's guards say each shape is
# --------------------------------------------------------------------------


@dataclass
class RefusalIndex:
    """Closed operation shapes the repository exercises as refusals, and as permits."""

    #: shape -> refusal class -> the places that guard it
    refusals: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    #: shapes a test function calls unguarded
    permitted: set[str] = field(default_factory=set)
    files_read: int = 0

    def refusal_only(self, shape: str) -> dict[str, list[str]]:
        """The typed refusals the repository requires of ``shape``, or ``{}``."""
        if shape in self.permitted:
            return {}
        return self.refusals.get(shape, {})

    @classmethod
    def build(cls, repository: "RepositoryText") -> "RefusalIndex":
        from .outcome_contract import _is_test_path

        index = cls()
        if repository.repo is None:
            return index
        parsed: list[tuple[str, ProgramAnalysis]] = []
        defined: set[str] = set()
        for path in sorted(repository.tracked()):
            if PurePosixPath(path).suffix != ".py":
                continue
            try:
                full = repository.repo / path
                if full.stat().st_size > _MAX_FILE_BYTES:
                    continue
                text = full.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            analysis = analyse_program(text)
            if analysis is None:
                continue
            index.files_read += 1
            parsed.append((path, analysis))
            if not _is_test_path(path):
                try:
                    tree = ast.parse(text)
                except (SyntaxError, ValueError, RecursionError):
                    continue
                defined.update(n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
        typed = defined - _BUILTIN_EXCEPTIONS
        for path, analysis in parsed:
            for operation in analysis.operations:
                if operation.guarded:
                    for name in operation.guard_classes:
                        if name in typed:
                            where = f"{path}:{operation.lineno}"
                            places = index.refusals.setdefault(operation.shape, {}).setdefault(name, [])
                            if where not in places:
                                places.append(where)
                elif operation.in_test and not operation.callable_scope:
                    index.permitted.add(operation.shape)
        return index


# --------------------------------------------------------------------------
# The program a command runs
# --------------------------------------------------------------------------


def _is_python(word: str) -> bool:
    base = PurePosixPath(word).name.lower()
    return any(base == name or base.startswith(name + ".") for name in _PYTHON_BASENAMES) or (
        base.startswith("python") and base[6:].replace(".", "").isdigit()
    )


def command_program(command: str, repository: "RepositoryText") -> tuple[str, str] | None:
    """``(label, source)`` of the Python program ``command`` runs, when readable.

    An inline ``-c`` program, or a tracked ``.py`` file handed to the
    interpreter. Anything else — a module run with ``-m``, a shell pipeline, a
    non-Python program — is not read, and nothing is concluded about it.
    """
    try:
        words = shlex.split(command or "")
    except ValueError:
        return None
    for position, word in enumerate(words):
        if not _is_python(word):
            continue
        rest = words[position + 1 :]
        cursor = 0
        while cursor < len(rest):
            item = rest[cursor]
            if item == "-c" and cursor + 1 < len(rest):
                return "inline program", rest[cursor + 1]
            if item == "-m":
                return None
            if item.startswith("-"):
                cursor += 1
                continue
            if item.endswith(".py") and repository.repo is not None and item in repository.tracked():
                try:
                    return item, (repository.repo / item).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    return None
            return None
        return None
    return None


# --------------------------------------------------------------------------
# Per-action semantics, and the ways they can be unlawful
# --------------------------------------------------------------------------


@dataclass
class OperationSemantics:
    """What one operation of one command must do, and why."""

    action_index: int
    operation: Operation
    #: What the repository requires of it: "refused", or "" when it says nothing.
    required: str
    refusal_classes: dict[str, list[str]]

    def cite(self) -> str:
        name, places = next(iter(sorted(self.refusal_classes.items())))
        more = f" and {len(places) - 1} more" if len(places) > 1 else ""
        return f"{name} (the repository's own guard at {places[0]}{more})"


def operation_semantics(generated: object, repository: "RepositoryText") -> list[OperationSemantics]:
    """Every operation of every command action, with what the repository requires of it."""
    from .outcome_contract import command_actions

    index = repository.refusal_index()
    out: list[OperationSemantics] = []
    for action_index, action in command_actions(generated):
        program = command_program(str(getattr(action, "command", "") or ""), repository)
        if program is None:
            continue
        analysis = repository.program_analysis(program[1])
        if analysis is None:
            continue
        for operation in analysis.operations:
            classes = index.refusal_only(operation.shape)
            out.append(
                OperationSemantics(
                    action_index=action_index,
                    operation=operation,
                    required="refused" if classes else "",
                    refusal_classes=classes,
                )
            )
    return out


def _shares_fragment(literal: str, sources: Iterable[str], barred: Sequence[str]) -> bool:
    """Does ``literal`` contain text that one of ``sources`` has and ``barred`` lacks?"""
    text = literal.strip()
    pool = [s for s in sources if s]
    if not text or not pool:
        return False
    for size in range(len(text), MIN_FRAGMENT - 1, -1):
        for start in range(0, len(text) - size + 1):
            fragment = text[start : start + size]
            if not fragment.strip() or len(fragment.strip()) < MIN_FRAGMENT:
                continue
            if any(fragment in s for s in pool) and not any(fragment in b for b in barred):
                return True
    return False


def _producers(literal: str, analysis: ProgramAnalysis) -> list[int]:
    """Top-level statements whose own text can have printed ``literal``."""
    wanted = literal.strip()
    found: list[int] = []
    for statement, constants in analysis.statement_literals.items():
        if any(c in wanted or wanted in c for c in constants):
            found.append(statement)
    return sorted(set(found))


def semantics_problems(generated: object, repository: "RepositoryText") -> list[str]:
    """Every way a command's declared outcome contradicts its operations' semantics."""
    from .outcome_contract import PERMITTED, REFUSED, command_actions

    if repository.repo is None:
        return []
    actions = dict(command_actions(generated))
    problems: list[str] = []
    by_action: dict[int, list[OperationSemantics]] = {}
    for item in operation_semantics(generated, repository):
        by_action.setdefault(item.action_index, []).append(item)

    for action_index, items in sorted(by_action.items()):
        action = actions[action_index]
        outcome = getattr(action, "expect_outcome", None)
        where = f"actions[{action_index}]"
        refusing = [
            i
            for i in items
            if i.required and not i.operation.guarded and not i.operation.callable_scope
        ]
        handled = [
            i
            for i in items
            if i.required
            and i.operation.guarded
            and ("*" in i.operation.guard_classes or set(i.operation.guard_classes) & set(i.refusal_classes))
        ]
        if outcome == PERMITTED:
            for item in refusing:
                problems.append(
                    f"{where} expects its command to be PERMITTED, but the probe calls "
                    f"`{item.operation.source}` unguarded, and this repository's own guards "
                    f"exercise that operation only as the refusal {item.cite()}. Repository "
                    "authority outranks the generated expectation: the command could only exit "
                    "0 against a product that stopped refusing. Either catch and verify the "
                    "refusal inside the probe and assert what only that branch prints, or "
                    "declare expect_outcome='refused' with refusal evidence and a citation"
                )
            if not handled:
                continue
            analysis = repository.program_analysis(
                (command_program(str(getattr(action, "command", "")), repository) or ("", ""))[1]
            )
            barred = analysis.ordinary_literals if analysis is not None else []
            asserted = list(getattr(action, "expect_contains", []) or [])
            for item in handled:
                if not any(
                    _shares_fragment(literal, item.operation.handler_literals, barred)
                    for literal in asserted
                ):
                    problems.append(
                        f"{where} catches the required refusal of `{item.operation.source}` "
                        f"({item.cite()}) inside the probe but asserts nothing only that "
                        "refusal branch prints; a product that silently defaulted would exit 0 "
                        "through the same probe and pass. Assert the refusal branch's own output"
                    )
        elif outcome == REFUSED and refusing:
            first = min(
                (i.operation for i in refusing if not i.operation.deferred),
                key=lambda op: op.statement,
                default=None,
            )
            if first is None:
                continue
            analysis = repository.program_analysis(
                (command_program(str(getattr(action, "command", "")), repository) or ("", ""))[1]
            )
            if analysis is None:
                continue
            for literal in getattr(action, "expect_contains", []) or []:
                producers = [
                    p for p in _producers(str(literal), analysis) if p not in analysis.deferred_statements
                ]
                if producers and min(producers) > first.statement:
                    problems.append(
                        f"{where} expects {literal!r}, which the probe prints only after "
                        f"`{first.source}` — the operation whose refusal ends the program — so "
                        "it can never be observed"
                    )
    return problems


def refusal_note(command: str, repository: "RepositoryText") -> str:
    """A generation-brief annotation for an approved command, or ``""``."""
    program = command_program(command, repository)
    if program is None:
        return ""
    analysis = repository.program_analysis(program[1])
    if analysis is None:
        return ""
    index = repository.refusal_index()
    for operation in analysis.operations:
        classes = index.refusal_only(operation.shape)
        if classes and not operation.guarded and not operation.callable_scope:
            name = sorted(classes)[0]
            return (
                f"REFUSING OPERATION — this program calls `{operation.source[:120]}` unguarded, "
                f"and the repository exercises that call only as the refusal {name}. Reuse it "
                "only with expect_outcome='refused' (refusal evidence and a citation), and "
                "assert only text it prints before that call."
            )
    return ""


__all__ = [
    "MIN_FRAGMENT",
    "Operation",
    "OperationSemantics",
    "ProgramAnalysis",
    "RefusalIndex",
    "analyse_program",
    "command_program",
    "operation_semantics",
    "refusal_note",
    "semantics_problems",
]
