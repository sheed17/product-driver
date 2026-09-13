"""When the measurement a named risk needs is a guard this run already ran.

The sibling question to :mod:`~neyma_product_driver.changed_verification`. That
module answers "was the guard this change delivered ever operated?" and, since
the changed-verification repair, the answer is yes: the driver runs it, runs the
repository's own anti-vacuity case beside it, and reads the exit status itself.

This module answers the one that came next. A run whose whole deliverable was a
reconciliation guard executed that guard, green, with its forced-drift
discrimination case; the evaluator accepted; every scenario passed — and the run
still ended BLOCKED, because the risk-coverage ledger recognised exactly two
kinds of evidence, both of them scenarios::

    conflicting_evidence — the live-status restatement in CURRENT.md could drift
    from IMPLEMENTATION-REGISTRY ... no scenario exercising this risk was
    executed

Which was, by then, false in substance and true only about the shape of the
record. The direct oracle for that risk was sitting in the run's own evidence
directory with a passing exit status. Asking for a generated scenario on top of
it is asking the founder to relay a measurement the driver is holding.

So a third kind of evidence exists here, and it is deliberately the narrowest one
that closes that hole:

    **a repository-native guard that this run CHANGED, executed by this driver,
    that passed, and whose own assertions demonstrably measure every subject the
    risk names.**

Every clause in that sentence is load-bearing, and what is NOT here matters more
than what is:

* **grounded, never guessed.** The link is not a filename, not a similarity
  score, not a category the model wrote down. It is the set of concrete
  repository artifacts and identifiers the risk NAMES, checked against the ones
  the guard's own changed assertion span actually reads. A guard's own file name
  is excluded from its side of that comparison, because a driver that accepts
  ``test_current_status_reconciliation.py`` as proof about "current status
  reconciliation" has learned to read labels;
* **only the deliverable.** ``related`` guards — the repository's standing
  checks over the other files in the diff — never discharge a risk. They are
  collateral, and an unrelated green is exactly the evidence the whole
  changed-verification repair exists to refuse;
* **all of the risk, or none of it.** A guard that measures some of what a risk
  names discharges nothing. The risk stays open, the gap names the part still
  unmeasured, and the generated scenario that covers it is still required;
* **a negative guard owes its control.** A guard that asserts an absence passes
  vacuously the moment it stops looking, so it counts only when the repository's
  own discrimination / anti-vacuity case was observed passing beside it;
* **thin risks stay with the scenarios.** A risk that names fewer than
  :data:`MIN_SUBJECTS` concrete artifacts has not said enough for anything here
  to be checkable, and falls through to the ordinary path.

Nothing here reads a builder's report, an evaluator's prose, or a model's claim
about what a test covers. The inputs are the run's own risk register and the
executed-command records of the changed-verification obligation, both of which
are persisted, so a resumed run reaches the same answer from the same evidence.

No product, phase, unit, criterion or test name appears anywhere in this module.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# What a risk NAMES, and what a guard READS
# --------------------------------------------------------------------------

#: A concrete repository artifact: something with a file extension. The most
#: checkable thing a risk can name, because a guard either opens it or does not.
_ARTIFACT = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9_.\-/]*\."
    r"(?:py|pyi|md|markdown|ya?ml|json|jsonl|toml|cfg|ini|txt|rst|sql|csv|"
    r"ts|tsx|js|jsx|go|rs|rb|java|kt|sh|env|lock|proto|graphql)\b"
)

#: A declared constant, marker or state token: ``IMPLEMENTATION-REGISTRY``,
#: ``ROUTE_NOT_CONFIGURED``, ``LIVE-STATUS``. Upper case with a separator, so an
#: ordinary capitalised word at the start of a sentence is never one.
_SHOUTED = re.compile(r"\b[A-Z][A-Z0-9]*(?:[_-][A-Z0-9]+)+\b")

#: A field, function or attribute name: ``execution_state``, ``readiness_target``.
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

#: A type or class name with at least two humps: ``GateRegistry``.
_CAMEL = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")

#: Tokens that name nothing in particular. Present in almost every Python file
#: and in a good deal of English, so a match on one of them carries no
#: information about whether a guard measures a risk.
_GENERIC: frozenset[str] = frozenset(
    {
        "test", "tests", "conftest", "setup", "teardown", "fixture", "fixtures",
        "init", "main", "readme", "license", "licence", "changelog", "makefile",
        "pyproject", "requirements", "index", "utils", "util", "helpers", "helper",
        "common", "base", "core", "config", "settings", "constants", "types",
        "self", "none", "true", "false", "cls", "args", "kwargs",
        "read_text", "write_text", "read_bytes", "write_bytes", "safe_load",
        "splitlines", "startswith", "endswith", "strip", "resolve", "parents",
        "encoding", "utf_8", "add_argument", "model_dump", "model_validate",
        "type_error", "value_error", "os_error", "key_error", "index_error",
        "runtime_error", "assertion_error", "file_not_found_error",
        "not_a", "is_a", "such_that", "so_that", "rather_than", "as_well",
    }
)

#: The shortest token that can carry information. Three characters and under is
#: an abbreviation shared by everything.
MIN_TOKEN = 4

#: How many distinct artifacts a risk must name before a guard may be checked
#: against it at all. One shared token is a coincidence; this rule wants the
#: risk's whole named subject matter, so it asks the risk to have named some.
MIN_SUBJECTS = 2

_EXTENSION = re.compile(
    r"\.(?:py|pyi|md|markdown|ya?ml|json|jsonl|toml|cfg|ini|txt|rst|sql|csv|"
    r"ts|tsx|js|jsx|go|rs|rb|java|kt|sh|env|lock|proto|graphql)$",
    re.I,
)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def normalise(token: str) -> str:
    """One artifact name in the single spelling both sides are compared in.

    ``docs/impl/IMPLEMENTATION-REGISTRY.yaml``, ``IMPLEMENTATION-REGISTRY`` and
    ``implementation_registry`` are the same subject written three ways, and a
    rule that could not see that would be a rule about punctuation.
    """
    text = token.strip().strip("`'\"()[]{}<>,;:.!?")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    text = _EXTENSION.sub("", text)
    text = _CAMEL_BOUNDARY.sub("_", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text


def subjects(text: str, *, exclude: Sequence[str] = ()) -> list[str]:
    """The concrete repository artifacts a piece of text names, normalised.

    Deliberately blind to ordinary prose. Only four token shapes are read — a
    path with an extension, a shouted constant, a snake_case identifier, a
    CamelCase name — because those are the things a guard can be said to open,
    read or assert about. "drift", "authority" and "silently" name nothing a
    command could be pointed at, and this returns none of them.
    """
    barred = {normalise(e) for e in exclude}
    found: list[str] = []
    for pattern in (_ARTIFACT, _SHOUTED, _SNAKE, _CAMEL):
        for raw in pattern.findall(text or ""):
            token = normalise(raw)
            if len(token) < MIN_TOKEN or token in _GENERIC or token in barred:
                continue
            if token not in found:
                found.append(token)
    return found


# --------------------------------------------------------------------------
# One executed guard, as evidence
# --------------------------------------------------------------------------


class GuardMeasurement(BaseModel):
    """One repository-native guard this run changed, ran, and read the result of.

    Built only from records of commands this driver executed. There is no field
    a builder, an evaluator or a generator can write into.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = ""
    command: str = ""
    #: The command's exit status, as this driver read it.
    passed: bool = False
    #: False when the guard never reached an assertion here — a fact about the
    #: environment, and never evidence about a risk in either direction.
    executable: bool = True
    #: What the assertions this change MOVED actually read. See
    #: :func:`~neyma_product_driver.changed_verification.assertion_span`.
    measured: list[str] = Field(default_factory=list)
    #: Whether this guard asserts an absence, and so owes an anti-vacuity case.
    negative: bool = False
    #: The repository's own discrimination cases, observed passing beside it.
    discrimination: list[str] = Field(default_factory=list)
    detail: str = ""

    @property
    def discriminating(self) -> bool:
        """A green this driver is willing to read as a measurement.

        A positive assertion proves itself by passing. An absence-asserting one
        does not: it passes just as green when it has stopped looking, so the
        repository's own control has to have been observed beside it.
        """
        return (not self.negative) or bool(self.discrimination)

    def covers(self, wanted: Sequence[str]) -> bool:
        return bool(wanted) and set(wanted) <= set(self.measured)

    def brief(self) -> str:
        verdict = "PASS" if self.passed else ("COULD NOT RUN" if not self.executable else "FAIL")
        line = f"{self.path}: {verdict} via `{self.command}`"
        if self.discrimination:
            line += " — with its discrimination case(s): " + ", ".join(self.discrimination[:3])
        return line


