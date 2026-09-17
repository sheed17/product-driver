"""What a command's outcome MEANS, and whether a scenario's expectation of it is lawful.

A generated scenario used to carry exactly one statement about how a command
should end: ``expect_exit_code``, defaulting to 0. That default silently equated
"the command exited 0" with "the product behaved correctly", which is false for
the whole family of risks whose correct behaviour is a REFUSAL — an unregistered
action class, an unreadable authority store, a missing gate. Run
20260916-070036 is what the equation costs: its scenario S1 ran a probe whose
product call correctly raised the repository's typed fail-closed refusal, the
scenario expected exit 0, and Product Driver asked the builder for a product
that would stop refusing — a regression the repository forbids.

This module gives a command an explicit OUTCOME CONTRACT, and nothing more:

* ``expect_outcome`` — ``permitted`` (the operation must succeed) or ``refused``
  (the product must refuse it, and that refusal is the pass condition);
* ``refusal_evidence`` — for a refusal, the literal text the product's refusal
  path prints. A non-zero exit on its own is never read as a refusal: a crash,
  an import error and a missing interpreter all exit non-zero too;
* ``authority`` — for a refusal, the repository text that mandates it, quoted
  verbatim. A refusal is only a pass because the repository says so, and
  Product Driver may not invent that; it checks the quote is really there.

and classifies what actually happened into one of four outcomes: the operation
was permitted, the product refused it, the command failed unexpectedly, or the
harness never reached the product at all.

### WHAT THIS DOES NOT DO. It never decides, from prose, which way an authority
cuts. The polarity of a citation is the generator's declaration; what is checked
is that the quote exists in a repository document, that the refusal evidence is
text the repository's own files contain, and that the scenario's expectations do
not contradict the polarity it declared. Reading meaning out of the quote would
be the prose matching this harness refuses everywhere else.

### AND THE LEGACY CASE. A scenario written before this contract existed
declares no outcome. When such a scenario expected success and the product
answered with a TYPED refusal — an exception class the repository's own source
defines — nothing here can say whether that refusal is the product's mandated
fail-closed behaviour or a regression: that depends on authority the scenario
never cited. So Product Driver neither passes it nor reports a product defect.
The scenario is HELD: kept blocking, never executed again, and re-derived under
this contract (see :mod:`~neyma_product_driver.scenario_planner`). A builtin
exception (``KeyError``, ``TypeError`` …) is not a typed refusal, and stays an
ordinary product failure.
"""

from __future__ import annotations

import builtins
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

#: The two outcomes a command may be EXPECTED to have.
PERMITTED = "permitted"
REFUSED = "refused"
EXPECTED_OUTCOMES = (PERMITTED, REFUSED)

#: The four outcomes a command may ACTUALLY have.
ACTUAL_PERMITTED = "permitted"
ACTUAL_REFUSED = "refused"
ACTUAL_UNEXPECTED_FAILURE = "unexpected_failure"
ACTUAL_INFRASTRUCTURE_FAILURE = "infrastructure_failure"

#: What a citation says the repository requires.
REQUIRES_REFUSAL = "refusal"
REQUIRES_PERMISSION = "permission"

#: A citation shorter than this proves nothing: "no" and "must" are in every
#: document in every repository.
MIN_QUOTE_CHARS = 24
#: Likewise for refusal evidence. A three-letter needle is found in any
#: traceback, so it would discriminate nothing.
MIN_EVIDENCE_CHARS = 6

#: Documents a citation may quote. Product source is deliberately absent: the
#: code under test cannot be the authority for its own behaviour, or a
#: regression that also rewrote a docstring would certify itself.
AUTHORITY_DOCUMENT_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc", ".yaml", ".yml"})

#: Exit codes the shell uses for "the program never ran".
_SHELL_NEVER_RAN = frozenset({126, 127})

#: Output shapes that mean the harness did not reach the product: the
#: interpreter or script is missing, or the inline program itself did not parse.
_INFRASTRUCTURE_SIGNATURES: tuple[re.Pattern[str], ...] = (
    re.compile(r"command not found"),
    re.compile(r"can't open file"),
    re.compile(r"No such file or directory: '[^']*(?:python|\.py)[^']*'"),
    re.compile(r"^\s*(?:bash|sh|zsh): [^\n]*: (?:No such file|Permission denied)", re.M),
    re.compile(r'File "<string>", line \d+\n(?:.*\n){0,3}?\s*(?:SyntaxError|IndentationError|TabError):'),
    re.compile(r"^ModuleNotFoundError: ", re.M),
)

