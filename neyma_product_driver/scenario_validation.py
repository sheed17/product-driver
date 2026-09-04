"""Deterministic validation of generated scenarios. The safety boundary.

This module is the wall between "a model proposed a situation" and "the driver
executed something". Nothing a model writes reaches a subprocess, a socket or a
browser without passing every rule here, and every rule is ordinary Python — no
model is consulted about whether a model's output is safe.

Two independent things are checked, and a scenario must pass both:

**Safety.** May this run at all? Commands must come from the approved set, which
contains only commands a human already wrote into a scenario file or into
``driver.config.yaml`` — a generated scenario can *choose* which approved command
to run and in what order, but it can never author a new one. HTTP must be
loopback. Services may only be ones the base scenario declared. Nothing may touch
repository authority or credential material. The existing command guard is
consulted as well, so anything hard-blocked for the builder is hard-blocked here.

**Quality.** Is this worth running? A scenario is refused when it has no
requirement grounding, no observable outcome, duplicates coverage that already
exists, tests implementation detail with no product meaning, claims an effect it
has no oracle for, or mutates local state with no way to clean up.

Every refusal produces a reason string, and every reason is persisted with the
wave record, so a rejected proposal is visible evidence rather than a silent gap.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import signal
import subprocess
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from .command_guard import classify_command, classify_worktree_ownership, is_secret_path
from .runner import child_env
from .scenario_plan import (
    EFFECT_FAMILY,
    GeneratedRequest,
    GeneratedScenario,
    GeneratedScenarioPlan,
    Priority,
    RiskCategory,
)
from .scenarios import Scenario, _join_url

#: Loopback hosts a generated request may address. A scenario that wants to talk
#: to anything else is trying to produce an external effect, which is refused
#: outright rather than approved case by case.
DEFAULT_LOCAL_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"})

#: Repository surfaces that carry authority. A verification scenario observes the
#: product; it never edits the rules the product is judged against.
_AUTHORITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|[/\s])CLAUDE\.md\b"),
    re.compile(r"docs/implementation/"),
    re.compile(r"docs/specifications/acceptance/"),
    re.compile(r"IMPLEMENTATION-REGISTRY"),
    re.compile(r"BUILD-STATUS"),
    re.compile(r"(?:^|[/\s])\.claude/"),
    re.compile(r"founder_context/"),
)

#: High-confidence credential shapes. Deliberately narrower than the redaction
#: patterns used when persisting evidence: a scenario that exercises an
#: authorization boundary legitimately posts something called "password", and
#: refusing that would delete the whole authorization category. What is refused
#: is material that looks like a *real* live credential.
_SECRET_MATERIAL: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),
    # Reading a credential out of the environment, in any of the usual spellings.
    re.compile(
        r"\$\{?(?:ANTHROPIC_API_KEY|OPENAI_API_KEY|GITHUB_TOKEN|GH_TOKEN"
        r"|[A-Z0-9_]*(?:_SECRET|_TOKEN|_PASSWORD|_API_KEY|_CREDENTIALS))\b"
    ),
)

#: What a generated fixture may be. An allowlist of *data* extensions, because
#: the fixture's path is substituted into an approved command after validation
#: has finished, and the approved commands in this repository are interpreters:
#: ``python -m pytest … /abs/fixtures/x.py`` runs the model's file at collection
#: with the driver's full authority. Inspecting the content for "code" is the
#: wrong instrument — the payload is ordinary Python and there is nothing
#: suspicious to match. Making a fixture inert by construction is.
#:
#: Deliberately absent: ``.py``, ``.sh``, ``.js``, ``.rb``, ``.pl``, ``.ini``,
#: ``.cfg``, ``.pth``, ``.toml`` and anything else an interpreter executes,
#: imports, collects or reads as configuration.
FIXTURE_DATA_EXTENSIONS: frozenset[str] = frozenset(
    {".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".txt", ".yaml", ".yml", ".xml"}
)

#: Upper bounds on anything a scenario can ask the executor to wait for. A
#: generated plan cannot stall a run by proposing a ten-minute sleep.
MAX_WAIT_MS = 60_000
MAX_TIMEOUT_S = 900
MIN_PURPOSE_CHARS = 20

#: How alike a refused command must be to an approved one before the refusal
#: says "you nearly had it, cite it instead". A near miss is still a miss and
#: this decides nothing about approval; it only decides how the refusal is
#: phrased.
_NEAR_MISS_RATIO = 0.9


# --------------------------------------------------------------------------
# The approved command set
# --------------------------------------------------------------------------


#: Any control character. Checked on the *raw* string before normalization,
#: because normalization collapses whitespace and a newline is whitespace: a
#: scanner that runs after it can never see the one character that most reliably
#: turns one approved command into two. Tab is included — no command needs one,
#: and allowing it only widens what has to be reasoned about.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def _control_character_problem(command: str) -> str:
    """Refuse control characters before anything else touches the string."""
    match = _CONTROL_CHARS.search(command or "")
    if not match:
        return ""
    return (
        f"command contains the control character {match.group(0)!r} "
        f"(U+{ord(match.group(0)):04X}); a command is a single line of ordinary text"
    )


def _norm_command(command: str) -> str:
    """Collapse whitespace runs. Only ever called after control chars are refused,
    so this can no longer hide a separator inside what looks like a space."""
    return re.sub(r"\s+", " ", (command or "").strip())


#: How an approved command is CITED rather than retyped: ``@`` and the command's
#: token, optionally followed by an argument tail.
#:
#: ### WHY A CITATION EXISTS AT ALL. A generated scenario may only run a command a
#: human already approved, and until now the only way to say *which* one was to
#: reproduce it byte for byte. That is a transcription task, and it is performed
#: by a model answering in JSON. It scales with the command: `pytest -q eval/...`
#: survives it; a 4,545-character ``python -c`` oracle carrying ``\\b``, ``\\(``,
#: ``\x27``, nested quoting and implicitly concatenated string literals does not.
#:
#: Run 20260901-015631 is what that costs. Three coverage-gap waves proposed
#: cases for three P0/P1 risks, every one of them built on an approved oracle the
#: generator had correctly chosen, and four of the six were refused as "not in the
#: approved set" for differences of two to sixteen characters of backslash depth.
#: The run ended NOT READY with those risks unexercised. Nothing was wrong with
#: the proposals except the typing.
#:
#: A citation removes the transcription instead of forgiving it. The token is
#: eight hex characters; what RUNS is the human's own text, looked up here. So
#: this is not a widening of the approval boundary — it narrows it, because a
#: cited command can no longer carry a model's spelling of the approved part at
#: all. There is no fuzzy matching anywhere in it: a token either names an
#: approved command or it does not.
_CITATION = re.compile(r"^@([0-9a-f]{8})(?=\s|$)")


def citation_token(command: str) -> str:
    """The stable token an approved command is cited by. Derived from its key."""
    return hashlib.sha256(_norm_command(command).encode("utf-8")).hexdigest()[:8]


#: Shell syntax that composes one command out of several, or substitutes the
#: output of another. Detected positionally and quote-aware by
#: :func:`scan_shell_operators` — never by scanning raw text, because
#: ``sqlite3 db "SELECT ... HAVING count(*) > 1"`` contains four of these
#: characters and composes nothing at all.
_OPERATOR_CHARS = frozenset(";&|<>()")


def scan_shell_operators(command: str) -> tuple[list[tuple[int, str]], bool]:
    """Locate shell composition operators that are *outside* quotes.

    Returns ``([(index, operator), ...], quotes_unbalanced)``.

    Quoting decides meaning, so quoting is tracked rather than pattern-matched:

    * inside single quotes nothing expands, so nothing is an operator;
    * inside double quotes ``$(`` and a backtick still substitute, so those are
      operators there, while ``>``, ``|``, ``;`` and parentheses are literal;
    * outside quotes every operator character composes.

    A backslash escapes the next character except inside single quotes. Unbalanced
    quotes are reported rather than guessed at — a command whose quoting does not
    close means something different than it appears to, and is refused.
    """
    found: list[tuple[int, str]] = []
    in_single = in_double = False
    index, length = 0, len(command)

    while index < length:
        char = command[index]

        if char == "\\" and not in_single:
            index += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            index += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            index += 1
            continue

        # Substitution survives double quotes; it is only inert inside single ones.
        if not in_single:
            if command.startswith("$(", index):
                found.append((index, "$("))
                index += 2
                continue
            if char == "`":
                found.append((index, "`"))
                index += 1
                continue

        if not in_single and not in_double and char in _OPERATOR_CHARS:
            found.append((index, char))

        index += 1

    return found, (in_single or in_double)


class ApprovedCommands:
    """Command strings a generated scenario is permitted to run.

    The set is assembled from human-authored sources only: the commands already
    written into the repository's scenario YAML files, plus anything explicitly
    listed under ``scenario_generation.approved_commands`` in the driver config.
    There is no built-in allowlist and no pattern language — a model chooses
    *which* approved command runs *when*, and nothing more.

    That is a real constraint, and it is the point. Generated scenarios get their
    power from ordering, concurrency, repetition, restarts, HTTP payloads and
    expectations; they do not get it from authoring shell.

    **A command has two forms here, and they are not the same string.**
    :attr:`entries` is the *matching key* — whitespace-collapsed, so that two
    spellings of one invocation compare equal. :attr:`verbatim` is the *text*,
    exactly as a human wrote it. Only the key may be compared; only the text may
    be shown to anything that will copy it.

    Collapsing whitespace is lossless for matching and lossy for text, because
    :func:`_norm_command` cannot see quoting: a run of spaces inside a quoted
    argument is program syntax, not separation. Run 20260830-034455 is what
    conflating the two costs. The generation brief rendered the *keys* as the
    list of approved commands, an approved command embedded a Python body whose
    nested block was indented by two spaces, the key held one, and the generated
    scenario m9-w2-01 copied the key faithfully and died at parse with
    ``IndentationError: expected an indented block after 'try' statement``
    without ever reaching the product. The command was approved, the copy was
    honest, and the string a human wrote had already been destroyed upstream.
    """

    def __init__(
        self,
        entries: Iterable[str],
        *,
        names: "Mapping[str, str] | None" = None,
    ) -> None:
        # Deduplicated by key, so two spellings of one invocation stay one
        # entry; the first spelling wins the text, and `verbatim` is emitted in
        # `entries` order so the two tuples index the same command.
        by_key: dict[str, str] = {}
        # key -> the human-authored NAME the command was written under. See
        # `by_name` below for why this is not the same identity as the token.
        name_by_key: dict[str, str] = {}
        for raw_name, raw_command in (names or {}).items():
            key = _norm_command(raw_command)
            label = " ".join(str(raw_name or "").split())
            if not key or not label:
                continue
            # A name that two different commands answer to identifies neither,
            # so it identifies nothing. Marked, and dropped below.
            if name_by_key.setdefault(key, label) != label:
                name_by_key[key] = ""
        for raw in entries:
            key = _norm_command(raw)
            if not key:
                continue
            text = str(raw).strip()
            # A control character in the text would split one rendered command
            # across two lines wherever it is shown, which is the same
            # corruption from the other end. Such a command is refused by
            # `approves` anyway, so the key — which cannot contain one — is the
            # honest thing to show for it.
            by_key.setdefault(key, key if _control_character_problem(text) else text)
        self.entries: tuple[str, ...] = tuple(sorted(by_key))
        #: The same commands, in :attr:`entries` order, as a human wrote them.
        #: This is what gets rendered anywhere a reader or a model may copy it.
        self.verbatim: tuple[str, ...] = tuple(by_key[key] for key in self.entries)
        #: The same commands, in :attr:`entries` order, as the eight characters
        #: they are CITED by. Derived from the key, so it is stable across runs
        #: and across the two spellings of one invocation.
        self.tokens: tuple[str, ...] = tuple(citation_token(key) for key in self.entries)
        #: token -> the human's text. A citation resolves through here and
        #: nowhere else, so what runs is always a string a human wrote.
        self.by_token: dict[str, str] = {
            token: text for token, text in zip(self.tokens, self.verbatim)
        }
        #: The same commands, in :attr:`entries` order, under the NAME a human
        #: wrote them beside in the scenario file (``""`` where there is none).
        self.names: tuple[str, ...] = tuple(name_by_key.get(key, "") for key in self.entries)
        #: name -> the human's text, for names that identify exactly one command.
        #:
        #: ### WHY A SECOND IDENTITY EXISTS. :attr:`tokens` is a digest of the
        #: command's own body, which is exactly what a citation needs and
        #: exactly what a *repair* destroys. When a human legitimately corrects
        #: an oracle — run 20260903-065810 repaired seven of them in
        #: ``p6_m11_policy.yaml``, changing no name and adding and removing no
        #: command — every token over those bodies changes, and a run resuming
        #: across the repair can no longer say which approved command its
        #: generated scenario had been built on. The name is the half a human
        #: authored and a body repair leaves alone, so it is the identity that
        #: survives one. It is never an approval path: nothing is approved
        #: because of its name, and rebinding through it can only ever replace a
        #: command with the CURRENT text of a CURRENTLY approved one.
        self.by_name: dict[str, str] = {}
        for key, text in zip(self.entries, self.verbatim):
            label = name_by_key.get(key, "")
            if not label:
                continue
            if label in self.by_name and self.by_name[label] != text:
                # Ambiguous across two surviving commands: identifies neither.
                self.by_name[label] = ""
                continue
            self.by_name[label] = text
        self.by_name = {k: v for k, v in self.by_name.items() if v}

    def name_for(self, command: str) -> str:
        """The human-authored name of the approved command ``command`` IS.

        Empty when the command is not approved as written, or is approved under
        no name (a ``scenario_generation.approved_commands`` config entry), or
        under a name two commands share. Used to record, at generation time,
        which approved command a generated scenario was built on — see
        :attr:`by_name`.
        """
        key = _norm_command(command)
        for entry, label in zip(self.entries, self.names):
            if entry == key and label and self.by_name.get(label):
                return label
        return ""

    def __bool__(self) -> bool:
        return bool(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    @classmethod
    def from_sources(
        cls,
        *,
        scenarios: Sequence[Scenario] = (),
        configured: Sequence[str] = (),
    ) -> "ApprovedCommands":
        """Harvest every command a human already approved by writing it down.

        Two things are harvested, not one: the command string, and the *name*
        the human wrote beside it. A ``setup``/``teardown`` entry has no name
        and gets none; everything a scenario file names carries it. See
        :attr:`by_name` for what the second identity is for.
        """
        entries: list[str] = list(configured)
        names: dict[str, str] = {}
        ambiguous: set[str] = set()

        def named(label: str, command: str) -> None:
            entries.append(command)
            label = " ".join(str(label or "").split())
            if not label or not command:
                return
            # A name two different commands answer to identifies neither, so it
            # is dropped rather than resolved to whichever was written first.
            if names.setdefault(label, command) != command:
                ambiguous.add(label)

        for scenario in scenarios:
            entries.extend(scenario.setup)
            entries.extend(scenario.teardown)
            for spec in scenario.commands:
                named(spec.name, spec.run)
            for check in scenario.expect_state:
                named(check.name, check.command)
            for step in scenario.steps:
                if step.command is not None:
                    named(step.command.name or step.name, step.command.run)
                if step.state_check is not None:
                    named(step.state_check.name or step.name, step.state_check.command)
        return cls(entries, names={k: v for k, v in names.items() if k not in ambiguous})

    def expand(self, command: str) -> str:
        """Resolve a citation to the human's own text. Any other string is returned as is.

        ``@a3f1c2e9 --case foo`` becomes the approved command that token names,
        followed by ``--case foo``. The argument tail is the model's and is
        judged exactly as an appended tail always was; the approved part is no
        longer the model's at all.

        An unrecognised token is deliberately NOT expanded and NOT repaired
        here. It travels on to :meth:`approves`, which refuses it by name — a
        citation of nothing must fail closed and say so, not quietly become a
        command that happens to parse.
        """
        text = (command or "").strip()
        match = _CITATION.match(text)
        if match is None:
            return command
        resolved = self.by_token.get(match.group(1))
        if resolved is None:
            return command
        tail = text[match.end() :]
        return f"{resolved}{tail}"

    def nearest(self, command: str) -> tuple[str, str] | None:
        """The approved entry a refused command most nearly is, and its token.

        Used only to phrase a refusal. Nothing decides approval from it: a near
        miss is still a miss, and the answer to one is to cite the command
        rather than to retype it more carefully.
        """
        normalized = _norm_command(command)
        if not normalized or not self.entries:
            return None
        # Similarity, not shared prefix. The drift a retyped command suffers is
        # a handful of characters in the MIDDLE — a backslash that lost a level,
        # a space that vanished inside a quoted program — and a prefix measure
        # reports a two-character miss on a 1,119-character oracle as no
        # relationship at all. Called only on the refusal path, so the cost of
        # comparing against the whole approved set is paid once per refusal.
        matcher = SequenceMatcher(a="", b=normalized, autojunk=False)
        best, best_ratio = "", 0.0
        for entry in self.entries:
            matcher.set_seq1(entry)
            if matcher.real_quick_ratio() < _NEAR_MISS_RATIO:
                continue
            if matcher.quick_ratio() < _NEAR_MISS_RATIO:
                continue
            ratio = matcher.ratio()
            if ratio > best_ratio:
                best, best_ratio = entry, ratio
        # High, deliberately: "same program, different script" must not be
        # reported as "you nearly had it".
        if best_ratio < _NEAR_MISS_RATIO:
            return None
        return best, citation_token(best)

    def approves(self, command: str) -> tuple[bool, str]:
        """Whether ``command`` may run, and why not when it may not."""
        # Order matters. Control characters are refused against the raw string,
        # before normalization, because normalization would turn a newline into
        # a space and hide the composition entirely.
        control = _control_character_problem(command)
        if control:
            return False, control

        normalized = _norm_command(command)
        if not normalized:
            return False, "empty command"

        guard = classify_command(normalized)
        if guard is not None:
            return False, f"hard-blocked command: {guard}"
        ownership = classify_worktree_ownership(normalized)
        if ownership is not None:
            return False, f"worktree-ownership violation: {ownership}"

        if not self.entries:
            return False, (
                "no approved commands are configured, so a generated scenario may not run "
                "any command. Add commands under scenario_generation.approved_commands, or "
                "write them into a scenario file first."
            )

        operators, unbalanced = scan_shell_operators(normalized)
        if unbalanced:
            return False, (
                f"command has unbalanced quoting ({normalized[:80]!r}); what it would "
                "actually run cannot be determined, so it is refused"
            )

        # A human wrote this exact string into a scenario file or the config. It
        # is approved as written, composition and all — that is a human's call.
        if normalized in self.entries:
            return True, ""

        for entry in self.entries:
            if not normalized.startswith(entry):
                continue
            tail = normalized[len(entry) :]
            if tail and not tail[0].isspace():
                continue  # `pytest-x` is not `pytest `
            # Only operators the *tail* introduces are the model's doing; any that
            # fall inside the human-approved prefix were already approved.
            introduced = [op for position, op in operators if position >= len(entry)]
            if introduced:
                return False, (
                    f"command extends the approved entry {entry!r} with shell composition "
                    f"({', '.join(sorted(set(introduced)))} outside quotes); only argument "
                    "tails are permitted"
                )
            return True, ""

        # An unresolved citation, named. `expand` leaves an unknown token alone
        # precisely so it arrives here: a token naming no approved command is a
        # reference to nothing, and the honest answer says which token failed
        # rather than reporting the raw `@…` string as an unapproved command.
        # Placed last, after every approval path has already declined, so it can
        # only ever refine a refusal — never cause one.
        dangling = _CITATION.match(normalized)
        if dangling is not None and dangling.group(1) not in self.by_token:
            return False, (
                f"command cites the approved-command token {dangling.group(1)!r}, which "
                "names no approved command. A citation must be one of the tokens listed "
                "beside the approved commands."
            )

        # The refusal names the citation form, because retyping is the thing
        # that failed. Run 20260901-015631 spent three coverage-gap waves being
        # told only "not in the approved set" about commands it was two
        # characters away from, and repeated the shape each time.
        near = self.nearest(normalized)
        hint = ""
        if near is not None:
            entry, token = near
            hint = (
                f" This is very nearly the approved command @{token} — "
                f"{entry[:120]!r}… — so cite it as `@{token}` (plus any argument tail) "
                "instead of reproducing it."
            )
        return False, (
            f"command is not in the approved set: {normalized!r}. Generated scenarios may "
            "only run commands a human already approved, either verbatim or by citing "
            "its `@token`." + hint
        )

    def resolve(self, commands: Iterable[str]) -> tuple[set[str], list[str]]:
        """Split ``commands`` into the approved set and the refusal reasons."""
        approved: set[str] = set()
        reasons: list[str] = []
        for command in commands:
            ok, why = self.approves(command)
            if ok:
                approved.add(command)
            else:
                reasons.append(why)
        return approved, reasons


# --------------------------------------------------------------------------
# Validation context
# --------------------------------------------------------------------------


@dataclass
class ValidationContext:
    """Everything validation needs to judge a proposal, gathered by the planner."""

    approved_commands: ApprovedCommands
    #: Tokens that prove a requirement reference is grounded in repository
    #: authority: the active unit id, its acceptance criteria, AC ids.
    grounding_tokens: set[str] = field(default_factory=set)
    #: Tokens that prove a product principle reference is grounded in founder
    #: context: rubric category ids and never-acceptable/boundary ids.
    principle_tokens: set[str] = field(default_factory=set)
    #: Signatures of coverage that already exists (permanent + accepted).
    existing_signatures: set[str] = field(default_factory=set)
    existing_ids: set[str] = field(default_factory=set)
    #: Scenario ids that actually failed in this run, and the cluster ids those
    #: failures were grouped into. An adaptive scenario may only cite these:
    #: they are the evidence it claims to be responding to.
    known_failure_ids: set[str] = field(default_factory=set)
    #: The identified risks in this run's register, by key and by the id the
    #: generator gave them. A coverage-gap scenario may only cite these: a case
    #: claiming to close a gap the run never named is not closing a gap.
    known_risk_ids: set[str] = field(default_factory=set)
    known_cluster_ids: set[str] = field(default_factory=set)
    declared_services: set[str] = field(default_factory=set)
    app_url: str = ""
    local_hosts: frozenset[str] = DEFAULT_LOCAL_HOSTS
    #: When False, a browser scenario still validates — the suite reports it as
    #: SKIPPED with a reason, which is honest coverage reporting rather than a
    #: silent gap.
    browser_enabled: bool = True
    #: Literal output a human already bound to one exact command invocation, in
    #: the permanent scenario files. Normalized command string -> the literals
    #: that command's own reviewed expectations say it prints. Read only by
    #: :func:`unattributed_observations`; empty is safe, and simply means the
    #: only basis a generated scenario can offer is its own.
    established_observations: dict[str, frozenset[str]] = field(default_factory=dict)
    #: Asks one approved invocation what it actually prints. Supplied by the
    #: planner; ``None`` in a context that cannot run anything, which is safe
    #: because an unanswerable contest is refused rather than waved through.
    contract_probe: "Callable[[str], ContractProbeResult] | None" = None

    def grounds_requirement(self, reference: str) -> bool:
        text = (reference or "").strip().lower()
        if not text:
            return False
        if re.search(r"\bAC-[A-Z]+-\d+\b", reference):
            return True
        return any(token and token in text for token in self.grounding_tokens)

    def grounds_principle(self, reference: str) -> bool:
        text = (reference or "").strip().lower()
        if not text:
            return False
        return any(token and token in text for token in self.principle_tokens)


# --------------------------------------------------------------------------
# Oracle attribution — which command has a basis for emitting a literal
# --------------------------------------------------------------------------


def established_observations_from(
    scenarios: Sequence[Scenario],
) -> dict[str, frozenset[str]]:
    """Literal output a human already bound to one exact command invocation.

    Returns ``{normalized command: literals that command is said to print}``,
    harvested from the same human-authored files
    :meth:`ApprovedCommands.from_sources` harvests commands from, and from
    nothing else. Three sources, all reviewed prose in a scenario file: a
    command's own ``expect_contains``, a state check's ``contains``, and the
    ``observations`` of a ``verifies:`` claim — attributed to the commands
    behind the ``checks`` that claim names, because that is the only text the
    executor matches those observations against.

    **The key is an invocation, not a program.** ``probe.py`` and
    ``probe.py --case second-detection`` are two different invocations, and the
    literals a human wrote down belong to the one they wrote. An argument tail a
    generated scenario adds is the model's own composition: it narrows what the
    program does, and nothing in this repository can say what the narrowed form
    still prints. So the lookup below is exact, deliberately *not* the prefix
    match the approved-command set uses. Prefix matching is the right rule for
    "may this run" and the wrong rule for "does this print that": one is about
    authority a human granted over a command, the other about output a human
    observed from one invocation of it.

    Nothing here duplicates a product string into Product Driver. Every literal
    is read at runtime out of the repository's own scenario files, so a
    repository that binds no literals to any command simply yields an empty map
    and the rule below falls back to a scenario's own attribution.
    """
    declared: dict[str, set[str]] = {}

    def bind(command: str, literals: Iterable[str]) -> None:
        key = _norm_command(command)
        if not key:
            return
        wanted = {text for text in literals if str(text).strip()}
        if wanted:
            declared.setdefault(key, set()).update(wanted)

    for scenario in scenarios:
        # name -> the invocations that name runs, so a `verifies:` claim's
        # checks can be resolved back to command strings.
        by_name: dict[str, set[str]] = {}

        def remember(name: str, command: str) -> None:
            key = _norm_command(command)
            if name and key:
                by_name.setdefault(name, set()).add(key)

        for spec in scenario.commands:
            bind(spec.run, spec.expect_contains)
            remember(spec.name, spec.run)
        for check in scenario.expect_state:
            bind(check.command, check.contains)
            remember(check.name, check.command)
        for step in scenario.steps:
            if step.command is not None:
                bind(step.command.run, step.command.expect_contains)
                remember(step.command.name, step.command.run)
            if step.state_check is not None:
                bind(step.state_check.command, step.state_check.contains)
                remember(step.state_check.name, step.state_check.command)

        for claim in getattr(scenario, "verifies", []) or []:
            # A claim's observations are matched against the CONCATENATED output
            # of every check it names, so only a claim naming exactly one check
            # attributes anything to a single command. Binding a multi-check
            # claim's literals to each of its checks is the same
            # over-attribution this whole rule exists to refuse — it says "this
            # command prints that" on evidence that says only "these commands
            # together print that", and it is enough to let the M7 S3 shape
            # through: the concurrency claim names the probe *and* the index
            # introspection, and a scenario running only the second would
            # inherit a basis for a sentence only the first can print.
            if len(claim.checks) != 1:
                continue
            for key in by_name.get(claim.checks[0], ()):
                bind(key, claim.observations)

    return {command: frozenset(literals) for command, literals in declared.items()}


def _emitted_by(literal: str, declared: Iterable[str]) -> bool:
    """Would any of ``declared`` appearing put ``literal`` in the output too?

    Containment rather than equality, and in exactly one direction: if a command
    is said to print ``B`` and the asserted literal ``L`` is a substring of
    ``B``, then ``B`` appearing means ``L`` appears. The reverse does not hold
    and is not accepted. This is the same exact-substring test the executor
    applies — no similarity, no token overlap, no prose comparison.
    """
    return any(literal in str(text) for text in declared)


def _attributed_literals(generated: GeneratedScenario) -> set[str]:
    """Every literal this scenario attributes to one of its own operations."""
    out: set[str] = set()
    for action in generated.actions:
        out.update(action.expect_contains)
        if action.request is not None:
            out.update(action.request.expect_contains)
        for request in action.requests:
            out.update(request.expect_contains)
        if action.state_check is not None:
            out.update(action.state_check.contains)
        for step in action.browser_steps:
            if step.expect_text:
                out.add(step.expect_text)
    for check in generated.persisted_state_checks:
        out.update(check.contains)
    return {text for text in out if str(text).strip()}


# --------------------------------------------------------------------------
# Invocation shape — what a narrowing is, and what it can be asked
# --------------------------------------------------------------------------


def _invocation_tokens(command: str) -> tuple[str, ...]:
    """The normalized command as whitespace-separated tokens.

    Naive on purpose. A quoted argument containing spaces splits into several
    tokens, and that is harmless here because both sides of every comparison
    below are split the same way — the only question ever asked is whether one
    invocation is the other plus a tail, and that is answered identically
    whichever way a quoted region is chopped, as long as it is chopped
    consistently.
    """
    normalized = _norm_command(command)
    return tuple(normalized.split(" ")) if normalized else ()


def _narrows(outer: str, inner: str) -> bool:
    """``outer`` is ``inner`` plus a non-empty, token-aligned argument tail.

    This is the same shape :meth:`ApprovedCommands.approves` accepts — a human
    approved a command and a model appended arguments — seen from the other
    side. There it answers "may this run". Here it answers "is this a different
    observable contract from the one a human wrote down", and the answer is yes:
    a selector narrows what the program does, so what it prints is a *part* of
    what the approved form prints, and nothing static can say which part.
    """
    outer_tokens, inner_tokens = _invocation_tokens(outer), _invocation_tokens(inner)
    return (
        len(outer_tokens) > len(inner_tokens) > 0
        and outer_tokens[: len(inner_tokens)] == inner_tokens
    )


#: The executor's own assertion label for a `contains` check, as
#: :meth:`ScenarioExecutor._do_command` writes it: ``f"{name}: contains {needle!r}"``.
#: A model that reads a result file and copies a target back into a proposal
#: reproduces this shape, and the prose in front of the literal must not become
#: a way to assert a literal nothing was asked about.
_WRAPPED_LITERAL = re.compile(r"^.*?: contains (?P<repr>['\"].*['\"])$", re.DOTALL)


def unwrap_literal(text: str) -> str:
    """The literal an observation wrapper quotes, or ``text`` unchanged.

    Deterministic and exact: the wrapper is recognized only in the one shape the
    executor emits, and the quoted region is decoded with
    :func:`ast.literal_eval` rather than by stripping characters, so a literal
    that itself contains quotes survives. Anything that is not exactly that
    shape is returned untouched — this normalizes, it never guesses, and it
    never matches prose.
    """
    raw = str(text or "")
    match = _WRAPPED_LITERAL.match(raw.strip())
    if not match:
        return raw
    try:
        inner = ast.literal_eval(match.group("repr"))
    except (ValueError, SyntaxError):
        return raw
    return inner if isinstance(inner, str) and inner.strip() else raw


def _kill_group(proc: "subprocess.Popen[str]") -> None:
    """Kill the probe and everything it started, and never raise doing it."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


@dataclass(frozen=True)
class ContractProbeResult:
    """What an invocation actually printed, when it could be asked at all."""

    #: False when the invocation could not be run to a conclusion — refused,
    #: timed out, failed to launch. An undetermined contract is not a licence:
    #: the caller refuses the oracle, because it still cannot prove it.
    determined: bool
    output: str = ""
    #: Why it could not be determined, in words a human can act on.
    detail: str = ""


class ApprovedInvocationProbe:
    """Asks one approved invocation what it prints, once, and remembers.

    This is not a second oracle system. It runs the same command string the
    executor would run, in the same repository, and applies the same
    exact-substring test the executor applies — the only difference is *when*,
    and therefore how a mismatch is classified. Asked before the scenario is
    compiled, a sentence the selected invocation cannot print is a generation
    contract error. Asked after, it is indistinguishable from a product defect,
    which is the whole failure this exists to prevent.

    Three constraints make it safe to run a command in order to validate one:

    * **only already-approved invocations.** A command outside the approved set
      is refused by :meth:`ApprovedCommands.approves` before this is reached, and
      refused again here, so validation never runs something a scenario would
      not have been permitted to run anyway;
    * **bounded.** A hard timeout, and a timeout is an *undetermined* contract,
      never a satisfied one;
    * **asked at most once.** Results are cached per normalized invocation, so a
      wave of scenarios sharing a probe case costs one execution between them.
    """

    def __init__(
        self,
        repo: Path,
        *,
        approved: "ApprovedCommands | None" = None,
        timeout_s: int = 120,
        env: dict[str, str] | None = None,
    ) -> None:
        self.repo = Path(repo)
        self.approved = approved
        self.timeout_s = max(1, int(timeout_s))
        self.env = dict(env or {})
        self._cache: dict[str, ContractProbeResult] = {}

    def __call__(self, command: str) -> ContractProbeResult:
        key = _norm_command(command)
        if key in self._cache:
            return self._cache[key]
        result = self._ask(key)
        self._cache[key] = result
        return result

    def _ask(self, command: str) -> ContractProbeResult:
        if not command:
            return ContractProbeResult(False, detail="empty command")
        if self.approved is not None:
            ok, why = self.approved.approves(command)
            if not ok:
                return ContractProbeResult(
                    False, detail=f"not an approved invocation, so it was not run: {why}"
                )
        try:
            # `start_new_session` gives the command its own process group, so the
            # timeout below can take its children with it. The executor runs this
            # same string the same way for the same reason: a probe that outlives
            # its bound and leaves a subprocess behind has not been bounded.
            proc = subprocess.Popen(  # noqa: S602 - the executor runs this same string
                command,
                shell=True,
                cwd=str(self.repo),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=child_env(self.env) if self.env else None,
                start_new_session=True,
            )
        except OSError as exc:
            return ContractProbeResult(False, detail=f"failed to launch: {exc}")

        try:
            stdout, stderr = proc.communicate(timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            proc.communicate()
            return ContractProbeResult(
                False,
                detail=(
                    f"did not finish within {self.timeout_s}s, so what it prints is unknown"
                ),
            )
        if proc.returncode == 127:
            # POSIX "command not found". A non-zero exit is otherwise perfectly
            # normal here — a probe refusing a bad fault exits 2 and printing the
            # refusal is the whole point — but 127 means the shell never ran
            # anything, and an empty contract is not evidence of an empty output.
            return ContractProbeResult(
                False,
                detail=(
                    "the shell could not run it (exit 127), so what it prints is unknown: "
                    + " ".join((stderr or "").split())[:160]
                ),
            )
        return ContractProbeResult(True, output=f"{stdout}\n{stderr}")


def _operation_assertions(
    generated: GeneratedScenario,
) -> list[tuple[str, str, str]]:
    """``(invocation, label, literal)`` for every literal a *command* asserts.

    Only operations that run a command are listed. An HTTP expectation or a
    browser ``expect_text`` has no invocation to interrogate, and is left to the
    rules that already cover it.
    """
    out: list[tuple[str, str, str]] = []

    def take(command: str, label: str, literals: Iterable[str]) -> None:
        invocation = _norm_command(command)
        if not invocation:
            return
        for literal in literals:
            text = unwrap_literal(literal)
            if text.strip():
                out.append((invocation, label or invocation, text))

    for action in generated.actions:
        if action.command:
            take(action.command, action.name, action.expect_contains)
        if action.state_check is not None:
            take(
                action.state_check.command,
                action.state_check.name,
                action.state_check.contains,
            )
    for check in generated.persisted_state_checks:
        take(check.command, check.name, check.contains)
    return out


def contested_producers(
    invocation: str, literal: str, established: dict[str, frozenset[str]]
) -> tuple[str, ...]:
    """Invocations a human bound ``literal`` to that ``invocation`` is not.

    Contested means the repository already says where this sentence comes from,
    and it does not say it comes from here. Two shapes count, and both are the
    same fact — a selector was applied and the binding sits on the other side of
    it:

    * ``invocation`` narrows an approved form the literal is bound to. The
      approved form runs the whole program; this one runs a selection of it;
    * ``invocation`` and the bound form are *sibling* narrowings of one approved
      form — different selections of the same program.

    Silence is still not contested. A literal no file binds to anything returns
    ``()`` here and keeps the cheap path, which is what leaves a scenario free to
    name output no human has written down.
    """
    normalized = _norm_command(invocation)
    # A human wrote this exact invocation down and said it prints this. That is
    # the strongest basis there is, and it outranks any binding on a wider or a
    # sibling form — there is nothing left to contest.
    if _emitted_by(literal, established.get(normalized, ())):
        return ()
    approved_forms = [key for key in established if _narrows(normalized, key)]
    if not approved_forms:
        return ()
    out: list[str] = []
    for command, literals in established.items():
        if command == normalized or not _emitted_by(literal, literals):
            continue
        if any(command == form or _narrows(command, form) for form in approved_forms):
            out.append(command)
    return tuple(sorted(out))


def cross_contract_observations(
    generated: GeneratedScenario, context: ValidationContext
) -> list[tuple[str, str, str, tuple[str, ...], str]]:
    """Literals a scenario asserts against an invocation that cannot produce them.

    The hole this closes is the one a scenario walks straight through when it
    attributes an oracle to its own command. Naming the operation that prints a
    sentence is free — a model writes the command and the expectation in the same
    breath — so self-attribution proves only that the model believed it, and a
    scenario that runs ``--case A`` while requiring the sentence ``--case B``
    prints fails closed against a perfectly correct product and arrives at the
    gate as a product defect.

    So when the repository contests the attribution — the literal is bound to a
    *different* invocation of the same program, on the other side of a selector —
    belief is not enough and the invocation is asked. It is the only thing that
    can answer: no static source in this repository maps a selector value to the
    output that selection prints, and inventing one from the selector's spelling
    would be exactly the prose matching this rule exists to refuse.

    Returns ``[(invocation, label, literal, contesting producers, detail), ...]``.
    """
    established = context.established_observations or {}
    if not established:
        return []

    out: list[tuple[str, str, str, tuple[str, ...], str]] = []
    for invocation, label, literal in _operation_assertions(generated):
        producers = contested_producers(invocation, literal, established)
        if not producers:
            continue
        probe = context.contract_probe
        if probe is None:
            out.append(
                (
                    invocation,
                    label,
                    literal,
                    producers,
                    "this run cannot ask an invocation what it prints, so the "
                    "attribution cannot be proven",
                )
            )
            continue
        answer = probe(invocation)
        if not answer.determined:
            out.append((invocation, label, literal, producers, answer.detail))
            continue
        if literal not in answer.output:
            out.append(
                (
                    invocation,
                    label,
                    literal,
                    producers,
                    "the invocation was run and did not print it",
                )
            )
    return out


def unattributed_observations(
    generated: GeneratedScenario, context: ValidationContext
) -> list[tuple[str, tuple[str, ...]]]:
    """Asserted output literals nothing this scenario runs has a basis for emitting.

    ``expected_observations`` is matched against *everything* the run produced,
    which makes it the one oracle in a generated scenario that names no command.
    That is exactly how a scenario comes to run the command for case A and
    require the sentence case B prints: both literals are true of the program,
    the harness has no reason to prefer one, and the mismatch only surfaces as a
    failed assertion after execution — where it reads as a defect in the product
    rather than as the mapping error it is. The permanent side has refused this
    shape at load time since ``Scenario._claims_name_a_check_that_can_emit_them``;
    this is the same rule one layer over, for the side a model writes.

    A literal has a basis when either:

    1. **the scenario attributes it itself** — it appears in an action's
       ``expect_contains``, a state check's ``contains`` or a browser step's
       ``expect_text``. The proposal has named the operation that prints it, and
       if that turns out to be wrong the failure is at least attributed to a
       command rather than to the run at large; or
    2. **a human already bound it to a command the scenario runs as written** —
       the literal is in :func:`established_observations_from` for an invocation
       this scenario executes verbatim. A tail makes it a different invocation
       and no longer that basis.

    Returns ``[(literal, known producers), ...]``. The producers are carried so
    the refusal can say where the literal *does* come from, which is the
    difference between "we refuse this" and a reason a human can act on.
    """
    asserted = [
        unwrap_literal(text)
        for text in generated.expected_observations
        if str(unwrap_literal(text)).strip()
    ]
    if not asserted:
        return []

    # A literal the invocation was asked about and did not print is no longer
    # an attribution. Without this subtraction the scenario-level copy of a
    # refused oracle still finds a basis in the very operation that was just
    # proven unable to emit it, and the refusal reads as one problem when it is
    # two: the command asserts what it cannot print, and the run at large is
    # asked to find it somewhere.
    refuted = {
        literal for _invocation, _label, literal, _producers, _detail
        in cross_contract_observations(generated, context)
    }
    attributed = {
        literal for literal in _attributed_literals(generated) if literal not in refuted
    }
    established = context.established_observations or {}
    run_verbatim = {
        _norm_command(command) for command in generated.command_strings()
    }
    from_commands: set[str] = set()
    for command in run_verbatim:
        from_commands.update(established.get(command, ()))

    out: list[tuple[str, tuple[str, ...]]] = []
    for literal in asserted:
        if _emitted_by(literal, attributed) or _emitted_by(literal, from_commands):
            continue
        producers = tuple(
            sorted(
                command
                for command, literals in established.items()
                if _emitted_by(literal, literals)
            )
        )
        out.append((literal, producers))
    return out


def grounding_tokens_from(unit: object | None) -> set[str]:
    """Lowercase tokens proving a reference names something the repository says."""
    tokens: set[str] = set()
    if unit is None:
        return tokens
    unit_id = str(getattr(unit, "unit_id", "") or "").strip().lower()
    if unit_id:
        tokens.add(unit_id)
    name = str(getattr(unit, "name", "") or "").strip().lower()
    if len(name) >= 4:
        tokens.add(name)
    for criterion in getattr(unit, "acceptance_criteria", None) or []:
        if isinstance(criterion, dict):
            label = str(criterion.get("criterion", "") or "").strip().lower()
            if len(label) >= 4:
                tokens.add(label)
    return tokens


def principle_tokens_from(founder: object | None) -> set[str]:
    """Lowercase rubric ids a product-principle reference may name."""
    tokens: set[str] = {"product rubric", "founder context"}
    if founder is None:
        return tokens
    rubric = getattr(founder, "rubric", None)
    if not isinstance(rubric, dict):
        return tokens
    for key in ("categories", "never_acceptable", "ask_user_boundaries"):
        for item in rubric.get(key, []) or []:
            if isinstance(item, dict):
                ident = str(item.get("id", "") or "").strip().lower()
                if ident:
                    tokens.add(ident)
    return tokens


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate_scenario(
    generated: GeneratedScenario, context: ValidationContext
) -> list[str]:
    """Return every reason ``generated`` must be refused. Empty means accepted."""
    reasons: list[str] = []
    reasons += _check_safety(generated, context)
    reasons += _check_quality(generated, context)
    reasons += _check_provenance(generated, context)
    return reasons


#: The stages that may produce a scenario. Anything else is not a stage the
#: planner runs, so a scenario claiming one did not come from this system.
_KNOWN_STAGES = frozenset(
    {"initial", "diff_refinement", "adaptive", "coverage_gap"}
)


def _check_provenance(generated: GeneratedScenario, context: ValidationContext) -> list[str]:
    """A scenario must be able to answer "why did Product Driver test this?".

    Provenance is load-bearing, not decoration: it is what lets a reader of
    ``scenario-plan.json`` reconstruct the decision months later without the
    model conversation. A scenario that cannot say which run generated it, at
    which wave, from which repository state, and — when it claims to be a
    response to a failure — *which* failure, is refused here rather than
    executed and cited as evidence of something.
    """
    reasons: list[str] = []
    provenance = generated.provenance

    # Deliberately the fields that are always derivable from the run itself.
    # ``repository_head`` is recorded and rendered, but is not a refusal
    # condition: a target that is not a git checkout has no head, and refusing
    # every scenario there would punish the proposal for the environment rather
    # than for anything about the proposal.
    missing = [
        label
        for label, value in (
            ("run/task identity (task_hash)", provenance.task_hash),
            ("generation stage", provenance.stage),
            ("generation source (model or session_id)", provenance.model or provenance.session_id),
        )
        if not str(value).strip()
    ]
    if missing:
        reasons.append(
            "scenario carries no usable provenance, so nothing records why it exists: "
            + ", ".join(missing)
        )

    if provenance.stage and provenance.stage not in _KNOWN_STAGES:
        reasons.append(
            f"scenario claims generation stage {provenance.stage!r}, which is not a stage "
            f"this planner runs ({', '.join(sorted(_KNOWN_STAGES))})"
        )

    if provenance.wave < 1:
        reasons.append(
            f"scenario records generation wave {provenance.wave}, but waves are numbered "
            "from 1; an unnumbered wave cannot be located in the run"
        )

    if not (provenance.generating_risk.strip() or generated.rationale.strip()):
        reasons.append(
            "scenario states neither a generating risk nor a rationale, so the risk it "
            "was meant to cover cannot be recovered"
        )

    # A coverage-gap scenario exists *because a named risk has no evidence*.
    # The citation requirement is the same shape as the adaptive one and exists
    # for the same reason: without it, "this closes a gap" is a label rather
    # than a claim anyone can check. It is a different citation because the
    # cause is different — and running these two stages as one is what produced
    # a wave that could only ever be refused, since with nothing failed there
    # was no failure any proposal could have named.
    if provenance.stage == "coverage_gap":
        cited = [r for r in provenance.source_risks if str(r).strip()]
        if not cited:
            reasons.append(
                "coverage-gap scenario names no identified risk; a case generated to "
                "close a gap must record which risk from this run's register it closes"
            )
        elif context.known_risk_ids:
            unknown = sorted(set(cited) - context.known_risk_ids)
            if unknown:
                reasons.append(
                    "coverage-gap scenario cites risk(s) this run never identified: "
                    + ", ".join(unknown)
                )

    # An adaptive scenario exists *because something failed*. If it cannot name
    # what, it is an ordinary proposal wearing an adaptive label, and the claim
    # that verification responded to evidence would be unfounded.
    if provenance.stage == "adaptive":
        named = list(provenance.source_failures) + list(provenance.source_clusters)
        if not named:
            reasons.append(
                "adaptive scenario names no source failure or failure cluster; an adaptive "
                "case must record which observed failure caused it"
            )
        else:
            known = context.known_failure_ids | context.known_cluster_ids
            unknown = sorted(set(named) - known) if known else []
            if unknown:
                reasons.append(
                    "adaptive scenario cites failure/cluster id(s) that this run never "
                    f"observed: {', '.join(unknown)}"
                )

    return _dedupe(reasons)


def safety_reasons(generated: GeneratedScenario, context: ValidationContext) -> list[str]:
    """Only the "may this run at all?" half of validation.

    Used by paths that re-check a plan which was already judged for quality when
    it was generated — replay, principally. Grounding and duplication are
    authorship questions and are settled at generation time; safety is a
    property of the moment of execution and is re-established every time.
    """
    return _check_safety(generated, context)


def _check_safety(generated: GeneratedScenario, context: ValidationContext) -> list[str]:
    reasons: list[str] = []

    # -- commands: approved set only, and never a hard-blocked action -------
    for command in generated.command_strings():
        ok, why = context.approved_commands.approves(command)
        if not ok:
            reasons.append(f"unsafe or unapproved operation: {why}")

    # -- credential material anywhere in the proposal ----------------------
    for text in _all_strings(generated):
        for pattern in _SECRET_MATERIAL:
            if pattern.search(text):
                reasons.append(
                    "scenario depends on credential material or a credential environment "
                    f"variable ({pattern.pattern[:40]}...); scenarios never handle secrets"
                )
                break
        if is_secret_path(text):
            reasons.append(f"scenario references a secret/credential path ({text[:80]})")

    # -- repository authority is never a scenario's target -----------------
    for text in _all_strings(generated):
        for pattern in _AUTHORITY_PATTERNS:
            if pattern.search(text):
                reasons.append(
                    "scenario would touch repository authority "
                    f"({pattern.pattern}); authority is read, never exercised"
                )
                break

    # -- HTTP: loopback only ------------------------------------------------
    for request in _all_requests(generated):
        control = _control_character_problem(request.url or request.path or "")
        if control:
            reasons.append(f"unsupported request target: {control}")
            continue
        _target, problem = resolve_http_target(
            app_url=context.app_url,
            url=request.url or "",
            path=request.path or "",
            local_hosts=context.local_hosts,
        )
        if problem:
            reasons.append(f"unsupported external effect: {problem}")
        if request.timeout_s is not None and not 0 < request.timeout_s <= MAX_TIMEOUT_S:
            reasons.append(f"request timeout {request.timeout_s}s is outside 1..{MAX_TIMEOUT_S}s")

    # -- browser: relative or loopback navigation only ---------------------
    #    An allowlist, and the same resolution the executor performs. A
    #    denylist here was strictly *wider* than what the executor treated as
    #    absolute, which is the one direction that cannot be tolerated.
    for action in generated.actions:
        for step in action.browser_steps:
            if step.goto is None:
                continue
            target, problem = resolve_browser_target(
                app_url=context.app_url, goto=step.goto
            )
            if problem:
                reasons.append(f"unsupported external navigation: {problem}")
                continue
            problem = _local_url_problem(target, context.local_hosts)
            if problem:
                reasons.append(f"unsupported external navigation: {problem}")

    # -- services: only what the base scenario declared --------------------
    unknown = sorted(set(generated.service_refs) - context.declared_services)
    if unknown:
        reasons.append(
            "scenario references service(s) no base scenario declares: " + ", ".join(unknown)
        )
    for action in generated.actions:
        if action.kind in {"restart_service", "stop_service", "start_service"}:
            if action.service not in generated.service_refs:
                reasons.append(
                    f"{action.kind} names service {action.service!r}, which the scenario "
                    "does not declare in service_refs"
                )

    # -- fixtures: a name, never a path, and always data --------------------
    for action in generated.actions:
        if action.kind != "fixture":
            continue
        name = action.fixture_name
        if not name or "/" in name or "\\" in name or ".." in name or Path(name).is_absolute():
            reasons.append(
                f"fixture name {name!r} must be a bare filename; fixtures are written "
                "into the run's evidence directory and nowhere else"
            )
            continue
        suffix = Path(name).suffix.casefold()
        if suffix not in FIXTURE_DATA_EXTENSIONS:
            reasons.append(
                f"fixture name {name!r} must end in one of "
                f"{', '.join(sorted(FIXTURE_DATA_EXTENSIONS))}. A fixture is data the "
                "product reads; its path is substituted into an approved command, and an "
                "approved command here is an interpreter, so a fixture an interpreter "
                "would execute, import or collect is model-authored code with the "
                "driver's own authority."
            )

    # -- bounded waits and timeouts ----------------------------------------
    for action in generated.actions:
        if action.kind == "wait" and not 0 <= (action.wait_ms or 0) <= MAX_WAIT_MS:
            reasons.append(f"wait of {action.wait_ms}ms exceeds the {MAX_WAIT_MS}ms bound")
        if action.timeout_s is not None and not 0 < action.timeout_s <= MAX_TIMEOUT_S:
            reasons.append(f"timeout {action.timeout_s}s is outside 1..{MAX_TIMEOUT_S}s")

    return _dedupe(reasons)


def _check_quality(generated: GeneratedScenario, context: ValidationContext) -> list[str]:
    reasons: list[str] = []

    if not generated.actions:
        reasons.append("scenario performs no actions, so it observes nothing")

    # -- grounding ---------------------------------------------------------
    if not generated.requirement_reference.strip():
        reasons.append("no requirement_reference: a scenario must name what it verifies")
    elif not context.grounds_requirement(generated.requirement_reference):
        reasons.append(
            f"requirement_reference {generated.requirement_reference!r} does not name the "
            "active unit, one of its acceptance criteria, or an AC-<AREA>-<nnn> id — a "
            "scenario may not invent a product requirement"
        )
    if not generated.product_principle_reference.strip():
        reasons.append("no product_principle_reference")
    elif not context.grounds_principle(generated.product_principle_reference):
        reasons.append(
            f"product_principle_reference {generated.product_principle_reference!r} does not "
            "name a founder rubric category"
        )
    if len((generated.purpose or "").strip()) < MIN_PURPOSE_CHARS and not generated.rationale.strip():
        reasons.append(
            "scenario states neither a purpose nor a rationale, so its risk basis is unknown"
        )

    # -- observability -----------------------------------------------------
    if not generated.has_observable_outcome():
        reasons.append(
            "scenario declares no observable outcome: nothing it does could pass or fail"
        )

    # -- an effect claim needs an oracle -----------------------------------
    if generated.risk_category in EFFECT_FAMILY and not generated.inspects_persisted_state():
        reasons.append(
            f"a {generated.risk_category.value} scenario asserts something about whether an "
            "effect durably happened, but inspects no persisted state. An HTTP status or a "
            "local response is not evidence that the underlying outcome occurred."
        )

    # -- an asserted observation needs a command that could emit it --------
    #
    # The mirror of the rule above. That one refuses a claim about an effect
    # with no oracle; this one refuses an oracle with no operation behind it.
    # Both are the same principle — an assertion Product Driver cannot attribute
    # to anything it runs is not evidence about the product — and this half was
    # missing, so a scenario could execute the command for one case and require
    # the sentence a different case prints. It fails closed forever, against a
    # perfectly correct product, and the gate reads it as a product defect.
    # -- an asserted observation must be producible by the invocation that
    #    was asked for it -------------------------------------------------
    #
    # Naming the operation that prints a sentence is free: a model writes the
    # command and the expectation together, so `expect_contains` proves only
    # that it believed the two go together. Where the repository already binds
    # that sentence to a different invocation of the same program — the other
    # side of a `--case`, an `--inject`, any selector — belief is not enough,
    # and the invocation is asked what it prints. A sentence it cannot print is
    # unsatisfiable however correct the product is, and refusing it here is the
    # difference between a generation contract error and a false product defect.
    for invocation, label, literal, producers, detail in cross_contract_observations(
        generated, context
    ):
        reasons.append(
            f"{label!r} runs {invocation!r} and requires {literal!r}, which that "
            f"invocation has no basis for printing: {detail}. This repository binds "
            "that literal to " + "; ".join(repr(command) for command in producers[:3])
            + " — a different invocation of the same program. A selector narrows what a "
            "command does, so an observation belonging to one selection is not evidence "
            "about another, and this assertion would fail against a correct product. "
            "Assert what the invocation this scenario runs actually prints, or run the "
            "invocation the literal belongs to."
        )

    for literal, producers in unattributed_observations(generated, context):
        where = ""
        if producers:
            where = (
                " In this repository's own scenario files that literal is established "
                "by: " + "; ".join(repr(command) for command in producers[:3])
                + ". This scenario runs none of those invocations as written — an "
                "argument tail narrows what a command does, and nothing here can know "
                "what the narrowed form still prints."
            )
        reasons.append(
            f"expected_observations requires {literal!r}, and no operation this scenario "
            "performs has a basis for emitting it: no action's expect_contains, no state "
            "check's contains and no browser step's expect_text names it, and the scenario "
            "runs no approved command a human already bound it to. This is a generation "
            "contract error, not a statement about the product: the assertion is matched "
            "against everything the run produced, so it cannot be attributed to any "
            "command, and it would fail against a correct product. Name the operation that "
            "prints it, in that operation's own expectations." + where
        )

    # -- cleanup / isolation ----------------------------------------------
    if generated.mutates_local_state() and not (generated.cleanup or generated.isolation_note.strip()):
        reasons.append(
            "scenario mutates local state but declares neither cleanup commands nor an "
            "isolation strategy, so it would contaminate every scenario after it"
        )

    # -- duplicate coverage -------------------------------------------------
    if generated.signature() in context.existing_signatures:
        reasons.append(
            "duplicate: this exercises the same situation with the same expectations as "
            "coverage that already exists, and adds nothing"
        )
    # Compared the way the filesystem compares them, and against permanent
    # scenario names as well as generated ids. An exact duplicate was already
    # refused here; one differing only in case was two scenarios in memory and
    # one evidence directory on disk, and the second silently overwrote the
    # first's record while the gate credited both.
    if identity_key(generated.id) in {identity_key(i) for i in context.existing_ids}:
        reasons.append(
            f"scenario id {generated.id!r} is already used in this run (ids are compared "
            "the way the filesystem compares them, so two ids differing only in case are "
            "one evidence directory and therefore one identity)"
        )

    # -- regression scope ---------------------------------------------------
    if generated.risk_category is RiskCategory.REGRESSION:
        if not (generated.provenance.diff_files_consulted or generated.generated_from):
            reasons.append(
                "a regression scenario must name the diff or prior evidence that puts the "
                "behaviour it guards inside this task's scope"
            )

    return _dedupe(reasons)


def validate_plan(
    scenarios: Sequence[GeneratedScenario], context: ValidationContext
) -> tuple[list[GeneratedScenario], list[tuple[GeneratedScenario, list[str]]]]:
    """Validate a batch, refusing later duplicates of earlier accepted entries.

    Returns ``(accepted, [(refused, reasons), ...])``. Order is preserved so the
    first of two identical proposals wins, which keeps the outcome deterministic
    for a given batch.
    """
    accepted: list[GeneratedScenario] = []
    refused: list[tuple[GeneratedScenario, list[str]]] = []
    seen_signatures = set(context.existing_signatures)
    seen_ids = set(context.existing_ids)

    for scenario in scenarios:
        # Copied wholesale, with only the running duplicate-detection state
        # replaced. Listing the fields by hand silently dropped every field
        # added later, which is how an adaptive scenario citing a failure that
        # never happened came to be admitted.
        local = replace(
            context, existing_signatures=seen_signatures, existing_ids=seen_ids
        )
        reasons = validate_scenario(scenario, local)
        if reasons:
            refused.append((scenario, reasons))
            continue
        accepted.append(scenario)
        seen_signatures.add(scenario.signature())
        seen_ids.add(scenario.id)
    return accepted, refused


def plan_signatures(plan: GeneratedScenarioPlan | None) -> set[str]:
    return plan.signatures() if plan is not None else set()


def permanent_signatures(scenarios: Sequence[Scenario]) -> set[str]:
    """Coverage signatures for handwritten scenarios, for duplicate detection.

    Built with the same :func:`~neyma_product_driver.scenario_plan.coverage_signature`
    a generated scenario uses, so a proposal that merely restates a permanent
    scenario's operations and expectations is refused as a duplicate.
    """
    from .scenario_plan import _norm, coverage_signature

    out: set[str] = set()
    for scenario in scenarios:
        # `expect_state` is the phase form's *trailing* state check, which is
        # what a generated scenario's `persisted_state_checks` compiles to. It
        # therefore contributes to the state commands, not to the ordered
        # actions — an inline, ordered state_check is a different situation and
        # must not collide with it.
        actions = [f"command:{_norm(c.run)}" for c in scenario.commands]
        actions += [
            f"request:{r.method.upper()}:{_norm(r.url or r.path or '')}" for r in scenario.requests
        ]
        out.add(
            coverage_signature(
                actions=actions,
                expected=scenario.expect_visible,
                forbidden=scenario.forbidden,
                state_commands=[c.command for c in scenario.expect_state],
            )
        )
    return out


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


#: A leading scheme (``https:``, ``file:``, ``javascript:``) anywhere a *relative*
#: path is expected. ``scenarios._join_url`` returns such a string verbatim, so a
#: path that carries a scheme silently replaces the approved base URL.
_HAS_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


def resolve_http_target(
    *, app_url: str, url: str, path: str, local_hosts: frozenset[str]
) -> tuple[str, str]:
    """Resolve what a generated request would actually address.

    Returns ``(target, problem)``. A non-empty problem means refuse.

    This is the single place that decides a generated request's destination, and
    both validation and compilation call it, so the executor can never be handed
    a target that nothing checked. ``path`` is deliberately *not* trusted to be
    relative: an absolute or scheme-relative path overrides the approved base URL
    entirely, which is exactly the bypass this closes.
    """
    if url and path:
        return "", "a request names both a url and a path; only one may decide the target"

    if url:
        return url, _local_url_problem(url, local_hosts)

    if not path:
        return "", "a request action names neither a url nor a path"

    # A relative path is the only supported shape. Anything that can re-point the
    # request away from the approved base is refused rather than normalized —
    # normalizing an attack into something safe-looking hides the intent.
    if _HAS_SCHEME.match(path):
        return "", (
            f"request path {path[:80]!r} carries a URL scheme, so it would replace the "
            "approved base URL. A generated request path must be relative."
        )
    if path.startswith("//") or path.startswith("\\\\"):
        return "", (
            f"request path {path[:80]!r} is scheme-relative, so it would address another "
            "host. A generated request path must be relative."
        )
    if not app_url:
        return "", (
            f"request {path!r} is relative but no base scenario supplies an app_url, so "
            "there is nothing local to address"
        )

    problem = _local_url_problem(app_url, local_hosts)
    if problem:
        return "", problem
    return app_url.rstrip("/") + "/" + path.lstrip("/"), ""


def identity_key(scenario_id: str) -> str:
    """The key two scenario identities collide on, as the filesystem sees it.

    Identity was case-sensitive in memory and case-insensitive on disk, and
    nothing reconciled the two: ``gen-AUTH-01`` and ``gen-auth-01`` were two
    required scenarios everywhere in the suite, the plan and the gate, and one
    directory on APFS and NTFS. Unicode is normalised for the same reason —
    two spellings of the same character are one filename.
    """
    import unicodedata

    return unicodedata.normalize("NFC", scenario_id or "").casefold()


def resolve_browser_target(*, app_url: str, goto: str | None) -> tuple[str, str]:
    """Resolve where a browser step would actually navigate.

    Returns ``(target, problem)``. A non-empty problem means refuse; the target
    is then empty and nothing may navigate.

    This is the single place that decides a browser step's destination, and
    both validation and the executor call it, so the string that was inspected
    and the string that is dialled cannot differ. They did: validation screened
    a ``goto`` only when it began ``http://`` or ``https://``, while the
    executor treated anything beginning with the four letters ``http`` as
    absolute. Every string in between — ``http:/host/x``, ``http:host/x``,
    ``http:\\\\host\\\\x``, ``httpx://host/x`` — was inspected by nothing and
    navigated to anyway, and Chromium's parser puts the authority back.

    So the shape rule is an allowlist rather than a denylist: an absolute
    ``http(s)://`` URL, or a path beginning with a single ``/``. Anything else
    is refused rather than normalised, because normalising an escape into
    something that looks safe hides what was asked for. Whether an absolute URL
    is *permitted* — loopback only — is a separate question, asked by the
    validator with the run's configured host set; this decides only where the
    string points.
    """
    raw = goto or ""
    control = _control_character_problem(raw)
    if control:
        return "", f"browser navigation target contains a control character: {control}"

    if raw.startswith("http://") or raw.startswith("https://"):
        return raw, ""

    if not raw:
        return "", "a browser step names an empty navigation target"

    if _HAS_SCHEME.match(raw):
        return "", (
            f"browser navigation target {raw[:80]!r} carries a URL scheme, so it would "
            "replace the approved app_url. Navigation must be either an http:// or "
            "https:// URL, or a path beginning with '/'."
        )
    if raw.startswith("//") or raw.startswith("\\\\") or raw.startswith("\\"):
        return "", (
            f"browser navigation target {raw[:80]!r} is scheme-relative, so it would "
            "address another host. A relative navigation must begin with a single '/'."
        )
    if not raw.startswith("/"):
        return "", (
            f"browser navigation target {raw[:80]!r} is neither an http:// or https:// "
            "URL nor a path beginning with '/', so where it would navigate cannot be "
            "determined."
        )
    if not app_url:
        return "", (
            f"browser navigation {raw[:80]!r} is relative but no base scenario supplies "
            "an app_url, so there is nothing local to address"
        )
    return _join_url(app_url, raw), ""


def _local_url_problem(url: str, local_hosts: frozenset[str]) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return f"unparseable url {url!r}"
    if parsed.scheme not in {"http", "https", ""}:
        return f"unsupported scheme {parsed.scheme!r} in {url!r}"
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return f"url {url!r} names no host"
    if host not in local_hosts:
        return (
            f"url {url!r} addresses {host!r}, which is not loopback. Scenarios operate the "
            "locally running product and never produce an external effect."
        )
    return ""


def _all_requests(generated: GeneratedScenario) -> list[GeneratedRequest]:
    out: list[GeneratedRequest] = []
    for action in generated.actions:
        if action.request is not None:
            out.append(action.request)
        out.extend(action.requests)
    return out


def _all_strings(generated: GeneratedScenario) -> list[str]:
    """Every free-text field that could smuggle a path, a host or a credential."""
    out: list[str] = list(generated.command_strings())
    for action in generated.actions:
        out.append(action.fixture_name)
        out.append(action.fixture_content)
        for request in [action.request, *action.requests]:
            if request is None:
                continue
            out.append(request.url)
            out.append(request.path)
            out.append(request.body)
            out.extend(request.headers.values())
            if request.json_body is not None:
                out.append(str(request.json_body))
        for step in action.browser_steps:
            out.extend(
                s for s in (step.goto, step.click, step.fill, step.value, step.wait_for) if s
            )
    return [s for s in out if s]


def _dedupe(reasons: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            out.append(reason)
    return out


__all__ = [
    "ApprovedCommands",
    "DEFAULT_LOCAL_HOSTS",
    "FIXTURE_DATA_EXTENSIONS",
    "Priority",
    "ValidationContext",
    "grounding_tokens_from",
    "identity_key",
    "permanent_signatures",
    "plan_signatures",
    "principle_tokens_from",
    "resolve_browser_target",
    "validate_plan",
    "validate_scenario",
]