def guard_measurements(verification: Any) -> list[GuardMeasurement]:
    """The executed changed guards of one run, in a form the gate can weigh.

    ``verification`` is a
    :class:`~neyma_product_driver.changed_verification.ChangedSurfaceVerification`,
    read through ``getattr`` so the gate does not depend on that module and a
    run without the obligation simply contributes nothing.

    Only ``changed`` guards appear. A ``related`` guard — one the repository
    already kept over another file in the diff — is collateral this run did not
    deliver, and letting collateral discharge a named risk is the "any green
    test counts" failure this whole path exists to refuse.
    """
    if verification is None:
        return []
    guards = {
        str(getattr(g, "path", "")): g
        for g in (getattr(verification, "guards", None) or [])
    }
    out: list[GuardMeasurement] = []
    for result in getattr(verification, "results", None) or []:
        target = getattr(result, "target", None)
        path = str(getattr(target, "path", "") or "")
        guard = guards.get(path)
        if guard is None or str(getattr(guard, "relation", "")) != "changed":
            continue
        out.append(
            GuardMeasurement(
                path=path,
                command=str(getattr(target, "command", "") or ""),
                passed=bool(getattr(result, "passed", False)),
                executable=not bool(getattr(result, "infrastructure", False)),
                measured=[str(s) for s in (getattr(guard, "measured_subjects", None) or [])],
                negative=bool(getattr(guard, "negative_names", None)),
                discrimination=[
                    str(n) for n in (getattr(guard, "discrimination_names", None) or [])
                ],
                detail=str(getattr(result, "detail", "") or "")[:300],
            )
        )
    return out