#: The terminal line of a Python traceback: ``pkg.mod.Name: message`` or ``Name``.
_TRACEBACK = re.compile(r"Traceback \(most recent call last\):")
_TERMINAL = re.compile(r"^(?P<qual>[A-Za-z_][\w.]*)(?::\s|:$|$)")

#: Builtin exception names. Never a typed refusal: a product that raises one
#: crashed, it did not decide.
_BUILTIN_EXCEPTIONS = frozenset(
    name
    for name, value in vars(builtins).items()
    if isinstance(value, type) and issubclass(value, BaseException)
)


def normalize_text(text: str) -> str:
    """Whitespace-collapsed, with inline markup that is not content removed."""
    return " ".join(str(text or "").replace("*", "").replace("`", "").split())


# --------------------------------------------------------------------------
# Classification of what actually happened
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandVerdict:
    """One command judged against its outcome contract."""

    passed: bool
    actual: str
    detail: str

    @property
    def infrastructure(self) -> bool:
        return self.actual == ACTUAL_INFRASTRUCTURE_FAILURE


def infrastructure_problem(exit_code: int | None, timed_out: bool, output: str) -> str:
    """Why this execution never reached the product, or ``""``."""
    if timed_out:
        return "the command timed out, so what the product would have done is unknown"
    if exit_code is None:
        return "the command produced no exit status"
    if exit_code in _SHELL_NEVER_RAN:
        return f"the shell could not run the command (exit {exit_code})"
    if exit_code == 0:
        return ""
    for pattern in _INFRASTRUCTURE_SIGNATURES:
        match = pattern.search(output or "")
        if match is not None:
            return (
                "the command failed before reaching the product "
                f"({' '.join(match.group(0).split())[:160]})"
            )
    return ""


def classify(
    exit_code: int | None,
    timed_out: bool,
    output: str,
    refusal_evidence: Sequence[str] = (),
) -> tuple[str, str]:
    """``(actual outcome, detail)`` for one execution.

    Refusal evidence outranks an infrastructure signature: output that carries
    the product's own refusal text reached the product, whatever else it says.
    Without declared evidence a non-zero exit is an unexpected failure, never a
    refusal — the anti-vacuity half of the contract.
    """
    text = output or ""
    if not timed_out and exit_code == 0:
        return ACTUAL_PERMITTED, "exit 0"
    evidence = [e for e in refusal_evidence if str(e).strip()]
    if not timed_out and exit_code is not None and evidence and all(e in text for e in evidence):
        return ACTUAL_REFUSED, f"exit {exit_code} with the declared refusal evidence"
    problem = infrastructure_problem(exit_code, timed_out, text)
    if problem:
        return ACTUAL_INFRASTRUCTURE_FAILURE, problem
    if evidence:
        missing = [e for e in evidence if e not in text]
        return (
            ACTUAL_UNEXPECTED_FAILURE,
            f"exit {exit_code} WITHOUT the declared refusal evidence "
            + ", ".join(repr(m) for m in missing[:3])
            + ": the command failed, but not by the refusal it was expected to reach",
        )
    return ACTUAL_UNEXPECTED_FAILURE, f"exit {exit_code}"


def judge(
    *,
    expect_outcome: str,
    expect_exit_code: int | None,
    refusal_evidence: Sequence[str],
    exit_code: int | None,
    timed_out: bool,
    output: str,
) -> CommandVerdict:
    """Judge one execution against a DECLARED outcome contract."""
    actual, detail = classify(exit_code, timed_out, output, refusal_evidence)
    if actual == ACTUAL_INFRASTRUCTURE_FAILURE:
        return CommandVerdict(
            False,
            actual,
            f"{detail}. This is a harness/infrastructure failure, not a product refusal and "
            "not a product defect: the product was never observed.",
        )
    if expect_outcome == REFUSED:
        if actual == ACTUAL_REFUSED:
            if expect_exit_code not in (None, 0) and exit_code != expect_exit_code:
                return CommandVerdict(
                    False,
                    actual,
                    f"the product refused, but with exit {exit_code} rather than the declared "
                    f"exit {expect_exit_code}",
                )
            return CommandVerdict(True, actual, f"the product refused as required ({detail})")
        if actual == ACTUAL_PERMITTED:
            return CommandVerdict(
                False,
                actual,
                "the product PERMITTED an operation the cited repository authority requires it "
                "to refuse (a silent allow or default); the declared refusal was never reached",
            )
        return CommandVerdict(False, actual, detail)
    # permitted
    if actual == ACTUAL_PERMITTED:
        return CommandVerdict(True, actual, "the operation was permitted as required")
    return CommandVerdict(
        False,
        actual,
        f"the operation was expected to be permitted and was not ({detail})",
    )


