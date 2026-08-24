"""What a reviewer actually established by running something, as opposed to what
it was merely *permitted* to run.

The distinction this module exists for
--------------------------------------

:mod:`~neyma_product_driver.reviewer_boundary` answers one question: *may this
reviewer execute this command?* It is a security boundary and it is very good at
that question. It is not, and cannot be, an answer to a second and completely
different one: *did what the command showed actually establish the thing the
review is claiming?*

Before this module, ``evidence_reproduced`` collapsed the two. It meant "the
reviewer asked to run something and the boundary let it through" — which is a
fact about the boundary, not about the product. A reviewer could launch
``git status``, have it allowed, and the run would report that runtime evidence
had been reproduced. Nothing in that chain observed a single thing about whether
the implementation works.

So the claim is rebuilt on three separate facts, each recorded:

    1. the command the reviewer **requested**, and what the boundary decided;
    2. the command that was **executed** and what it actually produced — exit
       code and output, captured from the reviewer's own session by a
       ``PostToolUse`` hook rather than read out of the reviewer's prose;
    3. a **named deterministic expectation** — an oracle — and whether the
       observation in (2) satisfied it.

``evidence_reproduced`` is true only when all three line up on at least one
command that actually exercises the product.

WHY THE ORACLE IS THE SCENARIO ORACLE
-------------------------------------

Product Driver already has exactly one model of "an expectation that a machine,
not a model, decides": a scenario's ``expect_exit_code`` / ``expect_contains``
and a state check's ``contains`` / ``not_contains``. A reviewer's expectation is
the same shape of thing, so it is the same shape:
:class:`EvidenceExpectation` carries a required exit code, required substrings
and prohibited substrings, and nothing else. Where the repository already
declares an oracle for a command — because a human wrote that command, with its
assertions, into a scenario file — that human-authored oracle is the one used.
A reviewer's own declaration is the fallback, and it is a *declaration checked
against a real observation*, never prose accepted at face value.

WHY "RAN SOMETHING" IS NOT "REPRODUCED RUNTIME EVIDENCE"
--------------------------------------------------------

``git status``, ``git diff``, ``grep`` and ``ls`` read the repository. They can
establish that a file exists, that a diff says what it is claimed to say, that a
guard is present in the source — real, useful, *structural* facts. They cannot
establish that the product behaves. ``pytest``, a probe, a mutation battery
execute the thing under review, and only those can.

:class:`VerificationKind` keeps the two apart, and the founder summary reports
them apart, because "the reviewer verified this structurally" and "the reviewer
ran the product and watched it behave" are different sentences and only one of
them is what an independent review of an effect boundary was called for.

Nothing here executes anything, and nothing here can widen what a reviewer is
allowed to run. It classifies observations that have already happened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Sequence

__all__ = [
    "EvidenceStatus",
    "VerificationKind",
    "EvidenceExpectation",
    "EvidenceObservation",
    "ReproducedEvidence",
    "EvidenceLedger",
    "DeclaredExpectations",
    "expectations_from_scenarios",
    "classify_verification_kind",
    "classify_evidence",
    "observation_from_tool_response",
]


# --------------------------------------------------------------------------
# What a piece of reviewer evidence turned out to be
# --------------------------------------------------------------------------


class EvidenceStatus(str, Enum):
    """What one reviewer-executed command actually established.

    Exactly one of these is true of any single command, and only the first one
    licenses the sentence "the independent reviewer reproduced runtime evidence".
    The rest are the honest names for everything else that can happen, and every
    one of them is still worth recording — a refused command is a limit on the
    review, a failed expectation is a finding, and an inspection is an
    inspection.
    """

    #: The reviewer executed something that exercises the product, and a named
    #: deterministic expectation was satisfied by what it observed.
    RUNTIME_REPRODUCED = "RUNTIME_REPRODUCED"
    #: The reviewer executed a read-only inspection of the repository and a named
    #: deterministic expectation was satisfied. Real verification; not runtime.
    STRUCTURAL_VERIFIED = "STRUCTURAL_VERIFIED"
    #: The reviewer ran it and looked at the output, but no deterministic
    #: expectation was attached to it, so nothing was established by machine.
    REVIEWER_INSPECTED = "REVIEWER_INSPECTED"
    #: An expectation was named and the observation contradicted it.
    EXPECTATION_FAILED = "EXPECTATION_FAILED"
    #: The boundary allowed it, but no observation of what it produced reached
    #: the harness — so whether it ran at all is not a fact this run holds.
    OBSERVATION_MISSING = "OBSERVATION_MISSING"
    #: The command itself failed to run, or exited in a way nothing expected.
    COMMAND_ERRORED = "COMMAND_ERRORED"
    #: The boundary refused it. It never ran.
    REFUSED = "REFUSED"
    #: Not reviewer-executed at all: read out of records this harness collected.
    CORROBORATED = "CORROBORATED"

    @property
    def established_by_reviewer(self) -> bool:
        """Whether a deterministic expectation was satisfied by a real run."""
        return self in {
            EvidenceStatus.RUNTIME_REPRODUCED,
            EvidenceStatus.STRUCTURAL_VERIFIED,
        }


class VerificationKind(str, Enum):
    """Whether a command exercises the product or inspects the repository."""

    #: Runs the thing under review: a test suite, a probe, a mutation battery.
    RUNTIME = "RUNTIME"
    #: Reads the tree, the diff, the history or the source. Never executes the
    #: product's own behaviour.
    STRUCTURAL = "STRUCTURAL"


#: Command heads that only ever read the repository. Deliberately the same set
#: the reviewer boundary calls read-only verification, minus the test runners:
#: the boundary asks "is this safe", this asks "does this exercise anything".
_STRUCTURAL_HEADS: frozenset[str] = frozenset(
    {
        "git", "grep", "egrep", "fgrep", "rg", "ls", "find", "cat", "head",
        "tail", "wc", "sort", "uniq", "cut", "tr", "nl", "diff", "stat",
        "file", "basename", "dirname", "realpath", "pwd", "jq", "shasum",
        "sha256sum", "md5sum",
    }
)

#: Heads that run the thing under review.
_RUNTIME_HEADS: frozenset[str] = frozenset({"pytest", "py.test"})

_PYTHON_HEAD = re.compile(r"^(?:python|python3|python3\.\d+)$")

_WHITESPACE = re.compile(r"\s+")


def _norm(command: str) -> str:
    return _WHITESPACE.sub(" ", str(command or "").strip())


def _head_of(command: str) -> str:
    token = _norm(command).split(" ")[0] if _norm(command) else ""
    return token.rsplit("/", 1)[-1]


def classify_verification_kind(command: str) -> VerificationKind:
    """Whether ``command`` exercises the product or only inspects the tree.

    Deterministic and generic: it reads the command's head, never a unit name, a
    repository path or anything a particular project is called. A head this
    module does not recognise is RUNTIME — the only way a command reaches a
    reviewer without being on the read-only list is by being a deterministic
    command a human wrote into a scenario file, and those are the probes and
    batteries that run the product.
    """
    head = _head_of(command)
    if not head:
        return VerificationKind.STRUCTURAL
    if head in _RUNTIME_HEADS:
        return VerificationKind.RUNTIME
    if _PYTHON_HEAD.match(head):
        # `python -m pytest` runs the suite; `python probe.py` runs the product;
        # neither merely reads the tree.
        return VerificationKind.RUNTIME
    if head in _STRUCTURAL_HEADS:
        return VerificationKind.STRUCTURAL
    return VerificationKind.RUNTIME


# --------------------------------------------------------------------------
# The oracle
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceExpectation:
    """A named, deterministic expectation about what a command must show.

    The same three assertion shapes a scenario already uses — an exit code, the
    substrings that must appear, the substrings that must not — because a
    reviewer's expectation is not a different kind of thing from a scenario's
    and giving it a different vocabulary would make it a third verification
    system nobody reconciles.

    ``name`` is not decoration. An expectation with no name is not an oracle,
    it is an assertion nobody can point at afterwards, and
    :attr:`deterministic` is false for it — so it can never be what makes a
    review claim reproduced evidence.
    """

    #: The oracle's name: the probe invariant, the scenario check, the thing
    #: this expectation is *about*.
    name: str = ""
    #: The exit code the command must produce. ``None`` does not mean "any exit
    #: code": it means this oracle announces none, and an unannounced non-zero
    #: exit is :attr:`EvidenceStatus.COMMAND_ERRORED` however well the output
    #: matched. A command that is supposed to fail says so here.
    expect_exit_code: int | None = None
    #: Substrings that must all be present in what the command produced.
    expect_contains: tuple[str, ...] = ()
    #: Substrings that must not appear. A negative control's oracle.
    expect_absent: tuple[str, ...] = ()
    #: Where this expectation came from: the repository's own scenario files, or
    #: the reviewer's declaration. Recorded because they are not equally strong.
    source: str = ""

    @property
    def assertions(self) -> int:
        return (
            (1 if self.expect_exit_code is not None else 0)
            + len(self.expect_contains)
            + len(self.expect_absent)
        )

    @property
    def deterministic(self) -> bool:
        """Whether this can decide anything by machine."""
        return bool(self.name.strip()) and self.assertions > 0

    def describe(self) -> str:
        parts: list[str] = []
        if self.expect_exit_code is not None:
            parts.append(f"exit {self.expect_exit_code}")
        for needle in self.expect_contains:
            parts.append(f"contains {needle!r}")
        for needle in self.expect_absent:
            parts.append(f"absent {needle!r}")
        body = "; ".join(parts) or "nothing asserted"
        return f"{self.name or '(unnamed)'}: {body}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "expect_exit_code": self.expect_exit_code,
            "expect_contains": list(self.expect_contains),
            "expect_absent": list(self.expect_absent),
            "source": self.source,
            "deterministic": self.deterministic,
        }

    @classmethod
    def from_any(cls, data: Any, *, source: str = "") -> "EvidenceExpectation | None":
        """Build one from whatever a model or a scenario handed over. Never raises."""
        if data is None:
            return None
        get = (
            data.get
            if isinstance(data, dict)
            else lambda key, default=None: getattr(data, key, default)
        )
        name = str(get("name", "") or "").strip()
        raw_exit = get("expect_exit_code", None)
        try:
            expect_exit = None if raw_exit is None else int(raw_exit)
        except (TypeError, ValueError):
            expect_exit = None
        return cls(
            name=name[:200],
            expect_exit_code=expect_exit,
            expect_contains=_needles(get("expect_contains", ()) or ()),
            expect_absent=_needles(get("expect_absent", ()) or ()),
            # An explicit source names where this expectation is being read
            # from now; a persisted one already carries where it came from, and
            # "a human wrote this into a scenario" must survive a round trip
            # through the run record.
            source=source or str(get("source", "") or ""),
        )


def _needles(value: Any) -> tuple[str, ...]:
    """Non-empty literal substrings, deduplicated, order preserved.

    Whitespace-only entries are dropped rather than kept: an expectation that a
    command's output contains ``" "`` is satisfied by anything at all, and an
    oracle that cannot fail is not an oracle.
    """
    items = [value] if isinstance(value, str) else list(value or [])
    out: list[str] = []
    for item in items:
        text = str(item or "")
        if not text.strip() or text in out:
            continue
        out.append(text[:500])
    return tuple(out[:20])


# --------------------------------------------------------------------------
# The observation
# --------------------------------------------------------------------------

#: How much of a command's output is kept. Enough to see the assertion hold or
#: fail; not so much that a run directory becomes a log archive.
_MAX_OUTPUT_CHARS = 8_000


@dataclass
class EvidenceObservation:
    """What a command actually did, captured from the reviewer's own session.

    Filled from a ``PostToolUse`` hook, which fires after the tool has run — so
    every field here is the harness's own record of the reviewer's execution,
    not the reviewer's account of it.
    """

    #: The command string the tool was actually invoked with. Recorded
    #: separately from the requested one because a hook, a wrapper or an SDK
    #: rewrite could in principle make them differ, and "what was judged" must
    #: be "what ran".
    command_executed: str = ""
    exit_code: int | None = None
    #: True when the exit code was not reported and had to be read off the
    #: tool's own success/failure. Recorded so nobody mistakes it for measured.
    exit_code_inferred: bool = False
    output: str = ""
    #: The tool itself failed — a timeout, an interrupt, a launch failure. Not
    #: the same as a command that ran and exited non-zero, which is an
    #: observation a negative control may legitimately expect.
    execution_failed: bool = False
    error_detail: str = ""
    #: Whether a tool result was actually read. Separate from the fields above
    #: because knowing the command's *text* is not knowing what it did, and an
    #: unreadable result must be an absence rather than a silent success.
    response_seen: bool = False

    @property
    def has_result(self) -> bool:
        return bool(self.response_seen or self.execution_failed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_executed": self.command_executed,
            "exit_code": self.exit_code,
            "exit_code_inferred": self.exit_code_inferred,
            "output": self.output,
            "execution_failed": self.execution_failed,
            "error_detail": self.error_detail,
            "response_seen": self.response_seen,
        }


#: Keys a Bash tool result may carry its exit status under, across SDK versions.
_EXIT_KEYS = (
    "exit_code", "exitCode", "returncode", "return_code", "returnCode",
    "status", "code",
)
_OUTPUT_KEYS = ("stdout", "output", "result", "content", "text", "stderr")


def observation_from_tool_response(
    tool_input: Any,
    tool_response: Any,
    *,
    execution_failed: bool = False,
    error: str = "",
) -> EvidenceObservation:
    """Read one Bash tool result into an observation. Never raises.

    Tolerant by construction: the exact shape of a Bash tool response is the
    SDK's business and has changed before. What matters is that anything this
    cannot read becomes an *absence* — an unreported exit code stays ``None``
    and an unreadable response leaves :attr:`EvidenceObservation.has_result`
    false — so a shape this does not understand degrades to
    ``OBSERVATION_MISSING`` rather than to a satisfied expectation.
    """
    executed = ""
    if isinstance(tool_input, dict):
        executed = str(tool_input.get("command", "") or "")

    exit_code: int | None = None
    inferred = False
    chunks: list[str] = []
    failed = bool(execution_failed)

    payload = tool_response
    seen = isinstance(payload, (dict, str, list))
    if isinstance(payload, list):
        # Some transports wrap the result in content blocks.
        text = "\n".join(
            str(block.get("text", "") if isinstance(block, dict) else block)
            for block in payload
        )
        payload = {"stdout": text}

    if isinstance(payload, dict):
        for key in _EXIT_KEYS:
            if key in payload:
                try:
                    exit_code = int(payload[key])
                except (TypeError, ValueError):
                    continue
                break
        for key in _OUTPUT_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value:
                chunks.append(value)
        if payload.get("is_error") or payload.get("isError"):
            failed = failed or exit_code is None
        if payload.get("interrupted"):
            failed = True
    elif isinstance(payload, str):
        chunks.append(payload)

    if exit_code is None and not failed and seen:
        # The tool returned a result and reported no failure. That is the SDK
        # saying the command succeeded, which is a weaker fact than a measured
        # status — so it is used, and it is marked as inferred wherever it is
        # shown. It is *not* inferred from a response this could not read: an
        # unreadable result is an absence, and an absence must never satisfy an
        # expectation.
        exit_code, inferred = 0, True

    return EvidenceObservation(
        command_executed=executed,
        exit_code=exit_code,
        exit_code_inferred=inferred,
        output=_clip("\n".join(c for c in chunks if c)),
        execution_failed=failed,
        error_detail=str(error or "")[:1000],
        response_seen=seen,
    )


def _clip(text: str) -> str:
    """Keep the head and the tail; a test summary lives at one end or the other."""
    body = str(text or "")
    if len(body) <= _MAX_OUTPUT_CHARS:
        return body
    half = _MAX_OUTPUT_CHARS // 2
    return f"{body[:half]}\n... [{len(body) - _MAX_OUTPUT_CHARS} characters omitted] ...\n{body[-half:]}"


# --------------------------------------------------------------------------
# The join: request + observation + oracle
# --------------------------------------------------------------------------


@dataclass
class ReproducedEvidence:
    """One piece of evidence a reviewer either did or did not establish.

    Every field a reader needs in order to disbelieve it: what was asked for,
    what ran, what came back, what was supposed to come back, whether it did,
    and what that adds up to.
    """

    command_requested: str = ""
    command_executed: str = ""
    exit_code: int | None = None
    exit_code_inferred: bool = False
    observed: str = ""
    expectation: EvidenceExpectation | None = None
    expectation_satisfied: bool = False
    kind: VerificationKind = VerificationKind.STRUCTURAL
    status: EvidenceStatus = EvidenceStatus.CORROBORATED
    detail: str = ""
    #: The boundary's own record: whether it let this through, and on what basis.
    allowed: bool = False
    basis: str = ""

    @property
    def reproduced_runtime(self) -> bool:
        return self.status is EvidenceStatus.RUNTIME_REPRODUCED

    @property
    def established(self) -> bool:
        return self.status.established_by_reviewer

    def brief(self) -> str:
        oracle = self.expectation.describe() if self.expectation else "no oracle declared"
        return (
            f"{self.status.value}: `{self.command_requested[:120]}` "
            f"(exit {self.exit_code if self.exit_code is not None else '?'}) "
            f"against [{oracle}]"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_requested": self.command_requested,
            "command_executed": self.command_executed,
            "exit_code": self.exit_code,
            "exit_code_inferred": self.exit_code_inferred,
            "observed": self.observed,
            "expectation": self.expectation.to_dict() if self.expectation else None,
            "expectation_satisfied": self.expectation_satisfied,
            "verification_kind": self.kind.value,
            "status": self.status.value,
            "detail": self.detail,
            "allowed": self.allowed,
            "basis": self.basis,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ReproducedEvidence":
        """Rebuild one from the persisted record. Never raises."""
        if not isinstance(data, dict):
            return cls()
        try:
            status = EvidenceStatus(str(data.get("status", "")))
        except ValueError:
            status = EvidenceStatus.CORROBORATED
        try:
            kind = VerificationKind(str(data.get("verification_kind", "")))
        except ValueError:
            kind = VerificationKind.STRUCTURAL
        raw_exit = data.get("exit_code")
        try:
            exit_code = None if raw_exit is None else int(raw_exit)
        except (TypeError, ValueError):
            exit_code = None
        return cls(
            command_requested=str(data.get("command_requested", "") or ""),
            command_executed=str(data.get("command_executed", "") or ""),
            exit_code=exit_code,
            exit_code_inferred=bool(data.get("exit_code_inferred", False)),
            observed=str(data.get("observed", "") or ""),
            expectation=EvidenceExpectation.from_any(data.get("expectation")),
            expectation_satisfied=bool(data.get("expectation_satisfied", False)),
            kind=kind,
            status=status,
            detail=str(data.get("detail", "") or ""),
            allowed=bool(data.get("allowed", False)),
            basis=str(data.get("basis", "") or ""),
        )


def evaluate_expectation(
    expectation: EvidenceExpectation, observation: EvidenceObservation
) -> tuple[bool, str]:
    """Whether the observation satisfies the oracle, and the sentence that says why.

    Every clause is checked and every failure is reported, because a reviewer
    reading back "it failed" learns less than one reading back "exit was 1, not
    0, and 'exactly one winner' never appeared".
    """
    problems: list[str] = []
    text = observation.output or ""

    if expectation.expect_exit_code is not None:
        if observation.exit_code is None:
            problems.append("no exit code was observed, so the expected one cannot be checked")
        elif observation.exit_code != expectation.expect_exit_code:
            problems.append(
                f"exit code was {observation.exit_code}, not the expected "
                f"{expectation.expect_exit_code}"
            )

    missing = [needle for needle in expectation.expect_contains if needle not in text]
    if missing:
        problems.append(
            "the output does not contain " + ", ".join(repr(n) for n in missing)
        )
    present = [needle for needle in expectation.expect_absent if needle in text]
    if present:
        problems.append(
            "the output contains what must not appear: "
            + ", ".join(repr(n) for n in present)
        )

    if expectation.expect_contains or expectation.expect_absent:
        if not text.strip():
            problems.append("no output was captured, so no substring assertion could hold")

    if problems:
        return False, "; ".join(problems)[:600]
    return True, f"the observation satisfied {expectation.name!r}"


def classify_evidence(
    *,
    command_requested: str,
    allowed: bool,
    basis: str = "",
    refusal_reason: str = "",
    observation: EvidenceObservation | None = None,
    expectation: EvidenceExpectation | None = None,
) -> ReproducedEvidence:
    """Decide what one reviewer command established. The only place that decides.

    The order is the order of the things that can go wrong, each of which stops
    the chain before it reaches "reproduced":

    refused → never ran → the tool failed → an exit code nobody announced → no
    oracle → oracle failed → and only then, satisfied, which is RUNTIME or
    STRUCTURAL according to whether the command exercises the product or reads
    the tree.
    """
    kind = classify_verification_kind(command_requested)
    evidence = ReproducedEvidence(
        command_requested=str(command_requested or "")[:1000],
        expectation=expectation,
        kind=kind,
        allowed=bool(allowed),
        basis=basis,
    )
    if observation is not None:
        evidence.command_executed = observation.command_executed
        evidence.exit_code = observation.exit_code
        evidence.exit_code_inferred = observation.exit_code_inferred
        evidence.observed = observation.output

    if not allowed:
        evidence.status = EvidenceStatus.REFUSED
        evidence.detail = refusal_reason or "the reviewer boundary refused this command"
        return evidence

    if observation is None or not observation.has_result:
        evidence.status = EvidenceStatus.OBSERVATION_MISSING
        evidence.detail = (
            "the boundary allowed this command, but nothing observed what it produced, "
            "so this run does not hold the fact that it ran"
        )
        return evidence

    if observation.execution_failed:
        evidence.status = EvidenceStatus.COMMAND_ERRORED
        evidence.detail = (
            "the command did not complete: "
            + (observation.error_detail or "the tool reported a failure")
        )
        return evidence

    # Nothing declared that this should fail, and it did. A non-zero exit is a
    # legitimate observation only when something named it in advance;
    # unannounced, it is a command that did not do what the reviewer went there
    # for, whatever its output happened to say.
    #
    # This is checked BEFORE the oracle rather than inside it, because an
    # expectation that asserts only substrings does not thereby announce an exit
    # code. `pytest -q` exiting 1 still prints "5 passed", and an oracle reading
    # `expect_contains: ["passed"]` with no `expect_exit_code` would otherwise
    # be satisfied by a suite that failed — the same false green this module
    # exists to close, one layer further in. An exit code counts as announced
    # only when a deterministic expectation names it: an oracle that cannot
    # decide anything cannot license a failure either.
    announced_exit = (
        expectation is not None
        and expectation.deterministic
        and expectation.expect_exit_code is not None
    )
    if not announced_exit and observation.exit_code not in (None, 0):
        evidence.status = EvidenceStatus.COMMAND_ERRORED
        evidence.detail = (
            f"the command exited {observation.exit_code} and no expectation declared "
            "that it should, so nothing here was established"
            + (
                " — an expectation that asserts only output does not announce an exit "
                "code; declare `expect_exit_code` to make a non-zero exit evidence"
                if expectation is not None and expectation.deterministic
                else ""
            )
        )
        return evidence

    if expectation is None or not expectation.deterministic:
        evidence.status = EvidenceStatus.REVIEWER_INSPECTED
        evidence.detail = (
            "the reviewer ran this and read the output, but named no deterministic "
            "expectation, so nothing here was decided by machine"
        )
        return evidence

    satisfied, why = evaluate_expectation(expectation, observation)
    evidence.expectation_satisfied = satisfied
    evidence.detail = why
    if not satisfied:
        evidence.status = EvidenceStatus.EXPECTATION_FAILED
        return evidence

    evidence.status = (
        EvidenceStatus.RUNTIME_REPRODUCED
        if kind is VerificationKind.RUNTIME
        else EvidenceStatus.STRUCTURAL_VERIFIED
    )
    return evidence


# --------------------------------------------------------------------------
# Oracles the repository already declares
# --------------------------------------------------------------------------


class DeclaredExpectations:
    """Oracles harvested from the repository's own scenario files.

    A human writing ``run: ./probe.py`` with ``expect_exit_code: 0`` and
    ``expect_contains: ["exactly one winner"]`` into a scenario has already said
    what that command must show. When a reviewer runs it, that is the oracle —
    not one the reviewer made up, and not one this module invented. Matching is
    the same prefix rule
    :class:`~neyma_product_driver.scenario_validation.ApprovedCommands` uses for
    the same strings, so a probe invoked with an extra ``--case`` argument still
    finds the expectation the scenario declared for it.
    """

    def __init__(
        self, expectations: Iterable[tuple[str, EvidenceExpectation]] = ()
    ) -> None:
        self._by_command: dict[str, EvidenceExpectation] = {}
        for command, expectation in expectations:
            key = _norm(command)
            if key and expectation.deterministic and key not in self._by_command:
                self._by_command[key] = expectation

    def __len__(self) -> int:
        return len(self._by_command)

    def __bool__(self) -> bool:
        return bool(self._by_command)

    @property
    def commands(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_command))

    def for_command(self, command: str) -> EvidenceExpectation | None:
        """The repository's own oracle for this command, if it declared one.

        Longest match wins, so a scenario that declares both ``./probe.py`` and
        ``./probe.py --case forged`` gives the more specific invocation the more
        specific expectation.
        """
        target = _norm(command)
        if not target:
            return None
        best: tuple[int, EvidenceExpectation] | None = None
        for key, expectation in self._by_command.items():
            if target == key:
                return expectation
            if not target.startswith(key):
                continue
            tail = target[len(key) :]
            if tail and not tail[0].isspace():
                continue
            if best is None or len(key) > best[0]:
                best = (len(key), expectation)
        return best[1] if best else None


def expectations_from_scenarios(scenarios: Sequence[Any]) -> DeclaredExpectations:
    """Harvest every deterministic oracle a human wrote into a scenario file.

    Reads the same four places
    :meth:`~neyma_product_driver.scenario_validation.ApprovedCommands.from_sources`
    reads commands from — phase-form commands and state checks, and the same two
    inside ``steps`` — so a command a reviewer may run is a command whose
    expectations this can find. Duck-typed and total: a scenario shape this does
    not recognise contributes nothing rather than raising.
    """
    pairs: list[tuple[str, EvidenceExpectation]] = []

    def add_command(spec: Any) -> None:
        run = str(getattr(spec, "run", "") or "")
        if not run:
            return
        name = str(getattr(spec, "name", "") or "") or f"scenario command `{_norm(run)[:80]}`"
        pairs.append(
            (
                run,
                EvidenceExpectation(
                    name=name[:200],
                    expect_exit_code=getattr(spec, "expect_exit_code", None),
                    expect_contains=_needles(getattr(spec, "expect_contains", ()) or ()),
                    source="repository scenario",
                ),
            )
        )

    def add_state_check(spec: Any) -> None:
        command = str(getattr(spec, "command", "") or "")
        if not command:
            return
        name = (
            str(getattr(spec, "name", "") or "")
            or f"scenario state check `{_norm(command)[:80]}`"
        )
        pairs.append(
            (
                command,
                EvidenceExpectation(
                    name=name[:200],
                    expect_exit_code=0,
                    expect_contains=_needles(getattr(spec, "contains", ()) or ()),
                    expect_absent=_needles(getattr(spec, "not_contains", ()) or ()),
                    source="repository scenario",
                ),
            )
        )

    for scenario in scenarios or ():
        try:
            for spec in getattr(scenario, "commands", ()) or ():
                add_command(spec)
            for spec in getattr(scenario, "expect_state", ()) or ():
                add_state_check(spec)
            for step in getattr(scenario, "steps", ()) or ():
                if getattr(step, "command", None) is not None:
                    add_command(step.command)
                if getattr(step, "state_check", None) is not None:
                    add_state_check(step.state_check)
        except Exception:  # a scenario shape this cannot read contributes nothing
            continue

    return DeclaredExpectations(pairs)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


@dataclass
class EvidenceLedger:
    """Every piece of evidence one review either established or did not.

    Held as a list rather than a summary because the summary is the thing the
    founder is entitled to disbelieve, and disbelieving it means reading the
    commands.
    """

    records: list[ReproducedEvidence] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self):
        return iter(self.records)

    @classmethod
    def from_dicts(cls, data: Any) -> "EvidenceLedger":
        return cls([ReproducedEvidence.from_dict(item) for item in (data or [])])

    @property
    def runtime_reproduced(self) -> list[ReproducedEvidence]:
        return [r for r in self.records if r.status is EvidenceStatus.RUNTIME_REPRODUCED]

    @property
    def structurally_verified(self) -> list[ReproducedEvidence]:
        return [r for r in self.records if r.status is EvidenceStatus.STRUCTURAL_VERIFIED]

    @property
    def failed(self) -> list[ReproducedEvidence]:
        return [
            r
            for r in self.records
            if r.status
            in {
                EvidenceStatus.EXPECTATION_FAILED,
                EvidenceStatus.COMMAND_ERRORED,
                EvidenceStatus.OBSERVATION_MISSING,
            }
        ]

    @property
    def inspected(self) -> list[ReproducedEvidence]:
        return [r for r in self.records if r.status is EvidenceStatus.REVIEWER_INSPECTED]

    @property
    def refused(self) -> list[ReproducedEvidence]:
        return [r for r in self.records if r.status is EvidenceStatus.REFUSED]

    @property
    def reproduced_runtime_evidence(self) -> bool:
        """The one predicate the whole module exists to make true or false."""
        return bool(self.runtime_reproduced)

    def basis(self) -> str:
        """One line naming what this review actually rests on."""
        if self.runtime_reproduced:
            return (
                f"reviewer-reproduced runtime evidence "
                f"({len(self.runtime_reproduced)} command(s) executed by the reviewer whose "
                "named expectation held)"
            )
        if self.structurally_verified:
            return (
                f"reviewer-verified structurally only "
                f"({len(self.structurally_verified)} inspection(s) whose named expectation "
                "held); the product itself was not re-run by the reviewer"
            )
        if self.failed:
            return (
                f"not reproduced: {len(self.failed)} reviewer command(s) ran without their "
                "named expectation holding"
            )
        if self.inspected:
            return (
                f"reviewer-inspected only ({len(self.inspected)} command(s) run with no "
                "deterministic expectation attached); nothing was established by machine"
            )
        return "corroborated from Product Driver's captured records; not reviewer-reproduced"

    def to_dicts(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.records]
