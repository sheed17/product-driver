"""Staged, bounded scenario planning — and the promotion-candidate ledger.

The planner owns *when* generation happens and *how much* of it is allowed:

    Stage 1  initial          before the builder's work is judged, from the task,
                              the active READY unit, its acceptance criteria, the
                              founder context and the permanent suite
    Stage 2  diff refinement  after the builder changes the repository, from what
                              was actually touched — a task that looked UI-only
                              but moved approval state, persistence or
                              authorization earns verification of those
    Stage 3  adaptive         after execution, from the failures and their
                              clusters — one failing transition suggests the
                              whole family around it

Expansion is bounded on every axis, because "generate more scenarios until
something passes" is a way to burn an afternoon and a Claude subscription
without learning anything: scenarios per wave, waves per run, scenarios per risk
category, scenarios in total, and a wall-clock execution budget. Every refusal
caused by a bound is recorded on the wave, so a plan that stopped early says so
rather than looking complete.

Generated scenarios are **ephemeral**. They live under the run directory and are
never written into the repository's scenario files. When one catches a real
defect and later passes, it is recorded in ``promotion-candidates.json`` — a
suggestion to a human, with the evidence attached. Promotion itself is a separate,
explicit command.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .config import ScenarioGenerationConfig
from .evidence import EvidenceStore
from .failure_clustering import FailureCluster
from .models import redact_obj, utcnow
from .scenario_generator import (
    GenerationBrief,
    ScenarioReasoner,
    parse_notes,
    parse_risks,
    parse_scenarios,
    provenance_for,
)
from .scenario_plan import (
    REJECTED_CONTRACT,
    CompilationError,
    GeneratedScenario,
    GeneratedScenarioPlan,
    GenerationBasis,
    RejectedScenario,
    WaveRecord,
    compile_to_scenario,
    task_digest,
)
from .scenario_validation import (
    ApprovedInvocationProbe,
    ApprovedCommands,
    ValidationContext,
    established_observations_from,
    grounding_tokens_from,
    permanent_signatures,
    principle_tokens_from,
    validate_plan,
)
from .scenarios import Scenario

PLAN_FILENAME = "scenario-plan.json"
WAVES_DIRNAME = "scenario-generation"
PROMOTION_FILENAME = "promotion-candidates.json"

STAGE_INITIAL = "initial"
STAGE_DIFF = "diff_refinement"
STAGE_ADAPTIVE = "adaptive"
#: A wave whose purpose is to close a *named coverage gap* rather than to
#: respond to a failure. It exists because the two were one stage, and an
#: adaptive scenario must name the observed failure that caused it — so a wave
#: launched when nothing had failed (the evaluator asked for more verification,
#: or a risk had no evidence) could only ever produce refusals, while still
#: contributing newly identified risks to the register. Every wave of that shape
#: made the run less acceptable and no more verified. A coverage-gap scenario
#: cites the risk it closes instead, which is a citation the run can check.
STAGE_COVERAGE_GAP = "coverage_gap"
#: Recorded on a wave whose "generation" was the resume itself: scenarios the
#: plan had committed to that can no longer be executed.
STAGE_RESUME = "resume"


@dataclass(frozen=True)
class PlanRestore:
    """What ``restore_from_store`` found, as three states rather than one.

    "There is no plan" and "there is a plan and it cannot be read" were the same
    empty string, so the second was handled as the first: the run started again
    at wave zero with an empty plan, and the next ``persist()`` wrote that empty
    plan over the only machine-readable record of what the run had decided to
    verify. They are opposite situations — one is a run with nothing behind it,
    the other is a run whose state exists and is inaccessible — and only the
    second must fail closed.
    """

    state: str  # "absent" | "restored" | "unreadable"
    note: str = ""
    #: Where the unreadable file was preserved, so nothing can overwrite it.
    preserved_path: str = ""

    @property
    def unreadable(self) -> bool:
        return self.state == "unreadable"

    def __str__(self) -> str:  # the note is what callers used to receive
        return self.note


def permanent_risk_coverage(
    scenarios: Sequence[Scenario],
) -> dict[str, list[str]]:
    """Read the reviewed ``verifies:`` blocks off the permanent scenarios.

    Returns risk category -> the claims that declare they verify it, each
    labelled ``"<scenario>: <claim>"``. This is the planner's half of the same
    fact the gate measures: what permanent coverage *intends* to verify.

    It matters here for one reason. A wave that does not know a risk is already
    covered asks for a scenario that duplicates the permanent case — which
    validation then refuses as a duplicate, correctly — and the risk stays on
    the uncovered list. That loop ran three times in one run and produced
    nothing. Knowing what permanent coverage claims lets a wave be aimed at what
    is genuinely missing.

    Pass only the scenarios this run will actually EXECUTE. A claim made by a
    scenario file that is never run is not coverage this run has planned, and
    counting it would make the planner report fewer gaps than the gate will
    find — which fails closed at the gate but leaves the generator un-aimed,
    reintroducing the loop this exists to break.
    """
    coverage: dict[str, list[str]] = {}
    seen: set[str] = set()
    for scenario in scenarios:
        if scenario is None or scenario.name in seen:
            continue
        seen.add(scenario.name)
        for claim in scenario.verifies:
            coverage.setdefault(claim.risk_category, []).append(
                f"{scenario.name}: {claim.claim}"
            )
    return coverage


# --------------------------------------------------------------------------
# Promotion candidates
# --------------------------------------------------------------------------


class PromotionCandidate(BaseModel):
    """A generated scenario that earned a look at permanent regression coverage.

    Recorded only when the scenario did real work: it failed while a defect was
    present, and passed after the fix. A scenario that never failed proved
    nothing new; a scenario that never passed has not yet demonstrated it
    describes a fixed behaviour rather than a broken expectation.
    """

    model_config = ConfigDict(extra="forbid")

    recorded_at: str = Field(default_factory=utcnow)
    scenario_id: str
    title: str = ""
    risk_category: str = ""
    priority: str = ""
    #: What the scenario observed when it failed — the defect it discovered.
    bug_discovered: str = ""
    #: Iteration in which it first failed, and the one in which it first passed.
    discovered_in_iteration: int = 0
    fixed_in_iteration: int = 0
    evidence_path: str = ""
    reason: str = ""
    requirement_reference: str = ""
    #: The full generated model, so promotion needs nothing but this file.
    scenario: dict[str, Any] = Field(default_factory=dict)
    #: Always false here. Promotion is a separate explicit command; nothing in a
    #: run may flip this.
    promoted: bool = False


class PromotionLedger:
    """The run's promotion-candidate file. Suggests; never promotes."""

    def __init__(self, run_dir: Path) -> None:
        self.path = Path(run_dir) / PROMOTION_FILENAME

    def load(self) -> list[PromotionCandidate]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out: list[PromotionCandidate] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                out.append(PromotionCandidate.model_validate(item))
            except Exception:
                continue
        return out

    def save(self, candidates: Sequence[PromotionCandidate]) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([c.model_dump(mode="json") for c in candidates], indent=2),
            encoding="utf-8",
        )
        return self.path

    def record(self, candidate: PromotionCandidate) -> bool:
        """Add a candidate unless one already exists for that scenario."""
        existing = self.load()
        if any(c.scenario_id == candidate.scenario_id for c in existing):
            return False
        existing.append(candidate)
        self.save(existing)
        return True