# --------------------------------------------------------------------------
# Repository grounding
# --------------------------------------------------------------------------


def _is_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return any(
        part in {"tests", "test", "testing"}
        or part.startswith("test_")
        or part.endswith("_test.py")
        or part == "conftest.py"
        for part in parts
    )


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, proc.stdout


class RepositoryText:
    """Read-only questions about the repository's own tracked text. Cached."""

    def __init__(self, repo: Path | None) -> None:
        self.repo = Path(repo) if repo is not None else None
        self._files: dict[str, str | None] = {}
        self._literal: dict[str, bool] = {}
        self._classes: dict[str, str] = {}
        self._tracked: set[str] | None = None
        self._refusal_index: object | None = None
        self._index_head = ""
        self._programs: dict[str, object] = {}

    def refusal_index(self):
        """What the repository's own guards say each closed operation is. Cached.

        See :class:`~neyma_product_driver.refusal_semantics.RefusalIndex`.
        """
        if self._refusal_index is None:
            from .refusal_semantics import RefusalIndex

            self._refusal_index = RefusalIndex.build(self)
        return self._refusal_index

    def refresh(self) -> None:
        """Forget every cached answer if the repository's HEAD has moved.

        A builder commits between iterations of one run, and what the
        repository's files and guards say must be read from the tree being
        judged. Called by the planner once per wave, resume and hold pass —
        not per question, which would cost a subprocess per approved command.
        """
        head = _git(self.repo, "rev-parse", "HEAD")[1].strip() if self.repo is not None else ""
        if head == self._index_head:
            return
        self._index_head = head
        self._files.clear()
        self._literal.clear()
        self._classes.clear()
        self._tracked = None
        self._refusal_index = None

    def program_analysis(self, text: str):
        """The operations of one probe program, parsed once. ``None`` if unparseable."""
        if text not in self._programs:
            from .refusal_semantics import analyse_program

            self._programs[text] = analyse_program(text)
        return self._programs[text]

    def tracked(self) -> set[str]:
        if self._tracked is None:
            if self.repo is None:
                self._tracked = set()
            else:
                code, out = _git(self.repo, "ls-files", "-z")
                self._tracked = set(filter(None, out.split("\0"))) if code == 0 else set()
        return self._tracked

    def _read(self, path: str) -> str | None:
        if path not in self._files:
            text: str | None = None
            if self.repo is not None and path in self.tracked():
                try:
                    text = (self.repo / path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    text = None
            self._files[path] = text
        return self._files[path]

    def citation_problem(self, path: str, quote: str) -> str:
        """Why this citation is not repository authority, or ``""``."""
        clean = str(path or "").strip()
        if self.repo is None:
            return "this run cannot read the repository, so no citation can be verified"
        if not clean or clean.startswith("/") or ".." in PurePosixPath(clean).parts:
            return f"cited path {path!r} is not a repository-relative path"
        if PurePosixPath(clean).suffix.lower() not in AUTHORITY_DOCUMENT_SUFFIXES:
            return (
                f"cited path {clean!r} is not a repository document; a citation must quote "
                "documentation, never the code under test, which cannot be its own authority"
            )
        if _is_test_path(clean):
            return f"cited path {clean!r} is verification, not authority"
        text = self._read(clean)
        if text is None:
            return f"cited path {clean!r} is not a tracked file in this repository"
        wanted = normalize_text(quote)
        if len(wanted) < MIN_QUOTE_CHARS:
            return (
                f"the quote from {clean!r} is {len(wanted)} characters; at least "
                f"{MIN_QUOTE_CHARS} are needed for a citation to identify anything"
            )
        if wanted not in normalize_text(text):
            return (
                f"{clean!r} does not contain the quoted text {wanted[:120]!r}; Product "
                "Driver may not invent repository authority"
            )
        return ""

    def contains_literal(self, literal: str) -> bool:
        """Does any tracked, non-test repository file contain ``literal`` verbatim?"""
        if self.repo is None or not literal:
            return False
        if literal not in self._literal:
            code, out = _git(self.repo, "grep", "-l", "-F", "-e", literal, "--")
            found = code == 0 and any(
                line and not _is_test_path(line) for line in out.splitlines()
            )
            self._literal[literal] = found
        return self._literal[literal]

    def exception_class_home(self, name: str) -> str:
        """The non-test source file that defines ``class name(...)``, or ``""``."""
        if self.repo is None or not re.fullmatch(r"[A-Za-z_]\w*", name or ""):
            return ""
        if name not in self._classes:
            pattern = rf"^[[:space:]]*class[[:space:]]+{name}[[:space:]]*[(:]"
            code, out = _git(self.repo, "grep", "-l", "-E", "-e", pattern, "--", "*.py")
            homes = [
                line for line in out.splitlines() if line and not _is_test_path(line)
            ] if code == 0 else []
            self._classes[name] = sorted(homes)[0] if homes else ""
        return self._classes[name]


def terminal_exception(output: str) -> str:
    """The exception a Python traceback in ``output`` ended with, or ``""``."""
    text = output or ""
    matches = list(_TRACEBACK.finditer(text))
    if not matches:
        return ""
    # The exception line is the first unindented line after the last
    # traceback header: frames, source lines and carets are all indented.
    for line in text[matches[-1].end():].splitlines():
        if not line.strip() or line.startswith((" ", "\t")):
            continue
        match = _TERMINAL.match(line)
        return match.group("qual") if match is not None else ""
    return ""


def typed_refusal(output: str, repository: RepositoryText) -> str:
    """``qualified name (defined in path)`` when the output ends in a typed refusal.

    Typed means the exception class is defined by the repository's own non-test
    source. A builtin exception is a crash, not a decision, and an exception
    the repository does not define is not something it chose to raise.
    """
    qual = terminal_exception(output)
    if not qual:
        return ""
    name = qual.rsplit(".", 1)[-1]
    if name in _BUILTIN_EXCEPTIONS:
        return ""
    home = repository.exception_class_home(name)
    return f"{qual} (defined in {home})" if home else ""


# --------------------------------------------------------------------------
# Static contract validation
# --------------------------------------------------------------------------


def command_actions(generated: object) -> list[tuple[int, object]]:
    return [
        (index, action)
        for index, action in enumerate(getattr(generated, "actions", []) or [])
        if getattr(action, "kind", "") == "command"
    ]


def declares_outcomes(generated: object) -> bool:
    """True when every command action declares its expected outcome."""
    actions = command_actions(generated)
    return bool(actions) and all(getattr(a, "expect_outcome", None) for _i, a in actions)


def undeclared(generated: object) -> bool:
    """True when some command action predates the outcome contract."""
    return any(not getattr(a, "expect_outcome", None) for _i, a in command_actions(generated))


def contract_problems(generated: object, repository: RepositoryText) -> list[str]:
    """Every way ``generated``'s outcome expectations are unlawful. Empty is lawful.

    Checked before anything executes. Each problem is a harness-generation
    defect: the scenario would ask the product for something the repository's
    own authority contradicts, or would read something as a pass that proves
    nothing.
    """
    problems: list[str] = []
    citations = list(getattr(generated, "authority", []) or [])
    requires = set()
    for index, citation in enumerate(citations):
        kind = str(getattr(citation, "requires", "") or "")
        why = repository.citation_problem(
            str(getattr(citation, "path", "")), str(getattr(citation, "quote", ""))
        )
        if why:
            problems.append(f"authority[{index}]: {why}")
            continue
        requires.add(kind)

    expected: set[str] = set()
    for index, action in command_actions(generated):
        outcome = getattr(action, "expect_outcome", None)
        exit_code = getattr(action, "expect_exit_code", None)
        evidence = [str(e) for e in getattr(action, "refusal_evidence", []) or []]
        where = f"actions[{index}]"
        if not outcome:
            if evidence:
                problems.append(
                    f"{where} declares refusal_evidence but no expect_outcome; evidence of a "
                    "refusal is meaningless unless the refusal is the expected outcome"
                )
            continue
        expected.add(outcome)
        if outcome == PERMITTED:
            if exit_code not in (None, 0):
                problems.append(
                    f"{where} expects the operation to be PERMITTED and also expects exit "
                    f"{exit_code}; those contradict each other"
                )
            if evidence:
                problems.append(
                    f"{where} expects the operation to be PERMITTED and declares refusal "
                    "evidence; a permitted operation has no refusal to evidence"
                )
            continue
        # refused
        if exit_code == 0:
            problems.append(
                f"{where} expects a REFUSAL and exit 0; a refusal that exits 0 cannot be told "
                "apart from a silent allow"
            )
        if not evidence:
            problems.append(
                f"{where} expects a REFUSAL but declares no refusal_evidence. A non-zero exit "
                "alone is not a refusal — a crash, an import error and a missing interpreter "
                "all exit non-zero — so the scenario would pass on any failure"
            )
        for literal in evidence:
            if len(literal.strip()) < MIN_EVIDENCE_CHARS:
                problems.append(
                    f"{where} refusal evidence {literal!r} is too short to discriminate a "
                    "refusal from any other output"
                )
            elif not repository.contains_literal(literal):
                problems.append(
                    f"{where} refusal evidence {literal!r} appears in no tracked, non-test "
                    "file of this repository, so it is not the product's refusal; Product "
                    "Driver may not invent what the product prints when it refuses"
                )
        if REQUIRES_REFUSAL not in requires:
            problems.append(
                f"{where} treats a REFUSAL as the pass condition without citing repository "
                "authority that requires refusal (an `authority` entry with requires="
                "'refusal' whose quote the repository contains). Without it, a failing "
                "command could be read as a passing scenario"
            )

    # The operations inside each command, read against what the repository's
    # own guards say they are. A generator that cites nothing cannot thereby
    # outvote the repository: a compound probe that runs an operation the
    # repository only ever exercises as a refusal, and declares the command
    # permitted, is the same contradiction as citing a refusal mandate and
    # expecting success — it is merely silent about it.
    from .refusal_semantics import semantics_problems

    problems += semantics_problems(generated, repository)

    if REQUIRES_REFUSAL in requires and REFUSED not in expected:
        problems.append(
            "the scenario cites repository authority that requires a REFUSAL, but no command "
            "it runs expects one: its expectations contradict the authority it claims to "
            "verify, and could only be met by a product that stopped refusing"
        )
    if REQUIRES_PERMISSION in requires and PERMITTED not in expected:
        problems.append(
            "the scenario cites repository authority that requires the operation to be "
            "PERMITTED, but no command it runs expects that"
        )
    return problems


def undeclared_refusal(
    generated: object,
    executions: Iterable[tuple[int | None, bool, str]],
    repository: RepositoryText,
) -> str:
    """Why a pre-contract scenario's recorded result cannot be judged, or ``""``.

    ``executions`` are ``(exit code, timed out, combined output)`` for the
    commands the scenario actually ran. Returns a reason only when the scenario
    declares no outcome for some command, expected that command to exit 0, and
    the product instead ended in a typed refusal.
    """
    if not undeclared(generated):
        return ""
    if not any(
        not getattr(a, "expect_outcome", None) and getattr(a, "expect_exit_code", None) == 0
        for _i, a in command_actions(generated)
    ):
        return ""
    for exit_code, timed_out, output in executions:
        if timed_out or exit_code in (None, 0):
            continue
        typed = typed_refusal(output, repository)
        if typed:
            return (
                f"it declares no outcome contract, expected exit 0, and the product ended in "
                f"the typed refusal {typed} (exit {exit_code}). Whether that refusal is the "
                "product's mandated fail-closed behaviour or a regression depends on "
                "repository authority this scenario never cited, so Product Driver can "
                "neither pass it nor ask the product to change. It is HELD — blocking, not "
                "executed again — until it is re-derived under the outcome contract "
                "(expect_outcome, refusal_evidence, and a verified authority citation)"
            )
    return ""


__all__ = [
    "ACTUAL_INFRASTRUCTURE_FAILURE",
    "ACTUAL_PERMITTED",
    "ACTUAL_REFUSED",
    "ACTUAL_UNEXPECTED_FAILURE",
    "CommandVerdict",
    "EXPECTED_OUTCOMES",
    "PERMITTED",
    "REFUSED",
    "REQUIRES_PERMISSION",
    "REQUIRES_REFUSAL",
    "RepositoryText",
    "classify",
    "contract_problems",
    "declares_outcomes",
    "infrastructure_problem",
    "judge",
    "terminal_exception",
    "typed_refusal",
    "undeclared",
    "undeclared_refusal",
]
