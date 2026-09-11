"""What the repository's own scenario files say a program accepts, and refuses.

Approval answers one question — *may this string run at all?* — and answers it
by prefix: a human approved a program, so a generated scenario may append an
argument tail to it. That is the right rule for command safety and the wrong
rule for meaning. A prefix match cannot tell a tail the program understands
from one it refuses, and a refusal that reaches the executor is scored exactly
like a product that misbehaved. So a generator that composes an argument, a
case, a fault or a control form the program does not have, and expects the
product to succeed, manufactures a failing scenario against a correct product —
and the builder is then asked to make an impossible command green, which only
weakening the program's closed vocabulary could do.

This module reads what the repository has already said, and nothing else:

* **the exit contract of an invocation a human wrote down.** A scenario file
  that runs ``prog --x y`` and reviews it as exiting 2 has stated what that
  exact invocation does. Reusing the invocation does not let a generated
  scenario change it.
* **refusal controls.** An invocation reviewed to exit non-zero, and never
  zero, is the repository demonstrating that the program REFUSES its
  arguments. It does not drive the product; it proves the program's input is
  closed. Whatever extends it inherits the refusal.
* **closed options.** Where a refusal control hands an option a value that no
  non-refusal invocation of the same program, and no literal those invocations
  are reviewed to print, ever vouches for, the repository has proven that
  option's values form a closed vocabulary. A generated value for that option
  must be one the repository vouches for.
* **what a refusal can establish.** A refused invocation never reaches the
  behaviour a risk is about, so it establishes only the risk categories the
  repository's own ``verifies:`` claims attribute to it.

Deliberately NOT inferred: a closed domain for any option the repository has
not proven closed, a value range for a numeric argument, or any relevance
judgement between a risk and a command that actually drives the product. Those
would need either prose matching or authority this harness does not have, and
refusing on a guess would delete correct coverage. What the repository has not
made mechanical stays governed by the approval rule alone.

Everything here is ordinary deterministic Python over the permanent scenario
files, and no program, fault, case or option name is written into it: a
repository that declares no refusal control simply yields no closure.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field as _field
from typing import Callable, Iterable, Mapping, Sequence

from .scenario_plan import GeneratedScenario
from .scenario_validation import ApprovedCommands, _norm_command, established_observations_from
from .scenarios import Scenario

#: A numeric argument. Its bounds are the program's to enforce; nothing in a
#: scenario file states them, so a number is never judged against a vocabulary.
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")

#: How a reviewed literal is split into the words it vouches for. A line a
#: program is reviewed to print — an enumeration entry, a listed value — is a
#: statement that those words are part of what it knows.
_PIECE = re.compile(r"[\s,;()\[\]{}'\"`]+")

#: The four kinds of invocation problem, as recorded. Each is a harness
#: generation defect: nothing here is a statement about the product.
EXIT_CONTRACT = "exit_contract"
REFUSAL_INHERITED = "refusal_inherited"
REFUSED_ARGUMENT = "refused_argument"
CLOSED_VOCABULARY = "closed_vocabulary"
UNPARSEABLE = "unparseable"
IRRELEVANT_REFUSAL = "irrelevant_refusal"

#: What every refusal in this module is, in one phrase, so a reader of a wave
#: record, a founder summary or a correction cannot mistake it for a finding.
HARNESS_DEFECT = (
    "This is a harness-generation defect in Product Driver, not a product failure: "
    "Product Driver refuses to execute the invocation, and a refusal by the program "
    "would say nothing about the product."
)


def _tokens(command: str) -> tuple[str, ...]:
    """The invocation as the shell would split it; naive when quoting is broken."""
    try:
        return tuple(shlex.split(command))
    except ValueError:
        return tuple(_norm_command(command).split())


def _is_option(token: str) -> bool:
    return token.startswith("-") and len(token) > 1 and not _NUMBER.fullmatch(token)


def arguments(tail: Sequence[str]) -> list[tuple[str | None, str | None]]:
    """``(option, value)`` pairs, in order.

    ``--opt value`` and ``--opt=value`` both give ``(opt, value)``; a flag with
    nothing after it gives ``(opt, None)``; a positional gives ``(None, value)``.
    The same reading is applied to what a human wrote and to what a generator
    wrote, which is all that matters: the question is only ever whether one
    carries an element the other does not.
    """
    out: list[tuple[str | None, str | None]] = []
    index = 0
    while index < len(tail):
        token = tail[index]
        if _is_option(token):
            if token.startswith("--") and "=" in token:
                option, value = token.split("=", 1)
                out.append((option, value))
                index += 1
                continue
            following = tail[index + 1] if index + 1 < len(tail) else None
            if following is not None and not _is_option(following):
                out.append((token, following))
                index += 2
                continue
            out.append((token, None))
            index += 1
            continue
        out.append((None, token))
        index += 1
    return out


def _element(option: str | None, value: str | None) -> str:
    """The token an argument pair is judged by: its value, or a bare flag."""
    return value if value is not None else (option or "")


def _extends(outer: str, inner: str) -> bool:
    """``outer`` is ``inner`` plus a whitespace-delimited tail — the approval shape."""
    return len(outer) > len(inner) and outer.startswith(inner) and outer[len(inner)].isspace()


@dataclass(frozen=True)
class AuthoredInvocation:
    """One invocation a human wrote into a scenario file, with what they said of it."""

    command: str
    name: str = ""
    #: Every exit code a human reviewed this exact invocation as producing.
    #: Empty where none was stated (a state check, a setup line, a config entry).
    exit_codes: frozenset[int] = frozenset()
    #: Literal output a human reviewed this invocation as printing.
    literals: frozenset[str] = frozenset()
    #: Risk categories a reviewed ``verifies:`` claim attributes to it.
    risk_categories: frozenset[str] = frozenset()

    @property
    def refusal(self) -> bool:
        """Reviewed to fail, and never to succeed: the program refuses these arguments."""
        return bool(self.exit_codes) and 0 not in self.exit_codes

    def describe(self) -> str:
        label = f" ({self.name!r})" if self.name else ""
        return f"{self.command!r}{label}"


@dataclass(frozen=True)
class ProgramVocabulary:
    """What the repository has made mechanical about one program's arguments."""

    #: The shortest approved, non-refusal invocation every member extends.
    base: str
    #: Every token a non-refusal invocation of the program uses, and every word
    #: such an invocation is reviewed to print.
    vouched: frozenset[str]
    #: option -> the refusal controls that prove its values are a closed set.
    closed_options: Mapping[str, tuple[AuthoredInvocation, ...]]
    #: token -> the refusal controls that single it out as the refused element.
    refused: Mapping[str, tuple[AuthoredInvocation, ...]]
    #: option -> the values non-refusal invocations give it, for a refusal
    #: message a generator can act on.
    admitted: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class InvocationProblem:
    """One generated invocation that is not valid under the program's grammar."""

    field: str
    command: str
    kind: str
    detail: str

    def reason(self) -> str:
        return f"{self.field} runs {self.command!r}: {self.detail} {HARNESS_DEFECT}"