class DefectMemory:
    """Which generated scenarios have failed so far, and with what observation.

    Kept separate from the ledger because "this failed once" is not yet a
    promotion candidate — it becomes one only when the same scenario later
    passes.
    """

    def __init__(self) -> None:
        self.first_failure: dict[str, tuple[int, str]] = {}

    def note_failure(self, scenario_id: str, iteration: int, observation: str) -> None:
        self.first_failure.setdefault(scenario_id, (iteration, observation))

    def discovered(self, scenario_id: str) -> tuple[int, str] | None:
        return self.first_failure.get(scenario_id)


# --------------------------------------------------------------------------
# The planner
# --------------------------------------------------------------------------


class ScenarioPlanner:
    """Generates, validates, compiles and persists a run's scenario plan."""

    def __init__(
        self,
        *,
        repo: Path,
        config: ScenarioGenerationConfig,
        reasoner: ScenarioReasoner,
        store: EvidenceStore | None = None,
        base_scenario: Scenario | None = None,
        permanent_scenarios: Sequence[Scenario] = (),
        founder: Any = None,
        browser_enabled: bool = False,
        contract_probe: Callable[[str], Any] | None = None,
        emit: Callable[[str], None] = lambda _m: None,
    ) -> None:
        #: Whether this run can actually drive a browser. Passed to the
        #: generator so it does not propose coverage the suite will only skip.
        self.browser_enabled = browser_enabled
        self.repo = Path(repo)
        self.config = config
        self.reasoner = reasoner
        self.store = store
        self.base_scenario = base_scenario
        self.permanent_scenarios = list(permanent_scenarios)
        self.founder = founder
        self.emit = emit
        #: Overridable so a test can answer from recorded output instead of
        #: running the real program. Left unset on the real path, where the
        #: probe is built against the repository under test.
        self._contract_probe_cache = contract_probe

        self.plan = GeneratedScenarioPlan()
        # The base scenario alone: it is the only permanent scenario a run's
        # suite executes (`_assemble_suite` puts it there and nothing else).
        # `permanent_scenarios` is a wider set — it supplies the approved
        # command vocabulary and the duplicate-signature index — and a claim
        # from a file this run never runs is not coverage this run has.
        self.plan.permanent_coverage = permanent_risk_coverage(
            [base_scenario] if base_scenario else []
        )
        self.approved_commands = ApprovedCommands.from_sources(
            scenarios=[*self.permanent_scenarios, *([base_scenario] if base_scenario else [])],
            configured=config.approved_commands,
        )
        #: id -> compiled Scenario, for everything accepted so far.
        self.compiled: dict[str, Scenario] = {}
        self._wave = 0
        #: The active READY unit as of the most recent basis. Validation needs
        #: its vocabulary to tell a grounded requirement from an invented one.
        self._unit: Any = None
        #: Failures and clusters this run has actually observed. An adaptive
        #: scenario may only name one of these as its cause.
        self._observed_failure_ids: set[str] = set()
        self._observed_cluster_ids: set[str] = set()
        #: Set when a plan existed on disk and could not be read. A run in this
        #: state cannot say what it had already decided to verify, so it may not
        #: reach an acceptance — see :meth:`generation_problems`.
        self._restore_failure: str = ""

    # -- public API --------------------------------------------------------

    @property
    def waves_used(self) -> int:
        return self._wave

    @property
    def restore_failed(self) -> bool:
        """True when this run's plan exists on disk and could not be read."""
        return bool(self._restore_failure)

    def generation_problems(self) -> list[str]:
        """Waves that failed rather than waves that had nothing to add.

        These are different facts and the difference decides an acceptance. A
        generator that raised produced no coverage *and no information*; a
        generator that answered with an empty list said something. Only the
        first belongs here, and the acceptance gate refuses to accept a run that
        has one.

        A resume that *dropped* coverage belongs here for the same reason, and
        did not reach it. The plan had committed to those scenarios, the builder
        was told about them, and they can no longer be executed — the run
        verified less than it set out to and the only report was a line of
        terminal scrollback the gate never saw. Same for a plan that could not
        be read at all: a run that cannot say what it decided to verify has not
        established that it verified it.

        And so does a candidate this harness could not READ. A generator that
        proposed nine scenarios which Product Driver then discarded, because none
        of them satisfied the structured schema Product Driver itself authored,
        produced no coverage *and no information* — the same state as a generator
        that raised, reached by a different route. It is emphatically not the
        state "the generator had nothing to add", and until this it was recorded
        as though it were: the P6/M6 re-verification run proposed nine, invalided
        nine on an unrecognised ``risk_category``, reported ``0 generated
        case(s)``, and ACCEPTed. Neyma logged that as ``P6-D46``.

        A *mixed* wave belongs here too, and this is the deliberate part. Some
        candidates parsing does not excuse the ones that did not: what an
        unreadable candidate would have exercised is unknown, so nothing can say
        whether the coverage that did run reaches it. That is an unquantifiable
        coverage gap, and it follows the rule this method already applies to a
        wave whose reasoner raised — which blocks even when other waves produced
        good coverage. Same fact, same answer.

        What is NOT here: a duplicate, a safety or quality refusal, a budget.
        Those are planning working, on a proposal it understood, and a harness
        that treated them as failures would block every healthy run.
        """
        problems = [
            f"generation wave {record.wave} failed: {record.reasoner_error}"
            for record in self.plan.waves
            if record.reasoner_error
        ]
        if self._restore_failure:
            problems.append(self._restore_failure)
        for record in self.plan.waves:
            invalid = record.contract_rejections
            if invalid:
                named = "; ".join(
                    f"{r.id or '(unnamed)'}: "
                    + (r.reasons[0] if r.reasons else "no reason recorded")
                    for r in invalid[:6]
                )
                if len(invalid) > 6:
                    named += f"; ... and {len(invalid) - 6} more"
                problems.append(
                    f"generation wave {record.wave} ({record.stage}) is a "
                    "generation-contract failure in Product Driver itself, not an absence "
                    f"of proposed coverage: {record.accounting()}. Product Driver could "
                    "not interpret its own generated schema or taxonomy for "
                    f"{len(invalid)} candidate(s), so what they would have exercised is "
                    "unknown and this run may not be read as having generated "
                    f"{record.accepted_count} scenario(s) by choice. Invalid candidates: "
                    + named
                )
            if record.stage != STAGE_RESUME:
                continue
            for rejected in record.rejected:
                problems.append(
                    f"scenario {rejected.id!r} was planned and executed by this run and "
                    "could not be restored on resume, so the coverage it provided is gone: "
                    + (rejected.reasons[0] if rejected.reasons else "no reason recorded")
                )
        return problems

    def budget_exhausted(self) -> bool:
        return (
            self._wave >= self.config.max_waves
            or len(self.plan.scenarios) >= self.config.max_total_scenarios
        )

    def plan_initial(
        self,
        *,
        task: str,
        unit: Any = None,
        run_id: str = "",
    ) -> GeneratedScenarioPlan:
        """Stage 1 — what should be tested, given the task and the requirements."""
        self.plan.run_id = run_id
        self.plan.task = task
        self.plan.active_unit_id = str(getattr(unit, "unit_id", "") or "")
        basis = self._basis(task=task, unit=unit)
        self.plan.generation_basis = basis
        self._generate(basis, stage=STAGE_INITIAL, limit=self.config.max_initial_scenarios)
        return self.plan

    def refine_for_diff(
        self,
        *,
        task: str,
        unit: Any = None,
        diff_files: Sequence[str] | None = None,
        diff_stat: str = "",
    ) -> GeneratedScenarioPlan:
        """Stage 2 — what the builder actually touched changes what is at risk."""
        if not self.config.diff_aware:
            return self.plan
        files = list(diff_files) if diff_files is not None else changed_files(self.repo)
        if not files:
            self.emit("  no repository changes to refine the scenario plan against")
            return self.plan
        basis = self._basis(task=task, unit=unit, diff_files=files, diff_stat=diff_stat)
        self._generate(basis, stage=STAGE_DIFF, limit=self.config.max_adaptive_scenarios_per_wave)
        return self.plan

    def expand_after_failures(
        self,
        *,
        task: str,
        unit: Any = None,
        failures: Sequence[Any],
        clusters: Sequence[FailureCluster] = (),
        investigation_findings: Sequence[str] = (),
        evaluator_requests: Sequence[str] = (),
        diff_files: Sequence[str] | None = None,
        gaps: Sequence[Any] | None = None,
    ) -> GeneratedScenarioPlan:
        """Stage 3 — respond to what happened: a failure, or a gap with no evidence.

        Two shapes, distinguished by whether anything actually failed, because
        the two produce scenarios that must justify themselves differently.

        With failures, this is the *adaptive* stage: one failing transition
        suggests the family around it, and every case it produces must name the
        observed failure that caused it.

        Without failures — the evaluator asked for more verification, or a risk
        the run identified still has no evidence — it is the *coverage gap*
        stage, and a case must name the identified risk it closes instead.
        Running that wave as "adaptive" is what broke: with nothing failed there
        was no failure to cite, so every proposal was refused for the same
        reason, while the wave's newly identified risks joined the register and
        widened the gap list. Three waves of that turned two known gaps into six
        without a single scenario being added.

        ``gaps`` names the risks this wave is aimed at. The default — what the
        *plan* still intends to exercise — is right before anything has run.
        Once a suite has executed, the authority on what is uncovered is the
        acceptance gate, which reads execution records rather than intent, and
        the caller passes that set in so the wave is aimed at exactly the risks
        that will otherwise block the run.
        """
        gaps = list(gaps) if gaps is not None else self.plan.planned_gaps()
        if not failures and not evaluator_requests and not gaps:
            return self.plan
        if not self.config.use_prior_failures:
            return self.plan

        # Record what was actually observed, so an adaptive proposal can only
        # cite a failure or cluster this run really produced, and so the
        # scenario→failure edge can be checked rather than trusted.
        rendered: list[str] = []
        for failure in failures:
            render = getattr(failure, "render", None)
            rendered.append(render() if callable(render) else str(failure))
            scenario_id = getattr(failure, "scenario_id", "")
            if scenario_id:
                self._observed_failure_ids.add(str(scenario_id))
            cluster_id = getattr(failure, "cluster_id", "")
            if cluster_id:
                self._observed_cluster_ids.add(str(cluster_id))
        for cluster in clusters:
            self._observed_cluster_ids.add(cluster.cluster_id)

        basis = self._basis(
            task=task,
            unit=unit,
            diff_files=list(diff_files or []),
            prior_failures=rendered,
            investigation_findings=(
                list(investigation_findings) if self.config.use_investigation_findings else []
            ),
            evaluator_requests=list(evaluator_requests),
        )
        self._generate(
            basis,
            stage=STAGE_ADAPTIVE if rendered else STAGE_COVERAGE_GAP,
            limit=self.config.max_adaptive_scenarios_per_wave,
            clusters=list(clusters),
            gaps=gaps,
        )
        return self.plan

    # -- the wave ----------------------------------------------------------

    def _generate(
        self,
        basis: GenerationBasis,
        *,
        stage: str,
        limit: int,
        clusters: Sequence[FailureCluster] = (),
        gaps: Sequence[Any] | None = None,
    ) -> None:
        record = WaveRecord(wave=self._wave + 1, stage=stage, basis=basis)

        if self._wave >= self.config.max_waves:
            record.budget_notes.append(
                f"refused: {self.config.max_waves} generation wave(s) already used"
            )
            self._finish_wave(record)
            return

        room = self.config.max_total_scenarios - len(self.plan.scenarios)
        if room <= 0:
            record.budget_notes.append(
                f"refused: the run's {self.config.max_total_scenarios}-scenario total is "
                "already reached"
            )
            self._finish_wave(record)
            return

        allowed = min(limit, room)
        if allowed < limit:
            record.budget_notes.append(
                f"wave narrowed from {limit} to {allowed} by the total-scenario budget"
            )

        self._wave += 1
        record.wave = self._wave

        brief = GenerationBrief(
            stage=stage,
            wave=self._wave,
            basis=basis,
            max_scenarios=allowed,
            # `verbatim`, never `entries`: this list is copied into a proposal
            # character for character, so it must be the string a human wrote
            # and not the whitespace-collapsed key it is matched by. See
            # ApprovedCommands — the collapse is invisible until an approved
            # command carries syntax that spaces are part of.
            available_commands=list(self.approved_commands.verbatim),
            # The token beside each one, so a proposal can NAME an approved
            # command instead of reproducing it. Same order, same length.
            available_tokens=list(self.approved_commands.tokens),
            available_services=[s.name for s in (self.base_scenario.services if self.base_scenario else [])],
            app_url=self.base_scenario.app_url if self.base_scenario else "",
            # What is actually available, not what would be convenient. Telling
            # the generator a browser exists when the run cannot drive one earns
            # a wave of browser scenarios that the run then skips — coverage
            # that was planned, reported, and never executed.
            browser_enabled=self.browser_enabled,
            existing_coverage=[s.summary() for s in self.plan.scenarios]
            + [
                f"permanent coverage claims {category}: {claim}"
                for category, claims in sorted(self.plan.permanent_coverage.items())
                for claim in claims
            ],
            failure_clusters=[c.render() for c in clusters],
            product_principles=sorted(principle_tokens_from(self.founder)),
            # What the run has named and nothing intends to exercise. Without
            # this the generator proposed whatever looked interesting next,
            # identified fresh risks while it was there, and left the previous
            # wave's gaps exactly where they were.
            uncovered_risks=[
                f"{risk.label()} [{risk.severity.value} {risk.risk_category.value}] "
                f"{risk.description}"
                for risk in (
                    gaps if gaps is not None else self.plan.planned_gaps()
                )
            ],
            # What this run already tried and had refused. Without it a wave
            # cannot learn from the one before it: run 20260827-063257 refused a
            # candidate in wave 2 for using an unapproved command, and wave 3
            # proposed the identical command and was refused for the identical
            # reason. A closure wave that repeats a refused shape has spent
            # itself and closed nothing.
            prior_rejections=self._prior_rejections(),
        )

        try:
            payload = self.reasoner.propose(brief)
        except Exception as exc:  # a reasoner must never take the run with it
            record.reasoner_error = f"{type(exc).__name__}: {exc}"
            self._finish_wave(record)
            return

        if not isinstance(payload, dict):
            record.reasoner_error = (
                "the scenario generator returned no usable structured output; this wave "
                "produced nothing and the run continues with existing coverage"
            )
            self._finish_wave(record)
            return

        provenance = provenance_for(
            basis,
            stage=stage,
            wave=self._wave,
            model=self.config.model,
            session_id=str(getattr(self.reasoner, "session_id", "") or ""),
        )
        parsed, malformed = parse_scenarios(payload, provenance=provenance)
        record.proposed = len(parsed) + len(malformed)
        # Citations become the human's own text BEFORE anything judges,
        # compiles, signs or runs the proposal, so every stage downstream sees
        # one kind of command string and none of them has to know a citation
        # exists. Recorded, never silent: the plan states which command a
        # generator named rather than reproduced.
        for scenario in parsed:
            for before, after in scenario.rewrite_commands(self.approved_commands.expand):
                record.resolved_citations.append(
                    f"{scenario.id}: {before.split()[0]} -> {after}"
                )
        # Which STAGE rejected a candidate is what classifies it, not what the
        # reason string happens to say. Everything in `malformed` failed before
        # it was ever a model: the payload did not satisfy the structured schema
        # this harness itself authored and handed to the generator, so what the
        # candidate would have exercised is unknown. Everything below, from
        # `validate_plan` onwards, is planning working as designed on a proposal
        # it understood — a duplicate, a safety refusal, a budget — and stays
        # `filtered`.
        record.rejected += [
            RejectedScenario(
                id=str(raw.get("id") or ""),
                title=str(raw.get("title") or ""),
                reasons=reasons,
                raw=raw,
                kind=REJECTED_CONTRACT,
            )
            for raw, reasons in malformed
        ]

        accepted, refused = validate_plan(parsed, self._validation_context())
        record.rejected += [
            RejectedScenario(id=s.id, title=s.title, reasons=reasons, raw={})
            for s, reasons in refused
        ]

        # The per-wave allowance is enforced here, not merely requested of the
        # model in the brief. A model that returns more than it was asked for is
        # ordinary, and a budget that only holds when the model cooperates is not
        # a budget.
        if len(accepted) > allowed:
            overflow = accepted[allowed:]
            accepted = accepted[:allowed]
            note = (
                f"wave budget: {len(overflow)} scenario(s) beyond this wave's limit of "
                f"{allowed} were not admitted"
            )
            record.budget_notes.append(note)
            record.rejected += [
                RejectedScenario(id=s.id, title=s.title, reasons=[note]) for s in overflow
            ]

        for scenario in accepted:
            note = self._admit(scenario)
            if note:
                record.rejected.append(
                    RejectedScenario(id=scenario.id, title=scenario.title, reasons=[note])
                )
                if note.startswith("budget"):
                    record.budget_notes.append(f"{scenario.id}: {note}")
                continue
            record.accepted_ids.append(scenario.id)

        # Risks are additive across waves; the plan keeps the union.
        self._merge_risks(parse_risks(payload))
        assumptions, questions = parse_notes(payload)
        self.plan.assumptions += [a for a in assumptions if a not in self.plan.assumptions]
        self.plan.unresolved_questions += [
            q for q in questions if q not in self.plan.unresolved_questions
        ]
        self._link_risks()
        self._finish_wave(record)

    def _admit(self, scenario: GeneratedScenario) -> str:
        """Apply the remaining budgets and compile. Returns a refusal, or ""."""
        if len(self.plan.scenarios) >= self.config.max_total_scenarios:
            return (
                f"budget: the run's {self.config.max_total_scenarios}-scenario total is "
                "reached, so this scenario was not admitted"
            )
        per_category = self.plan.count_for(scenario.risk_category)
        if per_category >= self.config.max_scenarios_per_risk_category:
            return (
                f"budget: {self.config.max_scenarios_per_risk_category} scenarios already "
                f"cover {scenario.risk_category.value}"
            )
        try:
            compiled = compile_to_scenario(
                scenario,
                base=self.base_scenario,
                approved_commands=set(self._approved_for(scenario)),
            )
        except CompilationError as exc:
            return str(exc)
        except Exception as exc:  # a compiler bug must not execute anything
            return f"compilation failed: {type(exc).__name__}: {exc}"

        self.compiled[scenario.id] = compiled
        self.plan.scenarios.append(scenario)
        self.plan.recompute_coverage()
        return ""

    def _approved_for(self, scenario: GeneratedScenario) -> set[str]:
        """The exact command strings validation approved for this scenario.

        Recomputed rather than remembered, so the compiler's independent check
        is genuinely independent: a command that slipped through validation would
        have to slip through this too.
        """
        approved, _reasons = self.approved_commands.resolve(scenario.command_strings())
        return approved

    def _prior_rejections(self, limit: int = 16) -> list[str]:
        """Why this run's earlier candidates were refused, newest wave first.

        Deduplicated on the reason text, because one wave refusing six
        candidates for the same unapproved command is one fact, not six, and a
        brief that spends its budget restating it teaches less than one line
        would. Bounded for the same reason every other brief section is.
        """
        out: list[str] = []
        seen: set[str] = set()
        for record in reversed(self.plan.waves):
            for rejected in record.rejected:
                for reason in rejected.reasons:
                    text = " ".join(str(reason).split())
                    if not text or text in seen:
                        continue
                    seen.add(text)
                    out.append(
                        f"wave {record.wave} ({record.stage}) refused "
                        f"{rejected.id or '(unnamed)'}: {text}"
                    )
                    if len(out) >= limit:
                        return out
        return out

    @property
    def _contract_probe(self) -> ApprovedInvocationProbe:
        """One probe per planner, so its cache spans every wave of a run."""
        probe = self._contract_probe_cache
        if probe is None:
            probe = ApprovedInvocationProbe(
                self.repo,
                approved=self.approved_commands,
                timeout_s=self.config.contract_probe_timeout_s,
            )
            self._contract_probe_cache = probe
        return probe

    def _validation_context(self) -> ValidationContext:
        return ValidationContext(
            approved_commands=self.approved_commands,
            # Harvested from exactly the sources the approved command set is
            # harvested from, so "which commands may run" and "what has a human
            # said those commands print" can never drift apart.
            established_observations=established_observations_from(
                [
                    *self.permanent_scenarios,
                    *([self.base_scenario] if self.base_scenario else []),
                ]
            ),
            # The one thing no file in either repository can answer: what a
            # selector-narrowed invocation actually prints. Asked only when the
            # established map contests an attribution, at most once per
            # invocation, and only of commands already in the approved set.
            contract_probe=self._contract_probe,
            grounding_tokens=grounding_tokens_from(self._unit),
            principle_tokens=principle_tokens_from(self.founder),
            existing_signatures=self.plan.signatures()
            | permanent_signatures(self.permanent_scenarios),
            # Permanent scenario names are ids too: `_assemble_suite` uses the
            # name verbatim as the suite id and the evidence directory is
            # derived from it. Leaving them out let a generated id collide with
            # a handwritten P0 regression anchor's evidence, which nothing
            # compared and nothing reported.
            existing_ids={s.id for s in self.plan.scenarios}
            | {s.name for s in self.permanent_scenarios}
            | ({self.base_scenario.name} if self.base_scenario else set()),
            declared_services={
                s.name for s in (self.base_scenario.services if self.base_scenario else [])
            },
            app_url=self.base_scenario.app_url if self.base_scenario else "",
            local_hosts=frozenset(self.config.local_http_hosts),
            browser_enabled=self.browser_enabled,
            # What an adaptive scenario is allowed to claim caused it. Anything
            # outside this was not observed, so citing it is not provenance.
            known_failure_ids=set(self._observed_failure_ids),
            known_cluster_ids=set(self._observed_cluster_ids),
            # What a coverage-gap scenario is allowed to claim it closes. Both
            # spellings are accepted because both are shown in the brief; a risk
            # this run never identified is in neither.
            known_risk_ids={r.key for r in self.plan.risks}
            | {r.id for r in self.plan.risks if r.id},
        )

    def _merge_risks(self, incoming: Sequence[Any]) -> None:
        """Add newly identified risks to the register, keeping identities distinct.

        Two problems, both bookkeeping, both of which made the coverage
        accounting wrong rather than merely untidy:

        *Duplicate descriptions.* The same risk restated in a later wave is one
        risk, and counting it twice inflated the gap list without adding
        information. Deduplicated on the risk's own key.

        *Colliding ids.* Every wave's generator numbers its list from ``R1``, so
        a three-wave run held three unrelated risks all called ``R1``. Anything
        keyed on the id — a citation, a report line, a reader — merged them. The
        first keeps its id; a later collision is re-labelled with its wave, and
        the original label is preserved in ``basis`` so nothing is lost.
        """
        seen_keys = {r.key for r in self.plan.risks}
        taken_ids = {r.id for r in self.plan.risks if r.id}
        for risk in incoming:
            if risk.key in seen_keys:
                continue
            if risk.id and risk.id in taken_ids:
                original = risk.id
                risk.id = f"{original}-w{self._wave}"
                suffix = f"(the generator called this {original} in wave {self._wave})"
                risk.basis = f"{risk.basis} {suffix}".strip()
            seen_keys.add(risk.key)
            if risk.id:
                taken_ids.add(risk.id)
            self.plan.risks.append(risk)

    def _link_risks(self) -> None:
        """Record what this plan *intends* to exercise for each identified risk.

        Two sources, and both are declarations rather than results: a generated
        scenario carrying the risk's category, and a permanent scenario whose
        reviewed ``verifies:`` block names it. The second used to be invisible
        here, so a risk that the permanent suite already exercised — and would
        exercise again this run — was reported as uncovered, asked of a builder,
        and reported as uncovered again after the builder added coverage that
        already existed.

        None of this is evidence. ``covered_by`` says what was planned; whether
        any of it held is decided from execution records by
        :func:`~neyma_product_driver.scenario_gate.risk_coverage`, which does not
        read this field.
        """
        for risk in self.plan.risks:
            risk.covered_by = [
                s.id for s in self.plan.scenarios if s.risk_category is risk.risk_category
            ] + list(self.plan.permanent_coverage.get(risk.risk_category.value, []))
        self.plan.recompute_coverage()

    def _finish_wave(self, record: WaveRecord) -> None:
        self.plan.waves.append(record)
        # Recomputed here, not by the caller: the plan's four generation counts
        # are summed across `waves`, and every path into this method reaches it
        # with the record still unrecorded. `_generate` recomputes via
        # `_link_risks` one line earlier, which is one line too early — the wave
        # that just proposed nine and invalided nine was not yet in the list, so
        # the summary read "0 proposed".
        self.plan.recompute_coverage()
        for note in record.budget_notes:
            self.emit(f"  scenario budget: {note}")
        if record.reasoner_error:
            self.emit(f"  scenario generation: {record.reasoner_error}")
        # All four counts, always, even when three of them are zero. A wave line
        # that reported only what survived is what let nine invalid candidates
        # read as nothing proposed.
        self.emit(f"  wave {record.wave} ({record.stage}): {record.accounting()}")
        if record.rejected:
            self.emit(f"  {len(record.rejected)} proposed scenario(s) refused:")
            for rejected in record.rejected[:8]:
                marker = "INVALID" if rejected.is_contract_failure else "filtered"
                self.emit(
                    f"    - [{marker}] {rejected.id or '(unnamed)'}: "
                    + (rejected.reasons[0] if rejected.reasons else "no reason recorded")
                )
        if record.contract_rejections:
            self.emit(
                f"  HARNESS ERROR: {len(record.contract_rejections)} of "
                f"{record.proposed} proposed scenario(s) could not be read by Product "
                "Driver's own schema/taxonomy. This is not zero generated coverage; it "
                "is unknown generated coverage, and it blocks acceptance."
            )
        if record.accepted_ids:
            self.emit(f"  accepted: {', '.join(record.accepted_ids)}")
        self.persist()

    # -- basis -------------------------------------------------------------

    def _basis(
        self,
        *,
        task: str,
        unit: Any = None,
        diff_files: Sequence[str] | None = None,
        diff_stat: str = "",
        prior_failures: Sequence[str] = (),
        investigation_findings: Sequence[str] = (),
        evaluator_requests: Sequence[str] = (),
    ) -> GenerationBasis:
        # Remembered for the validation context, which needs the unit's own
        # vocabulary to tell a grounded requirement from an invented one.
        self._unit = unit
        criteria: list[str] = []
        if unit is not None:
            criteria = list(getattr(unit, "criteria_labels", lambda: [])())
        return GenerationBasis(
            task=task,
            task_hash=task_digest(task),
            active_unit_id=str(getattr(unit, "unit_id", "") or ""),
            acceptance_criteria=criteria,
            founder_context_version=str(getattr(self.founder, "version", "") or ""),
            repository_head=head_commit(self.repo),
            diff_files=list(diff_files or []),
            diff_stat=diff_stat,
            existing_scenarios=[s.name for s in self.permanent_scenarios],
            prior_failures=list(prior_failures),
            investigation_findings=list(investigation_findings),
            evaluator_requests=list(evaluator_requests),
        )

    # -- persistence -------------------------------------------------------

    def _waves_recorded_on_disk(self) -> int:
        """The highest wave number the surviving per-wave records show.

        The wave budget is the only thing bounding how much a run may generate,
        and it lived solely in the plan file. When that file could not be read
        the counter restarted at zero and the run was handed back an allowance
        it had already spent — repeatedly, once per resume. The per-wave records
        are written separately and survive, so the spent allowance can be
        reconstructed from them rather than forgotten.
        """
        if self.store is None:
            return 0
        waves_dir = self.store.run_dir / WAVES_DIRNAME
        if not waves_dir.exists():
            return 0
        highest = 0
        for path in sorted(waves_dir.glob("wave-*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(raw, dict):
                try:
                    highest = max(highest, int(raw.get("wave", 0) or 0))
                except (TypeError, ValueError):
                    continue
        return highest

    def restore_from_store(self) -> PlanRestore:
        """Continue a run's plan instead of starting a new one.

        Returns a :class:`PlanRestore` describing which of three things was
        found: no plan at all, a plan that was restored, or a plan that exists
        and cannot be read. The last used to be indistinguishable from the
        first, which is what made an unreadable plan *fail open*: the run began
        again at wave zero, with an empty plan and a fresh wave budget, and the
        next ``persist()`` destroyed the surviving record.

        Resuming without any of this silently began again at wave zero: prior
        scenarios were forgotten, duplicates regenerated, the wave budget
        started over, and the next ``persist()`` overwrote the earlier plan —
        destroying the record of what the run had already decided.

        A plan generated against a different repository state is restored but
        flagged: its scenarios were chosen for code that has since changed, and
        that is a fact the run should state rather than paper over.
        """
        if self.store is None:
            return PlanRestore(state="absent")
        path = self.store.run_dir / PLAN_FILENAME
        if not path.exists():
            # No plan, but possibly waves already spent — a plan file that a
            # previous resume preserved as corrupt, say. The allowance still
            # binds.
            self._wave = self._waves_recorded_on_disk()
            return PlanRestore(state="absent")

        try:
            plan = GeneratedScenarioPlan.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return self._plan_is_unreadable(path, exc)

        self.plan = plan
        # Recomputed, never restored. Permanent coverage is a fact about the
        # scenario files as they are *now*, not about the plan as it was
        # written: a claim added since (or removed since) must be reflected, and
        # a plan written before this field existed must not resume believing
        # the permanent suite covers nothing.
        self.plan.permanent_coverage = permanent_risk_coverage(
            [self.base_scenario] if self.base_scenario else []
        )
        self._wave = max(
            max((w.wave for w in plan.waves), default=0), self._waves_recorded_on_disk()
        )
        self._observed_failure_ids = set(plan.observed_failure_ids)
        self._observed_cluster_ids = set(plan.observed_cluster_ids)

        # Recompile rather than trusting a stored compilation: the approved
        # command set is derived from the repository as it is *now*, so a
        # scenario whose command is no longer approved must not come back to life.
        restored, dropped = 0, []
        rejected: list[RejectedScenario] = []
        for scenario in list(plan.scenarios):
            approved = self._approved_for(scenario)
            try:
                self.compiled[scenario.id] = compile_to_scenario(
                    scenario, base=self.base_scenario, approved_commands=approved
                )
                restored += 1
            except Exception as exc:
                dropped.append(f"{scenario.id} ({type(exc).__name__}: {exc})")
                rejected.append(
                    RejectedScenario(
                        id=scenario.id,
                        title=scenario.title,
                        reasons=[
                            "this run had already committed to verifying it, and on resume "
                            f"it no longer compiles: {type(exc).__name__}: {exc}"
                        ],
                    )
                )
                plan.scenarios.remove(scenario)
        if dropped:
            # Recorded in the plan, not only on the terminal. A scenario the run
            # had committed to and can no longer execute is lost coverage, and
            # coverage that vanishes between two processes with nothing but a
            # print to show for it is exactly the state the gate exists to
            # refuse. `generation_problems()` reads this back, which is the
            # channel `evaluate_gate` already consumes.
            plan.waves.append(
                WaveRecord(
                    wave=self._wave,
                    stage=STAGE_RESUME,
                    basis=plan.generation_basis,
                    proposed=len(rejected),
                    rejected=rejected,
                )
            )
            self.emit(
                f"  {len(dropped)} restored scenario(s) no longer compile and were dropped: "
                + "; ".join(dropped[:4])
            )
        plan.recompute_coverage()

        note = (
            f"resumed scenario plan: {restored} scenario(s), {self._wave} wave(s) already used"
        )
        previous_head = plan.generation_basis.repository_head
        current_head = head_commit(self.repo)
        if previous_head and current_head and previous_head != current_head:
            note += (
                f"; repository moved from {previous_head[:8]} to {current_head[:8]} since "
                "the plan was made, so its coverage was chosen against different code"
            )
        self.emit(f"  {note}")
        if dropped:
            self.persist()
        return PlanRestore(state="restored", note=note)

    def _plan_is_unreadable(self, path: Path, exc: Exception) -> PlanRestore:
        """Fail closed on a plan that exists and cannot be read.

        Three things, none of them optional. The file is preserved under a name
        nothing writes, so ``persist()`` cannot destroy the only record of what
        the run decided. The spent wave allowance is reconstructed from the
        per-wave records that survive, so a corrupt plan is not a way to earn
        unbounded generation across repeated resumes. And the failure is
        recorded as a generation problem, so the acceptance gate refuses rather
        than accepting a run whose committed verification silently collapsed.
        """
        detail = f"{type(exc).__name__}: {exc}"
        preserved = path.with_name(
            f"{path.stem}.corrupt-{utcnow().replace(':', '').replace('-', '')}.json"
        )
        try:
            path.replace(preserved)
        except OSError as move_exc:  # pragma: no cover - filesystem refusal
            self.emit(f"  could not preserve the unreadable scenario plan: {move_exc}")
            preserved = path

        self._wave = self._waves_recorded_on_disk()
        self._restore_failure = (
            f"the scenario plan for this run exists and could not be read ({detail}); "
            f"it was preserved at {preserved.name} and this run cannot re-establish what "
            "it had already decided to verify"
        )
        self.emit(f"  {self._restore_failure}")
        if self._wave:
            self.emit(
                f"  {self._wave} generation wave(s) were already spent according to the "
                "surviving per-wave records; that allowance still binds"
            )
        return PlanRestore(
            state="unreadable",
            note=self._restore_failure,
            preserved_path=str(preserved),
        )

    def note_executed(self, scenario_ids: Sequence[str]) -> None:
        """Record which scenarios have actually run, for a later resume."""
        known = set(self.plan.executed_scenario_ids)
        self.plan.executed_scenario_ids += [s for s in scenario_ids if s not in known]
        self.persist()

    def persist(self) -> None:
        """Write the plan and each wave. Generated plans never enter git.

        The plan is written whole and then moved into place, and only after the
        exact bytes have been read back through the model that will have to
        parse them on resume. Both halves are load-bearing:

        * **atomic.** ``Path.write_text`` truncates first, so a crash inside the
          window leaves a half-written plan — and a crash mid-run is precisely
          the event resume exists to survive. ``write_case_evidence`` in the
          suite already stages and replaces for this reason; the plan, which is
          the run's own record of what it decided to verify, did not.
        * **round-tripped.** A write-time transform silently rendered this file
          unparseable once already (key-based redaction replacing an ``int``
          count with the string ``"[REDACTED]"``), and nothing noticed until a
          resume found the plan unreadable. Refusing the write when the payload
          will not re-validate makes that class of failure loud at the moment it
          is caused rather than at the moment it is fatal.
        """
        if self.store is None:
            return
        # Carried in the plan so a resumed run can still verify an adaptive
        # scenario's stated cause against what was actually observed.
        self.plan.observed_failure_ids = sorted(self._observed_failure_ids)
        self.plan.observed_cluster_ids = sorted(self._observed_cluster_ids)

        payload = redact_obj(self.plan.model_dump(mode="json"))
        text = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
        try:
            GeneratedScenarioPlan.model_validate_json(text)
        except Exception as exc:
            self.emit(
                "  REFUSED to write the scenario plan: the record it would have written "
                f"cannot be read back as a plan ({type(exc).__name__}: {exc}). The "
                "previous plan is left intact rather than replaced with one no resume and "
                "no reader could parse."
            )
            return

        path = self.store.run_dir / PLAN_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{PLAN_FILENAME}.partial")
        staging.write_text(text, encoding="utf-8")
        staging.replace(path)
        # Indexed by position in the wave list, not by wave number. A wave
        # refused before it could run never increments the wave counter, so
        # numbering these files by wave number silently overwrote each refusal
        # record with the next one. Positions are unique by construction, and
        # for an ordinary run position and wave number coincide.
        for index, record in enumerate(self.plan.waves, start=1):
            self.store.write_json(
                f"{WAVES_DIRNAME}/wave-{index:02d}.json",
                record.model_dump(mode="json"),
            )


# --------------------------------------------------------------------------
# Repository inspection (read-only)
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str, strip: bool = True) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=30, check=False
        )
        return proc.stdout.strip() if strip else proc.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def head_commit(repo: Path) -> str:
    return _git(repo, "rev-parse", "--short", "HEAD")


