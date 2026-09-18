"""Ground a named risk in concrete subjects, and measure the ones that are structural.

Run 20260917-063502 ended with two acceptance-blocking risks it could never
discharge::

    R8                 The rule capability is wired into production (a production
                       importer, a channel/adapter/network/timer import) rather than
                       shipping dark ...
    RISK-R8-ships-dark The rule capability is wired into production through a
                       channel/adapter/network/timer import (or reached from outside
                       the package) ... violating the ships-dark posture.

Two sentences, one hypothesis, and neither names a file. The guard ledger
(:mod:`~neyma_product_driver.guard_coverage`) correctly refused to hold a guard
against a risk that names nothing, so both stayed "uncovered". Meanwhile the
run had executed, green on the judged tree, the repository's own guard for
exactly that property — a test that walks every import statement of the
package, asserts the importer set of the rule machine, and carries its own
positive control. Generation could not close the gap either: the scenarios it
proposed for ships-dark were refused as out-of-scope regressions. The
obligation had no admissible way to be discharged.

This module supplies the missing step between "a risk was written down" and "a
risk blocks acceptance":

* **grounding.** A risk's subjects are what it names (paths, identifiers — the
  guard ledger's own reading), plus the product modules THE TASK changed that
  the risk refers to by name: ``the rule capability`` is ``rule.py`` when the
  task changed ``rule.py``. A module named outright wins, then an exact
  module-name match of an ordinary word; only when there is neither are
  modules whose name merely contains the word considered. A compound name is
  one subject: ``rule_admission.py`` does not also mean ``rule.py``.
  The modules come from the run's base, not from the last commit, so a later
  verification-only commit cannot erase what a risk is about; the measurement
  of them is always the judged tree's.
* **the property.** A small, closed set of hypotheses is STRUCTURAL: it can be
  decided by reading the tree rather than running the product. Today there is
  one — :data:`REACHABILITY`, "this capability is wired into / reachable from
  the live path". It is recognised from the risk's words, and recognising it
  only chooses which measurement may be asked for. Recognising it never
  discharges anything.
* **one obligation per semantic risk.** Two risks with the same category, the
  same structural property and the same resolved repository modules are one
  obligation, however differently they are worded and whatever package name or
  milestone label one of them also mentions. Risks whose subjects differ stay
  distinct, and a risk with no structural property is never merged with
  anything.
* **the driver's own measurement.** :func:`measure_reachability` reads every
  tracked Python file's imports and decides, per capability module, whether
  any product module imports it, whether it is itself an entry point, and
  whether anything names it for dynamic import. It says DARK only when all of
  that is empty AND its positive control fired: the same resolver found the
  module imported by the repository's own verification, so an empty product
  answer cannot be a resolver that simply stopped finding imports. A module
  some product code imports is IMPORT_REACHABLE, which is recorded and never
  read as a refutation — an import is not a live binding, and deciding
  whether that importer is live needs the repository's own guard or a
  scenario.

No product, module, phase or test name is written into this module.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .guard_coverage import normalise, subjects

#: The one structural hypothesis this module recognises and can measure.
REACHABILITY = "import_reachability"

#: Measurement statuses.
DARK = "dark"
IMPORT_REACHABLE = "import_reachable"
UNMEASURED = "unmeasured"

#: A risk about reaching the live path: it names the live side ...
_LIVE_TERMS = re.compile(
    r"\b(?:production|live(?:\s+path)?|reach(?:ed|able)?|entry[- ]?points?|ships?[- ]dark|shipping\s+dark)\b",
    re.I,
)
#: ... and is not about wiring that went MISSING, which is the opposite hypothesis
#: and would be "discharged" by exactly the scan that proves it realised.
_ABSENCE_TERMS = re.compile(
    r"\b(?:no\s+longer|removed|remov(?:es|ing)|stops?|stopped|missing|dropped|unwired|"
    r"unreachable|not\s+(?:wired|reachable|imported|called)|never\s+(?:wired|reached|called))\b",
    re.I,
)
#: ... and the way something would get there.
_WIRING_TERMS = re.compile(
    r"\b(?:import(?:s|ed|er|ers)?|adapters?|channels?|network|timers?|routes?|"
    r"entry[- ]?points?|wired|imported)\b",
    re.I,
)

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]+")

#: Words too common in risk prose to refer to a module.
_STOP: frozenset[str] = frozenset(
    {
        "the", "and", "into", "from", "with", "that", "this", "than", "rather", "before",
        "after", "letting", "being", "been", "which", "while", "where", "when", "through",
        "outside", "inside", "path", "live", "production", "wired", "import", "importer",
        "importers", "imports", "channel", "adapter", "network", "timer", "shipping", "ships",
        "dark", "deliberate", "activation", "human", "package", "reached", "violating",
        "posture", "capability", "act", "observe", "module", "modules", "code", "test",
        "tests", "init", "main", "core", "base", "util", "utils", "common", "config",
    }
)

_MIN_WORD = 4

#: Files that name an entry point for an installed program.
_ENTRY_DECLARATIONS = ("pyproject.toml", "setup.cfg", "setup.py", "Procfile")


def _singular(word: str) -> str:
    low = word.lower()
    if len(low) > _MIN_WORD and low.endswith("s") and not low.endswith("ss"):
        return low[:-1]
    return low


def risk_words(text: str) -> set[str]:
    return {
        _singular(w)
        for w in _WORD.findall(text or "")
        if len(w) >= _MIN_WORD and _singular(w) not in _STOP
    }


def hypothesis_property(text: str) -> str:
    """The structural property a risk hypothesises, or ``""``."""
    body = text or ""
    if _ABSENCE_TERMS.search(body):
        return ""
    if _LIVE_TERMS.search(body) and _WIRING_TERMS.search(body):
        return REACHABILITY
    return ""


def _is_verification(path: str) -> bool:
    from .changed_verification import VERIFICATION_FILE
    from .outcome_contract import _is_test_path

    return bool(VERIFICATION_FILE.search(path)) or _is_test_path(path)


def product_modules(paths: Iterable[str]) -> list[str]:
    """The changed Python files that are product code rather than verification."""
    out: list[str] = []
    for raw in paths:
        path = str(raw).strip()
        if not path.endswith(".py") or _is_verification(path):
            continue
        if PurePosixPath(path).name == "__init__.py":
            continue
        if path not in out:
            out.append(path)
    return out


class Grounding(BaseModel):
    """What one risk is about, concretely."""

    model_config = ConfigDict(extra="forbid")

    #: The structural property the risk hypothesises (``""``: none).
    hypothesis: str = ""
    #: Normalised subjects: what the risk names, plus the modules it refers to.
    subjects: list[str] = Field(default_factory=list)
    #: The changed product modules the risk refers to, as repository paths.
    capability_paths: list[str] = Field(default_factory=list)
    basis: list[str] = Field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return bool(self.subjects)

    def key(self, category: str) -> tuple[str, str, tuple[str, ...]] | None:
        """The identity of the obligation, when it can be compared at all.

        Keyed on the repository modules the risk resolved to, not on every token
        its prose happened to contain: "the rule capability" and "the U8.2 rule
        capability (rule.py / M12) ... outside the freight_recon package" are
        the same hypothesis about the same module, and a package name or a
        milestone label in one wording must not make them two obligations.
        """
        if not self.hypothesis or not self.capability_paths:
            return None
        return (category, self.hypothesis, tuple(sorted(self.capability_paths)))


#: A compound identifier or path — ``rule_admission``, ``rule.py``,
#: ``src/pkg/mod.py``. Its parts are not separate words about separate things.
_COMPOUND = re.compile(r"\b\w+(?:[./]\w+)+\b|\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b")


def ground(description: str, diff_files: Sequence[str]) -> Grounding:
    """Ground one risk description against the task's changed product modules.

    A module the risk names outright is its subject. Only the risk's ordinary
    words are then matched against module names, with compound identifiers
    removed first: a risk about ``rule_admission.py`` is not a risk about
    ``rule.py`` because the one name contains the other's word.
    """
    named = subjects(description or "")
    changed = product_modules(diff_files)
    named_modules = [p for p in changed if normalise(p) in named]
    words = risk_words(_COMPOUND.sub(" ", description or ""))
    exact = [
        p for p in changed if p not in named_modules and _singular(normalise(p)) in words
    ]
    loose: list[str] = []
    if not named_modules and not exact:
        for path in changed:
            tokens = {_singular(t) for t in normalise(path).split("_") if len(t) >= _MIN_WORD}
            if tokens & words:
                loose.append(path)
    capability = named_modules + exact or loose
    grounding = Grounding(
        hypothesis=hypothesis_property(description),
        subjects=list(dict.fromkeys(named + [normalise(p) for p in capability])),
        capability_paths=capability,
    )
    if named:
        grounding.basis.append("named by the risk: " + ", ".join(named[:6]))
    if capability:
        grounding.basis.append(
            ("changed modules the risk names: " if not loose else "changed modules sharing a word with the risk: ")
            + ", ".join(capability)
        )
    return grounding


# --------------------------------------------------------------------------
# The driver's structural measurement
# --------------------------------------------------------------------------


class ModuleReachability(BaseModel):
    """Whether one module is reachable from product code, as read from one tree."""

    model_config = ConfigDict(extra="forbid")

    path: str
    status: str = UNMEASURED
    #: Product modules that import it, statically or by dotted name.
    product_importers: list[str] = Field(default_factory=list)
    #: Verification files that import it — the resolver's positive control.
    control_importers: list[str] = Field(default_factory=list)
    #: Why it is itself an entry point, when it is.
    entry_point: str = ""
    #: The population the scan covered.
    modules_scanned: int = 0
    tree: str = ""
    detail: str = ""

    def brief(self) -> str:
        control = (
            f"positive control: the same resolver found it imported by "
            f"{len(self.control_importers)} verification file(s) "
            f"({', '.join(self.control_importers[:2])})"
        )
        if self.status == DARK and self.product_importers:
            return (
                f"{self.path}: DARK on tree {self.tree[:12]} — imported by "
                f"{', '.join(self.product_importers[:3])}, and {self.detail}; {control}"
            )
        if self.status == DARK:
            return (
                f"{self.path}: DARK on tree {self.tree[:12]} — no product module of "
                f"{self.modules_scanned} imports it or names it and it is no entry point; "
                f"{control}"
            )
        if self.status == IMPORT_REACHABLE:
            reach = self.detail or self.entry_point or ", ".join(self.product_importers[:3])
            return f"{self.path}: REACHABLE on tree {self.tree[:12]} — {reach}"
        return f"{self.path}: not measured — {self.detail}"


def _module_names(path: str) -> list[str]:
    pure = PurePosixPath(path)
    parts = list(pure.parent.parts if pure.stem == "__init__" else pure.with_suffix("").parts)
    names: list[str] = []
    for start in range(len(parts)):
        dotted = ".".join(parts[start:])
        if dotted and dotted not in names:
            names.append(dotted)
    return names


def _package_parts(path: str) -> list[str]:
    return list(PurePosixPath(path).parent.parts)


def _imports(path: str, tree: ast.AST) -> set[str]:
    """Every dotted module name ``tree`` imports, relative imports resolved."""
    found: set[str] = set()
    package = _package_parts(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)] if node.level > 1 else list(package)
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ""
            if prefix:
                found.add(prefix)
            for alias in node.names:
                if alias.name != "*":
                    found.add(f"{prefix}.{alias.name}" if prefix else alias.name)
    return found


def _names_module(target: set[str], imported: set[str]) -> bool:
    for name in imported:
        for candidate in target:
            if name == candidate or name.endswith("." + candidate):
                return True
            if "." in name and candidate.endswith("." + name):
                return True
    return False


def _main_guarded(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            left = node.test.left
            if isinstance(left, ast.Name) and left.id == "__name__":
                return True
    return False


#: How far a transitive reachability walk may go before it stops claiming anything.
MAX_CLOSURE = 250


class _ImportGraph:
    """Who imports whom, across every tracked Python file of one tree."""

    def __init__(self, repo: Path) -> None:
        from .outcome_contract import RepositoryText

        self.repo = Path(repo)
        tracked = sorted(RepositoryText(self.repo).tracked())
        self.parsed: dict[str, tuple[ast.AST, str]] = {}
        for path in tracked:
            if not path.endswith(".py"):
                continue
            try:
                source = (self.repo / path).read_text(encoding="utf-8")
                self.parsed[path] = (ast.parse(source), source)
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError, RecursionError):
                continue
        self.declarations = ""
        for name in _ENTRY_DECLARATIONS:
            if name in tracked:
                try:
                    self.declarations += (self.repo / name).read_text(encoding="utf-8") + "\n"
                except (OSError, UnicodeDecodeError):
                    pass
        self.imports = {path: _imports(path, node) for path, (node, _source) in self.parsed.items()}
        self._importers: dict[str, tuple[list[str], list[str]]] = {}

    def importers(self, module: str) -> tuple[list[str], list[str]]:
        """``(product importers, verification importers)`` of one module."""
        if module in self._importers:
            return self._importers[module]
        names = set(_module_names(module))
        # The last component alone is too loose to identify a module; it is only
        # used together with the package it lives in.
        qualified = {n for n in names if "." in n} or names
        stem = PurePosixPath(module).parent.name if PurePosixPath(module).stem == "__init__" else PurePosixPath(module).stem
        product: list[str] = []
        control: list[str] = []
        for path, imported in self.imports.items():
            if path == module:
                continue
            hit = _names_module(qualified, imported)
            if not hit and _package_parts(path) == _package_parts(module):
                hit = stem in imported or any(i.endswith("." + stem) for i in imported)
            verification = _is_verification(path)
            if not hit and not verification:
                source = self.parsed[path][1]
                # Named for a dynamic import: an importlib string is still a route.
                hit = any(f"'{n}'" in source or f'"{n}"' in source for n in qualified)
            if hit:
                (control if verification else product).append(path)
        self._importers[module] = (product, control)
        return product, control

    def entry_point(self, module: str) -> str:
        if PurePosixPath(module).name == "__main__.py":
            return "it is a package's `__main__`"
        node = self.parsed[module][0]
        if _main_guarded(node):
            return "it runs as a program (`if __name__ == ...`)"
        qualified = {n for n in _module_names(module) if "." in n}
        if any(n in self.declarations for n in qualified):
            return "a packaging or process declaration names it"
        return ""


def measure_reachability(
    repo: Path, capability_paths: Sequence[str], *, tree: str = ""
) -> list[ModuleReachability]:
    """Measure each module's reachability from product code on the current tree.

    DARK means: no chain of product imports that reaches the module starts at
    an entry point, a dynamic import names none of the modules on those chains,
    and — the positive control — the same resolver found the module imported by
    the repository's own verification. A module imported only by modules that
    nothing imports is dark: dead code cannot bring anything to life.
    """
    graph = _ImportGraph(Path(repo))
    out: list[ModuleReachability] = []
    for capability in capability_paths:
        record = ModuleReachability(path=capability, tree=tree, modules_scanned=len(graph.parsed))
        if capability not in graph.parsed:
            record.detail = "the module is not a tracked, parseable Python file on this tree"
            out.append(record)
            continue
        product, control = graph.importers(capability)
        record.product_importers = list(product)
        record.control_importers = list(control)
        record.entry_point = graph.entry_point(capability)
        if record.entry_point:
            record.status = IMPORT_REACHABLE
            out.append(record)
            continue
        # Walk every product chain that reaches the capability.
        seen: dict[str, str] = {}
        frontier = list(product)
        live = ""
        roots: list[str] = []
        while frontier:
            module = frontier.pop(0)
            if module in seen or module == capability:
                continue
            if len(seen) >= MAX_CLOSURE:
                record.detail = (
                    f"more than {MAX_CLOSURE} product modules transitively import it, so this "
                    "scan does not claim it is dark"
                )
                break
            reason = graph.entry_point(module) if module in graph.parsed else ""
            seen[module] = reason
            if reason:
                live = f"{module} ({reason})"
                break
            above, _ = graph.importers(module)
            if not above:
                roots.append(module)
            frontier.extend(m for m in above if m not in seen)
        if live:
            record.status = IMPORT_REACHABLE
            record.detail = f"a product import chain reaches it from the entry point {live}"
        elif record.detail:
            pass
        elif not control:
            # Only a DARK answer needs the control: an empty result from a
            # resolver that sees none of this module's imports proves nothing.
            record.detail = (
                "no product chain from an entry point was found, but the resolver found no "
                "verification file importing it either, so an empty answer cannot be told "
                "from a resolver that does not see this module's imports"
            )
        else:
            record.status = DARK
            if product:
                record.detail = (
                    f"every product import chain into it ({len(seen)} module(s)) ends at "
                    "module(s) nothing imports and that are no entry point: "
                    + ", ".join(sorted(roots)[:4])
                )
        out.append(record)
    return out


def reachability_evidence(
    grounding: Grounding, measurements: Sequence[ModuleReachability], *, tree: str = ""
) -> tuple[bool, str]:
    """``(discharges, why)`` for a reachability risk from the driver's own scan.

    ``tree`` is the tree being judged. A record read from any other tree is not
    evidence about this one, however DARK it was: the subject of a risk is
    stable across the task, its measurement never is.
    """
    if grounding.hypothesis != REACHABILITY or not grounding.capability_paths:
        return False, ""
    by_path = {m.path: m for m in measurements if not tree or m.tree == tree}
    missing = [p for p in grounding.capability_paths if p not in by_path]
    if missing:
        return False, (
            "no structural reachability measurement was taken of "
            + ", ".join(missing)
            + (f" on tree {tree[:12]}" if tree else "")
        )
    records = [by_path[p] for p in grounding.capability_paths]
    open_ = [r for r in records if r.status != DARK or not r.control_importers]
    if open_:
        return False, "; ".join(r.brief() for r in open_) + (
            " — a static import chain from an entry point is not proof of a live binding, "
            "so this neither discharges nor refutes the risk; the repository's own guard or "
            "a generated scenario must measure it"
        )
    return True, "; ".join(r.brief() for r in records)


def guard_structural_evidence(grounding: Grounding, measurements: Sequence[Any]) -> tuple[Any, Any, str]:
    """A changed guard with ONE test that structurally measures this risk.

    ``(measurement, test, why-not)``. The test must itself name every grounded
    subject, measure the grounded property, and carry its own positive control:
    a structural absence proved by a scan that could have come back empty for
    the wrong reason proves nothing.
    """
    if not grounding.hypothesis or not grounding.subjects:
        return None, None, ""
    wanted = set(grounding.subjects)
    partial = ""
    for measurement in measurements:
        for test in getattr(measurement, "tests", []) or []:
            if grounding.hypothesis not in test.properties:
                continue
            if not wanted <= set(test.subjects):
                partial = partial or (
                    f"{measurement.path}::{test.name} measures {grounding.hypothesis} but does not "
                    "name " + ", ".join(sorted(wanted - set(test.subjects))[:4])
                )
                continue
            if not test.positive_control:
                partial = partial or (
                    f"{measurement.path}::{test.name} measures {grounding.hypothesis} over "
                    + ", ".join(sorted(wanted))
                    + " but carries no positive control, so its absence could be vacuous"
                )
                continue
            if not measurement.executable:
                partial = partial or f"{measurement.path} could not be executed here"
                continue
            return measurement, test, ""
    return None, None, partial


__all__ = [
    "DARK",
    "Grounding",
    "IMPORT_REACHABLE",
    "ModuleReachability",
    "REACHABILITY",
    "UNMEASURED",
    "ground",
    "guard_structural_evidence",
    "hypothesis_property",
    "measure_reachability",
    "product_modules",
    "reachability_evidence",
    "risk_words",
]