# --------------------------------------------------------------------------
# Whether one guard measures one risk
# --------------------------------------------------------------------------


class GuardVerdict(BaseModel):
    """Whether this run's directly executed guards measure one named risk."""

    model_config = ConfigDict(extra="forbid")

    #: The artifacts the risk itself names. Empty or short means the risk has
    #: not said enough for anything here to be checkable.
    named: list[str] = Field(default_factory=list)
    #: The guard that measures the most of them, when any measures any.
    measurement: GuardMeasurement | None = None
    covered: list[str] = Field(default_factory=list)
    #: Named subjects no changed guard was observed to read. Non-empty means a
    #: generated scenario is still owed for the rest of the risk.
    missing: list[str] = Field(default_factory=list)
    #: True only when a guard that measures the WHOLE risk passed, and was
    #: entitled to have its green read as a measurement.
    discharges: bool = False
    #: True when a guard that measures the whole risk RAN AND REFUSED. Not a
    #: gap: a direct statement that the risk is realised.
    refuted: bool = False
    #: Why this does not discharge the risk, when it does not.
    reason: str = ""

    def citation(self) -> str:
        if self.measurement is None:
            return ""
        return self.measurement.brief()


def guard_evidence(risk: Any, measurements: Sequence[GuardMeasurement]) -> GuardVerdict:
    """Does a guard this run changed and ran measure everything this risk names?

    The whole rule, in one place, and it answers "no" in five different ways
    that are five different sentences — a risk too thin to check, nothing that
    read its subjects, something that read some of them, a guard that could not
    run, and a green that is not discriminating. Only the first branch is
    silent; the rest state what is missing, because a gap a reader cannot act on
    is the same as no answer.
    """
    named = subjects(str(getattr(risk, "description", "") or ""))
    verdict = GuardVerdict(named=named)
    if len(named) < MIN_SUBJECTS:
        verdict.missing = list(named)
        verdict.reason = (
            "the risk names too few concrete repository artifacts for a direct guard to "
            "be checked against it"
        )
        return verdict
    if not measurements:
        verdict.missing = list(named)
        return verdict

    # The guard that read the most of what the risk names. Ties keep the first,
    # which is the diff's own order.
    best: GuardMeasurement | None = None
    best_hits: list[str] = []
    for measurement in measurements:
        hits = [s for s in named if s in set(measurement.measured)]
        if len(hits) > len(best_hits):
            best, best_hits = measurement, hits
    verdict.measurement = best
    verdict.covered = best_hits
    verdict.missing = [s for s in named if s not in set(best_hits)]

    if best is None or not best_hits:
        verdict.reason = (
            "no verification file this change delivered was observed to read "
            + ", ".join(named[:4])
        )
        verdict.measurement = None
        return verdict
    if verdict.missing:
        verdict.reason = (
            f"{best.path} measures {', '.join(best_hits[:4])} but was not observed to read "
            + ", ".join(verdict.missing[:4])
            + ", so it does not speak for the whole risk"
        )
        return verdict
    if not best.executable:
        verdict.reason = (
            f"{best.path} measures this risk but could not be executed here: {best.detail[:160]}"
        )
        return verdict
    if not best.passed:
        verdict.refuted = True
        verdict.reason = (
            f"{best.path} measures this risk and REFUSED on this tree: {best.detail[:160]}"
        )
        return verdict
    if not best.discriminating:
        verdict.reason = (
            f"{best.path} measures this risk and passes, but it asserts an absence and no "
            "case proving it can still fire was observed beside it, so its green is not "
            "yet discriminating"
        )
        return verdict
    verdict.discharges = True
    return verdict


__all__ = [
    "MIN_SUBJECTS",
    "MIN_TOKEN",
    "GuardMeasurement",
    "GuardVerdict",
    "guard_evidence",
    "guard_measurements",
    "normalise",
    "subjects",
]