def changed_files(repo: Path) -> list[str]:
    """Files the builder touched: uncommitted changes plus untracked additions.

    Read-only. Both halves matter — a builder working under ``acceptEdits``
    usually leaves a dirty tree, and a new module that only exists as an
    untracked file is exactly the kind of change worth verifying.
    """
    files: list[str] = []
    # Not stripped: porcelain's first status column is a space for an unstaged
    # modification, and trimming the output would shift every path by one.
    for line in _git(repo, "status", "--porcelain", strip=False).splitlines():
        path = line[2:].strip()
        if " -> " in path:  # a rename; the destination is what now exists
            path = path.split(" -> ", 1)[1]
        if path:
            files.append(path.strip('"'))
    return sorted(dict.fromkeys(files))


def diff_stat(repo: Path) -> str:
    return _git(repo, "diff", "--stat")


def touched_areas(files: Sequence[str]) -> set[str]:
    """Coarse areas a change touched. Used to explain why a risk was raised."""
    areas: set[str] = set()
    for path in files:
        parts = Path(path).parts
        if len(parts) > 1:
            areas.add("/".join(parts[:2]))
        elif parts:
            areas.add(parts[0])
    return areas


# --------------------------------------------------------------------------
# Promotion candidacy
# --------------------------------------------------------------------------


