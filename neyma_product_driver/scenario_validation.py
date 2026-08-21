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

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import urlparse

from .command_guard import classify_command, classify_worktree_ownership, is_secret_path
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
    """

    def __init__(self, entries: Iterable[str]) -> None:
        self.entries: tuple[str, ...] = tuple(
            sorted({_norm_command(e) for e in entries if _norm_command(e)})
        )

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
        """Harvest every command a human already approved by writing it down."""
        entries: list[str] = list(configured)
        for scenario in scenarios:
            entries.extend(scenario.setup)
            entries.extend(scenario.teardown)
            entries.extend(spec.run for spec in scenario.commands)
            entries.extend(check.command for check in scenario.expect_state)
            for step in scenario.steps:
                if step.command is not None:
                    entries.append(step.command.run)
                if step.state_check is not None:
                    entries.append(step.state_check.command)
        return cls(entries)

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

        return False, (
            f"command is not in the approved set: {normalized!r}. Generated scenarios may "
            "only run commands a human already approved."
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