@dataclass
class _Slot:
    field: str
    command: str
    #: The exit code the scenario asserts for this operation. ``None`` where the
    #: operation asserts none (a state check, a cleanup line, an action that
    #: left it unset).
    expected_exit: int | None
    #: Whether the executor asserts an exit code here at all.
    asserts_exit: bool
    #: The literals this operation asserts of its own output.
    literals: list[str] = _field(default_factory=list)


class InvocationGrammar:
    """The argument grammar the repository's own scenario files make checkable."""

    def __init__(
        self,
        invocations: Iterable[AuthoredInvocation],
        approved: ApprovedCommands,
    ) -> None:
        self.approved = approved
        merged: dict[str, AuthoredInvocation] = {}
        for item in invocations:
            key = _norm_command(item.command)
            if not key:
                continue
            prior = merged.get(key)
            if prior is None:
                merged[key] = AuthoredInvocation(
                    command=key,
                    name=item.name,
                    exit_codes=frozenset(item.exit_codes),
                    literals=frozenset(item.literals),
                    risk_categories=frozenset(item.risk_categories),
                )
                continue
            merged[key] = AuthoredInvocation(
                command=key,
                name=min(n for n in (prior.name, item.name) if n) if (prior.name or item.name) else "",
                exit_codes=prior.exit_codes | item.exit_codes,
                literals=prior.literals | item.literals,
                risk_categories=prior.risk_categories | item.risk_categories,
            )
        # A command approved only through configuration is still human-written.
        # It states no exit code and no output, so it only ever vouches.
        for entry in approved.entries:
            merged.setdefault(entry, AuthoredInvocation(command=entry))
        self.invocations: dict[str, AuthoredInvocation] = merged
        self._refusals: list[AuthoredInvocation] = [
            item for _key, item in sorted(merged.items()) if item.refusal
        ]
        self._vocabularies: dict[str, ProgramVocabulary] = {}

    # -- construction ------------------------------------------------------

    @classmethod
    def from_scenarios(
        cls,
        scenarios: Sequence[Scenario],
        approved: ApprovedCommands,
        *,
        established: Mapping[str, frozenset[str]] | None = None,
    ) -> "InvocationGrammar":
        """Harvest exit contracts, literals and risk claims from the permanent files.

        The same files :meth:`ApprovedCommands.from_sources` harvests commands
        from, so "which commands may run" and "what a human said they do" are
        read out of one source and cannot drift apart.
        """
        literals = (
            dict(established)
            if established is not None
            else established_observations_from(scenarios)
        )
        found: list[AuthoredInvocation] = []

        for scenario in scenarios:
            by_name: dict[str, set[str]] = {}
            local: list[tuple[str, str, int | None]] = []

            def note(name: str, command: str, exit_code: int | None, *, asserts: bool) -> None:
                key = _norm_command(command)
                if not key:
                    return
                if name:
                    by_name.setdefault(name, set()).add(key)
                local.append((name, key, exit_code if asserts else None))

            for command in [*scenario.setup, *scenario.teardown]:
                note("", command, None, asserts=False)
            for spec in scenario.commands:
                note(spec.name, spec.run, spec.expect_exit_code, asserts=True)
            for check in scenario.expect_state:
                note(check.name, check.command, None, asserts=False)
            for step in scenario.steps:
                if step.command is not None:
                    note(
                        step.command.name or step.name,
                        step.command.run,
                        step.command.expect_exit_code,
                        asserts=True,
                    )
                if step.state_check is not None:
                    note(
                        step.state_check.name or step.name,
                        step.state_check.command,
                        None,
                        asserts=False,
                    )

            categories: dict[str, set[str]] = {}
            for claim in getattr(scenario, "verifies", []) or []:
                category = str(getattr(claim.risk_category, "value", claim.risk_category))
                for check in claim.checks:
                    for key in by_name.get(check, ()):
                        categories.setdefault(key, set()).add(category)

            for name, key, exit_code in local:
                found.append(
                    AuthoredInvocation(
                        command=key,
                        name=name,
                        exit_codes=frozenset({exit_code}) if exit_code is not None else frozenset(),
                        literals=frozenset(literals.get(key, frozenset())),
                        risk_categories=frozenset(categories.get(key, set())),
                    )
                )
        return cls(found, approved)

    # -- lookup ------------------------------------------------------------

    def authored(self, command: str) -> AuthoredInvocation | None:
        return self.invocations.get(_norm_command(command))

    def refusal_controls(self) -> list[AuthoredInvocation]:
        return list(self._refusals)

    def base_of(self, command: str) -> str:
        """The program ``command`` is an invocation of, as the approved form it extends.

        The SHORTEST approved, non-refusal invocation it extends — the program
        itself, rather than one selection of it — so that everything a human
        wrote about any selection of that program is read together. ``""`` when
        it extends nothing approved.
        """
        key = _norm_command(command)
        candidates = [
            entry
            for entry in self.approved.entries
            if _extends(key, entry) and not self.invocations.get(entry, AuthoredInvocation(entry)).refusal
        ]
        return min(candidates, key=len) if candidates else ""

    def vocabulary(self, base: str) -> ProgramVocabulary:
        """What the repository vouches for, closes and refuses for one program."""
        cached = self._vocabularies.get(base)
        if cached is not None:
            return cached

        base_tokens = _tokens(base)
        members = [
            item
            for key, item in sorted(self.invocations.items())
            if key == base or _extends(key, base)
        ]

        vouched: set[str] = set()
        admitted: dict[str, set[str]] = {}
        for item in members:
            if item.refusal:
                continue
            tokens = _tokens(item.command)
            tail = tokens[len(base_tokens):] if tokens[: len(base_tokens)] == base_tokens else ()
            vouched.update(tail)
            for option, value in arguments(tail):
                if option is not None and value is not None:
                    admitted.setdefault(option, set()).add(value)
            for literal in item.literals:
                text = str(literal).strip()
                if text:
                    vouched.add(text)
                    vouched.update(piece for piece in _PIECE.split(text) if piece)

        closed: dict[str, list[AuthoredInvocation]] = {}
        refused: dict[str, list[AuthoredInvocation]] = {}
        for item in members:
            if not item.refusal:
                continue
            tokens = _tokens(item.command)
            if tokens[: len(base_tokens)] != base_tokens:
                continue
            unvouched = [
                (option, value)
                for option, value in arguments(tokens[len(base_tokens):])
                if _element(option, value) not in vouched
                and not _NUMBER.fullmatch(_element(option, value))
            ]
            for option, value in unvouched:
                if option is not None and value is not None:
                    closed.setdefault(option, []).append(item)
            # Exactly one element nothing vouches for is the element refused.
            # With two, which one the program objected to cannot be read off
            # the files, so neither is named — the option is still closed.
            if len(unvouched) == 1:
                refused.setdefault(_element(*unvouched[0]), []).append(item)

        vocabulary = ProgramVocabulary(
            base=base,
            vouched=frozenset(vouched),
            closed_options={k: tuple(v) for k, v in closed.items()},
            refused={k: tuple(v) for k, v in refused.items()},
            admitted={k: tuple(sorted(v)) for k, v in admitted.items()},
        )
        self._vocabularies[base] = vocabulary
        return vocabulary

    # -- the rule ----------------------------------------------------------

    def problems(self, generated: GeneratedScenario) -> list[InvocationProblem]:
        """Every generated invocation that is not valid under the program's grammar."""
        out: list[InvocationProblem] = []
        category = generated.risk_category.value
        for slot in _slots(generated):
            out += self._slot_problems(slot, category)
        return out

    def _slot_problems(self, slot: _Slot, category: str) -> list[InvocationProblem]:
        key = _norm_command(slot.command)
        if not key:
            return []
        problems: list[InvocationProblem] = []
        #: The refusal controls whose refusal this invocation carries, if it is
        #: a refusal at all. Filled by whichever rule identifies it.
        refusing: list[AuthoredInvocation] = []

        def problem(kind: str, detail: str) -> None:
            problems.append(InvocationProblem(slot.field, key, kind, detail))

        def expected_text() -> str:
            if slot.asserts_exit and slot.expected_exit is not None:
                return f"exit {slot.expected_exit}"
            return (
                "neither the refusal's exit code nor the refusal's own reviewed output "
                "(it asserts no exit code here)"
            )

        def asserts_refusal(controls: Sequence[AuthoredInvocation]) -> bool:
            """Does this operation measure the refusal, rather than a product outcome?

            Where it asserts an exit code, that code must be the refusal's. Where
            it asserts none — a state check, or an action that left it unset —
            it must assert output a human reviewed the refusal as printing.
            """
            if slot.asserts_exit and slot.expected_exit is not None:
                return slot.expected_exit in frozenset().union(*(c.exit_codes for c in controls))
            printed = [text for c in controls for text in c.literals]
            return any(
                literal.strip() and any(literal in text for text in printed)
                for literal in slot.literals
            )

        authored = self.invocations.get(key)
        if authored is not None:
            # -- 1. the exit contract of an invocation a human wrote down ----
            if authored.refusal:
                refusing.append(authored)
                if not asserts_refusal([authored]):
                    problem(
                        EXIT_CONTRACT,
                        f"this exact invocation is {authored.describe()} in this repository's "
                        f"own scenario files, reviewed to exit {_codes(authored.exit_codes)}: it "
                        "is a REFUSAL CONTROL, which proves the program refuses these arguments "
                        f"and never drives the product. The scenario expects {expected_text()}, "
                        "which no correct product can produce. Reusing a human-authored "
                        "invocation does not let a generated scenario change what it is "
                        "reviewed to do.",
                    )
            elif (
                authored.exit_codes
                and slot.asserts_exit
                and slot.expected_exit is not None
                and slot.expected_exit not in authored.exit_codes
            ):
                problem(
                    EXIT_CONTRACT,
                    f"this exact invocation is {authored.describe()} in this repository's own "
                    f"scenario files, reviewed to exit {_codes(authored.exit_codes)}; the "
                    f"scenario expects exit {slot.expected_exit}. Reusing a human-authored "
                    "invocation does not let a generated scenario change what it is reviewed "
                    "to do.",
                )
        else:
            # -- 2. an invocation that extends a refusal control -------------
            for control in self.refusal_controls():
                if _extends(key, control.command):
                    refusing.append(control)
                    if not asserts_refusal([control]):
                        problem(
                            REFUSAL_INHERITED,
                            f"it extends the refusal control {control.describe()}, reviewed "
                            f"to exit {_codes(control.exit_codes)}. The program refuses the "
                            "arguments that control carries whatever is appended to them, so "
                            f"the scenario's expectation of {expected_text()} cannot be met "
                            "by a correct product.",
                        )
            # -- 3. the program's closed vocabulary --------------------------
            base = self.base_of(key)
            if base and not refusing:
                refusing += self._vocabulary_problems(key, base, problem, asserts_refusal)

        # -- 4. what a refusal can establish ---------------------------------
        if refusing:
            establishes = frozenset().union(*(c.risk_categories for c in refusing))
            if category not in establishes:
                names = "; ".join(c.describe() for c in refusing[:3])
                problem(
                    IRRELEVANT_REFUSAL,
                    "it is a refused invocation — the program rejects its arguments before "
                    "it reaches any product behaviour — so it can establish only what this "
                    "repository's reviewed `verifies:` claims say the refusal establishes "
                    f"({names}: "
                    + (", ".join(sorted(establishes)) if establishes else "no risk category")
                    + f"). It cannot establish a {category!r} risk. Select an operation that "
                    "actually exercises that risk, or leave the risk uncovered and say so.",
                )
        return _unique(problems)

    def _vocabulary_problems(
        self,
        key: str,
        base: str,
        problem: Callable[[str, str], None],
        asserts_refusal: Callable[[Sequence[AuthoredInvocation]], bool],
    ) -> list[AuthoredInvocation]:
        vocabulary = self.vocabulary(base)
        if not vocabulary.closed_options and not vocabulary.refused:
            return []
        tokens, base_tokens = _tokens(key), _tokens(base)
        if tokens[: len(base_tokens)] != base_tokens:
            problem(
                UNPARSEABLE,
                f"its arguments cannot be read against {base!r}, a program whose argument "
                "vocabulary this repository proves closed, because its quoting crosses the "
                "boundary of the approved form. What it would pass cannot be checked, so it "
                "is refused rather than executed.",
            )
            return []

        refusing: list[AuthoredInvocation] = []
        for option, value in arguments(tokens[len(base_tokens):]):
            element = _element(option, value)
            controls = vocabulary.refused.get(element, ())
            if controls:
                codes = frozenset().union(*(c.exit_codes for c in controls))
                refusing += list(controls)
                if not asserts_refusal(controls):
                    problem(
                        REFUSED_ARGUMENT,
                        f"it passes {element!r} to {base!r}, and this repository's refusal "
                        f"control(s) {'; '.join(c.describe() for c in controls[:3])} prove the "
                        f"program refuses exactly that argument (reviewed exit "
                        f"{_codes(codes)}). It is outside the program's vocabulary, and "
                        "expecting anything but the refusal is expecting the vocabulary to "
                        "be widened — which Product Driver may never ask for.",
                    )
                continue
            if option is None or value is None or option not in vocabulary.closed_options:
                continue
            if value in vocabulary.vouched or _NUMBER.fullmatch(value):
                continue
            controls = vocabulary.closed_options[option]
            codes = frozenset().union(*(c.exit_codes for c in controls))
            refusing += list(controls)
            if asserts_refusal(controls):
                continue
            admitted = vocabulary.admitted.get(option, ())
            sample = ", ".join(admitted[:12]) + (" …" if len(admitted) > 12 else "")
            problem(
                CLOSED_VOCABULARY,
                f"it passes {value!r} to {option} of {base!r}. This repository proves "
                f"{option}'s values are a CLOSED vocabulary — "
                f"{'; '.join(c.describe() for c in controls[:2])} is reviewed to exit "
                f"{_codes(codes)} on a value outside it — and {value!r} is not a value any of "
                "its scenario files vouches for"
                + (f" (values its own invocations use: {sample})" if sample else "")
                + ". Product Driver may not invent a member of a closed vocabulary; a value "
                "the program does not have is refused, and that refusal is not a product "
                "failure.",
            )
        return refusing

    # -- what the generator is shown ----------------------------------------

    def command_note(self, command: str) -> str:
        """A one-line annotation for an approved command in the generation brief."""
        item = self.invocations.get(_norm_command(command))
        if item is None or not item.refusal:
            return ""
        establishes = ", ".join(sorted(item.risk_categories)) or "no risk category"
        return (
            f"REFUSAL CONTROL — reviewed to exit {_codes(item.exit_codes)}: the program "
            "refuses this invocation and never drives the product. Reuse it only to "
            f"assert that same refusal; it establishes only: {establishes}."
        )

    def vocabulary_lines(self) -> list[str]:
        """Closed vocabularies, one line per closed option, for the generation brief.

        The admitted values are deliberately not enumerated here: which of the
        words a program is reviewed to print belong to which option cannot be
        read off the files, and a list that mixed them would teach the wrong
        thing. What is stated is the rule, and the values the repository has
        itself singled out as refused.
        """
        lines: list[str] = []
        bases = sorted({self.base_of(c.command) for c in self.refusal_controls()} - {""})
        for base in bases:
            vocabulary = self.vocabulary(base)
            refused = ", ".join(sorted(vocabulary.refused)) or "(none singled out)"
            for option in sorted(vocabulary.closed_options):
                lines.append(
                    f"{base} {option} <value>: CLOSED. Use only a value this repository's "
                    "scenario files name for this program (its reviewed enumeration output, "
                    "or an invocation that passes it); anything else is refused by the "
                    f"program. Values it refuses — reuse only to assert that refusal: {refused}."
                )
        return lines