def record_promotion_candidates(
    *,
    ledger: PromotionLedger,
    memory: DefectMemory,
    plan: GeneratedScenarioPlan,
    outcomes: Sequence[Any],
    iteration: int,
) -> list[PromotionCandidate]:
    """Record every generated scenario that found a defect and now passes.

    Recording is a suggestion with evidence attached. Nothing here writes into
    the permanent suite, and nothing here can be configured to.
    """
    recorded: list[PromotionCandidate] = []
    for outcome in outcomes:
        if getattr(outcome, "outcome", None) is None:
            continue
        if str(getattr(outcome.outcome, "value", outcome.outcome)) != "PASSED":
            continue
        if str(getattr(outcome.origin, "value", outcome.origin)) != "generated":
            continue

        discovery = memory.discovered(outcome.scenario_id)
        if discovery is None:
            continue  # never failed, so it demonstrated nothing new
        discovered_iteration, observation = discovery

        scenario = plan.by_id(outcome.scenario_id)
        candidate = PromotionCandidate(
            scenario_id=outcome.scenario_id,
            title=scenario.title if scenario else outcome.scenario_name,
            risk_category=outcome.risk_category,
            priority=str(getattr(outcome.priority, "value", outcome.priority)),
            bug_discovered=observation,
            discovered_in_iteration=discovered_iteration,
            fixed_in_iteration=iteration,
            evidence_path=outcome.evidence_path,
            requirement_reference=outcome.requirement_reference,
            reason=(
                "This generated scenario failed while a real defect was present and passes "
                "after the fix, so it describes behaviour the product now promises and could "
                "silently lose again. It may deserve permanent regression coverage."
            ),
            scenario=scenario.model_dump(mode="json") if scenario else {},
        )
        if ledger.record(candidate):
            recorded.append(candidate)
    return recorded