def _slots(generated: GeneratedScenario) -> list[_Slot]:
    """Every command this scenario runs, with the exit code it asserts there."""
    measured = {
        command_path: list(literals)
        for _path, command_path, literals, _assign in generated.observation_slots()
    }
    out: list[_Slot] = []
    for path, command, _assign in generated.command_slots():
        literals = measured.get(path, [])
        if path.startswith("setup["):
            # A setup line must succeed or the scenario aborts before the product.
            out.append(_Slot(path, command, 0, True, literals))
            continue
        match = re.fullmatch(r"actions\[(\d+)\]\.command", path)
        if match is not None:
            action = generated.actions[int(match.group(1))]
            out.append(_Slot(path, command, action.expect_exit_code, True, literals))
            continue
        out.append(_Slot(path, command, None, False, literals))
    return out


def _codes(codes: Iterable[int]) -> str:
    values = sorted(set(codes))
    return " or ".join(str(v) for v in values) if values else "(unstated)"


def _unique(problems: list[InvocationProblem]) -> list[InvocationProblem]:
    seen: set[tuple[str, str, str]] = set()
    out: list[InvocationProblem] = []
    for item in problems:
        marker = (item.field, item.kind, item.detail)
        if marker not in seen:
            seen.add(marker)
            out.append(item)
    return out


__all__ = [
    "AuthoredInvocation",
    "CLOSED_VOCABULARY",
    "EXIT_CONTRACT",
    "HARNESS_DEFECT",
    "IRRELEVANT_REFUSAL",
    "InvocationGrammar",
    "InvocationProblem",
    "ProgramVocabulary",
    "REFUSAL_INHERITED",
    "REFUSED_ARGUMENT",
    "UNPARSEABLE",
    "arguments",
]