def scenario_to_yaml_mapping(scenario: GeneratedScenario, compiled: Scenario) -> dict[str, Any]:
    """The YAML shape a promoted scenario would take in the permanent suite.

    Produced only by the explicit promotion command. The compiled scenario is
    the executable truth; the generated model contributes the provenance a human
    reviewer needs in order to decide whether to keep it.
    """
    data = compiled.model_dump(mode="json", exclude_defaults=True)
    data["name"] = scenario.id
    data["description"] = (
        f"{scenario.title}\n\n"
        f"Promoted from a generated scenario. Risk: {scenario.risk_category.value} "
        f"(priority {scenario.priority.value}).\n"
        f"Verifies: {scenario.requirement_reference}\n"
        f"Product principle: {scenario.product_principle_reference}\n\n"
        f"{scenario.provenance.render()}"
    )
    return data


__all__ = [
    "DefectMemory",
    "PROMOTION_FILENAME",
    "PLAN_FILENAME",
    "PromotionCandidate",
    "PromotionLedger",
    "STAGE_ADAPTIVE",
    "STAGE_DIFF",
    "STAGE_INITIAL",
    "ScenarioPlanner",
    "WAVES_DIRNAME",
    "changed_files",
    "diff_stat",
    "head_commit",
    "record_promotion_candidates",
    "scenario_to_yaml_mapping",
    "touched_areas",
]
