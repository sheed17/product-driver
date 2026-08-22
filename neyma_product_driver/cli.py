"""Command-line interface and the control loop.

Commands::

    python -m neyma_product_driver run
    python -m neyma_product_driver doctor
    python -m neyma_product_driver status
    python -m neyma_product_driver evaluate
    python -m neyma_product_driver stop
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, Sequence

from pydantic import ValidationError

from .config import (
    DriverConfig,
    api_key_present,
    load_config,
)
from .completion_auditor import AuditDecision, CompletionAuditor
from .task_scope import resolve_task_scope
from .paths import RepositoryPathError
from .policy import (
    ChangeRisk,
    assess_change_risk,
    protocol_diagnostic_notes,
    protocol_warrants_investigation,
    requires_founder_authority,
)
from .context import (
    ContextProvenance,
    ContextResolutionError,
    FounderFeedbackStore,
    RepositoryContextLoader,
    founder_dir,
    load_founder_context,
)
from .evidence import EvidenceStore, check_writable, new_run_id
from .run_journal import RunJournal
from .models import (
    CommandResult,
    Decision,
    EvaluatorDecision,
    GitSnapshot,
    IterationRecord,
    RunState,
    RunStatus,
    ScenarioResult,
    redact,
)
from .protocol_resolver import ProtocolResolver, ProtocolStatus, approve_option
from .remediation_planner import ApprovalStore, remediation_builder_prompt
from .review_cycle import (
    BlockerKind,
    ReviewLedger,
    ReviewRoute,
    ReviewTrigger,
    capture_fingerprint,
    correction_lines,
    resolve_review_requirement,
    route_review,
)
from .reviewer_boundary import ReviewerCommandPolicy
from .prompts import (
    builder_correction_prompt,
    builder_task_prompt,
    evaluator_prompt,
    render_correction_for_builder,
    validate_correction_quality,
)
from .scenarios import Scenario, ScenarioExecutor, load_scenario
from .scenario_planner import (
    DefectMemory,
    PromotionLedger,
    ScenarioPlanner,
    changed_files,
    diff_stat,
    record_promotion_candidates,
)
from .scenario_gate import evaluate_gate
from .scenario_suite import (
    Origin,
    Outcome,
    ScenarioSuite,
    SuiteExecutor,
    SuiteResult,
    build_failure_evidence,
    build_suite,
    merge_suite_results,
    select_rerun,
)

# --------------------------------------------------------------------------
# Terminal output
# --------------------------------------------------------------------------

_BOLD, _DIM, _RESET = "\033[1m", "\033[2m", "\033[0m"
_RED, _GREEN, _YELLOW, _CYAN = "\033[31m", "\033[32m", "\033[33m", "\033[36m"


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def out(msg: str = "") -> None:
    print(msg, flush=True)


def header(msg: str) -> None:
    if _supports_color():
        out(f"\n{_BOLD}{_CYAN}{msg}{_RESET}")
    else:
        out(f"\n=== {msg} ===")


def note(msg: str) -> None:
    out(f"{_DIM}{msg}{_RESET}" if _supports_color() else msg)


def warn(msg: str) -> None:
    out(f"{_YELLOW}{msg}{_RESET}" if _supports_color() else f"WARNING: {msg}")


def error(msg: str) -> None:
    out(f"{_RED}{msg}{_RESET}" if _supports_color() else f"ERROR: {msg}")


def good(msg: str) -> None:
    out(f"{_GREEN}{msg}{_RESET}" if _supports_color() else msg)


# --------------------------------------------------------------------------
# Git inspection (read-only)
# --------------------------------------------------------------------------


def git_snapshot(repo: Path) -> GitSnapshot:
    """Read-only view of the repository. Never mutates anything."""

    def run(*args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            return proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    status = run("status", "--porcelain")
    return GitSnapshot(
        branch=run("branch", "--show-current"),
        status_porcelain=redact(status),
        diff_stat=redact(run("diff", "--stat")),
        head_commit=run("rev-parse", "--short", "HEAD"),
        dirty_file_count=len([ln for ln in status.splitlines() if ln.strip()]),
    )


# --------------------------------------------------------------------------
# Protocols so the loop can be driven by fakes in tests
# --------------------------------------------------------------------------


class BuilderLike(Protocol):
    session_id: str | None

    async def send(self, prompt: str, timeout_s: int | None = None) -> Any: ...


class EvaluatorLike(Protocol):
    session_id: str | None

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision: ...


class ScenarioRunnerLike(Protocol):
    service_logs: dict[str, str]

    async def execute(self, scenario: Scenario) -> ScenarioResult: ...


@dataclass(kw_only=True)
class LoopResult:
    """What a terminated run carries out of the loop.

    Keyword-only, deliberately. A terminal path once built this by hand with
    four positional arguments; the three that follow — the protocol resolution,
    the suite and the promotion ledger — silently took their defaults, and a run
    that had executed a full scenario suite reported no coverage at all because
    ``suite`` was ``None``. Positional construction makes that failure silent
    and makes every future field one more thing a new terminal can drop. Every
    terminal now goes through ``_terminate``; this makes the alternative fail
    loudly rather than quietly.
    """

    status: RunStatus
    state: RunState
    final_decision: EvaluatorDecision | None = None
    audit: Any = None
    protocol: Any = None
    #: The final SuiteResult, when the run executed a suite rather than one
    #: scenario. Carries the coverage report the outcome is described with.
    suite: SuiteResult | None = None
    #: Generated scenarios that found a defect and later passed. Suggestions
    #: only; nothing has been written into the permanent suite.
    promotion_candidates: list[Any] = field(default_factory=list)
    #: The deterministic acceptance gate's verdict on the final suite, when a
    #: suite ran. Carried so the closing report states what the gate decided
    #: instead of asserting an outcome nobody computed.
    gate: Any = None
    #: How this run classified the change it made, and therefore what review it
    #: earned. See :mod:`~neyma_product_driver.policy`.
    risk: Any = None
    #: Independent reviews this run launched on its own, in order.
    reviews: list[Any] = field(default_factory=list)
    #: Whether this run's scoped task owed an independent review, and on whose
    #: authority. See :mod:`~neyma_product_driver.review_cycle`.
    review_requirement: Any = None
    #: Every review, bound to the exact repository state it was performed
    #: against, plus the record of which ones a later change retired.
    review_ledger: Any = None
    #: The review that actually satisfied the scoped task's requirement, if one
    #: did. Never a review of a different tree — that is the ledger's job.
    satisfying_review: Any = None
    #: Repository-protocol findings that were recorded rather than enforced,
    #: because clearing them needed no founder authority.
    protocol_diagnostics: list[str] = field(default_factory=list)
    #: What this run changed in the target repository's authority documents,
    #: from a snapshot taken before the first builder turn.
    authority_report: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# The control loop
# --------------------------------------------------------------------------


async def run_control_loop(
    *,
    config: DriverConfig,
    scenario: Scenario,
    store: EvidenceStore,
    state: RunState,
    builder: BuilderLike,
    evaluator: EvaluatorLike,
    make_executor: Callable[[Path], ScenarioRunnerLike],
    emit: Callable[[str], None] = out,
    founder: Any = None,
    repo_loader: Any = None,
    auditor: Any = None,
    protocol_resolver: Any = None,
    investigator_factory: Any = None,
    planner: Any = None,
    reviewer_factory: Any = None,
) -> LoopResult:
    """Drive builder → observe → evaluate → correct, bounded by max_iterations.

    The order is: builder claim → completion auditor → protocol resolver →
    scenario suite → product evaluator → combine. Repository authority is
    re-read before every evaluator decision, so a change to what the repository
    declares mid-run is picked up rather than served from cache. Returns as soon
    as a terminal decision is reached, the iteration budget is exhausted, or a
    stop is requested.

    **What stops this loop, and what does not.** A run ends and asks the founder
    for exactly four kinds of reason: the product evaluator raised a product or
    authority question, the deterministic acceptance gate says the verification
    never happened, an independent review still refuses after its budget is
    spent, or clearing a repository-protocol state would require a history
    rewrite, a remote mutation or a destructive operation. Everything else —
    every commit-topology difference, every missing metadata commit, every
    finalizer or receipt finding, every environmental oddity — is recorded,
    reported, handed to the investigator, and the loop keeps building. See
    :mod:`~neyma_product_driver.policy`, which holds that distinction in one
    place so it cannot drift back into a precedence table.

    **Missing coverage is generated, not escalated.** When the gate's only
    refusal is that a risk this run identified has no passing scenario, the loop
    generates one aimed at exactly that risk, executes it, and asks the same
    gate again — before the audit and before any reviewer. An absence of
    evidence is not a defect, so no correction is invented for it; a generated
    case that then *fails* is a real observation and reaches the builder as a
    grounded FIX. The gate refusal only becomes terminal once the generation
    budget is spent, the approved vocabulary cannot express the risk, or the
    wave produced nothing runnable.

    **Review is proportional and automatic.** Ordinary work gets none. A change
    touching a high-consequence product surface gets one focused read-only
    review, launched by the driver rather than by the founder; a reviewer that
    returns findings sends them straight back to the same builder as a grounded
    correction, and only becomes a founder question once the review budget is
    spent.

    When ``planner`` is supplied — the default now — the run generates
    verification scenarios for this task and executes them alongside the
    permanent one: an initial plan up front, a diff-aware refinement after each
    builder turn, and a bounded adaptive expansion after failures. Without a
    planner the loop behaves exactly as it always has: one scenario, one result.
    """
    feedback_store = FounderFeedbackStore(store.run_dir)
    last_audit: dict[str, Any] = {"value": None}
    last_protocol: dict[str, Any] = {"value": None}
    last_suite: dict[str, Any] = {"value": None}
    last_gate: dict[str, Any] = {"value": None}
    ledger = PromotionLedger(store.run_dir)
    defects = DefectMemory()
    last_risk: dict[str, Any] = {"value": None}
    reviews: list[Any] = []
    protocol_diagnostics: list[str] = []
    # Every review this run takes, bound to the exact tree it was taken against.
    # A correction changes the tree, the ledger retires the review that no longer
    # describes it, and the next round has to take a new one. There is no path
    # from "supported earlier" to "supported now".
    review_ledger = ReviewLedger()
    last_requirement: dict[str, Any] = {"value": None}
    satisfying: dict[str, Any] = {"value": None}
    # Watched from before the first builder turn, so "what did this run change"
    # is answerable. An edit that removes or softens a mandatory control makes
    # the change high-consequence whatever else it did, and that is the one
    # signal that must not depend on a file written after the run ends.
    from .authority import AuthorityWatcher

    authority_watcher = AuthorityWatcher(config.neyma_repo)
    authority_watcher.snapshot()

    def _terminate(status: RunStatus, decision: EvaluatorDecision, record: IterationRecord) -> LoopResult:
        """The only way out of this loop.

        Every terminal state persists identically and carries the same record,
        because a terminal that persists by hand carries whatever its author
        remembered — which is how a run reached a terminal state having executed
        a full scenario suite and reported that it had no suite at all.
        """
        record.decision = decision
        store.save_iteration(record)
        state.iterations.append(record)
        state.final_decision = decision
        state.status = status
        store.save_state(state)
        return LoopResult(
            status=status,
            state=state,
            final_decision=decision,
            audit=last_audit["value"],
            protocol=last_protocol["value"],
            suite=last_suite["value"],
            promotion_candidates=ledger.load(),
            gate=last_gate["value"],
            risk=last_risk["value"],
            reviews=list(reviews),
            review_requirement=last_requirement["value"],
            review_ledger=review_ledger,
            satisfying_review=satisfying["value"],
            protocol_diagnostics=list(protocol_diagnostics),
            authority_report=authority_watcher.report(),
        )

    # Resolve authority once up front so the builder's task is scoped correctly.
    #
    # A repository that declares an active unit scopes the work with it. One that
    # does not is not in an error state, and it is not the driver's business to
    # insist: the founder's task is the authority then. This used to terminate
    # the run before the builder had been asked to do anything, which made a
    # registry convention a precondition for all product work.
    active_unit_id = ""
    active_unit = None
    if repo_loader is not None:
        active_unit = repo_loader.resolve_active_unit_optional()
        active_unit_id = active_unit.unit_id
        if not active_unit.is_declared:
            emit("  the repository declares no active unit; the task is the authority")
            emit(f"    ({active_unit.resolution_problem})")
            protocol_diagnostics.append(
                f"no active unit resolved: {active_unit.resolution_problem}"
            )

    # Resolve what this run was actually asked for, once, from the task the
    # product owner wrote and the phase the repository declares. This is what
    # separates "build one unit inside a phase" from "finish the phase", and it
    # is read here — before the builder has said anything — so that nothing the
    # builder writes can widen or narrow the bar it is held to.
    task_scope = resolve_task_scope(state.task, active_unit, config.neyma_repo)
    state.task_scope = task_scope.model_dump(mode="json")
    store.write_json("task-scope.json", state.task_scope)
    for line in task_scope.summary_block().splitlines():
        emit(f"  {line}")
    if task_scope.is_nested:
        emit(
            f"  accepting {task_scope.scope_id} will not complete "
            f"{task_scope.parent_phase_id}, score one of its criteria, or unblock what "
            "follows it."
        )

    # Stage 1 — plan verification from the requirements, before judging anything.
    scenario_summary = scenario.summary()
    if planner is not None:
        emit("→ planning verification scenarios for this task...")
        plan = planner.plan_initial(task=state.task, unit=active_unit, run_id=state.run_id)
        emit(_indent(plan.coverage_summary.render()))
        scenario_summary = _summarize_verification(scenario, planner)

    next_prompt = builder_task_prompt(
        state.task,
        scenario_summary,
        active_unit_id,
        feedback_store.render(),
        scope=task_scope.render(),
    )
    prior_problems: list[str] = []
    sent_corrections: list[str] = []
    previous_suite: SuiteResult | None = None

    for iteration in range(1, config.max_iterations + 1):
        if store.stop_requested():
            emit("\nStop requested — halting before the next iteration.")
            state.status = RunStatus.STOPPED
            store.save_state(state)
            return LoopResult(status=RunStatus.STOPPED, state=state)

        state.iteration = iteration
        record = IterationRecord(iteration=iteration)
        header(f"ITERATION {iteration} / {config.max_iterations}")

        # 1. builder works
        emit("→ builder working...")
        turn = await builder.send(next_prompt, timeout_s=config.builder.turn_timeout_s)
        record.builder_session_id = builder.session_id
        state.builder_session_id = builder.session_id
        record.builder_summary = getattr(turn, "text", "") or ""
        denied = list(getattr(turn, "denied_requests", []) or [])
        record.paused_permission_requests = denied

        if getattr(turn, "is_error", False):
            record.notes.append(f"builder error: {getattr(turn, 'error_detail', '')}")
            emit(f"  builder reported an error: {getattr(turn, 'error_detail', '')}")

        # 2. read-only git snapshot
        record.git = git_snapshot(config.neyma_repo)

        # 2a. classify what was actually changed. This decides how much
        #     independent scrutiny the change earns later, and it is derived from
        #     the diff rather than from what the task said it would do: a change
        #     described as a UI tweak that moved an authorization check is a
        #     high-consequence change, whatever the description claimed.
        diff_files = changed_files(config.neyma_repo)
        authority_changes = authority_watcher.changes()
        risk = assess_change_risk(
            task=state.task,
            diff_files=diff_files,
            diff_stat=record.git.diff_stat if record.git else "",
            authority_findings=[f for c in authority_changes for f in c.findings],
            meaningful_files=config.review.meaningful_change_files,
            meaningful_lines=config.review.meaningful_change_lines,
        )
        last_risk["value"] = risk
        record.notes.append(f"change risk: {risk.brief()}")
        if risk.level is not ChangeRisk.ORDINARY:
            emit(f"  change risk: {risk.brief()}")

        # 2b. Stage 2 — the diff decides what is now at risk. A task that read
        #     as UI-only but moved persistence or authorization earns
        #     verification of those, whatever the task said.
        if planner is not None:
            emit("→ refining the scenario plan against what the builder changed...")
            planner.refine_for_diff(
                task=state.task,
                unit=active_unit,
                diff_files=diff_files,
                diff_stat=diff_stat(config.neyma_repo),
            )

        # 3. operate the product
        suite: ScenarioSuite | None = None
        suite_executor: SuiteExecutor | None = None
        if planner is None:
            # One scenario, run as a one-entry suite. Not ceremony: the suite is
            # what writes and verifies per-case evidence, and ``suite_result`` is
            # what the authoritative gate below reads. While this branch produced
            # no suite result, that gate was skipped entirely — a run without
            # generated coverage could fail its required scenario, receive an
            # ACCEPT from the evaluator, and be recorded as ACCEPTED. The
            # scenario still runs exactly once and the loop downstream still sees
            # the single result it has always seen.
            emit(f"→ running scenario '{scenario.name}' ({scenario.mode})...")
            suite = build_suite(permanent=[(scenario.name, scenario)])
            suite_executor = SuiteExecutor(
                make_executor=make_executor,
                artifact_root=store.iteration_dir(iteration),
                browser_enabled=config.run.browser_enabled,
                run_id=state.run_id,
                iteration=iteration,
                emit=lambda _m: None,
            )
            suite_result = await suite_executor.run(
                suite, selection_reason="the selected scenario"
            )
            scenario_result = _primary_result(scenario, suite_executor, suite_result)
            record.scenario = scenario_result
            record.suite = suite_result.model_dump(mode="json")
            last_suite["value"] = suite_result
            all_commands = list(scenario_result.commands)
            service_logs = suite_executor.service_logs
            emit(
                f"  scenario {'PASSED' if scenario_result.passed else 'FAILED'}"
                + (f" — {scenario_result.error}" if scenario_result.error else "")
            )
        else:
            suite = _assemble_suite(scenario, planner)
            only, reason = select_rerun(suite, previous_suite)
            emit(f"→ running scenario suite ({len(only)} of {len(suite)}): {reason}")

            def new_suite_executor() -> SuiteExecutor:
                return SuiteExecutor(
                    make_executor=make_executor,
                    artifact_root=store.iteration_dir(iteration),
                    browser_enabled=config.run.browser_enabled,
                    execution_budget_s=config.scenario_generation.execution_budget_s,
                    max_parallel=config.scenario_generation.max_parallel,
                    run_id=state.run_id,
                    iteration=iteration,
                    emit=emit,
                )

            suite_executor = new_suite_executor()
            suite_result = await suite_executor.run(suite, only=only, selection_reason=reason)

            # An ACCEPT may never rest on a narrowed pass. If everything the
            # narrowed set covered is green, widen to the full required
            # regression set now rather than discovering the gap after the
            # evaluator has already said yes. A fresh executor, so the widened
            # pass's results and evidence are the ones that get recorded.
            if suite_result.executed_required_all_passed and not suite_result.full_run:
                emit("→ narrowed suite is green; running the full required regression set...")
                suite_executor = new_suite_executor()
                suite_result = await suite_executor.run(
                    suite,
                    only=None,
                    selection_reason="full required regression set before acceptance",
                )

            previous_suite = suite_result
            last_suite["value"] = suite_result
            record.suite = suite_result.model_dump(mode="json")
            # Written now as well as by save_iteration below: an exception later
            # in this iteration (an evaluator that dies, an auditor that raises)
            # would otherwise discard the evidence for work that really ran.
            store.write_json(
                store.iteration_dir(iteration).relative_to(store.run_dir) / "suite-result.json",
                record.suite,
            )

            # Persisted before the evaluator is consulted: if this iteration dies
            # later, a resumed run still knows what already ran.
            planner.note_executed(
                [o.scenario_id for o in suite_result.outcomes if o.outcome is not Outcome.SKIPPED]
            )
            _record_defects(defects, suite_result, suite_executor, iteration)
            newly_promotable = record_promotion_candidates(
                ledger=ledger,
                memory=defects,
                plan=planner.plan,
                outcomes=suite_result.outcomes,
                iteration=iteration,
            )
            for candidate in newly_promotable:
                emit(
                    f"  promotion candidate: {candidate.scenario_id} found a defect in "
                    f"iteration {candidate.discovered_in_iteration} and now passes"
                )

            scenario_result = _primary_result(scenario, suite_executor, suite_result)
            record.scenario = scenario_result
            all_commands = [
                command
                for result in suite_executor.results.values()
                for command in result.commands
            ]
            service_logs = suite_executor.service_logs
            for line in suite_result.headline().splitlines():
                emit(f"  {line}")

        # 3b. audit the builder's completion claims BEFORE judging the product
        audit = None
        if auditor is not None:
            emit("→ auditing completion claims...")
            unit_now = repo_loader.resolve_active_unit_optional() if repo_loader else None
            # The scope the phase state is read from is refreshed each round —
            # the repository may have moved — but what the run was ASKED for
            # never changes mid-run.
            scope_now = task_scope.model_copy(
                update={
                    "parent_phase_state": getattr(unit_now, "status", "") or "",
                    "parent_phase_execution_state": getattr(
                        unit_now, "execution_state", ""
                    )
                    or "",
                }
            )
            audit = auditor.audit(
                record.builder_summary,
                unit=unit_now,
                run_commands=all_commands,
                evidence_dir=str(store.iteration_dir(iteration)),
                scope=scope_now,
            )
            record.task_scope = scope_now.model_dump(mode="json")
            if audit.completion is not None:
                record.scoped_completion = audit.completion.model_dump(mode="json")
            last_audit["value"] = audit
            record.completion_audit = audit.model_dump(mode="json")
            store.save_completion_audit(iteration, record.completion_audit)
            for line in audit.summary_block().splitlines():
                emit(f"  {line}")

        # 3c. resolve repository protocol: topology, authority, deadlocks.
        #     This runs before any finalizer or reviewer is considered, because
        #     a review of an invalid topology reviews the wrong thing.
        resolution = None
        if protocol_resolver is not None:
            emit("→ resolving repository protocol...")
            resolution = protocol_resolver.resolve(run_commands=all_commands)
            last_protocol["value"] = resolution
            record.protocol_resolution = resolution.model_dump(mode="json")
            store.save_protocol_resolution(record.protocol_resolution, iteration)
            for line in resolution.summary_block().splitlines():
                emit(f"  {line}")

        # 3d. open-ended diagnosis, when the situation calls for it. The known
        #     handlers above stay authoritative; this only fires when they leave
        #     something unexplained (a contradiction, an unproven blocker, a
        #     stuck loop) and a factory was supplied.
        if investigator_factory is not None:
            from .investigator import should_investigate

            prior = [
                "|".join(sorted(p for p in it.decision.problems)) if it.decision else ""
                for it in state.iterations
            ]
            # The previous iteration's confidence, because this runs before the
            # evaluator speaks. An evaluator that was unsure last time and is
            # about to be asked the same question again is exactly when an
            # open-ended probe is worth more than another correction.
            previous_confidence = (
                state.iterations[-1].decision.confidence
                if state.iterations and state.iterations[-1].decision
                else None
            )
            triggered, reason = should_investigate(
                builder_report=record.builder_summary,
                audit=audit,
                protocol=resolution,
                scenario_passed=scenario_result.passed,
                evaluator_confidence=previous_confidence,
                prior_failures=prior,
                suite_failed=(
                    suite_result is not None and bool(suite_result.blocking_failures())
                ),
            )
            if not triggered:
                # Protocol findings no longer stop the run, so something has to
                # pick up the ones that describe a broken environment or a
                # self-contradictory repository. This is that route: the founder
                # asked not to perform this kind of machine debugging by hand.
                triggered, reason = protocol_warrants_investigation(resolution)
            if triggered:
                emit(f"→ investigating: {reason}")
                try:
                    investigation = investigator_factory(reason).investigate(
                        issue=reason,
                        trigger=reason,
                        builder_report=record.builder_summary,
                        run_id=state.run_id,
                    )
                    record.investigation = investigation.result.model_dump(mode="json")
                    for line in investigation.result.summary_block().splitlines()[:4]:
                        emit(f"  {line}")
                except Exception as exc:  # diagnosis must never break the loop
                    emit(f"  investigation error: {type(exc).__name__}: {redact(str(exc))}")

        # 4. re-read repository authority — never reuse stale context
        repo_context = None
        if repo_loader is not None:
            try:
                repo_context = repo_loader.load(topics=["product", "architecture", "acceptance"])
            except ContextResolutionError as exc:
                # The repository could not be read at all (an unparseable file,
                # an unreadable tree). That is a fact about the environment, and
                # the investigator's problem — not a reason to abandon a run that
                # has already built and verified something. The evaluator simply
                # judges without the repository layer and says so.
                emit(f"  repository authority unreadable this iteration: {exc}")
                record.notes.append(f"repository authority unreadable: {exc}")
                protocol_diagnostics.append(f"repository authority unreadable: {exc}")
            else:
                # Authority is re-read every iteration, so a repository that
                # becomes self-contradictory mid-run is noticed here. It no
                # longer ends the run, so it has to be recorded, or a real
                # contradiction would pass unremarked.
                unit_now = repo_context.active_unit
                if not unit_now.is_declared and unit_now.resolution_problem:
                    note_text = f"active unit unresolvable: {unit_now.resolution_problem}"
                    if note_text not in protocol_diagnostics:
                        protocol_diagnostics.append(note_text)
                        record.notes.append(note_text)
                        emit(f"  {note_text}")

        # 5. evaluate observed behaviour against all three context layers
        emit("→ evaluating observed behaviour...")
        feedback_text = feedback_store.render()
        prompt = evaluator_prompt(
            task=state.task,
            iteration=iteration,
            max_iterations=config.max_iterations,
            builder_summary=record.builder_summary,
            git=record.git,
            scenario=scenario_result,
            service_logs=service_logs,
            evidence_dir=str(store.iteration_dir(iteration)),
            paused_permission_requests=denied,
            prior_problems=prior_problems,
            founder=founder,
            repo_context=repo_context,
            founder_feedback=feedback_text,
            previous_corrections=sent_corrections,
            suite=suite_result,
            coverage_gaps=_coverage_gap_briefs(planner, suite_result),
        )

        provenance = _build_provenance(
            founder=founder,
            repo_context=repo_context,
            git=record.git,
            scenario_result=scenario_result,
            store=store,
            iteration=iteration,
            feedback_count=len(feedback_store.load()),
            prompt_chars=len(prompt),
        )
        record.context_provenance = provenance.model_dump(mode="json")
        store.save_prompt_manifest(iteration, provenance.model_dump(mode="json"), prompt)

        decision = await evaluator.evaluate(prompt, timeout_s=config.evaluator.turn_timeout_s)
        record.evaluator_session_id = evaluator.session_id
        state.evaluator_session_id = evaluator.session_id

        # 6. prompt-quality gate — ungrounded work never reaches the builder
        reasons = validate_correction_quality(
            decision, founder=founder, previous_corrections=sent_corrections
        )
        if reasons:
            record.raw_decision = decision
            record.rejected_reasons = reasons
            emit("  FIX rejected by the prompt-quality contract:")
            for r in reasons:
                emit(f"    - {r}")
            decision = EvaluatorDecision(
                decision=Decision.BLOCKED,
                summary=(
                    "The evaluator returned a FIX that failed the prompt-quality "
                    "contract, so no work was generated."
                ),
                problems=reasons,
                observed_behavior=decision.observed_behavior,
                evidence_paths=decision.evidence_paths,
            )

        # 6a. repository protocol: diagnostic, unless clearing it is the
        #     founder's to authorize. The ordering this replaced put every
        #     governance signal above the product judgement, so a commit-topology
        #     difference or a stale receipt ended a run that had just
        #     demonstrated working behaviour, and the founder relayed the finding
        #     to a builder by hand. What survives is the part that is genuinely
        #     not the driver's call: a repair that would rewrite history, touch a
        #     remote, or destroy something.
        if resolution is not None:
            terminal, decision = _apply_protocol_policy(resolution, decision, emit)
            notes = protocol_diagnostic_notes(resolution)
            for note_text in notes:
                if note_text not in protocol_diagnostics:
                    protocol_diagnostics.append(note_text)
            record.notes.extend(notes)
            if terminal is not None:
                record.decision = decision
                _print_decision(decision, emit)
                return _terminate(terminal, decision, record)

        # 6b. combine: what the suite *measured* is folded in before any layer
        #     that judges *claims* combines with it. Measurement is not a peer
        #     of the claim-judging layers, it is their input: a completion audit
        #     deciding what to do about an ACCEPT must be looking at an ACCEPT
        #     the deterministic gate has already had its say on. While this ran
        #     last, the completion-audit branch reached a terminal state and
        #     returned before the gate was ever consulted — a required scenario
        #     could fail, the run could stop, and nothing anywhere said so.
        gate_overrode = False
        if suite_result is not None:
            last_gate["value"] = evaluate_gate(
                suite_result,
                generation_problems=(
                    planner.generation_problems() if planner is not None else ()
                ),
                risks=_identified_risks(planner),
            )
            before_gate = decision
            decision = _apply_suite_precedence(
                suite_result,
                decision,
                scenario.name,
                emit,
                generation_problems=(
                    planner.generation_problems() if planner is not None else ()
                ),
                risks=_identified_risks(planner),
            )
            gate_overrode = decision is not before_gate

            # 6b-ii. A passing suite that still names an unverified blocking
            #     risk is missing verification, not failing it. Stage 3 already
            #     knows how to generate for a named gap, and the planner still
            #     had waves left — but Stage 3 sat behind a Decision.FIX, and
            #     this shape produced a Decision.BLOCKED that returned at the
            #     route below. So a run that had identified a P0 risk, had the
            #     budget to cover it, and had the vocabulary to express it,
            #     stopped and asked the founder to request the scenario by hand.
            #     Closure runs here, before the audit and the reviewer, so
            #     everything downstream still sees a gate that has had its final
            #     say on this iteration's evidence.
            if (
                planner is not None
                and suite is not None
                and suite_executor is not None
                and _coverage_gap_only(last_gate["value"], suite_result)
            ):
                closure = await _close_coverage_gaps(
                    planner=planner,
                    suite=suite,
                    suite_executor=suite_executor,
                    suite_result=suite_result,
                    verdict=last_gate["value"],
                    accepted=before_gate,
                    scenario=scenario,
                    task=state.task,
                    unit=active_unit,
                    diff_files=diff_files,
                    emit=emit,
                )
                if closure.decision is not None:
                    suite = closure.suite
                    suite_result = closure.suite_result
                    last_suite["value"] = suite_result
                    last_gate["value"] = closure.verdict
                    previous_suite = suite_result
                    decision = closure.decision
                    gate_overrode = decision is not before_gate
                    planner.note_executed(closure.executed_ids)
                    _record_defects(defects, suite_result, suite_executor, iteration)
                    for candidate in record_promotion_candidates(
                        ledger=ledger,
                        memory=defects,
                        plan=planner.plan,
                        outcomes=suite_result.outcomes,
                        iteration=iteration,
                    ):
                        emit(
                            f"  promotion candidate: {candidate.scenario_id} found a defect "
                            f"in iteration {candidate.discovered_in_iteration} and now passes"
                        )
                    record.suite = suite_result.model_dump(mode="json")
                    store.write_json(
                        store.iteration_dir(iteration).relative_to(store.run_dir)
                        / "suite-result.json",
                        record.suite,
                    )
                    for line in suite_result.headline().splitlines():
                        emit(f"  {line}")
                record.notes.extend(closure.notes)

        # 6c. combine: a completion claim the repository does not support
        #     overrides an ACCEPT from the product evaluator.
        if audit is not None and audit.blocks_acceptance:
            if audit.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW:
                # Not a terminal state any more. The repository saying a
                # criterion needs a session other than the implementing one is a
                # true and useful fact; making the founder go and start that
                # session by hand was the ceremony. It is folded into the
                # proportional-review step below, which launches one itself.
                record.notes.append(audit.headline)
            elif gate_overrode:
                # The gate has already turned this into a FIX or a BLOCKED, so
                # the branch below that would have rewritten an ACCEPT no longer
                # fires and the audit's own findings would simply be dropped.
                # Both layers refuse; both refusals belong in the record.
                merged = list(decision.problems)
                for problem in [c.what for c in audit.contradictions] + audit.missing_evidence:
                    if problem and problem not in merged:
                        merged.append(problem)
                decision = decision.model_copy(update={"problems": merged})
            elif decision.decision is Decision.ACCEPT:
                # The product looks fine, but the claims do not hold. Correct
                # the claims rather than accepting them.
                emit("  product evaluation ACCEPTed, but completion claims are not supported.")
                decision = EvaluatorDecision(
                    decision=Decision.FIX,
                    summary=audit.headline,
                    problems=[c.what for c in audit.contradictions] + audit.missing_evidence,
                    observed_behavior=decision.observed_behavior,
                    evidence_paths=audit.evidence_paths,
                    correction_prompt=audit.correction_prompt,
                    requirement_reference=f"{audit.observed_state.active_unit_id} registry status "
                    f"and weighted acceptance criteria",
                    product_principle_reference="passing tests do not prove product behaviour; "
                    "a status document cannot prove itself",
                    scenario=scenario.name,
                    observed_result=audit.summary_block(),
                    expected_result="Status surfaces reflect only what the evidence supports.",
                    preserve="All implementation code, all valid evidence, and every acceptance guard.",
                    retest="Re-run the scenario and re-audit; the audit must reach VERIFIED "
                    "or REQUIRES_INDEPENDENT_REVIEW.",
                    confidence=audit.confidence,
                )

        # 6d. THE REQUIRED INDEPENDENT REVIEW, as a transition inside this loop.
        #
        #     This is the step the founder used to perform by hand: read the run
        #     output, decide a review was owed, start a separate command, read
        #     the verdict, decide whether to send it back, and start the driver
        #     again. Every part of that is here now — including the part that
        #     makes it safe, which is that a review is evidence about ONE exact
        #     repository state and stops applying the moment the builder changes
        #     it. See :mod:`~neyma_product_driver.review_cycle`.
        #
        #     What is still not here: scoring a repository criterion, writing a
        #     status file, pushing, or deciding a product question. Those remain
        #     the repository's and the founder's.
        if decision.decision is Decision.ACCEPT:
            step = await _independent_review_step(
                config=config,
                store=store,
                state=state,
                record=record,
                iteration=iteration,
                decision=decision,
                scenario=scenario,
                task_scope=task_scope,
                unit=active_unit,
                repo_context=repo_context,
                audit=audit,
                risk=risk,
                gate=last_gate["value"],
                suite_result=suite_result,
                diff_files=diff_files,
                ledger=review_ledger,
                reviewer_factory=reviewer_factory,
                builder_session_id=builder.session_id or "",
                emit=emit,
            )
            last_requirement["value"] = step.requirement
            record.review_requirement = (
                step.requirement.to_dict() if step.requirement is not None else None
            )
            record.notes.extend(step.notes)
            reviews.extend(step.new_reviews)
            if step.satisfied_by is not None:
                satisfying["value"] = step.satisfied_by
            decision = step.decision
            if step.terminal is not None:
                return _terminate(step.terminal, decision, record)

            # A supported review changes what the completion audit is looking
            # at: the one thing it said was outstanding has now happened. Re-ask
            # it, so the scoped completion record this run carries out says
            # VERIFIED rather than AWAITING INDEPENDENT REVIEW. The re-ask goes
            # through the same auditor with the same inputs plus the review, and
            # the auditor checks the review against the CURRENT tree itself —
            # so this can only ever confirm, never assert.
            if step.satisfied_by is not None and auditor is not None:
                audit = auditor.audit(
                    record.builder_summary,
                    unit=(repo_loader.resolve_active_unit_optional() if repo_loader else active_unit),
                    run_commands=all_commands,
                    evidence_dir=str(store.iteration_dir(iteration)),
                    scope=task_scope.model_copy(
                        update={
                            "parent_phase_state": getattr(active_unit, "status", "") or "",
                            "parent_phase_execution_state": getattr(
                                active_unit, "execution_state", ""
                            )
                            or "",
                        }
                    ),
                    satisfying_review=step.satisfied_by,
                )
                last_audit["value"] = audit
                record.completion_audit = audit.model_dump(mode="json")
                if audit.completion is not None:
                    record.scoped_completion = audit.completion.model_dump(mode="json")
                store.save_completion_audit(iteration, record.completion_audit)
                emit(f"  after review: {audit.headline}")

        record.decision = decision
        _print_decision(decision, emit)

        # 7. route
        if decision.decision is Decision.ACCEPT:
            result = _terminate(RunStatus.ACCEPTED, decision, record)
            store.save_accepted(iteration)
            return result

        if decision.decision is Decision.ASK_USER:
            return _terminate(RunStatus.NEEDS_USER, decision, record)

        if decision.decision is Decision.BLOCKED:
            return _terminate(RunStatus.BLOCKED, decision, record)

        # FIX — correct and retest, unless this was the last permitted iteration.
        prior_problems = list(decision.problems)
        if iteration >= config.max_iterations:
            record.notes.append("iteration budget exhausted before the fix could be retested")
            return _terminate(RunStatus.MAX_ITERATIONS, decision, record)

        # 7b. Stage 3 — a failure is evidence about a whole family of
        #     situations, not just the one that failed. Bounded by the planner's
        #     own budgets; when they are spent, the run continues with the
        #     coverage it has rather than generating forever.
        if planner is not None and suite_result is not None:
            requests = list(decision.scenario_requests)
            # The structured brief, not one truncated line per failure: what was
            # expected, every assertion that failed, and the output the product
            # actually produced. A generator shown "an expectation failed" cannot
            # target the risk that failure revealed.
            diff_now = changed_files(config.neyma_repo)
            failures = (
                build_failure_evidence(
                    suite, suite_result, suite_executor.results, diff_files=diff_now
                )
                if suite is not None and suite_executor is not None
                else []
            )
            # A named risk with no coverage is as much a reason to generate as
            # a failure is. It used to be neither: the wave only ran when
            # something failed or the evaluator asked, so a run whose scenarios
            # all passed while an identified P0 had no coverage generated
            # nothing for it and blocked on the gap it had just refused to
            # close.
            gaps = planner.plan.planned_gaps()
            if (failures or requests or gaps) and not planner.budget_exhausted():
                emit(
                    "→ expanding verification around what failed..."
                    if failures
                    else "→ generating coverage for identified risks that have none..."
                )
                planner.expand_after_failures(
                    task=state.task,
                    unit=active_unit,
                    failures=failures,
                    clusters=suite_result.clusters,
                    investigation_findings=_investigation_findings(record),
                    evaluator_requests=requests,
                    diff_files=diff_now,
                )
            elif (failures or requests or gaps) and planner.budget_exhausted():
                note = (
                    "scenario-generation budget is spent; no further situations were "
                    "generated for the remaining failures"
                )
                record.notes.append(note)
                emit(f"  {note}")

        grounded = render_correction_for_builder(decision)
        sent_corrections.append(decision.correction_prompt)
        next_prompt = builder_correction_prompt(
            grounded, iteration, active_unit_id, feedback_store.render()
        )
        record.correction_prompt_sent = next_prompt
        store.save_iteration(record)
        state.iterations.append(record)
        store.save_state(state)

    # Defensive: the loop above always returns, but never fall through silently.
    state.status = RunStatus.MAX_ITERATIONS
    store.save_state(state)
    return LoopResult(
        status=RunStatus.MAX_ITERATIONS, state=state, final_decision=state.final_decision
    )


# --------------------------------------------------------------------------
# Scenario suites inside the loop
# --------------------------------------------------------------------------


def _identified_risks(planner: Any) -> Sequence[Any]:
    """This run's own risk register, or nothing when generation is not in use.

    Read through ``getattr`` rather than by type so a run without a planner —
    the default, since generation is opt-in — simply contributes no risks and
    the gate behaves exactly as it did before.
    """
    plan = getattr(planner, "plan", None)
    return list(getattr(plan, "risks", None) or [])


def _coverage_gap_briefs(planner: Any, suite_result: Any) -> list[str]:
    """The deterministic coverage gaps, rendered for the evaluator.

    The evaluator is asked whether the coverage was sufficient. Asking that
    while withholding the gaps the driver has already computed invites a
    confident answer built on a partial view; this hands over the same facts the
    acceptance gate is about to enforce.
    """
    from .scenario_gate import uncovered_required_risks

    return [
        risk.brief()
        for risk in uncovered_required_risks(_identified_risks(planner), suite_result)
    ]


def _coverage_gap_only(verdict: Any, suite_result: SuiteResult) -> bool:
    """Is the *only* thing standing between this run and an acceptance a gap?

    True when every required scenario that the suite set out to verify passed
    with resolvable evidence, nothing failed, verification was actually
    produced — and the run still names acceptance-blocking risks with no
    scenario behind them. That is not a defect and not a failed verification; it
    is verification that was never written, which is the one refusal a run can
    answer by itself.

    Generation problems are deliberately disqualifying. A wave that *errored*
    leaves a permanent entry in the gate's problem list, so no amount of further
    generation can reach VERIFIED; attempting closure there would spend the
    remaining budget to arrive at the same refusal.
    """
    if verdict is None:
        return False
    return bool(
        getattr(verdict, "uncovered_risks", None)
        and not getattr(verdict, "unverified", None)
        and not getattr(verdict, "generation_problems", None)
        and not suite_result.blocking_failures()
    )


def _gap_risks(planner: Any, verdict: Any) -> list[Any]:
    """The plan's own risk objects for the gate's uncovered set.

    The *selection* is the gate's — computed from execution records, never from
    anything a model wrote in prose — and this only recovers the full
    ``IdentifiedRisk`` behind each one, because that is what carries the key a
    generated scenario must cite. A gate entry with no matching register entry
    is dropped rather than approximated: a wave aimed at a risk the plan cannot
    name would produce scenarios nothing could validate.
    """
    register = _identified_risks(planner)
    by_identity = {
        (r.risk_category.value, r.description.strip()): r for r in register
    }
    out_risks: list[Any] = []
    for gap in getattr(verdict, "uncovered_risks", []) or []:
        found = by_identity.get((gap.risk_category, gap.description.strip()))
        if found is not None and found not in out_risks:
            out_risks.append(found)
    return out_risks


@dataclass
class GapClosure:
    """What one round of coverage-gap closure did to the run's evidence.

    ``decision`` is ``None`` when nothing executed. That is the difference
    between "the gate was asked again" and "there was nothing new to ask it
    about", and it is why the caller keeps its existing decision rather than
    recomputing an identical one and reporting the same refusal twice.
    """

    suite: ScenarioSuite
    suite_result: SuiteResult
    verdict: Any
    decision: EvaluatorDecision | None = None
    executed_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: True once new coverage has actually been executed, so the suite record,
    #: the gate verdict and the decision all moved.
    ran: bool = False


async def _close_coverage_gaps(
    *,
    planner: Any,
    suite: ScenarioSuite,
    suite_executor: SuiteExecutor,
    suite_result: SuiteResult,
    verdict: Any,
    accepted: EvaluatorDecision,
    scenario: Scenario,
    task: str,
    unit: Any,
    diff_files: Sequence[str],
    emit: Callable[[str], None],
) -> GapClosure:
    """Generate and execute the coverage a passing run is missing, then re-gate.

    An acceptance-blocking risk with no scenario behind it says nothing about
    whether the product is correct — only that nobody looked. Sending a builder
    a correction for it would be inventing a defect out of an absence, and
    ending the run makes the founder ask by hand for a scenario the driver
    already knows it needs and already has the budget and the vocabulary to
    write. So the run writes it: a wave aimed at exactly those risks, executed,
    and then judged by the same deterministic gate as everything else.

    Nothing here can make a risk look covered. The wave is aimed by the gate's
    uncovered set rather than by the evaluator's prose; the generated cases are
    validated and compiled by the same path as every other generated scenario,
    against the same approved command vocabulary; and coverage is removed only
    by :func:`evaluate_gate` recomputing from the new execution records. If a
    generated case *fails*, that is a real observation about the product, and it
    reaches the builder through the ordinary suite-precedence route as a
    grounded FIX.

    The loop ends — always — on one of: the gaps are closed, the generator has
    no wave or scenario budget left, the wave produced nothing runnable for
    those risks, or a generated case failed. Each round that continues consumes
    one of the planner's bounded waves, so the bound is the run's existing one.
    """
    closure = GapClosure(suite=suite, suite_result=suite_result, verdict=verdict)

    while True:
        gaps = _gap_risks(planner, closure.verdict)
        if not gaps:
            break

        if planner.budget_exhausted():
            note = (
                "the scenario-generation budget is spent, so no coverage could be "
                f"generated for {len(gaps)} uncovered acceptance-blocking risk(s)"
            )
            closure.notes.append(note)
            emit(f"  {note}")
            break

        emit(
            f"→ closing coverage gaps: generating scenarios for {len(gaps)} identified "
            "risk(s) with no evidence..."
        )
        for risk in gaps:
            emit(f"    [{risk.severity.value} {risk.risk_category.value}] {risk.description}")

        known = {s.id for s in planner.plan.scenarios}
        # Failures and evaluator requests are deliberately absent. This wave
        # answers the deterministic gap set and nothing else: an evaluator's
        # prose about what else might be worth testing is not what is blocking
        # the run, and letting it steer here is how a targeted wave becomes a
        # general one that closes nothing.
        planner.expand_after_failures(
            task=task,
            unit=unit,
            failures=[],
            diff_files=list(diff_files),
            gaps=gaps,
        )
        fresh = [
            model.id
            for model in planner.plan.scenarios
            if model.id not in known and model.id in planner.compiled
        ]
        if not fresh:
            note = (
                "the coverage-gap wave produced no runnable scenario for "
                + "; ".join(f"[{r.severity.value} {r.risk_category.value}] {r.description}" for r in gaps)
            )
            closure.notes.append(note)
            emit(f"  {note}")
            break

        emit(f"  generated {len(fresh)} coverage-gap scenario(s): {', '.join(fresh)}")
        closure.suite = _assemble_suite(scenario, planner)
        added = await suite_executor.run(
            closure.suite,
            only=fresh,
            selection_reason=(
                "coverage-gap scenarios for identified risks with no passing evidence"
            ),
        )
        closure.ran = True
        closure.suite_result = merge_suite_results(closure.suite_result, added, closure.suite)
        closure.executed_ids += [
            o.scenario_id for o in added.outcomes if o.outcome is not Outcome.SKIPPED
        ]
        closure.verdict = evaluate_gate(
            closure.suite_result,
            generation_problems=planner.generation_problems(),
            risks=_identified_risks(planner),
        )

        if closure.suite_result.blocking_failures():
            note = (
                "a coverage-gap scenario failed; the run now has an observed defect to "
                "correct rather than a gap to close"
            )
            closure.notes.append(note)
            emit(f"  {note}")
            break

    if closure.ran:
        closure.decision = _apply_suite_precedence(
            closure.suite_result,
            accepted,
            scenario.name,
            emit,
            generation_problems=planner.generation_problems(),
            risks=_identified_risks(planner),
        )
    return closure


def _assemble_suite(scenario: Scenario, planner: Any) -> ScenarioSuite:
    """Permanent scenario plus this run's generated coverage.

    The explicitly selected scenario is always present and always permanent, so
    ``--scenario foo --auto-scenarios`` runs foo exactly as it would have run
    alone, with generated coverage added around it.
    """
    return build_suite(
        permanent=[(scenario.name, scenario)],
        generated=[
            (model, planner.compiled[model.id])
            for model in planner.plan.scenarios
            if model.id in planner.compiled
        ],
    )


def _summarize_verification(scenario: Scenario, planner: Any) -> str:
    """What the builder is told about how its work will be exercised."""
    parts = [scenario.summary()]
    generated = planner.plan.scenarios
    if generated:
        parts += [
            "",
            "The harness will also exercise these generated situations:",
            *(f"  - {s.title} ({s.risk_category.value}, {s.priority.value})" for s in generated),
            "",
            "These are verification cases derived from the requirements and the risk "
            "surface. They are not additional requirements: if one of them rests on a "
            "product decision the repository has not made, say so rather than inventing "
            "the behaviour it expects.",
        ]
    return "\n".join(parts)


def _primary_result(
    scenario: Scenario, executor: SuiteExecutor, suite_result: SuiteResult
) -> ScenarioResult:
    """The permanent scenario's own result, which the rest of the loop expects.

    The completion auditor, the protocol resolver and the iteration record all
    predate suites and reason about one scenario. They keep seeing exactly what
    they saw before — the explicitly selected scenario's result — while the
    suite aggregate travels alongside it.
    """
    result = executor.results.get(scenario.name)
    if result is not None:
        return result
    # The permanent scenario was skipped (a browser scenario with the browser
    # disabled, say). Say that plainly rather than inventing an empty pass.
    outcome = suite_result.by_id(scenario.name)
    reason = outcome.skip_reason if outcome is not None else "it was not executed"
    return ScenarioResult(
        scenario_name=scenario.name,
        mode=scenario.mode,
        error=f"the permanent scenario did not run: {reason}",
    )


def _record_defects(
    defects: DefectMemory,
    suite_result: SuiteResult,
    executor: SuiteExecutor,
    iteration: int,
) -> None:
    """Remember the first failure of each generated scenario.

    A generated scenario becomes a promotion candidate only if it *found*
    something, so what it observed when it first failed is the thing worth
    keeping.
    """
    for outcome in suite_result.failures():
        if outcome.origin is not Origin.GENERATED:
            continue
        observation = outcome.error or (
            outcome.failed_assertions[0] if outcome.failed_assertions else "failed"
        )
        defects.note_failure(outcome.scenario_id, iteration, observation)


def _investigation_findings(record: IterationRecord) -> list[str]:
    """Conclusions the investigator reached, as input to scenario generation.

    The two stay separate: the investigator answers "why did this happen", the
    generator answers "what should we test". A proven race is a reason to
    exercise a family of situations, and that is the only direction the
    information flows.
    """
    investigation = record.investigation or {}
    findings: list[str] = []
    for key in ("conclusion", "root_cause", "summary"):
        value = investigation.get(key)
        if isinstance(value, str) and value.strip():
            findings.append(value.strip())
    for hypothesis in investigation.get("hypotheses", []) or []:
        if isinstance(hypothesis, dict) and str(hypothesis.get("status", "")).upper() in {
            "SUPPORTED",
            "CONFIRMED",
        }:
            statement = str(hypothesis.get("statement", "")).strip()
            if statement:
                findings.append(statement)
    return findings


def _apply_suite_precedence(
    suite_result: SuiteResult,
    decision: EvaluatorDecision,
    scenario_name: str,
    emit: Callable[[str], None],
    generation_problems: Sequence[str] = (),
    risks: Sequence[Any] = (),
) -> EvaluatorDecision:
    """A required scenario that failed cannot be accepted away.

    The evaluator judges observed behaviour and may reasonably think the product
    is fine; the suite measured something concrete that is not. Where they
    disagree about a *required* scenario, the measurement wins, and the run gets
    a grounded correction built from the failure clusters rather than from the
    evaluator's impression.

    Only ACCEPT is overridden. A FIX, ASK_USER or BLOCKED already stops the run
    from completing, and replacing the evaluator's reasoning with the suite's
    would lose information.
    """
    if decision.decision is not Decision.ACCEPT:
        return decision

    # The authoritative gate. It recomputes from the outcome records, so a
    # required scenario that failed, was skipped, never ran, or cannot show its
    # evidence all reach here the same way: not verified.
    verdict = evaluate_gate(
        suite_result, generation_problems=generation_problems, risks=risks
    )

    if not verdict.blocks_acceptance and suite_result.full_run:
        return decision

    if not verdict.blocks_acceptance and not suite_result.full_run:
        emit("  an ACCEPT cannot rest on a partial suite; the full required set did not run.")
        return EvaluatorDecision(
            decision=Decision.BLOCKED,
            summary=(
                "The product evaluation accepted, but only part of the required scenario "
                "suite was executed, so there is no evidence the rest still passes."
            ),
            problems=[suite_result.selection_reason],
            observed_behavior=decision.observed_behavior,
            evidence_paths=[o.evidence_path for o in suite_result.outcomes if o.evidence_path][:12],
        )

    blocking = suite_result.blocking_failures()
    if not blocking and not verdict.unverified and verdict.uncovered_risks:
        # Every scenario that ran passed, and the run still identified risks it
        # never verified. Accepting here would be claiming the passing scenarios
        # speak for coverage that was never attempted. There is no defect to
        # correct, so this is a blocked run rather than a failing product.
        emit(
            f"  product evaluation ACCEPTed, but {len(verdict.uncovered_risks)} identified "
            "acceptance-blocking risk(s) have no passing scenario."
        )
        for line in verdict.summary_block().splitlines():
            emit(f"  {line}")
        return EvaluatorDecision(
            decision=Decision.BLOCKED,
            summary=(
                "The product evaluation accepted and every executed scenario passed, but "
                f"{len(verdict.uncovered_risks)} risk(s) this run identified as blocking "
                "were never verified, so the coverage does not support an acceptance."
            ),
            problems=[risk.brief() for risk in verdict.uncovered_risks],
            observed_behavior=decision.observed_behavior,
            evidence_paths=[
                o.evidence_path for o in suite_result.outcomes if o.evidence_path
            ][:12],
        )

    if not blocking:
        # Nothing the product did is wrong as far as anyone knows — the
        # verification simply did not happen. There is no grounded correction to
        # send a builder, and inventing one would send it chasing a defect no
        # evidence describes. This is a blocked run, not a failing product.
        emit("  product evaluation ACCEPTed, but required verification did not run.")
        for line in verdict.summary_block().splitlines():
            emit(f"  {line}")
        return EvaluatorDecision(
            decision=Decision.BLOCKED,
            summary=(
                f"The product evaluation accepted, but {len(verdict.unverified)} required "
                "scenario(s) never established a pass, so there is no evidence to accept on."
            ),
            problems=[c.brief() for c in verdict.unverified] + list(verdict.generation_problems),
            observed_behavior=decision.observed_behavior,
            evidence_paths=[c.evidence_path for c in verdict.unverified if c.evidence_path][:12],
        )

    emit(f"  product evaluation ACCEPTed, but {len(blocking)} required scenario(s) failed.")
    permanent = [f for f in blocking if f.origin is Origin.PERMANENT]
    lead = blocking[0]
    return EvaluatorDecision(
        decision=Decision.FIX,
        summary=(
            f"{len(blocking)} required scenario(s) failed, including "
            f"{'permanent regression coverage' if permanent else 'generated verification'}: "
            f"{lead.scenario_id}"
        ),
        problems=[f.brief() for f in blocking],
        observed_behavior=decision.observed_behavior,
        evidence_paths=[f.evidence_path for f in blocking if f.evidence_path][:12],
        correction_prompt=_suite_correction(suite_result),
        requirement_reference=(
            lead.requirement_reference
            or "the active unit's acceptance criteria, as exercised by the scenario suite"
        ),
        product_principle_reference=(
            "a button click, an HTTP 200 or a passing unit test is not success; the "
            "underlying outcome must be verified"
        ),
        scenario=lead.scenario_id or scenario_name,
        observed_result=suite_result.summary_block(),
        expected_result=(
            "Every required scenario passes: each situation the suite exercises produces "
            "the observable outcome it expects, and none of the forbidden observations."
        ),
        preserve=(
            "All behaviour the passing scenarios already demonstrate, every permanent "
            "regression scenario, and every guard. Do not weaken or delete a scenario to "
            "obtain a green result."
        ),
        retest=(
            "Re-run the scenario suite. The failed scenarios rerun first, their risk "
            "neighbours rerun with them, and the full required regression set must be "
            "green before acceptance."
        ),
        confidence=0.85,
    )


def _suite_correction(suite_result: SuiteResult) -> str:
    """A correction the builder can act on, organised by shared cause.

    Clustered failures produce one instruction naming the domain, not one
    instruction per symptom. Distinct failures stay distinct.
    """
    lines = [
        "SCENARIO SUITE FAILURES — the running product did not behave as the "
        "verification scenarios require.",
        "",
        suite_result.headline(),
        "",
    ]
    grouped = [c for c in suite_result.clusters if not c.singleton]
    singles = [c for c in suite_result.clusters if c.singleton]

    if grouped:
        lines.append(
            "THESE FAILURES APPEAR TO SHARE ONE CAUSE. Fix the cause once; do not patch "
            "each symptom separately:"
        )
        for cluster in grouped:
            lines.append("")
            lines.append(f"  {cluster.likely_failure_domain}")
            for scenario_id in cluster.affected_scenarios:
                outcome = suite_result.by_id(scenario_id)
                if outcome is None:
                    continue
                lines.append(f"    - {outcome.scenario_id}: {outcome.scenario_name}")
                if outcome.generated_because:
                    lines.append(f"        exercised because: {outcome.generated_because}")
                for assertion in outcome.failed_assertions[:3]:
                    lines.append(f"        observed: {assertion}")
            if cluster.evidence_paths:
                lines.append(f"    evidence: {', '.join(cluster.evidence_paths[:4])}")

    distinct = [c for c in singles if suite_result.by_id(c.affected_scenarios[0]) is not None]
    if distinct:
        lines.append("")
        lines.append("THESE FAILURES ARE DISTINCT and need separate attention:")
        for cluster in distinct:
            outcome = suite_result.by_id(cluster.affected_scenarios[0])
            if outcome is None:
                continue
            lines.append(f"  - {outcome.scenario_id}: {outcome.scenario_name}")
            if outcome.generated_because:
                lines.append(f"      exercised because: {outcome.generated_because}")
            for assertion in outcome.failed_assertions[:3]:
                lines.append(f"      observed: {assertion}")
            if outcome.evidence_path:
                lines.append(f"      evidence: {outcome.evidence_path}")

    lines += [
        "",
        "Make the smallest correction that resolves the cause above. Do not change a "
        "scenario, delete an assertion, or weaken a guard to make this pass — the "
        "scenarios describe what the product promises, and a green result obtained by "
        "editing them is worth nothing.",
    ]
    return "\n".join(lines)


@dataclass
class ReviewStepResult:
    """What the review transition did to the run, and what it left behind."""

    decision: EvaluatorDecision
    #: Set when the run must stop here. ``None`` means the loop continues.
    terminal: RunStatus | None = None
    #: The review that actually discharged the requirement, if one did.
    satisfied_by: Any = None
    requirement: Any = None
    new_reviews: list[Any] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _reviewer_command_policy(config: DriverConfig, base: Scenario) -> Any:
    """The commands one reviewer may run, assembled from human-authored sources.

    The approved set is the repository's own scenario vocabulary — the probes,
    batteries and suites a human wrote into a scenario file — so a reviewer can
    re-run this repository's verification without anyone authoring a second
    allowlist. See :mod:`~neyma_product_driver.reviewer_boundary` for what sits
    above it.
    """
    if not config.review.reviewer_can_execute:
        return None
    return ReviewerCommandPolicy(
        approved=_approved_commands(config, base),
        max_commands=config.review.reviewer_max_commands,
        extra_read_only=config.review.reviewer_extra_read_only,
    )


async def _independent_review_step(
    *,
    config: DriverConfig,
    store: EvidenceStore,
    state: RunState,
    record: IterationRecord,
    iteration: int,
    decision: EvaluatorDecision,
    scenario: Scenario,
    task_scope: Any,
    unit: Any,
    repo_context: Any,
    audit: Any,
    risk: Any,
    gate: Any,
    suite_result: Any,
    diff_files: Sequence[str],
    ledger: Any,
    reviewer_factory: Any,
    builder_session_id: str,
    emit: Callable[[str], None],
) -> ReviewStepResult:
    """Run the required independent review, and route what it says.

    Called with an ACCEPT in hand. Returns the decision the loop should carry
    forward — the same ACCEPT when the requirement is satisfied or there was
    none, a grounded FIX when the reviewer found a real defect, or a terminal
    status when the answer belongs to the founder or to nobody at all.

    The order matters and is the same every time:

    1. capture the exact repository state, and retire every review that no
       longer describes it;
    2. ask the repository, fresh, whether this *scoped task* owes a review;
    3. if one is owed and no surviving review covers this state, take one;
    4. route the verdict to whoever owns it.

    Step 1 before step 2 is what makes the loop honest. Ask the requirement
    first and a review of the previous tree is sitting there looking like an
    answer.
    """
    requirement = None
    fingerprint = capture_fingerprint(config.neyma_repo)

    # 1. Retire what the last correction invalidated. Recorded out loud: a
    #    review that stopped applying is a fact about this run, and the founder
    #    summary says so rather than quietly taking another one.
    retired = ledger.invalidate_stale(fingerprint)
    for stale in retired:
        emit(
            f"  the independent review of {stale.fingerprint.identity} "
            f"({stale.verdict}) no longer describes the implementation; "
            "the code changed after it"
        )
    if retired:
        store.write_json("independent-review-ledger.json", ledger.to_dict())

    # 2. What does the repository require, of THIS task? Re-read every round —
    #    the repository is above the driver, and a rule it states mid-run binds.
    requirement = resolve_review_requirement(
        config.neyma_repo,
        task_scope,
        unit=unit,
        risk=risk,
        audit_requires_review=(
            audit is not None and audit.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW
        ),
    )
    # Risk-proportional review is a separate, additive reason: a change that
    # touches a high-consequence surface earns one even where the repository
    # states no rule at all. `resolve_review_requirement` records the mandatory
    # half; this adds the discretionary half, which depends on how this run went.
    if not requirement.required and risk is not None:
        uncovered = len(getattr(gate, "uncovered_risks", []) or [])
        if risk.warrants_independent_review(iterations=iteration, uncovered_risks=uncovered):
            requirement.add(
                ReviewTrigger.CHANGE_RISK,
                f"this run classified the change {risk.brief()}, and a change of that size "
                "that needed more than one pass or carries an unverified risk earns a "
                "second opinion",
            )

    if not requirement.required:
        return ReviewStepResult(decision=decision, requirement=requirement)

    emit(f"→ independent review required: {requirement.brief()}")
    for reason in requirement.reasons[:4]:
        emit(f"    {reason}")
    emit(f"  reviewing {fingerprint.describe()}")

    # 3. Is there already a review OF THIS EXACT STATE? Only a supported one
    #    from a session that is not the builder's, and only for this tree.
    existing = ledger.satisfying(fingerprint)
    if existing is not None:
        emit("  a supported independent review of this exact state already stands.")
        return ReviewStepResult(
            decision=decision, requirement=requirement, satisfied_by=existing
        )

    if reviewer_factory is None:
        emit("  the required independent review could not be launched.")
        return ReviewStepResult(
            decision=decision,
            terminal=RunStatus.NEEDS_INDEPENDENT_REVIEW,
            requirement=requirement,
            notes=[
                "independent review was required for this task and no reviewer could be "
                "launched (automatic review is switched off, or no factory was supplied)"
            ],
        )

    notes: list[str] = []
    new_reviews: list[Any] = []
    prior = ledger.records[-1].review if ledger.records else None
    retried = False

    while True:
        review = await _run_independent_review(
            reviewer_factory=reviewer_factory,
            unit=unit,
            audit=audit,
            repository_context=(repo_context.render() if repo_context is not None else ""),
            task=state.task,
            risk=risk,
            changed_files=diff_files,
            suite_result=suite_result,
            builder_report=record.builder_summary,
            evidence_dir=str(store.iteration_dir(iteration)),
            scope=task_scope,
            requirement=requirement,
            fingerprint=fingerprint,
            builder_session_id=builder_session_id,
            # The refusal a builder has just corrected, or the one this retry is
            # answering. A fresh reviewer was not present for either, so it is
            # told — and told plainly that it is not bound by it. Without this,
            # every correction cycle re-litigates the same ground from zero and
            # the earlier finding is simply lost.
            prior_review=prior,
            diff_stat=(record.git.diff_stat if record.git is not None else ""),
            insist_on_execution=retried,
            emit=emit,
        )
        if review is None:
            emit("  the required independent review did not produce a verdict.")
            return ReviewStepResult(
                decision=decision,
                terminal=RunStatus.NEEDS_INDEPENDENT_REVIEW,
                requirement=requirement,
                new_reviews=new_reviews,
                notes=notes
                + ["the required independent review session failed to return a verdict"],
            )

        entry = ledger.record(
            review,
            fingerprint,
            iteration=iteration,
            scope_id=task_scope.scope_id,
            builder_session_id=builder_session_id,
        )
        new_reviews.append(review)
        record.independent_review = review.model_dump(mode="json")
        store.save_independent_review(iteration, record.independent_review)
        # The run-level ledger, written every time it changes rather than at the
        # end: which reviews were taken, of which trees, by which sessions, and
        # which a later change retired. A run that dies mid-correction still
        # leaves the record of what had been reviewed and what had not.
        store.write_json("independent-review-ledger.json", ledger.to_dict())
        emit(
            f"  review verdict: {review.verdict} "
            f"({len(review.blockers)} blocker(s)); {review.evidence_basis()}"
        )
        for refused in review.commands_refused[:4]:
            emit(f"    reviewer command refused: {str(refused.get('command', ''))[:100]}")

        if not entry.independent:
            # Structural, not stylistic. A "review" from the builder's own
            # session is not a second opinion, and it must never discharge a
            # requirement whose entire content is that it came from elsewhere.
            emit("  the review did not come from an independent session; it counts for nothing.")
            return ReviewStepResult(
                decision=decision,
                terminal=RunStatus.NEEDS_INDEPENDENT_REVIEW,
                requirement=requirement,
                new_reviews=new_reviews,
                notes=notes
                + [
                    "the review returned was not from a session independent of the builder, "
                    "so the independent-review requirement is not satisfied"
                ],
            )

        routing = route_review(
            review,
            refusals_so_far=len(ledger.refusals()),
            correction_budget=config.review.max_automatic_reviews,
            execution_available=bool(config.review.reviewer_can_execute),
            execution_used=bool(review.commands_allowed),
            retried_with_execution=retried,
        )

        if routing.route is ReviewRoute.RETRY_WITH_EXECUTION and not retried:
            # It had the capability and did not use it. Ask once more, with the
            # vocabulary spelled out and the earlier answer in front of it. Once
            # — a second identical answer is the answer.
            emit("  the reviewer did not run the verification it was allowed to run; asking once more.")
            notes.append(
                "the first review reported insufficient evidence without executing any "
                "permitted verification; a second reviewer was asked to reproduce it"
            )
            prior, retried = review, True
            continue

        return _apply_review_routing(
            routing=routing,
            review=review,
            entry=entry,
            requirement=requirement,
            decision=decision,
            scenario=scenario,
            new_reviews=new_reviews,
            notes=notes,
            emit=emit,
        )


def _apply_review_routing(
    *,
    routing: Any,
    review: Any,
    entry: Any,
    requirement: Any,
    decision: EvaluatorDecision,
    scenario: Scenario,
    new_reviews: list[Any],
    notes: list[str],
    emit: Callable[[str], None],
) -> ReviewStepResult:
    """Turn one routing decision into what the loop does next.

    Split out from the step above so the five outcomes are readable side by
    side. Every branch here is terminal or corrective; none of them can produce
    an acceptance the review did not support.
    """
    if routing.route is ReviewRoute.SATISFIED:
        emit("  independent review SUPPORTED this implementation.")
        return ReviewStepResult(
            decision=decision,
            satisfied_by=entry,
            requirement=requirement,
            new_reviews=new_reviews,
            notes=notes
            + [
                f"independent review satisfied {requirement.scope_id or 'this task'} at "
                f"{entry.fingerprint.identity} ({review.evidence_basis()})"
            ],
        )

    if routing.route is ReviewRoute.CORRECT_PRODUCT:
        emit("  the reviewer found a grounded defect; sending it to the same builder.")
        return ReviewStepResult(
            decision=_review_correction(
                review, decision, scenario.name, requirement, routing.grounded_findings
            ),
            requirement=requirement,
            new_reviews=new_reviews,
            notes=notes
            + [f"independent review returned {review.verdict}; routed to the builder"],
        )

    if routing.route is ReviewRoute.EXTERNAL_ACTION:
        emit("  the review needs an action outside this machine; stopping at that boundary.")
        return ReviewStepResult(
            decision=EvaluatorDecision(
                decision=Decision.ASK_USER,
                summary=(
                    "The independent review cannot conclude without an action only you can "
                    "perform. Nothing was fabricated in its place."
                ),
                problems=[
                    routing.requested_action
                    or routing.reason
                    or "an external action the driver may not perform"
                ],
                observed_behavior=decision.observed_behavior,
                evidence_paths=decision.evidence_paths,
                confidence=0.9,
            ),
            terminal=RunStatus.NEEDS_USER,
            requirement=requirement,
            new_reviews=new_reviews,
            notes=notes
            + [
                "the independent review requires an external action: "
                + (routing.requested_action or routing.reason)
            ],
        )

    if routing.route is ReviewRoute.FOUNDER_DECISION:
        emit("  the reviewer is describing a decision rather than a defect; this is yours.")
        return ReviewStepResult(
            decision=EvaluatorDecision(
                decision=Decision.ASK_USER,
                summary=routing.reason
                or (
                    "Independent review will not support this change, and what it is "
                    "raising is a product or authority decision rather than a defect the "
                    "builder can be sent back to fix."
                ),
                problems=[
                    f"[{getattr(f, 'severity', 'major')}] {getattr(f, 'finding', '')}"
                    for f in routing.grounded_findings
                ][:12]
                or [str(getattr(review, "summary", ""))[:200]],
                observed_behavior=decision.observed_behavior,
                evidence_paths=[
                    str(getattr(f, "evidence_path", ""))
                    for f in routing.grounded_findings
                    if getattr(f, "evidence_path", "")
                ][:12]
                or decision.evidence_paths,
                confidence=0.8,
            ),
            terminal=RunStatus.NEEDS_USER,
            requirement=requirement,
            new_reviews=new_reviews,
            notes=notes + [f"independent review escalated: {routing.reason[:300]}"],
        )

    # UNRESOLVED. Fail closed, with the exact reason and the owner named. This
    # is deliberately NOT a correction: asking a builder to change working code
    # because a measurement could not be taken changes nothing about the
    # measurement, and the loop that does it does not converge.
    owner = {
        BlockerKind.VERIFICATION_HARNESS: (
            "Product Driver's own verification capability — fix the driver, not the product"
        ),
        BlockerKind.REVIEWER_CAPABILITY: (
            "the reviewer's read-only boundary — widen the approved verification "
            "vocabulary if the command is genuinely safe, or accept that this cannot be "
            "reviewed automatically"
        ),
        BlockerKind.REPOSITORY_AUTHORITY: "the repository's own authority",
    }.get(routing.blocker, "unstated by the reviewer")
    emit("  the review could not conclude, and no permitted verification resolves it.")
    return ReviewStepResult(
        decision=EvaluatorDecision(
            decision=Decision.BLOCKED,
            summary=(
                "The required independent review could not reach a verdict, so this run "
                "does not accept. No evidence was manufactured to close the gap."
            ),
            problems=[routing.reason[:400], f"owner: {owner}"],
            observed_behavior=decision.observed_behavior,
            evidence_paths=decision.evidence_paths,
            confidence=0.85,
        ),
        terminal=RunStatus.BLOCKED,
        requirement=requirement,
        new_reviews=new_reviews,
        notes=notes
        + [
            f"independent review unresolved ({routing.blocker.value}); owner: {owner}"
        ],
    )


async def _run_independent_review(
    *,
    reviewer_factory: Any,
    unit: Any,
    audit: Any,
    repository_context: str,
    task: str,
    risk: Any,
    changed_files: Sequence[str],
    suite_result: Any,
    builder_report: str,
    evidence_dir: str,
    emit: Callable[[str], None],
    scope: Any = None,
    requirement: Any = None,
    fingerprint: Any = None,
    builder_session_id: str = "",
    prior_review: Any = None,
    diff_stat: str = "",
    insist_on_execution: bool = False,
) -> Any:
    """Launch one focused review. Returns ``None`` if it could not run.

    A review that cannot be launched is never silently treated as a review that
    passed — the caller keeps the ACCEPT only when a verdict actually came back,
    and a failure here is reported rather than swallowed into a green result.

    ``fingerprint``, ``scope`` and ``builder_session_id`` are handed to the
    session rather than to the prompt: they end up bound into the returned review
    by the harness, so what the review is evidence about is recorded by the
    machine and not by the model.
    """
    from .completion_auditor import CompletionAudit
    from .context import ActiveUnit
    from .reviewer import review_prompt

    if audit is None:
        # A run configured without an auditor still deserves the review its risk
        # earned. An empty audit is the honest input here: it says "no completion
        # claim was checked", rather than manufacturing one so the prompt has
        # something to quote.
        audit = CompletionAudit(
            decision=AuditDecision.VERIFIED,
            headline="no completion audit ran for this run",
        )

    reviewer_kwargs: dict[str, Any] = {
        "fingerprint": fingerprint,
        "scope_id": str(getattr(scope, "scope_id", "") or ""),
        "builder_session_id": builder_session_id,
    }
    try:
        session_cm = reviewer_factory(**reviewer_kwargs)
    except TypeError:
        # A factory that predates the binding arguments (every test fake does)
        # still works; it simply produces a review with no fingerprint attached,
        # which the ledger then treats as matching nothing.
        session_cm = reviewer_factory()

    try:
        async with session_cm as reviewer:
            prompt = review_prompt(
                unit=unit if unit is not None else ActiveUnit.undeclared("no repository unit registry"),
                audit=audit,
                builder_report=builder_report,
                evidence_dir=evidence_dir,
                repository_context=repository_context,
                task=task,
                risk=risk,
                changed_files=changed_files,
                suite_summary=suite_result.summary_block() if suite_result is not None else "",
                scope=scope,
                requirement=requirement,
                fingerprint=fingerprint,
                policy=getattr(reviewer, "command_policy", None),
                diff_stat=diff_stat,
                prior_review=prior_review,
            )
            if insist_on_execution:
                prompt += (
                    "\n\n--- THIS IS THE SECOND ASK ---\n"
                    "A previous reviewer returned INSUFFICIENT_EVIDENCE without executing "
                    "any of the deterministic verification it was permitted to run. Run it "
                    "before you answer. If, having run it, the evidence still does not "
                    "settle the question, say exactly which command or file would and set "
                    "`blocked_on` accordingly — that answer stops the run rather than "
                    "accepting it, so it needs to be precise."
                )
            return await reviewer.review(prompt)
    except Exception as exc:  # a reviewer that dies must not become an ACCEPT
        emit(f"  independent review could not run: {type(exc).__name__}: {redact(str(exc))}")
        return None


def _review_correction(
    review: Any,
    decision: EvaluatorDecision,
    scenario_name: str,
    requirement: Any = None,
    findings: Sequence[Any] | None = None,
) -> EvaluatorDecision:
    """Turn a reviewer's findings into a grounded correction for the same builder.

    This is the step that used to be the founder's: read the review, decide it
    was actionable, and paste it into a builder session. Each finding already
    carries an evidence path and the reasoning behind it, which is exactly what
    the prompt-quality contract requires of a correction, so nothing has to be
    invented to make it sendable.

    ``findings`` are the ones the router judged grounded. Only those are sent: a
    refusal with nothing citable behind it never reaches a builder at all, it
    goes to the founder, because turning an opinion into an automatic code change
    is how a review loop stops converging.
    """
    grounded = list(findings if findings is not None else review.blockers or review.findings)
    lines = correction_lines(review, grounded, requirement)

    return EvaluatorDecision(
        decision=Decision.FIX,
        summary=f"Independent review returned {review.verdict}: {review.summary[:200]}",
        problems=[f"[{f.severity}] {f.finding}" for f in grounded][:12]
        or [review.summary[:200]],
        observed_behavior=decision.observed_behavior,
        evidence_paths=[f.evidence_path for f in grounded if f.evidence_path][:12],
        correction_prompt="\n".join(lines),
        requirement_reference=(
            (requirement.reasons[0][:300] if getattr(requirement, "reasons", None) else "")
            or "the independent review this task's authority requires"
        ),
        product_principle_reference=(
            "a change to a high-consequence surface is not finished because the session "
            "that wrote it believes it is"
        ),
        scenario=scenario_name,
        observed_result=review.summary[:2000],
        expected_result=(
            "Every blocking review finding is resolved in the product, with evidence, and "
            "no guard or test was weakened to resolve it."
        ),
        preserve=(
            "All behaviour the passing scenarios already demonstrate, every test, and "
            "every guard."
        ),
        retest=(
            "Re-run the scenario suite; a NEW independent reviewer then judges the "
            "corrected state, because this review describes the state before it."
        ),
        confidence=max(0.6, float(getattr(review, "confidence", 0.8) or 0.8)),
    )


def _apply_protocol_policy(
    resolution: Any,
    decision: EvaluatorDecision,
    emit: Callable[[str], None],
) -> tuple[RunStatus | None, EvaluatorDecision]:
    """Apply the repository's protocol findings to the product verdict.

    One rule, replacing a six-level precedence table:

        A protocol finding stops the run only when clearing it would need an
        action the founder alone may authorize — a history rewrite, a change to
        pushed or shared history, or a destructive operation.

    Everything else is recorded and reported. That is not a relaxation of
    safety; the operations that were ever dangerous are exactly the ones still
    caught here, and the command guard refuses them independently whatever this
    returns. What is given up is the driver's habit of treating a *process*
    finding — a commit that carries status alongside content, a receipt that has
    not been regenerated, a finalizer that has not run — as more authoritative
    than a scenario suite that just watched the product work.

    A finding the repository no longer states never reaches here at all: the
    resolver only speaks where the repository states a rule.

    Returns ``(terminal_status, decision)``; ``None`` means the loop continues.
    """
    verdict = requires_founder_authority(resolution)
    if not verdict:
        notes = protocol_diagnostic_notes(resolution)
        if notes:
            emit("  repository protocol findings (recorded, not blocking):")
            for note_text in notes[:6]:
                emit(f"    - {note_text}")
        return None, decision

    emit("  a repository repair needs your authority before it can proceed.")
    for operation in verdict.operations[:6]:
        emit(f"    - {operation}")
    return RunStatus.REQUIRES_APPROVAL, EvaluatorDecision(
        decision=Decision.ASK_USER,
        summary=(
            f"A repository-governance repair needs your approval: {verdict.reason}. "
            "The implementation is not in question here."
        ),
        problems=list(verdict.operations)
        or [str(getattr(resolution, "next_safe_action", "")) or "an authorized repair"],
        observed_behavior=decision.observed_behavior,
        evidence_paths=list(getattr(resolution, "sources_read", []) or [])[:12],
        confidence=0.9,
    )


def _build_provenance(
    *,
    founder: Any,
    repo_context: Any,
    git: GitSnapshot | None,
    scenario_result: ScenarioResult | None,
    store: EvidenceStore,
    iteration: int,
    feedback_count: int,
    prompt_chars: int,
) -> ContextProvenance:
    """Record exactly what informed this decision."""
    iter_dir = store.iteration_dir(iteration)
    # The evidence root is always cited, because the prompt hands it to the
    # evaluator as the place to draw evidence_paths from. Files written after
    # this point (the iteration record itself) are listed by save_iteration.
    evidence_files: list[str] = [str(iter_dir)]
    if iter_dir.exists():
        evidence_files += sorted(str(p) for p in iter_dir.rglob("*") if p.is_file())
    if scenario_result and scenario_result.browser:
        evidence_files.extend(scenario_result.browser.screenshots)
        if scenario_result.browser.trace_path:
            evidence_files.append(scenario_result.browser.trace_path)

    prov = ContextProvenance(
        founder_context_version=getattr(founder, "version", "") if founder else "",
        founder_context_files=list(getattr(founder, "files", []) or []) if founder else [],
        repository_head=(repo_context.head_commit if repo_context else (git.head_commit if git else "")),
        repository_branch=(repo_context.branch if repo_context else (git.branch if git else "")),
        repository_dirty_files=(
            repo_context.dirty_file_count if repo_context else (git.dirty_file_count if git else 0)
        ),
        active_unit_id=repo_context.active_unit.unit_id if repo_context else "",
        active_unit_status=repo_context.active_unit.status if repo_context else "",
        active_unit_criteria=repo_context.active_unit.criteria_labels() if repo_context else [],
        repository_files_consulted=list(repo_context.files_consulted) if repo_context else [],
        evidence_files_consulted=sorted(set(evidence_files)),
        founder_feedback_count=feedback_count,
        prompt_chars=prompt_chars,
    )
    return prov


def _print_decision(decision: EvaluatorDecision, emit: Callable[[str], None]) -> None:
    colour = {
        Decision.ACCEPT: good,
        Decision.FIX: warn,
        Decision.ASK_USER: warn,
        Decision.BLOCKED: error,
    }[decision.decision]
    colour(f"  DECISION: {decision.decision.value}  (confidence {decision.confidence:.2f})")
    if decision.summary:
        emit(f"  {decision.summary}")
    for obs in decision.observed_behavior[:8]:
        emit(f"    observed: {obs}")
    for prob in decision.problems[:8]:
        emit(f"    problem:  {prob}")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _preflight_api_key(config: DriverConfig) -> bool:
    """Warn (and require confirmation) when API-key billing would take over."""
    if not api_key_present():
        return True
    warn(
        "\nANTHROPIC_API_KEY is set in this environment.\n"
        "  API-key billing takes precedence over your Claude Code subscription,\n"
        "  so this run would be billed to the API key, not the subscription."
    )
    if config.confirm_api_key_billing:
        note("  confirm_api_key_billing is set — proceeding.")
        return True
    if not sys.stdin.isatty():
        error("  Refusing to proceed non-interactively. Unset ANTHROPIC_API_KEY or set confirm_api_key_billing: true.")
        return False
    reply = input("  Proceed with API-key billing? Type 'yes' to continue: ").strip().lower()
    if reply != "yes":
        out("  Aborted.")
        return False
    return True


async def cmd_run(args: argparse.Namespace) -> int:
    config = _config_from_args(args)

    problems = config.validate_repo()
    if problems:
        for p in problems:
            error(p)
        return 2

    if not _preflight_api_key(config):
        return 3

    git = git_snapshot(config.neyma_repo)
    header("NEYMA PRODUCT DRIVER")
    out(f"repo:     {config.neyma_repo}")
    out(f"branch:   {git.branch}  (HEAD {git.head_commit}, {git.dirty_file_count} dirty files)")

    if config.require_branch and git.branch != config.require_branch:
        error(f"branch is {git.branch!r} but require_branch is {config.require_branch!r}")
        return 2
    if git.dirty_file_count and not config.allow_dirty_tree:
        error("working tree is dirty and allow_dirty_tree is false")
        return 2

    # Resume or start a run. The state is opened *before* the scenario is
    # chosen, because on a resume the run's own recorded base scenario is the
    # right answer and the config default is not: a resume that omitted
    # --scenario silently swapped the base scenario underneath a plan built
    # against a different one, and every generated scenario that referenced a
    # service the new base does not declare was then dropped.
    assert config.runs_dir is not None
    store: EvidenceStore
    state: RunState | None = None
    if args.resume_run:
        store = EvidenceStore.open_run(config.runs_dir, args.resume_run)
        state = store.load_state()
        if state is None:
            error(f"No resumable state found for run {args.resume_run}")
            return 2

    scenario_name = args.scenario
    if not scenario_name and state is not None and state.scenario_name:
        scenario_name = state.scenario_name
        note(f"resuming against the run's own base scenario: {scenario_name}")
    try:
        scenario = load_scenario(config.scenario_path(scenario_name))
    except (FileNotFoundError, ValueError) as exc:
        error(str(exc))
        return 2

    task = args.task or config.task
    if not task.strip():
        error("No task given. Pass --task '...' or set 'task:' in the config file.")
        return 2

    if scenario.mode == "browser" and not config.run.browser_enabled:
        note("scenario is a browser scenario — enabling browser support for this run")
        config.run.browser_enabled = True

    if config.run.browser_enabled:
        # Preflight, once, at the top. A run whose browser verification cannot
        # happen must say so here rather than producing browser scenarios that
        # observe nothing at the bottom. `doctor` checks this, but `doctor` is a
        # separate opt-in command and `cmd_run` checked nothing.
        ok, detail = await _check_chromium()
        if not ok:
            error(f"browser support is enabled for this run but unusable: {detail}")
            out("Install it with: pip install playwright && playwright install chromium")
            return 2

    if state is not None:
        store.clear_stop()
        state.status = RunStatus.RUNNING
        note(f"resuming run {state.run_id} (builder session {state.builder_session_id})")
    else:
        run_id = new_run_id()
        store = EvidenceStore(config.runs_dir, run_id)
        state = RunState(
            run_id=run_id,
            neyma_repo=str(config.neyma_repo),
            scenario_name=scenario.name,
            task=task,
            max_iterations=config.max_iterations,
        )
    state.task = task
    state.scenario_name = scenario.name
    state.max_iterations = config.max_iterations
    state.pid = os.getpid()
    store.save_state(state)

    out(f"scenario: {scenario.name} ({scenario.mode})")
    out(f"run:      {store.run_dir}")
    out(f"budget:   max {config.max_iterations} iterations")

    # Context layer A: durable founder context. Fails closed.
    try:
        founder = load_founder_context(config.driver_root)
    except ContextResolutionError as exc:
        error(f"founder context unusable: {exc}")
        return 2
    out(f"founder:  context version {founder.version} ({len(founder.category_ids)} rubric categories)")

    # Context layer B: repository authority, re-read before every decision.
    #
    # Reported, not required. A repository that declares an active unit gets its
    # scope honoured; one that does not is built in against the founder's task.
    # This used to exit 11 before the builder had done anything, which made the
    # presence of a unit registry a precondition for all product work — and made
    # every simplification of the target repository an outage here.
    repo_loader = RepositoryContextLoader(config.neyma_repo)
    unit = repo_loader.resolve_active_unit_optional()
    if unit.is_declared:
        out(f"unit:     {unit.unit_id} ({unit.status}) — {unit.name}")
    else:
        note(f"unit:     none declared — the task is the authority ({unit.resolution_problem})")

    planner = _make_planner(config, args, store, scenario, founder, out)
    if planner is not None:
        if planner.restore_failed:
            # Fail closed. This run has a plan on disk that cannot be read, so
            # it cannot say what it had already decided to verify. Starting over
            # at wave zero would hand back a spent generation budget and would
            # replace the run's own record of its committed coverage with an
            # empty one. The unreadable file has been preserved; a deliberate
            # fresh run is now the operator's decision to make, not the
            # driver's to make silently.
            error("\nBLOCKED — this run's scenario plan could not be read.")
            out(
                "Nothing was overwritten. To start this task's verification again from\n"
                "nothing, begin a new run rather than resuming this one."
            )
            state.status = RunStatus.BLOCKED
            store.save_state(state)
            return _exit_code_for(RunStatus.BLOCKED)
        out(
            f"scenarios: adaptive generation enabled — up to "
            f"{config.scenario_generation.max_total_scenarios} generated case(s) across "
            f"{config.scenario_generation.max_waves} wave(s), "
            f"{len(planner.approved_commands)} approved command(s)"
        )

    from .builder import BuilderSession
    from .evaluator import EvaluatorSession

    builder_resume = args.resume_session or state.builder_session_id
    result: LoopResult
    try:
        async with BuilderSession(
            config.neyma_repo,
            config.builder,
            resume_session_id=builder_resume,
            on_progress=lambda m: out(_indent(m)),
        ) as builder:
            async with EvaluatorSession(
                config.neyma_repo,
                config.evaluator,
                resume_session_id=state.evaluator_session_id,
            ) as evaluator:
                result = await run_control_loop(
                    config=config,
                    scenario=scenario,
                    store=store,
                    state=state,
                    builder=builder,
                    evaluator=evaluator,
                    make_executor=lambda artifact_dir: ScenarioExecutor(
                        config.neyma_repo,
                        config.run,
                        artifact_dir,
                        # So the executor can re-check a command whose text
                        # changed between validation and execution against the
                        # same set validation used.
                        approved_commands=(
                            planner.approved_commands if planner is not None else None
                        ),
                    ),
                    founder=founder,
                    repo_loader=repo_loader,
                    auditor=CompletionAuditor(config.neyma_repo),
                    protocol_resolver=ProtocolResolver(config.neyma_repo),
                    planner=planner,
                    reviewer_factory=_make_reviewer_factory(config, args, scenario),
                )
    except KeyboardInterrupt:
        warn("\ninterrupted — saving state")
        state.status = RunStatus.STOPPED
        store.save_state(state)
        return 130
    except Exception as exc:
        error(f"\ndriver error: {type(exc).__name__}: {redact(str(exc))}")
        state.status = RunStatus.ERROR
        store.save_state(state)
        return 1

    _write_run_journal(
        store,
        state,
        config,
        authority_report=result.authority_report,
        result=result,
        unit=unit,
        scenario=scenario,
    )
    _report_founder_summary(result, store, config)
    _report_coverage(result, store)
    _report_outcome(result, store)
    return _exit_code_for(result.status)


def _make_reviewer_factory(
    config: DriverConfig, args: argparse.Namespace, scenario: Scenario | None = None
) -> Any:
    """A callable the loop uses to launch one fresh independent reviewer.

    Returns ``None`` when automatic review is switched off, which makes the loop
    report that a review is required rather than quietly accepting without one.

    Each call builds a NEW session and a NEW command policy. That is deliberate:
    the policy carries the execution budget and the record of what this reviewer
    actually ran, so sharing one across reviewers would let a second reviewer
    inherit the first's spent budget and, worse, its evidence.
    """
    if getattr(args, "no_auto_review", False) or not config.review.automatic:
        return None

    base = scenario if scenario is not None else Scenario(name="(none)")

    def factory(**binding: Any) -> Any:
        from .reviewer import IndependentReviewerSession

        return IndependentReviewerSession(
            config.neyma_repo,
            model=config.review.model or config.evaluator.model,
            on_progress=lambda m: out(_indent(m)),
            command_policy=_reviewer_command_policy(config, base),
            **binding,
        )

    return factory


def _permanent_scenarios(config: DriverConfig) -> list[Scenario]:
    """Every handwritten scenario the repository holds.

    These are the source of both the authoritative regression coverage and the
    approved command set — a command is approved because a human wrote it into
    one of these files.
    """
    scenarios: list[Scenario] = []
    if config.scenarios_dir is None or not config.scenarios_dir.exists():
        return scenarios
    for path in sorted(config.scenarios_dir.glob("*.y*ml")):
        try:
            scenarios.append(load_scenario(path))
        except Exception:
            # A malformed scenario file is doctor's problem to report, not a
            # reason to refuse to plan.
            continue
    return scenarios


def _make_planner(
    config: DriverConfig,
    args: argparse.Namespace,
    store: EvidenceStore,
    scenario: Scenario,
    founder: Any,
    emit: Callable[[str], None],
) -> Any:
    """Build a ScenarioPlanner unless this run turned generated coverage off.

    On by default. Generating situations from the diff, the requirements and the
    failures already seen is the strongest verification this driver has, and
    making it opt-in meant the command anyone actually typed produced the
    weakest run available. ``--no-auto-scenarios`` switches it off for a run
    that has a concrete reason it cannot apply; ``--auto-scenarios`` remains
    accepted so existing commands and scripts keep working.

    Every budget in :class:`~neyma_product_driver.config.ScenarioGenerationConfig`
    still applies, so the default run is bounded exactly as the opt-in run was.
    """
    if bool(getattr(args, "no_auto_scenarios", False)):
        return None
    requested = bool(getattr(args, "auto_scenarios", False))
    if not requested and not config.scenario_generation.enabled:
        return None
    if requested and not config.scenario_generation.enabled:
        # --auto-scenarios is itself the opt-in; the config bound still applies.
        config.scenario_generation.enabled = True

    from .scenario_generator import LLMScenarioReasoner

    planner = ScenarioPlanner(
        repo=config.neyma_repo,
        config=config.scenario_generation,
        reasoner=LLMScenarioReasoner(
            config.neyma_repo,
            model=config.scenario_generation.model,
            max_turns=config.scenario_generation.generator_max_turns,
        ),
        store=store,
        base_scenario=scenario,
        permanent_scenarios=_permanent_scenarios(config),
        founder=founder,
        browser_enabled=config.run.browser_enabled,
        emit=emit,
    )
    # Resuming continues the plan the run already made. Without this the planner
    # began again at wave zero, regenerated what it had already decided, spent a
    # fresh wave budget, and overwrote the earlier plan on the next persist.
    planner.restore_from_store()
    return planner


async def cmd_scenarios_plan(args: argparse.Namespace) -> int:
    """Generate a scenario plan and print it. Executes nothing.

    Useful on its own: it shows what the driver would verify for a task, and
    what it refused to verify and why, without touching the product.
    """
    config = _config_from_args(args)
    problems = config.validate_repo()
    if problems:
        for problem in problems:
            error(problem)
        return 2

    task = args.task or config.task
    if not task.strip():
        error("No task given. Pass --task '...' or set 'task:' in the config file.")
        return 2

    try:
        founder = load_founder_context(config.driver_root)
    except ContextResolutionError as exc:
        error(f"founder context unusable: {exc}")
        return 2

    unit = RepositoryContextLoader(config.neyma_repo).resolve_active_unit_optional()
    if not unit.is_declared:
        note(f"no active unit declared — planning against the task ({unit.resolution_problem})")

    try:
        base = load_scenario(config.scenario_path(args.scenario))
    except (FileNotFoundError, ValueError) as exc:
        error(str(exc))
        return 2

    config.scenario_generation.enabled = True
    from .scenario_generator import LLMScenarioReasoner

    planner = ScenarioPlanner(
        repo=config.neyma_repo,
        config=config.scenario_generation,
        reasoner=LLMScenarioReasoner(
            config.neyma_repo,
            model=config.scenario_generation.model,
            max_turns=config.scenario_generation.generator_max_turns,
        ),
        store=None,
        base_scenario=base,
        permanent_scenarios=_permanent_scenarios(config),
        founder=founder,
        emit=lambda m: None if getattr(args, "as_json", False) else note(m),
    )
    plan = planner.plan_initial(task=task, unit=unit)
    if config.scenario_generation.diff_aware:
        planner.refine_for_diff(task=task, unit=unit)

    if getattr(args, "as_json", False):
        print(json.dumps(plan.model_dump(mode="json"), indent=2, default=str))
        return 0

    header("GENERATED SCENARIO PLAN")
    out(plan.render())
    out("")
    note(
        "Nothing was executed. This plan is ephemeral: run it with\n"
        "  python -m neyma_product_driver run --task '...' --auto-scenarios"
    )
    return 0


def _open_run(config: DriverConfig, run_id: str | None) -> EvidenceStore | None:
    assert config.runs_dir is not None
    if run_id:
        return EvidenceStore.open_run(config.runs_dir, run_id)
    return EvidenceStore.latest_run(config.runs_dir)


async def cmd_scenarios_run_generated(args: argparse.Namespace) -> int:
    """Re-execute a run's generated scenarios, without a builder or evaluator.

    Reads the plan a run already persisted and runs it again. Nothing is
    generated, nothing is judged, and no scenario is created — this is for
    looking at a plan's behaviour directly.
    """
    config = _config_from_args(args)
    store = _open_run(config, args.run)
    if store is None:
        error(f"No runs found under {config.runs_dir}. Pass --run <run-id>.")
        return 2

    plan_path = store.run_dir / "scenario-plan.json"
    if not plan_path.exists():
        error(f"Run {store.run_id} has no generated scenario plan ({plan_path}).")
        return 2

    from .scenario_plan import GeneratedScenarioPlan, compile_to_scenario

    try:
        plan = GeneratedScenarioPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    except Exception as exc:
        error(f"Could not read the scenario plan: {exc}")
        return 2

    try:
        base = load_scenario(config.scenario_path(args.scenario))
    except (FileNotFoundError, ValueError) as exc:
        error(str(exc))
        return 2

    from .scenario_validation import ValidationContext, safety_reasons

    approved = _approved_commands(config, base)
    # Replay is not a trusted path. The plan on disk was validated when it was
    # made, but the file is editable and the repository has moved since; a
    # scenario is re-validated here for the same reason it was validated the
    # first time, so that no execution path reaches the product unchecked.
    context = ValidationContext(
        approved_commands=approved,
        declared_services={s.name for s in base.services},
        app_url=base.app_url,
        local_hosts=frozenset(config.scenario_generation.local_http_hosts),
        browser_enabled=config.run.browser_enabled,
        known_failure_ids=set(plan.observed_failure_ids),
        known_cluster_ids=set(plan.observed_cluster_ids),
        # Grounding was established when the plan was generated against the
        # repository of the day; replay re-checks safety, not authorship.
        grounding_tokens=set(),
        principle_tokens=set(),
    )
    compiled: list[tuple[Any, Scenario]] = []
    for model in plan.scenarios:
        unsafe = safety_reasons(model, context)
        if unsafe:
            warn(f"refusing to replay {model.id}: {unsafe[0]}")
            continue
        allowed, _refusals = approved.resolve(model.command_strings())
        try:
            compiled.append((model, compile_to_scenario(model, base=base, approved_commands=allowed)))
        except Exception as exc:
            warn(f"skipping {model.id}: {exc}")

    if not compiled:
        error("No generated scenario in this plan could be compiled.")
        return 2

    suite = build_suite(generated=compiled)
    artifact_root = store.run_dir / "replay"
    header(f"REPLAYING {len(suite)} GENERATED SCENARIO(S) — run {store.run_id}")
    executor = SuiteExecutor(
        make_executor=lambda artifact_dir: ScenarioExecutor(
            config.neyma_repo, config.run, artifact_dir, approved_commands=approved
        ),
        artifact_root=artifact_root,
        browser_enabled=config.run.browser_enabled,
        execution_budget_s=config.scenario_generation.execution_budget_s,
        max_parallel=config.scenario_generation.max_parallel,
        emit=note,
    )
    result = await executor.run(suite, selection_reason="explicit replay")
    store.write_json("replay/suite-result.json", result.model_dump(mode="json"))

    out("")
    out(result.summary_block())
    # The replayed plan carries its own risk register, so the replay is held to
    # the same coverage standard as the run that produced it.
    verdict = evaluate_gate(result, risks=plan.risks)
    out("")
    out(verdict.summary_block())
    # This replay deliberately runs the generated half of the suite and not the
    # permanent base scenario, which can take hours. Any risk whose evidence
    # lives in the base scenario's reviewed `verifies:` block therefore shows as
    # a gap here — correctly, because that evidence was not produced in this
    # execution — and saying so is the difference between a legible partial
    # result and a misleading one.
    withheld = sorted(
        {
            risk.risk_category
            for risk in verdict.uncovered_risks
            if risk.risk_category in base.declared_risk_categories()
        }
    )
    if withheld:
        out("")
        out(
            f"NOTE — {len(withheld)} of the gap(s) above are risks the base scenario "
            f"{base.name!r} declares it verifies ({', '.join(withheld)}). This replay "
            "did not run it, so that evidence was not produced. Run the full suite to "
            "settle them; nothing here is a claim that they failed."
        )
    out(f"\nevidence: {artifact_root}")
    return 20 if verdict.blocks_acceptance else 0


def _approved_commands(config: DriverConfig, base: Scenario) -> Any:
    from .scenario_validation import ApprovedCommands

    return ApprovedCommands.from_sources(
        scenarios=[*_permanent_scenarios(config), base],
        configured=config.scenario_generation.approved_commands,
    )


def _approved_commands_from_scenarios(config: DriverConfig) -> Any:
    """The approved set without a base scenario — what ``doctor`` reports."""
    from .scenario_validation import ApprovedCommands

    return ApprovedCommands.from_sources(
        scenarios=_permanent_scenarios(config),
        configured=config.scenario_generation.approved_commands,
    )


async def cmd_scenarios_promotion_candidates(args: argparse.Namespace) -> int:
    """List the generated scenarios a run suggests for permanent coverage."""
    config = _config_from_args(args)
    store = _open_run(config, args.run)
    if store is None:
        error(f"No runs found under {config.runs_dir}. Pass --run <run-id>.")
        return 2

    candidates = PromotionLedger(store.run_dir).load()
    if getattr(args, "as_json", False):
        print(json.dumps([c.model_dump(mode="json") for c in candidates], indent=2, default=str))
        return 0

    header(f"PROMOTION CANDIDATES — run {store.run_id}")
    if not candidates:
        out(
            "None. A generated scenario becomes a candidate only when it failed while a\n"
            "real defect was present and passed after the fix."
        )
        return 0

    for candidate in candidates:
        out(f"\n{candidate.scenario_id}  [{candidate.priority} {candidate.risk_category}]")
        out(f"  {candidate.title}")
        out(f"  found a defect in iteration {candidate.discovered_in_iteration}: "
            f"{candidate.bug_discovered[:200]}")
        out(f"  passed in iteration {candidate.fixed_in_iteration}")
        out(f"  verifies: {candidate.requirement_reference}")
        out(f"  evidence: {candidate.evidence_path}")
        out(f"  status:   {'PROMOTED' if candidate.promoted else 'candidate only'}")

    note(
        "\nThese are suggestions. Nothing has been added to the permanent regression\n"
        "suite, and no run will ever add one on your behalf. To promote one:\n"
        f"  python -m neyma_product_driver scenarios promote --run {store.run_id} "
        "--scenario <id>"
    )
    return 0


async def cmd_scenarios_promote(args: argparse.Namespace) -> int:
    """Promote one candidate into the permanent suite. Asks first, always.

    The only path by which a generated scenario becomes a repository file. It
    shows the exact YAML, requires confirmation, and refuses to overwrite an
    existing scenario file.
    """
    config = _config_from_args(args)
    store = _open_run(config, args.run)
    if store is None:
        error(f"No runs found under {config.runs_dir}. Pass --run <run-id>.")
        return 2

    ledger = PromotionLedger(store.run_dir)
    candidates = ledger.load()
    candidate = next((c for c in candidates if c.scenario_id == args.scenario_id), None)
    if candidate is None:
        error(
            f"No promotion candidate {args.scenario_id!r} in run {store.run_id}. "
            "List them with 'scenarios promotion-candidates'."
        )
        return 2
    if candidate.promoted:
        out(f"{candidate.scenario_id} was already promoted.")
        return 0

    from .scenario_plan import GeneratedScenario, compile_to_scenario
    from .scenario_planner import scenario_to_yaml_mapping

    try:
        model = GeneratedScenario.model_validate(candidate.scenario)
    except Exception as exc:
        error(f"The recorded scenario could not be read back: {exc}")
        return 2

    try:
        base = load_scenario(config.scenario_path(args.scenario))
    except (FileNotFoundError, ValueError) as exc:
        error(str(exc))
        return 2

    approved = _approved_commands(config, base)
    allowed, refusals = approved.resolve(model.command_strings())
    if refusals:
        error("Refusing to promote: the scenario uses commands that are no longer approved.")
        for refusal in refusals:
            out(f"  - {refusal}")
        return 3

    try:
        compiled = compile_to_scenario(model, base=base, approved_commands=allowed)
    except Exception as exc:
        error(f"Refusing to promote: {exc}")
        return 3

    assert config.scenarios_dir is not None
    destination = config.scenarios_dir / f"{model.id}.yaml"
    if destination.exists():
        error(f"Refusing to overwrite an existing scenario file: {destination}")
        return 3

    import yaml as _yaml

    body = _yaml.safe_dump(
        scenario_to_yaml_mapping(model, compiled), sort_keys=False, default_flow_style=False
    )

    header("PROPOSED ADDITION TO THE PERMANENT REGRESSION SUITE")
    out(f"file: {destination}\n")
    out(body)
    out(
        "This scenario was generated by a run, not written by you. Read it before\n"
        "accepting it: once it is in the permanent suite, every future run treats a\n"
        "failure in it as blocking."
    )

    if args.yes:
        note("\n--yes given; writing.")
    elif not sys.stdin.isatty():
        error("\nRefusing to modify the permanent suite non-interactively. Re-run with --yes.")
        return 3
    else:
        reply = input("\nAdd this scenario to the permanent suite? Type 'yes': ").strip().lower()
        if reply != "yes":
            out("Aborted. The permanent suite is unchanged.")
            return 0

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(body, encoding="utf-8")
    for entry in candidates:
        if entry.scenario_id == candidate.scenario_id:
            entry.promoted = True
    ledger.save(candidates)

    good(f"\nPromoted {model.id} to {destination}")
    note("It is now permanent regression coverage. Commit it when you are ready.")
    return 0


def _journalled_commands(record: IterationRecord) -> list[tuple[CommandResult, str]]:
    """Every command this iteration actually recorded, with where it came from.

    The journal used to read ``record.commands``, a field ``IterationRecord`` has
    never had, so every run that did any work raised ``AttributeError`` on its
    first iteration and — because writing the journal is deliberately
    best-effort — produced no journal and no founder summary at all. A run with
    zero iterations never entered the loop, which is why the suite did not see it.

    The commands that genuinely exist in the run state are the ones the scenario
    executor ran: setup, the scenario body, and teardown. They are labelled as
    such. Calling them "builder" commands would replace a missing journal with a
    journal that misattributes its own evidence, which is worse.
    """
    scenario = getattr(record, "scenario", None)
    if scenario is None:
        return []
    out: list[tuple[CommandResult, str]] = []
    for source in ("setup", "commands", "teardown"):
        for command in getattr(scenario, source, None) or []:
            out.append((command, f"scenario:{source}"))
    return out


def _journal_the_outcome(
    journal: RunJournal,
    state: RunState,
    result: LoopResult | None,
    unit: Any,
    scenario: Scenario | None,
) -> None:
    """Hand the journal the run's FINAL records, so the plain-terms summary is
    rendered from them rather than from what the terminal happened to print.

    The founder summary used to be written from ``state`` alone, which holds no
    gate verdict and no evaluator decision. It could therefore describe what a
    run *did* and never what the run *established* — and a reader with only that
    document in front of them had no way to tell an accepted run from one that
    executed nothing. Everything copied here already exists as a record; none of
    it is computed at render time.
    """
    if unit is not None:
        journal.acceptance_criteria = [
            str(c.get("id") or c.get("criterion") or c) if isinstance(c, dict) else str(c)
            for c in (getattr(unit, "acceptance_criteria", None) or [])
        ]

    # What the run was asked for, beside the phase it sits inside. Copied from
    # the record the loop wrote before the builder started, so the founder
    # summary can never imply that accepting a unit moved its phase.
    scope = getattr(state, "task_scope", None) or {}
    if isinstance(scope, dict) and scope:
        journal.task_scope_id = str(scope.get("scope_id") or "")
        journal.parent_phase_id = str(scope.get("parent_phase_id") or "")
        journal.parent_phase_state = str(
            scope.get("parent_phase_execution_state")
            or scope.get("parent_phase_state")
            or ""
        )
        journal.scope_is_nested = (
            scope.get("level") == "TASK" and not scope.get("claims_phase_completion", True)
        )

    if result is None:
        journal.record_outcome(
            run_status=getattr(state.status, "value", state.status),
            unit=unit,
            scenario_name=scenario.name if scenario else state.scenario_name,
            scenario_phase=scenario.phase if scenario else "",
        )
        return

    if result.suite is not None:
        for outcome in result.suite.outcomes:
            journal.record_scenario_result(
                outcome.scenario_name or outcome.scenario_id,
                passed=(
                    getattr(outcome.outcome, "value", "") == "PASSED"
                    and bool(outcome.evidence_verified)
                ),
                detail=outcome.brief() if hasattr(outcome, "brief") else "",
            )

    # The review record, before the outcome, so a journal whose outcome copy
    # fails still carries the answer to "was this reviewed, and did the reviewer
    # measure anything itself".
    journal.record_independent_review(
        requirement=result.review_requirement,
        ledger=result.review_ledger,
        satisfying=result.satisfying_review,
        reviews=result.reviews,
        automatic=bool(result.reviews),
    )

    journal.record_outcome(
        run_status=getattr(result.status, "value", result.status),
        gate=result.gate,
        decision=result.final_decision,
        builder_claims=[r.builder_summary for r in state.iterations if r.builder_summary],
        reviews=[
            f"{getattr(r, 'verdict', '?')}: {len(getattr(r, 'blockers', []) or [])} blocker(s)"
            for r in result.reviews
        ],
        unit=unit,
        scenario_name=scenario.name if scenario else state.scenario_name,
        scenario_phase=scenario.phase if scenario else "",
    )

    # Unresolved material findings are what "still NOT built" means for a run:
    # blocking failures, unverified required coverage, and review blockers that
    # were never answered by a supported review.
    journal.incomplete = _unresolved_findings(result)

    decision = result.final_decision
    journal.record_stop(
        reason=(decision.summary if decision is not None and decision.summary
                else f"run ended {getattr(result.status, 'value', result.status)}"),
        next_safe_action=_next_safe_action(result),
        founder_decision_required=_founder_decision(result),
    )


def _next_safe_action(result: LoopResult) -> str:
    """One line, chosen by status. Never a list of options dressed as one move."""
    if result.status is RunStatus.REQUIRES_APPROVAL:
        return ("Read protocol-resolution.json in the run directory, then run "
                "`approve` or reject the option it proposes.")
    if result.status is RunStatus.NEEDS_USER:
        return "Answer the product or authority question the evaluator recorded, then resume."
    if result.status is RunStatus.NEEDS_INDEPENDENT_REVIEW:
        return ("Re-run with automatic review enabled — this task requires an independent "
                "review, the run normally takes one itself, and this one could not. "
                "`review --run <id>` inspects it by hand if you would rather.")
    if result.status is RunStatus.ACCEPTED and result.gate is not None and not result.gate.blocks_acceptance:
        return ("Read the diff yourself, then decide whether to commit and push it — the "
                "driver stops before every remote action, by design.")
    return ""


def _founder_decision(result: LoopResult) -> str:
    """The decision only the founder may make, when the run recorded one."""
    decision = result.final_decision
    if result.status is RunStatus.REQUIRES_APPROVAL:
        return ("A repository repair needs your authority before this run can finalize: "
                + (decision.summary if decision is not None and decision.summary
                   else "see protocol-resolution.json"))
    if result.status is RunStatus.NEEDS_USER:
        return (decision.summary if decision is not None and decision.summary
                else "The run stopped for a product or authority question.")
    return ""


def _write_run_journal(
    store: EvidenceStore,
    state: RunState,
    config: DriverConfig,
    authority_report: dict[str, Any] | None = None,
    result: LoopResult | None = None,
    unit: Any = None,
    scenario: Scenario | None = None,
) -> None:
    """Persist journal.json and FOUNDER-SUMMARY.md for the run.

    Run-journal evidence is acceptance evidence, so a run that produces none
    cannot be accepted. The control loop already records per-iteration evidence
    (git-status.txt, git-diff-stat.txt, commands.log, record.json); what was
    missing was the run-level journal and the founder summary that make the run
    reviewable without reading the raw logs.

    Derived from what was actually recorded — never fabricated. Best-effort by
    construction: failing to WRITE the journal must not destroy the run whose
    evidence it describes, and the integrity check will report its absence
    rather than passing silently.
    """
    try:
        journal = RunJournal(
            run_id=store.run_id,
            task=str(getattr(state, "task", "") or ""),
            repo=str(config.neyma_repo),
        )
        journal.record_start(config.neyma_repo)
        # Recorded so the review section can state, from this file alone, that
        # the reviewer was not the builder. A field the journal has always had
        # and nothing has ever populated is worse than no field.
        journal.builder_session_id = str(getattr(state, "builder_session_id", "") or "")
        journal.evaluator_session_id = str(getattr(state, "evaluator_session_id", "") or "")
        if authority_report:
            # Computed by the control loop, which held the before-snapshot. The
            # journal used to render an authority section that nothing ever
            # populated, so "did any authority file change?" answered "no"
            # whatever the run had done.
            journal.record_authority(authority_report)
        for record in state.iterations:
            for command, source in _journalled_commands(record):
                journal.record_command(
                    command.command,
                    exit_code=command.exit_code,
                    timed_out=command.timed_out,
                    duration_s=command.duration_s,
                    source=source,
                )
            if record.git and record.git.head_commit:
                journal.record_commit(
                    record.git.head_commit,
                    f"iteration {record.iteration}",
                    record.git.branch or "",
                )
        try:
            _journal_the_outcome(journal, state, result, unit, scenario)
        except Exception as exc:
            # Degrade the plain-terms section rather than destroying the whole
            # journal with it. The failure direction is safe by construction:
            # an outcome that could not be copied leaves `gate_status` empty,
            # and an empty gate status is not VERIFIED — so the summary
            # under-claims and says so, which is the only acceptable way for
            # this to fail.
            journal.incomplete.append(
                "the run's final outcome could not be journalled "
                f"({type(exc).__name__}: {redact(str(exc))}), so the plain-terms summary "
                "below is missing the gate verdict and states nothing as proven"
            )
        journal.record_end(config.neyma_repo)
        journal.save(store.run_dir)
    except Exception as exc:  # pragma: no cover - never fail a run on its own journal
        warn(f"could not write the run journal: {type(exc).__name__}: {redact(str(exc))}")


def _indent(msg: str) -> str:
    return "\n".join("  " + ln for ln in msg.rstrip().splitlines()) if msg.strip() else ""


def _report_coverage(result: LoopResult, store: EvidenceStore) -> None:
    """State what was verified — as coverage, never as a proof of correctness."""
    if result.suite is None:
        return
    header("VERIFIED COVERAGE")
    out(result.suite.summary_block())
    if result.promotion_candidates:
        out("")
        out(
            f"{len(result.promotion_candidates)} generated scenario(s) marked as "
            "regression-promotion candidates:"
        )
        for candidate in result.promotion_candidates:
            out(f"  - {candidate.scenario_id}: {candidate.title}")
        note(
            "  Nothing was added to the permanent suite. Review them with:\n"
            f"    python -m neyma_product_driver scenarios promotion-candidates "
            f"--run {store.run_id}"
        )
    out(f"\nscenario plan: {store.run_dir / 'scenario-plan.json'}")


def _report_founder_summary(result: LoopResult, store: EvidenceStore, config: DriverConfig) -> None:
    """The shipping report, written for the person paying for the product.

    This is what the founder reads. It leads with whether the change can be
    tried and whether it can be shipped, and it answers the questions actually
    worth a founder's attention: what the product can do now, what was
    exercised, what broke, what was fixed, what still fails, and what risk
    remains. Repository and protocol mechanics appear only where they genuinely
    block shipping — everything else that a run learns about the repository is
    in ``journal.json`` for whoever wants it.

    Every line is derived from what the run recorded. Where nothing was
    recorded, this says so rather than producing a confident blank.
    """
    state = result.state
    suite = result.suite
    gate = result.gate
    decision = result.final_decision

    failures = list(suite.blocking_failures()) if suite is not None else []
    passed = (
        sum(1 for o in suite.outcomes if getattr(o.outcome, "value", "") == "PASSED")
        if suite is not None
        else 0
    )
    executed = len(suite.outcomes) if suite is not None else 0
    verified = gate is not None and not gate.blocks_acceptance

    commit = _last_local_commit(config.neyma_repo)
    dirty = _tracked_dirty(config.neyma_repo)
    # A required review that nothing satisfied is not a detail. Nothing that
    # owes a review and does not have one is shippable, whatever the gate said.
    review_ok = (
        result.review_requirement is None
        or not getattr(result.review_requirement, "required", False)
        or result.satisfying_review is not None
    )
    shippable = (
        result.status is RunStatus.ACCEPTED
        and verified
        and review_ok
        and not failures
        and not dirty
    )

    header("READY TO SHIP" if shippable else "NOT READY TO SHIP")
    out(
        f"  behaviour verified:            "
        f"{'yes — ' + gate.headline() if gate is not None else 'no acceptance gate ran'}"
    )
    out(f"  scenarios:                     {passed} passed, {len(failures)} failed, "
        f"{max(0, executed - passed - len(failures))} not executed")
    tests = _recorded_test_commands(state)
    out(f"  tests run by the builder:      {tests or 'none recorded in this run'}")
    unresolved = _unresolved_findings(result)
    out(f"  unresolved material findings:  {len(unresolved)}")
    for finding in unresolved[:6]:
        out(f"      - {finding}")
    out(f"  independent review:            {_review_headline(result)}")
    out(f"  local commit:                  {commit or 'none created this run'}")
    if dirty:
        out(f"  uncommitted tracked changes:   {len(dirty.splitlines())} file(s)")
    out(f"  founder action required:       {_founder_action(result, shippable, dirty)}")

    header("FOR THE FOUNDER")
    out("1. What can Neyma do now that it could not before?")
    if decision is not None and decision.observed_behavior:
        for observed in decision.observed_behavior[:6]:
            out(f"     - {observed}")
    elif decision is not None and decision.summary:
        out(f"     {decision.summary}")
    else:
        out("     Nothing was recorded as newly working.")

    out("\n2. What real workflow did you exercise?")
    if suite is not None and suite.outcomes:
        for outcome in suite.outcomes[:10]:
            out(f"     - {outcome.scenario_name or outcome.scenario_id}")
    else:
        out(f"     - {state.scenario_name or '(none)'}")

    out(f"\n3. How many scenarios did you run?   {executed}"
        f" ({passed} passed, {len(failures)} failed)")

    out("\n4. What failures did you discover?")
    discovered = _discovered_failures(state)
    for item in discovered[:10] or ["     None — nothing failed at any point in this run."]:
        out(item if item.startswith("     ") else f"     - {item}")

    out("\n5. What did the builder fix?")
    fixes = [
        record.decision.summary
        for record in state.iterations
        if record.decision is not None and record.decision.decision is Decision.FIX
    ]
    for fix in fixes[:10] or ["     No corrections were needed."]:
        out(fix if fix.startswith("     ") else f"     - {fix}")

    out("\n6. What still fails?")
    for failure in [f.brief() for f in failures][:10] or ["     Nothing the suite exercised."]:
        out(failure if failure.startswith("     ") else f"     - {failure}")

    out("\n7. What consequential risks remain?")
    risks = _remaining_risks(result)
    for risk in risks[:10] or ["     None identified by this run."]:
        out(risk if risk.startswith("     ") else f"     - {risk}")

    out(f"\n8. Is it ready for you to try?      "
        f"{'Yes.' if verified and not failures else 'Not yet — see 6 and 7.'}")
    out(f"9. Is it ready to push or merge?    {_push_readiness(result, shippable, dirty, commit)}")

    out("\n10. What should we build next?")
    for suggestion in _next_steps(result)[:8] or ["     Nothing outstanding was recorded."]:
        out(suggestion if suggestion.startswith("     ") else f"     - {suggestion}")

    # Repository mechanics, only where they block. Everything else recorded is
    # in journal.json; putting it here is what buried the product report.
    if result.status is RunStatus.REQUIRES_APPROVAL:
        header("THIS NEEDS YOUR AUTHORITY")
        if decision is not None:
            out(decision.summary)
            for problem in decision.problems[:8]:
                out(f"  - {problem}")
    elif result.protocol_diagnostics:
        note(
            f"\n{len(result.protocol_diagnostics)} repository/protocol observation(s) were "
            "recorded and did not block this run. They are in journal.json."
        )

    out(f"\nrun artifacts: {store.run_dir}")


def _review_headline(result: LoopResult) -> str:
    """One line: was a review required, did it run, what did it say, did it measure.

    Rendered from the run's records. A required review that produced no verdict
    says so in the same place a supported one would, so the line cannot be read
    as reassurance by a reader who does not know what to look for.
    """
    requirement = result.review_requirement
    if requirement is None or not getattr(requirement, "required", False):
        return "not required for this task"
    if not result.reviews:
        return "REQUIRED — and no reviewer produced a verdict"
    last = result.reviews[-1]
    basis = (
        "reviewer-reproduced runtime evidence"
        if getattr(last, "reproduced_runtime_evidence", False)
        else "corroborated from this run's records, not reviewer-reproduced"
    )
    satisfied = (
        "satisfies this task"
        if result.satisfying_review is not None
        else "does NOT satisfy this task"
    )
    return f"required — {last.verdict}, {satisfied} ({basis})"


def _last_local_commit(repo: Path) -> str:
    """The current HEAD, described. Read-only, and never assumes it is this run's."""
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--pretty=%h %s"],
            cwd=str(repo), capture_output=True, text=True, timeout=20, check=False,
        )
        return redact(proc.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return ""


def _recorded_test_commands(state: RunState) -> str:
    """How many test commands this run's scenarios actually executed."""
    total = 0
    for record in state.iterations:
        for command, _source in _journalled_commands(record):
            if re.search(r"(?i)\b(?:pytest|unittest|npm (?:run )?test|go test|cargo test)\b",
                         command.command or ""):
                total += 1
    return f"{total} test command(s)" if total else ""


def _unresolved_findings(result: LoopResult) -> list[str]:
    """Material findings nothing in this run resolved.

    Blocking scenario failures, unverified required coverage, and any review
    finding that was never followed by a supported review. Deliberately not
    every note the run took: a founder reading "unresolved findings: 0" must be
    able to trust that it means what it says.
    """
    findings: list[str] = []
    if result.suite is not None:
        findings += [f.brief() for f in result.suite.blocking_failures()]
    if result.gate is not None and result.gate.blocks_acceptance:
        findings += [c.brief() for c in result.gate.unverified]
        findings += [r.brief() for r in result.gate.uncovered_risks]
    if result.reviews and result.reviews[-1].verdict != "SUPPORTED":
        findings += [f"[{f.severity}] {f.finding}" for f in result.reviews[-1].blockers]
    requirement = result.review_requirement
    if requirement is not None and getattr(requirement, "required", False):
        if not result.reviews:
            findings.append(
                "an independent review is required for this task and none produced a verdict"
            )
        elif result.satisfying_review is None:
            findings.append(
                "the independent review this task requires is not satisfied: "
                f"the last review returned {result.reviews[-1].verdict}"
            )
    return findings


def _discovered_failures(state: RunState) -> list[str]:
    """Everything that failed at any point, including what was later fixed."""
    seen: list[str] = []
    for record in state.iterations:
        for outcome in (record.suite or {}).get("outcomes", []) or []:
            if str(outcome.get("outcome", "")).upper() == "FAILED":
                label = f"{outcome.get('scenario_name') or outcome.get('scenario_id')}"
                detail = (outcome.get("error") or "").strip()
                entry = f"iteration {record.iteration}: {label}" + (f" — {detail[:160]}" if detail else "")
                if entry not in seen:
                    seen.append(entry)
    return seen


def _remaining_risks(result: LoopResult) -> list[str]:
    risks: list[str] = []
    if result.risk is not None and getattr(result.risk, "surfaces", None):
        risks.append(
            f"this change touches {', '.join(result.risk.surfaces)} — "
            + (
                f"reviewed independently ({result.reviews[-1].verdict})"
                if result.reviews
                else "no independent review was run"
            )
        )
    if result.gate is not None:
        risks += [f"identified but never verified: {r.brief()}" for r in result.gate.uncovered_risks]
    if result.risk is not None and getattr(result.risk, "weakened_controls", None):
        risks += [f"a mandatory control was weakened: {w}" for w in result.risk.weakened_controls]
    return risks


def _next_steps(result: LoopResult) -> list[str]:
    steps: list[str] = []
    if result.suite is not None:
        steps += [f"fix: {f.brief()}" for f in result.suite.blocking_failures()][:5]
    if result.gate is not None:
        steps += [f"verify: {r.brief()}" for r in result.gate.uncovered_risks][:5]
    for candidate in result.promotion_candidates[:5]:
        steps.append(
            f"consider promoting {candidate.scenario_id} into the permanent suite "
            f"({candidate.title})"
        )
    if result.final_decision is not None and result.final_decision.decision is Decision.ASK_USER:
        steps.append(f"decide: {result.final_decision.summary}")
    return steps


def _founder_action(result: LoopResult, shippable: bool, dirty: str) -> str:
    if result.status is RunStatus.REQUIRES_APPROVAL:
        return "approve or refuse a repository repair that needs your authority"
    if result.status is RunStatus.NEEDS_USER:
        return "answer the product or authority question below"
    if result.status is RunStatus.NEEDS_INDEPENDENT_REVIEW:
        return "this task requires an independent review and none could be taken"
    if shippable:
        return "push / merge"
    if dirty:
        return "none yet — the work is not committed"
    return "none yet — the run did not reach a verified acceptance"


def _push_readiness(result: LoopResult, shippable: bool, dirty: str, commit: str) -> str:
    if shippable:
        return f"Yes — local commit {commit or '(none)'}; push and merge are yours to perform."
    if result.status is RunStatus.ACCEPTED and dirty:
        return "Not yet — the accepted work is still uncommitted in the working tree."
    return "No — see the unresolved findings above."


def _report_outcome(result: LoopResult, store: EvidenceStore) -> None:
    header("RESULT")
    d = result.final_decision

    if result.audit is not None:
        out(result.audit.summary_block())
        out("")

    if result.status is RunStatus.REQUIRES_APPROVAL and result.protocol is not None:
        resolution = result.protocol
        warn("REQUIRES APPROVAL — repository governance blocks finalization\n")
        out(resolution.render_report(run_id=store.run_id))
        if resolution.approval_prompt:
            out("")
            out(resolution.approval_prompt)
        out(
            "\nThis is neither a failure nor a completion. The implementation is not in "
            "question here;\nthe repository's own rules are what block it. Nothing has been "
            "changed."
        )
        out(f"\nrun artifacts: {store.run_dir}")
        return

    if result.status is RunStatus.NEEDS_INDEPENDENT_REVIEW:
        warn("IMPLEMENTED — THE REQUIRED INDEPENDENT REVIEW DID NOT RUN\n")
        out(
            "The implementation stands and the product evaluation reached ACCEPT, but this\n"
            "task requires one focused independent review and none produced a usable\n"
            "verdict. A run normally takes that review itself, as a step inside its own\n"
            "loop; it reaches this state only when review was switched off, the reviewer\n"
            "session failed, or what came back was not from an independent session."
        )
        requirement = result.review_requirement
        if requirement is not None and requirement.required:
            out("\nWhy a review is required here:")
            for reason in requirement.reasons[:6]:
                out(f"  - {reason}")
            if requirement.sources:
                out(f"  read from: {', '.join(requirement.sources[:4])}")
        elif result.risk is not None and result.risk.surfaces:
            out("\nWhy a review is required here:")
            for surface in result.risk.surfaces:
                out(f"  - {surface}")
        # What the deterministic gate said, rather than an assertion nobody
        # computed. This text used to state flatly that "the product evaluation
        # passed" on a path that returned before the gate ran at all, so it
        # could be printed over a required scenario that had just failed.
        if result.gate is not None:
            out("")
            for line in result.gate.summary_block().splitlines():
                out(line)
        elif result.suite is None:
            note(
                "\nNo scenario suite ran in this configuration, so the deterministic "
                "acceptance gate was not applied to this outcome."
            )
        audit = result.audit
        if audit is not None:
            pending = audit.observed_state.progress.independent_pending
            if pending:
                out("\nRequires a session other than the implementing one:")
                for name in pending:
                    out(f"  - {name}")
        out(
            "\nThis is neither a failure nor a completion. Re-run with automatic review\n"
            "enabled and the loop takes the review itself. To look at it by hand instead:\n"
            f"  python -m neyma_product_driver review --run {store.run_id}"
        )
        out(f"\nrun artifacts: {store.run_dir}")
        return

    if result.status is RunStatus.ACCEPTED:
        good("ACCEPTED — the observed product behaviour was judged good enough.")
        if d:
            out(d.summary)
        out(f"\naccepted evidence: {store.run_dir / 'accepted'}")
    elif result.status is RunStatus.NEEDS_USER:
        warn("ASK_USER — a product decision is needed from you.\n")
        if d:
            out(d.summary)
            if d.observed_behavior:
                out("\nObserved:")
                for o in d.observed_behavior:
                    out(f"  - {o}")
            if d.evidence_paths:
                out("\nEvidence:")
                for p in d.evidence_paths:
                    out(f"  - {p}")
    elif result.status is RunStatus.BLOCKED:
        error("BLOCKED\n")
        if d:
            out(d.summary)
            for p in d.problems:
                out(f"  - {p}")
    elif result.status is RunStatus.MAX_ITERATIONS:
        warn(f"MAX ITERATIONS reached ({result.state.max_iterations}) without acceptance.")
        if d:
            out(f"\nLast decision was FIX: {d.summary}")
            for p in d.problems:
                out(f"  - {p}")
    elif result.status is RunStatus.STOPPED:
        out("STOPPED by request.")

    out(f"\nrun artifacts: {store.run_dir}")
    out(f"resume with:   python -m neyma_product_driver run --resume-run {store.run_id}")


def _exit_code_for(status: RunStatus) -> int:
    return {
        RunStatus.ACCEPTED: 0,
        RunStatus.NEEDS_USER: 10,
        RunStatus.BLOCKED: 11,
        RunStatus.MAX_ITERATIONS: 12,
        RunStatus.STOPPED: 13,
        RunStatus.NEEDS_INDEPENDENT_REVIEW: 14,
        RunStatus.REQUIRES_APPROVAL: 15,
        RunStatus.ERROR: 1,
        RunStatus.RUNNING: 1,
    }[status]


def _git_ignored(repo: Path, rel: str) -> bool:
    """True when ``rel`` is git-ignored in ``repo`` (so writing it is invisible)."""
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", rel],
            cwd=str(repo),
            capture_output=True,
            timeout=15,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _tracked_dirty(repo: Path) -> str:
    """Porcelain status of TRACKED files only (ignored/untracked scratch excluded)."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return proc.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


async def _check_builder_write_capability(repo: Path) -> tuple[bool, str]:
    """Prove a builder-style session can create, edit, read and remove a
    disposable, git-ignored scratch file — without an interactive callback and
    without touching any tracked Neyma file.

    This is the read-only-safe reproduction of the P4 run's first failure, where
    every Write/Edit was denied for want of a human approval the harness could
    not give. It exercises the real builder permission surface — the working set
    is in ``allowed_tools`` (auto-approved, no human in the loop) and the
    PreToolUse hook does not deny it — then performs the actual filesystem
    operations at an ignored path.
    """
    from .builder import BuilderSession, classify_tool_use
    from .config import BuilderConfig

    if not (repo / ".git").exists():
        return False, "not a git repository; cannot prove an ignored scratch path"

    before = _tracked_dirty(repo)

    # An ignored path so the probe never becomes a tracked change. Verified with
    # git, never assumed. `.pytest_tmp/` is a temp dir; the `*.db` fallback is
    # ignored by extension anywhere in the tree.
    candidates = [
        f".pytest_tmp/neyma-driver-writecheck-{os.getpid()}.txt",
        f".neyma-driver-writecheck-{os.getpid()}.tmp.db",
    ]
    rel = next((c for c in candidates if _git_ignored(repo, c)), "")
    if not rel:
        return False, "no git-ignored scratch path available to probe with"

    scratch = repo / rel

    # 1. A builder-style session must AUTONOMOUSLY service writing this path.
    #    Write/Edit are in allowed_tools (auto-approved with no human in the
    #    loop), and the PreToolUse enforcement hook must NOT deny it.
    config = BuilderConfig()
    if not ({"Write", "Edit"} <= set(config.allowed_tools)):
        return False, "Write/Edit are not in the builder's allowed_tools (would need approval)"
    if classify_tool_use("Write", {"file_path": rel}) is not None:
        return False, f"the builder would refuse to write {rel} (classified as a hard block)"

    session = BuilderSession(repo, config)

    for tool in ("Write", "Edit"):
        decision = await session._pre_tool_use_hook(
            {"tool_name": tool, "tool_input": {"file_path": rel}}, None, None
        )
        if decision:  # a non-empty hook result is a deny
            return False, (
                f"the builder's PreToolUse hook denied {tool} on {rel} "
                f"({decision}) — this ordinary edit must run autonomously"
            )

    # 2. Actually create → edit → read → remove the ignored scratch file.
    try:
        scratch.parent.mkdir(parents=True, exist_ok=True)
        scratch.write_text("neyma-driver write probe: create\n", encoding="utf-8")
        if not scratch.exists():
            return False, f"could not create {rel}"
        scratch.write_text("neyma-driver write probe: edited\n", encoding="utf-8")
        readback = scratch.read_text(encoding="utf-8")
        if "edited" not in readback:
            return False, f"edit to {rel} did not read back"
        scratch.unlink()
        if scratch.exists():
            return False, f"could not remove {rel}"
    except OSError as exc:
        return False, f"filesystem probe on {rel} failed: {exc}"
    finally:
        # Never leave the probe behind, even on an early return above.
        try:
            if scratch.exists():
                scratch.unlink()
        except OSError:
            pass

    # 3. No tracked file may have changed.
    if _tracked_dirty(repo) != before:
        return False, "the probe changed the tracked working tree (it must not)"

    return True, f"create/edit/read/remove OK at ignored {rel}; no tracked file touched"


async def cmd_calibrate(args: argparse.Namespace) -> int:
    """Read-only preflight against the target repository.

    Writes nothing, starts no Claude session, and runs only read-only git
    inspection. Intended as the first thing you run before trusting the driver
    unattended: it shows exactly what the driver derives from the repository, so
    a disagreement with your own reading surfaces before a run, not during one.

    Exit codes: 0 calibrated cleanly; 2 the repository could not be read;
    10 calibration succeeded but a founder decision is required first.
    """
    from .calibration import calibrate

    config = _config_from_args(args)
    report = calibrate(config.neyma_repo)

    if getattr(args, "as_json", False):
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(report.render())

    if report.problems:
        return 2
    if report.founder_decision_required:
        return 10
    return 0


async def cmd_doctor(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    header("NEYMA PRODUCT DRIVER — DOCTOR")

    failures = 0
    warnings = 0

    def check(label: str, ok: bool, detail: str = "", fatal: bool = True) -> None:
        nonlocal failures, warnings
        if ok:
            good(f"  PASS  {label}" + (f" — {detail}" if detail else ""))
        elif fatal:
            failures += 1
            error(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))
        else:
            warnings += 1
            warn(f"  WARN  {label}" + (f" — {detail}" if detail else ""))

    out("\nEnvironment")
    v = sys.version_info
    check(f"Python {v.major}.{v.minor}.{v.micro}", v >= (3, 11), "requires 3.11+")

    try:
        import claude_agent_sdk  # noqa: F401

        check("claude-agent-sdk import", True, getattr(claude_agent_sdk, "__version__", "installed"))
    except ImportError as exc:
        check("claude-agent-sdk import", False, str(exc))

    claude_bin = shutil.which("claude")
    check("Claude Code CLI on PATH", claude_bin is not None, claude_bin or "not found", fatal=False)
    if claude_bin:
        try:
            proc = subprocess.run([claude_bin, "--version"], capture_output=True, text=True, timeout=20)
            check("Claude Code version", proc.returncode == 0, proc.stdout.strip(), fatal=False)
        except (OSError, subprocess.SubprocessError) as exc:
            check("Claude Code version", False, str(exc), fatal=False)

    out("\nAuthentication")
    if api_key_present():
        check(
            "ANTHROPIC_API_KEY",
            False,
            "SET — API-key billing will take precedence over your subscription",
            fatal=False,
        )
    else:
        good("  PASS  ANTHROPIC_API_KEY is not set (subscription auth will be used)")
    authed, detail = _check_claude_auth(claude_bin)
    check("Claude authentication", authed, detail, fatal=False)

    out("\nFounder context")
    base = founder_dir(config.driver_root)
    for name in ("PRODUCT_OWNER_CONTEXT.md", "PRODUCT_TASTE_RUBRIC.yaml"):
        check(f"{name} present", (base / name).exists(), str(base / name))
    try:
        founder = load_founder_context(config.driver_root)
    except ContextResolutionError as exc:
        check("founder context parses", False, str(exc))
    else:
        check(
            "founder context parses",
            True,
            f"version {founder.version}, {len(founder.category_ids)} categories",
        )
        check(
            "rubric confidence thresholds",
            0.0 < founder.minimum_confidence_for_fix <= 1.0,
            f"minimum_for_fix={founder.minimum_confidence_for_fix}, "
            f"customer_facing={founder.minimum_confidence_for_customer_facing_fix}",
        )
        check(
            "rubric defines vague-correction phrases",
            len(founder.vague_phrases) >= 5,
            f"{len(founder.vague_phrases)} phrases",
            fatal=False,
        )

    out("\nNeyma repository")
    repo_problems = config.validate_repo()
    check(f"repository at {config.neyma_repo}", not repo_problems, "; ".join(repo_problems))
    if not repo_problems:
        git = git_snapshot(config.neyma_repo)
        check(f"branch: {git.branch or '(detached)'}", bool(git.branch), f"HEAD {git.head_commit}", fatal=False)
        check(
            f"working tree: {git.dirty_file_count} modified file(s)",
            True,
            "dirty trees are expected mid-phase",
            fatal=False,
        )
        for name in ("CLAUDE.md", ".claude"):
            p = config.neyma_repo / name
            check(f"{name} present", p.exists(), str(p), fatal=(name == "CLAUDE.md"))

        write_ok, write_detail = await _check_builder_write_capability(config.neyma_repo)
        check("builder can write (ignored scratch probe)", write_ok, write_detail)

        unit = RepositoryContextLoader(config.neyma_repo).resolve_active_unit_optional()
        if not unit.is_declared:
            # Not a failure. A repository is entitled to declare no unit; the
            # driver then works to the founder's task. Reported so an operator
            # who *expected* one can see that it is not being read.
            check(
                "active unit declared by the repository",
                True,
                f"none declared — the task is the authority ({unit.resolution_problem})",
            )
        else:
            pending = [
                c.get("criterion")
                for c in unit.acceptance_criteria
                if str(c.get("result", "")).upper() == "PENDING"
            ]
            check(
                "exactly one READY unit resolvable",
                True,
                f"{unit.unit_id} ({unit.status}) — {len(unit.acceptance_criteria)} criteria, "
                f"{len(pending)} PENDING",
            )

            out("\nCompletion evidence")
            auditor = CompletionAuditor(config.neyma_repo)
            progress = auditor.weighted_progress(unit)
            check(
                f"verified progress for {unit.unit_id}",
                True,
                f"{progress.percent:.0f}% (ceiling without independent review: "
                f"{progress.self_awardable_ceiling_percent:.0f}%)",
                fatal=False,
            )
            state = auditor.observe(unit)
            for r in state.receipts:
                check(
                    f"{r.name} receipt",
                    r.exists and r.passed and r.matches_head,
                    r.detail[:110],
                    fatal=False,
                )
            audit = auditor.audit("", unit=unit)
            check(
                "repository status surfaces are self-consistent",
                not audit.contradictions,
                f"{len(audit.contradictions)} contradiction(s); run "
                "'neyma-product-driver audit' for detail",
                fatal=False,
            )

        out("\nRepository protocol / topology")
        resolution = ProtocolResolver(config.neyma_repo).resolve()
        topo = resolution.topology
        content = next((c for c in topo.commits if c.is_content), None) if topo else None
        metadata = next(
            (c for c in reversed(topo.commits) if c.is_status), None
        ) if topo else None
        if content:
            check(f"content commit: {content.short}", True, content.role.value, fatal=False)
        if metadata:
            check(f"metadata commit: {metadata.short}", True, metadata.role.value, fatal=False)
        if topo:
            for r in topo.receipts:
                if r.exists:
                    check(
                        f"{r.name} bound to {r.recorded_commit[:12]} / tree {r.recorded_tree[:12]}",
                        r.fresh,
                        r.fresh_reason or r.detail[:100],
                        fatal=False,
                    )
        check(
            f"topology: {resolution.status.value}",
            resolution.status is ProtocolStatus.CONSISTENT,
            resolution.next_safe_action,
            fatal=False,
        )

    out("\nBrowser testing")
    try:
        import playwright  # noqa: F401

        check("playwright import", True, "")
        ok, detail = await _check_chromium()
        check("chromium browser", ok, detail, fatal=False)
    except ImportError:
        check("playwright import", False, "pip install playwright", fatal=False)

    out("\nRequired binaries")
    for binary in ("git", "python3"):
        path = shutil.which(binary)
        check(binary, path is not None, path or "not found")
    for binary in ("sqlite3", "node"):
        path = shutil.which(binary)
        check(binary, path is not None, path or "not found", fatal=False)

    out("\nRun artifacts")
    assert config.runs_dir is not None
    ok, detail = check_writable(config.runs_dir)
    check("run-artifact directory writable", ok, detail)

    out("\nScenarios")
    assert config.scenarios_dir is not None
    found = sorted(config.scenarios_dir.glob("*.y*ml")) if config.scenarios_dir.exists() else []
    check(
        f"{len(found)} scenario file(s) in {config.scenarios_dir}",
        bool(found),
        ", ".join(p.stem for p in found),
        fatal=False,
    )
    for path in found:
        try:
            load_scenario(path)
        except Exception as exc:
            check(f"scenario {path.stem} parses", False, str(exc), fatal=False)
        else:
            good(f"  PASS  scenario {path.stem} parses")

    out("\nGenerated scenarios")
    generation = config.scenario_generation
    check(
        "scenario generation",
        True,
        "enabled" if generation.enabled else "disabled (opt in with --auto-scenarios)",
        fatal=False,
    )
    approved = _approved_commands_from_scenarios(config)
    # The approved set is the whole safety story for generated commands: a
    # generated scenario can never author shell, only choose from here. An empty
    # set is not an error — it means generated scenarios get HTTP, browser,
    # ordering, concurrency and restarts, and no commands at all.
    check(
        f"approved command set: {len(approved)} command(s)",
        True,
        ", ".join(list(approved.entries)[:3]) or "none — generated scenarios may run no commands",
        fatal=False,
    )
    check(
        "generated-scenario budgets",
        generation.max_total_scenarios >= generation.max_initial_scenarios,
        f"{generation.max_initial_scenarios} initial, "
        f"{generation.max_adaptive_scenarios_per_wave}/adaptive wave, "
        f"{generation.max_waves} waves, {generation.max_total_scenarios} total",
        fatal=False,
    )
    check(
        "promotion into the permanent suite requires a human",
        generation.promotion_requires_approval,
        "generated scenarios are never written to scenarios/ by a run",
        fatal=False,
    )

    check(
        "generated scenarios run sequentially",
        generation.max_parallel == 1,
        "scenarios share services, ports and a workspace; isolation is not yet provable",
        fatal=False,
    )

    out("\nIndependent review")
    review = config.review
    check(
        "review is taken inside the run",
        review.automatic,
        "the run launches the review a scoped task requires; you relay nothing"
        if review.automatic
        else "OFF — a task that requires a review will stop at "
        "NEEDS_INDEPENDENT_REVIEW instead of taking one",
        fatal=False,
    )
    # The reviewer's own capability, reported beside the vocabulary it draws on,
    # because "the reviewer can verify" and "there is something for it to run"
    # are two different facts and only the pair is useful.
    policy = _reviewer_command_policy(config, Scenario(name="(none)"))
    check(
        "the reviewer can reproduce runtime evidence itself",
        policy is not None,
        (
            f"read-only verification plus {len(approved)} deterministic command(s) this "
            f"repository declares; budget {review.reviewer_max_commands}"
            if policy is not None
            else "OFF — reviews will rest on records this harness collected rather than "
            "on anything the reviewer reproduced"
        ),
        fatal=False,
    )
    check(
        "the reviewer cannot change the product",
        True,
        "writes, git state changes, pushes, merges, deploys, installs, network and "
        "secret reads are refused by a PreToolUse hook whatever else is configured",
        fatal=False,
    )
    check(
        "a review is bound to one exact tree",
        True,
        "HEAD + tree + working-tree digest; a later change retires it and a new "
        "reviewer is taken",
        fatal=False,
    )

    header("SUMMARY")
    if failures:
        error(f"{failures} failure(s), {warnings} warning(s) — fix the failures before running.")
        return 1
    if warnings:
        warn(f"0 failures, {warnings} warning(s) — you can run, but read the warnings.")
        return 0
    good("All checks passed.")
    return 0


def _check_claude_auth(claude_bin: str | None) -> tuple[bool, str]:
    """Report whether Claude Code appears authenticated, without reading tokens."""
    if claude_bin is None:
        return False, "Claude Code CLI not found"
    # Presence of a credentials store is checked by existence only. The driver
    # never opens, reads, prints or persists credential material.
    candidates = [
        Path.home() / ".claude" / ".credentials.json",
        Path.home() / ".config" / "claude" / ".credentials.json",
    ]
    if any(p.exists() for p in candidates):
        return True, "credential store present (contents never read)"
    if sys.platform == "darwin":
        try:
            proc = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0:
                return True, "macOS Keychain entry present (contents never read)"
        except (OSError, subprocess.SubprocessError):
            pass
    return False, "no credential store found — run 'claude' once to log in"


async def _check_chromium() -> tuple[bool, str]:
    """Async probe — doctor runs inside an event loop, so the sync API is unusable."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False, "playwright not installed"
    try:
        async with async_playwright() as pw:
            path = pw.chromium.executable_path
            if path and Path(path).exists():
                return True, path
            return False, "not installed — run: playwright install chromium"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc} — try: playwright install chromium"


async def cmd_status(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    assert config.runs_dir is not None

    store = (
        EvidenceStore.open_run(config.runs_dir, args.run_id)
        if args.run_id
        else EvidenceStore.latest_run(config.runs_dir)
    )
    if store is None:
        out(f"No runs found under {config.runs_dir}")
        return 0

    state = store.load_state()
    if state is None:
        error(f"Run {store.run_id} has no readable state.")
        return 1

    header(f"RUN {state.run_id}")
    out(f"status:     {state.status.value}")
    out(f"created:    {state.created_at}")
    out(f"updated:    {state.updated_at}")
    out(f"repo:       {state.neyma_repo}")
    out(f"scenario:   {state.scenario_name}")
    out(f"iterations: {state.iteration} / {state.max_iterations}")
    out(f"builder session:   {state.builder_session_id or '(none)'}")
    out(f"evaluator session: {state.evaluator_session_id or '(none)'}")
    if state.stop_requested or store.stop_requested():
        warn("stop has been requested for this run")

    if state.iterations:
        out("\nIterations:")
        for rec in state.iterations:
            d = rec.decision
            verdict = d.decision.value if d else "(no decision)"
            scen = "PASS" if (rec.scenario and rec.scenario.passed) else "FAIL"
            out(f"  {rec.iteration:>2}. scenario={scen}  decision={verdict}  {rec.timestamp}")
            if d and d.problems:
                for p in d.problems[:3]:
                    out(f"        - {p}")

    if state.final_decision:
        out(f"\nfinal decision: {state.final_decision.decision.value}")
        out(f"  {state.final_decision.summary}")

    out(f"\nartifacts: {store.run_dir}")
    return 0


async def cmd_evaluate(args: argparse.Namespace) -> int:
    """Run the scenario and evaluate it once, without touching the builder.

    The evaluator's verdict is a recommendation here exactly as it is in the
    loop: what the scenario *measured* passes through the same authoritative
    gate first. This command used to write whatever the evaluator said, so a
    required scenario could fail, the evaluator could return ACCEPT, and
    ACCEPTED was persisted and exited 0 — no gate had ever been consulted,
    because the gate was only wired into the loop's suite branch. It runs the
    selected scenario as a one-entry suite for that reason: the suite is what
    writes and verifies per-case evidence, and the gate is what reads it.
    """
    config = _config_from_args(args)
    problems = config.validate_repo()
    if problems:
        for p in problems:
            error(p)
        return 2
    if not _preflight_api_key(config):
        return 3

    try:
        scenario = load_scenario(config.scenario_path(args.scenario))
    except (FileNotFoundError, ValueError) as exc:
        error(str(exc))
        return 2

    if scenario.mode == "browser":
        config.run.browser_enabled = True

    assert config.runs_dir is not None
    run_id = f"{new_run_id()}-evaluate"
    store = EvidenceStore(config.runs_dir, run_id)
    state = RunState(
        run_id=run_id,
        neyma_repo=str(config.neyma_repo),
        scenario_name=scenario.name,
        task=args.task or config.task or "(evaluate-only: no builder task)",
        max_iterations=1,
    )

    header("EVALUATE (no builder session)")
    out(f"scenario: {scenario.name} ({scenario.mode})")
    out(f"run:      {store.run_dir}")

    suite = build_suite(permanent=[(scenario.name, scenario)])
    executor = SuiteExecutor(
        make_executor=lambda artifact_dir: ScenarioExecutor(
            config.neyma_repo, config.run, artifact_dir
        ),
        artifact_root=store.iteration_dir(1),
        browser_enabled=config.run.browser_enabled,
        run_id=run_id,
        iteration=1,
        emit=out,
    )
    out("→ running scenario...")
    suite_result = await executor.run(
        suite, selection_reason="evaluate-only: the selected scenario"
    )
    result = _primary_result(scenario, executor, suite_result)
    for line in suite_result.headline().splitlines():
        out(f"  {line}")

    git = git_snapshot(config.neyma_repo)

    try:
        founder = load_founder_context(config.driver_root)
        repo_context = RepositoryContextLoader(config.neyma_repo).load(
            topics=["product", "architecture", "acceptance"]
        )
    except ContextResolutionError as exc:
        error(f"BLOCKED — {exc}")
        return 11

    out(f"unit:     {repo_context.active_unit.unit_id} ({repo_context.active_unit.status})")

    from .evaluator import EvaluatorSession

    feedback_store = FounderFeedbackStore(store.run_dir)
    prompt = evaluator_prompt(
        task=state.task,
        iteration=1,
        max_iterations=1,
        builder_summary="(no builder ran; this is an evaluate-only pass)",
        git=git,
        scenario=result,
        service_logs=executor.service_logs,
        evidence_dir=str(store.iteration_dir(1)),
        founder=founder,
        repo_context=repo_context,
        founder_feedback=feedback_store.render(),
    )
    provenance = _build_provenance(
        founder=founder, repo_context=repo_context, git=git, scenario_result=result,
        store=store, iteration=1, feedback_count=len(feedback_store.load()),
        prompt_chars=len(prompt),
    )
    store.save_prompt_manifest(1, provenance.model_dump(mode="json"), prompt)

    out("→ evaluating...")
    async with EvaluatorSession(config.neyma_repo, config.evaluator) as evaluator:
        decision = await evaluator.evaluate(prompt)
        state.evaluator_session_id = evaluator.session_id

    reasons = validate_correction_quality(decision, founder=founder)
    if reasons:
        warn("  FIX rejected by the prompt-quality contract:")
        for r in reasons:
            out(f"    - {r}")
        decision = EvaluatorDecision(
            decision=Decision.BLOCKED,
            summary="The evaluator returned a FIX that failed the prompt-quality contract.",
            problems=reasons,
        )

    # What the suite measured outranks what the evaluator concluded, on exactly
    # the terms the loop uses: the same gate, reading the same outcome records
    # and the same per-case evidence. An ACCEPT survives only when every
    # required scenario passed and can show its evidence.
    decision = _apply_suite_precedence(suite_result, decision, scenario.name, out)

    record = IterationRecord(
        iteration=1,
        git=git,
        scenario=result,
        decision=decision,
        evaluator_session_id=state.evaluator_session_id,
        context_provenance=provenance.model_dump(mode="json"),
    )
    record.suite = suite_result.model_dump(mode="json")
    store.write_json(
        store.iteration_dir(1).relative_to(store.run_dir) / "suite-result.json",
        record.suite,
    )
    store.save_iteration(record)
    state.iterations.append(record)
    state.final_decision = decision
    state.status = {
        Decision.ACCEPT: RunStatus.ACCEPTED,
        Decision.FIX: RunStatus.MAX_ITERATIONS,
        Decision.ASK_USER: RunStatus.NEEDS_USER,
        Decision.BLOCKED: RunStatus.BLOCKED,
    }[decision.decision]
    store.save_state(state)

    header("DECISION")
    _print_decision(decision, out)
    if decision.decision is Decision.FIX and decision.correction_prompt:
        out("\nSuggested correction prompt:\n")
        out(decision.correction_prompt)
    out(f"\nartifacts: {store.run_dir}")
    # The exit code carries the same verdict the run state does. Returning 0
    # whatever happened was the other half of the false accept: a caller that
    # only checks the status of this process was told the certification
    # succeeded even when the decision recorded beside it says otherwise.
    return _exit_code_for(state.status)


async def cmd_audit(args: argparse.Namespace) -> int:
    """Audit the repository's current completion claims. No Claude session used."""
    config = _config_from_args(args)
    problems = config.validate_repo()
    if problems:
        for p in problems:
            error(p)
        return 2

    auditor = CompletionAuditor(config.neyma_repo)
    unit = RepositoryContextLoader(config.neyma_repo).resolve_active_unit_optional()
    if not unit.is_declared:
        note(f"no active unit declared ({unit.resolution_problem}); auditing the claims alone")

    report = ""
    if args.report:
        p = Path(os.path.expanduser(args.report))
        if not p.exists():
            error(f"report file not found: {p}")
            return 2
        report = p.read_text(encoding="utf-8", errors="replace")

    audit = auditor.audit(report, unit=unit)

    header("COMPLETION AUDIT")
    out(audit.summary_block())

    if audit.claims:
        out("\nClaims examined:")
        for c in audit.claims:
            out(f"  - {c.label()}")

    if audit.contradictions:
        out(f"\nContradictions ({len(audit.contradictions)}):")
        for i, c in enumerate(audit.contradictions, 1):
            out(f"  {i}. {c.render()}")

    if audit.missing_evidence:
        out("\nMissing evidence:")
        for m in audit.missing_evidence:
            out(f"  - {m}")

    st = audit.observed_state
    out("\nReceipts:")
    for r in st.receipts:
        mark = "OK " if (r.exists and r.passed and r.matches_head) else "!! "
        out(f"  {mark}{r.name}: exists={r.exists} passed={r.passed} matches_tree={r.matches_head}")
        out(f"      {r.detail}")

    if args.json:
        out("\n" + json.dumps(audit.model_dump(mode="json"), indent=2, default=str))

    return 0 if audit.decision is AuditDecision.VERIFIED else 20


_PROTOCOL_EXIT = {
    ProtocolStatus.CONSISTENT: 0,
    ProtocolStatus.VIOLATION: 20,
    ProtocolStatus.DEADLOCK: 21,
    ProtocolStatus.REQUIRES_APPROVAL: 22,
    ProtocolStatus.BLOCKED_ENVIRONMENT: 23,
    ProtocolStatus.BLOCKED_AUTHORITY: 24,
}


def _open_store(config: DriverConfig, run_id: str | None, create: bool = False):
    """Resolve a run store: the named run, the latest, or a new one."""
    assert config.runs_dir is not None
    if run_id:
        return EvidenceStore.open_run(config.runs_dir, run_id)
    latest = EvidenceStore.latest_run(config.runs_dir)
    if latest is not None:
        return latest
    return EvidenceStore(config.runs_dir, new_run_id()) if create else None


async def cmd_protocol(args: argparse.Namespace) -> int:
    """Diagnose repository protocol, topology and deadlocks. No Claude session."""
    config = _config_from_args(args)
    problems = config.validate_repo()
    if problems:
        for p in problems:
            error(p)
        return 2

    resolver = ProtocolResolver(config.neyma_repo)
    resolution = resolver.resolve(baseline=getattr(args, "baseline", None))

    store = _open_store(config, getattr(args, "run", None), create=bool(getattr(args, "run", None)))
    if store is not None:
        store.save_protocol_resolution(resolution.model_dump(mode="json"))

    header("REPOSITORY PROTOCOL")
    out(resolution.render_report(run_id=store.run_id if store else ""))

    if getattr(args, "sources", False):
        out("\nPROTOCOL SOURCES READ:")
        for path in resolution.sources_read:
            out(f"  {path}")
        out(f"\nRULES DISCOVERED ({len(resolution.rules_consulted)}):")
        for rule in resolution.rules_consulted:
            out(f"  [{rule.authority_level.value}] {rule.kind.value}  {rule.cite()}")
            out(f"      {rule.description[:150]}")

    if resolution.status is ProtocolStatus.REQUIRES_APPROVAL and resolution.approval_prompt:
        out("")
        out(resolution.approval_prompt)

    if getattr(args, "json", False):
        out("\n" + json.dumps(resolution.model_dump(mode="json"), indent=2, default=str))

    if store is not None:
        out(f"\nsaved: {store.run_dir / 'protocol-resolution.json'}")
    return _PROTOCOL_EXIT[resolution.status]


async def cmd_approve(args: argparse.Namespace) -> int:
    """Approve one remediation option, for one plan hash. Executes nothing."""
    config = _config_from_args(args)
    problems = config.validate_repo()
    if problems:
        for p in problems:
            error(p)
        return 2

    assert config.runs_dir is not None
    store = _open_store(config, args.run, create=True)
    if store is None:
        error(f"No runs found under {config.runs_dir}. Pass --run <run-id>.")
        return 2

    resolver = ProtocolResolver(config.neyma_repo)
    resolution = resolver.resolve(baseline=getattr(args, "baseline", None))

    # An approval applies to the plan the human was shown. Without a reported
    # plan to compare against there is nothing to have read, and with a
    # different hash the repository moved since they read it. Neither is an
    # approval of *this* plan.
    previous = store.load_protocol_resolution()
    if previous is None:
        error(f"No reported plan for run {store.run_id}.")
        note(
            "An approval authorizes a specific plan, so the plan has to have been reported "
            "first. Run:\n"
            f"  python -m neyma_product_driver protocol --run {store.run_id}"
            + (f" --baseline {args.baseline}" if getattr(args, "baseline", None) else "")
        )
        return 3

    current_hash = _plan_hash_of(resolution, args.option)
    stale = [
        o
        for o in (previous.get("options") or [])
        if o.get("option_id") == args.option
        and o.get("plan_hash")
        and o.get("plan_hash") != current_hash
    ]
    if stale:
        error(
            f"The plan for option {args.option} changed since it was reported "
            f"(was {stale[0]['plan_hash']}, now {current_hash})."
        )
        note(
            "The repository state moved, so the approval you are giving would not be for "
            "the plan you read. Re-run:\n"
            f"  python -m neyma_product_driver protocol --run {store.run_id}"
        )
        return 3

    record, reasons = approve_option(
        resolution=resolution,
        option_id=args.option,
        confirmation=args.confirmation,
        store=ApprovalStore(store.run_dir),
        run_id=store.run_id,
    )

    if record is None:
        error("Not approved.")
        for reason in reasons:
            out(f"  - {reason}")
        option = next((o for o in resolution.options if o.option_id == args.option), None)
        if option is not None and not option.disqualified:
            note(f"\nThe exact phrase required is:\n    {option.approval_phrase}")
        return 3

    option = next(o for o in resolution.options if o.option_id == record.option_id)
    good(f"\nApproved: option {option.option_id} — {option.title}")
    out(f"plan hash: {record.plan_hash}")
    note(
        "This approval authorizes this plan only. Any change to the repository — HEAD, the\n"
        "baseline, the commit range or the rules themselves — expires it."
    )

    prompt = remediation_builder_prompt(
        option=option,
        topology=resolution.topology,
        approval=record,
        unit_id=resolution.unit_id,
    )
    path = store.write_text("remediation-prompt.md", prompt)

    header("BUILDER PROMPT FOR THE APPROVED PLAN")
    out(prompt)
    out(f"\nsaved: {path}")
    note(
        "\nThe driver does not execute history rewrites. Run this plan yourself, or hand this\n"
        "prompt to a fresh builder session, then re-run:\n"
        f"  python -m neyma_product_driver protocol --run {store.run_id}\n"
        "The resulting graph and tree are checked against the approved plan; any deviation is "
        "reported as BLOCKED."
    )
    return 0


def _plan_hash_of(resolution: Any, option_id: str) -> str:
    for option in resolution.options:
        if option.option_id == option_id:
            return option.plan_hash
    return ""


def _make_investigation_reasoner(config: DriverConfig):
    """The production reasoner. A seam tests monkeypatch to avoid Claude usage."""
    from .investigation_reasoner import Challenger, LLMReasoner

    return (
        LLMReasoner(config.neyma_repo, model=config.evaluator.model),
        Challenger(config.neyma_repo, model=config.evaluator.model),
    )


async def cmd_investigate(args: argparse.Namespace) -> int:
    """Open-ended diagnosis of an unfamiliar failure. Read-only, autonomous."""
    config = _config_from_args(args)
    problems = config.validate_repo()
    if problems:
        for p in problems:
            error(p)
        return 2

    from .investigator import Investigator, builder_correction_from_investigation
    from .investigation_memory import InvestigationMemory, InvestigationStatus
    from .probe_runner import EvidenceCollector, ProbeRunner

    assert config.runs_dir is not None
    store = _open_store(config, getattr(args, "run", None), create=True)
    if store is None:
        store = EvidenceStore(config.runs_dir, new_run_id())

    builder_report = ""
    state_obj = store.load_state()
    if state_obj is not None and state_obj.iterations:
        builder_report = state_obj.iterations[-1].builder_summary or ""

    reasoner, challenger = _make_investigation_reasoner(config)

    header("DIAGNOSTIC INVESTIGATION")
    out(f"repo:  {config.neyma_repo}")
    out(f"run:   {store.run_id}")
    if args.issue:
        out(f"issue: {args.issue}")

    investigator = Investigator(
        config.neyma_repo,
        reasoner,
        memory=InvestigationMemory(store.run_dir),
        collector=EvidenceCollector(config.neyma_repo),
        runner=ProbeRunner(config.neyma_repo, scratch_dir=store.run_dir),
        emit=lambda m: out(f"  {m}"),
        challenger=challenger,
    )

    state = investigator.investigate(
        issue=args.issue or "",
        trigger="cli",
        builder_report=builder_report,
        max_iterations=args.max_iterations,
        run_id=store.run_id,
    )

    header("RESULT")
    out(state.result.summary_block())

    correction = builder_correction_from_investigation(state)
    if correction:
        path = store.write_text("investigation-correction.md", correction)
        out(f"\nA grounded builder correction was written to: {path}")

    out(f"\ninvestigation artifacts: {store.run_dir / 'investigation'}")
    return {
        InvestigationStatus.ROOT_CAUSE_FOUND: 0,
        InvestigationStatus.PARTIAL_DIAGNOSIS: 30,
        InvestigationStatus.NEEDS_MORE_EVIDENCE: 31,
        InvestigationStatus.ASK_USER: 32,
        InvestigationStatus.BLOCKED: 33,
        InvestigationStatus.BUDGET_EXHAUSTED: 34,
    }[state.result.status]


def _recorded_suite_gate(state: RunState) -> Any:
    """The acceptance gate re-derived from what the run recorded, or None.

    Deterministic and evidence-only: the suite result was persisted by
    ``save_iteration`` and again as ``suite-result.json``, so this recomputes
    the same verdict from the same records rather than trusting anything the
    run said about itself. Returns None when the run executed no suite, which
    is not the same as a suite that passed.
    """
    for record in reversed(state.iterations or []):
        if not record.suite:
            continue
        try:
            suite = SuiteResult.model_validate(record.suite)
        except Exception:
            continue
        return evaluate_gate(suite)
    return None


async def cmd_review(args: argparse.Namespace) -> int:
    """Launch a fresh independent reviewer over a finished run, by hand.

    No longer part of the ordinary path: ``run`` takes the review a task
    requires as a step inside its own loop, feeds the verdict back to the same
    builder, and takes a new review of whatever comes out. This command remains
    for the case it was always good for — looking again at a run that has
    already ended, with a reviewer that never saw it.

    It launches the same session the loop does, with the same execution
    boundary, so an ad-hoc review is not a weaker one.
    """
    config = _config_from_args(args)
    problems = config.validate_repo()
    if problems:
        for p in problems:
            error(p)
        return 2
    if not _preflight_api_key(config):
        return 3

    assert config.runs_dir is not None
    store = (
        EvidenceStore.open_run(config.runs_dir, args.run)
        if args.run
        else EvidenceStore.latest_run(config.runs_dir)
    )
    if store is None:
        error(f"No runs found under {config.runs_dir}. Pass --run <run-id>.")
        return 2

    state = store.load_state()
    if state is None:
        error(f"Run {store.run_id} has no readable state.")
        return 2

    unit = RepositoryContextLoader(config.neyma_repo).resolve_active_unit_optional()
    if not unit.is_declared:
        note(f"no active unit declared ({unit.resolution_problem}); reviewing the change itself")

    last = state.iterations[-1] if state.iterations else None
    builder_report = last.builder_summary if last else ""
    iteration = last.iteration if last else 1

    auditor = CompletionAuditor(config.neyma_repo)
    # The reviewer has to know what was asked for. Reviewing a one-unit build
    # against a whole phase's bar is the same mistake as auditing it that way,
    # and it costs the one reviewer whose independence cannot be spent twice.
    review_scope = resolve_task_scope(state.task, unit, config.neyma_repo)
    audit = auditor.audit(
        builder_report,
        unit=unit,
        evidence_dir=str(store.iteration_dir(iteration)),
        scope=review_scope,
    )

    header("INDEPENDENT REVIEW")
    out(f"run:  {store.run_id}")
    out(f"unit: {unit.unit_id} ({unit.status})")
    out(f"scope: {review_scope.describe()}")
    out(audit.summary_block())

    # Repository topology and authority must be valid first. Reviewing a state
    # the repository forbids reviews the wrong thing, and burns the one reviewer
    # whose independence cannot be recovered once spent.
    resolver = ProtocolResolver(config.neyma_repo)
    resolution = resolver.resolve()
    store.save_protocol_resolution(resolution.model_dump(mode="json"))
    reason = resolver.review_block_reason(resolution)
    if reason:
        error("\nBLOCKED — no reviewer was launched.")
        out(reason)
        out("")
        out(resolution.render_report(run_id=store.run_id))
        return 11
    note("protocol resolver: topology and authority are valid.")

    # The run's own scenario evidence, read back from what it recorded. This is
    # the documented way out of NEEDS_INDEPENDENT_REVIEW, and it had never heard
    # of the acceptance gate: it re-ran the auditor and the resolver from
    # scratch, built a prompt with no scenario section in it at all, and could
    # exit 0 over a run in which a required scenario had failed. A reviewer
    # cannot adjudicate evidence it is never shown, and the reviewer's
    # independence is spent the moment it is launched.
    gate = _recorded_suite_gate(state)
    if gate is not None and gate.blocks_acceptance:
        error("\nBLOCKED — no reviewer was launched.")
        out(
            "This run's scenario suite did not establish the coverage it set out to.\n"
            "An independent review cannot substitute for verification that never happened."
        )
        out("")
        out(gate.summary_block())
        out(
            "\nRe-run the suite and reach a verified gate first:\n"
            f"  python -m neyma_product_driver run --resume-run {store.run_id}"
        )
        return 11
    if gate is not None:
        note(f"scenario gate: {gate.headline()}")

    base_scenario = None
    try:
        base_scenario = load_scenario(config.scenario_path(state.scenario_name or None))
    except Exception:
        base_scenario = None
    policy = _reviewer_command_policy(config, base_scenario or Scenario(name="(none)"))
    fingerprint = capture_fingerprint(config.neyma_repo)

    note(
        "\nThis launches a FRESH Claude session. It does not resume or inherit the\n"
        "builder conversation, it cannot write, commit, push, deploy or read a secret,\n"
        "and it will not write any status file."
    )
    if policy is not None:
        note(
            "It CAN re-run this repository's deterministic verification — its tests, "
            "probes and\nbatteries — under the reviewer command boundary, so its verdict "
            "need not rest on\nwhat Product Driver captured."
        )
    else:
        note(
            "Reviewer command execution is switched off, so this review will rest on "
            "records\nrather than on anything it reproduced itself."
        )
    note(f"It will review exactly: {fingerprint.describe()}")

    if not args.yes:
        if not sys.stdin.isatty():
            error("\nRefusing to launch a reviewer non-interactively. Re-run with --yes.")
            return 3
        reply = input("\nAuthorize the transition from implementer to independent reviewer? Type 'yes': ")
        if reply.strip().lower() != "yes":
            out("Aborted. No reviewer was launched.")
            return 0

    from .reviewer import IndependentReviewerSession, review_prompt

    requirement = resolve_review_requirement(
        config.neyma_repo,
        review_scope,
        unit=unit,
        audit_requires_review=(audit.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW),
    )
    out(_indent(requirement.summary_block()))

    out("\n→ independent reviewer working...")
    async with IndependentReviewerSession(
        config.neyma_repo,
        model=config.review.model or config.evaluator.model,
        on_progress=lambda m: out(_indent(m)),
        command_policy=policy,
        fingerprint=fingerprint,
        scope_id=review_scope.scope_id,
        builder_session_id=str(state.builder_session_id or ""),
    ) as reviewer:
        review = await reviewer.review(
            review_prompt(
                unit=unit,
                audit=audit,
                builder_report=builder_report,
                evidence_dir=str(store.iteration_dir(iteration)),
                task=state.task,
                scope=review_scope,
                requirement=requirement,
                fingerprint=fingerprint,
                policy=policy,
            )
        )

    store.save_independent_review(iteration, review.model_dump(mode="json"))
    if last is not None:
        last.independent_review = review.model_dump(mode="json")
        store.save_iteration(last)
        store.save_state(state)

    header("REVIEW VERDICT")
    colour = {"SUPPORTED": good, "NOT_SUPPORTED": error}.get(review.verdict, warn)
    colour(f"{review.verdict}  (confidence {review.confidence:.2f})")
    out(review.summary)
    out(f"\nevidence: {review.evidence_basis()}")
    for command in review.commands_allowed[:12]:
        out(f"  ran:     {str(command.get('command', ''))[:150]}")
    for command in review.commands_refused[:6]:
        out(f"  REFUSED: {str(command.get('command', ''))[:110]} — {command.get('reason', '')}")
    if review.blocked_on.blocking:
        warn(f"\nblocked on {review.blocked_on.kind}: {review.blocked_on.detail}")
        if review.blocked_on.requested_action:
            out(f"  requested action: {review.blocked_on.requested_action}")

    if review.findings:
        out("\nFindings:")
        for f in review.findings:
            out(f"  [{f.severity}] {f.finding}")
            out(f"      evidence: {f.evidence_path}")

    if review.adjudications:
        out("\nAdjudications:")
        for a in review.adjudications:
            out(f"  {a.ruling}: {a.discrepancy}")
            out(f"      basis: {a.basis}")

    if review.criteria_assessment:
        out("\nCriteria assessment:")
        for c in review.criteria_assessment:
            out(f"  {c.assessment:<18} {c.criterion}")

    note(
        "\nThis review is advisory evidence about the exact tree named above. It does not\n"
        "mark any status file, and it stops describing the implementation the moment the\n"
        "implementation changes. Recording an adjudication in the repository remains a\n"
        "human decision, made under the repository's own rules."
    )
    out(f"\nsaved: {store.iteration_dir(iteration) / 'independent-review.json'}")
    return 0 if review.verdict == "SUPPORTED" else 20


async def cmd_feedback(args: argparse.Namespace) -> int:
    """Record explicit founder direction for one run. Never permanent."""
    config = _config_from_args(args)
    assert config.runs_dir is not None

    store = (
        EvidenceStore.open_run(config.runs_dir, args.run)
        if args.run
        else EvidenceStore.latest_run(config.runs_dir)
    )
    if store is None:
        error(f"No runs found under {config.runs_dir}. Pass --run <run-id>.")
        return 2

    message = (args.message or "").strip()
    if not message:
        error("--message is required and must not be empty.")
        return 2

    state = store.load_state()
    feedback_store = FounderFeedbackStore(store.run_dir)
    entry = feedback_store.add(message, iteration=state.iteration if state else None)

    good(f"Founder direction recorded for run {store.run_id}.")
    out(f"  {entry.message}")
    note(
        "\nThis applies to THIS RUN ONLY and is included in subsequent evaluator and\n"
        "builder prompts as the highest-priority product input. It has NOT been added\n"
        "to the durable founder context. To make it permanent:\n"
        f"  python -m neyma_product_driver promote-feedback --run {store.run_id}"
    )
    return 0


async def cmd_promote_feedback(args: argparse.Namespace) -> int:
    """Promote run feedback into durable context — shows the diff, asks first."""
    config = _config_from_args(args)
    assert config.runs_dir is not None

    store = (
        EvidenceStore.open_run(config.runs_dir, args.run)
        if args.run
        else EvidenceStore.latest_run(config.runs_dir)
    )
    if store is None:
        error(f"No runs found under {config.runs_dir}. Pass --run <run-id>.")
        return 2

    entries = [e for e in FounderFeedbackStore(store.run_dir).load() if not e.promoted]
    if not entries:
        out(f"No unpromoted founder feedback in run {store.run_id}.")
        return 0

    try:
        founder = load_founder_context(config.driver_root)
    except ContextResolutionError as exc:
        error(f"founder context unusable: {exc}")
        return 2

    heading = "## 10. Founder feedback promoted from runs"
    addition_lines = [f"\n- {e.message}  _(from run {store.run_id}, {e.timestamp})_" for e in entries]
    current = founder.owner_context
    addition = "".join(addition_lines)
    new_text = (
        current.rstrip() + "\n" + addition + "\n"
        if heading in current
        else current.rstrip() + f"\n\n{heading}\n" + addition + "\n"
    )

    header("PROPOSED CHANGE TO DURABLE FOUNDER CONTEXT")
    out(f"file: {founder.owner_context_path}")
    out(f"current version: {founder.version}\n")
    out("The following lines would be ADDED:\n")
    if heading not in current:
        out(f"+ {heading}")
    for line in addition_lines:
        out(f"+ {line.strip()}")

    if args.yes:
        note("\n--yes given; applying.")
    elif not sys.stdin.isatty():
        error("\nRefusing to modify durable context non-interactively. Re-run with --yes.")
        return 3
    else:
        reply = input("\nApply this change to the durable founder context? Type 'yes': ").strip().lower()
        if reply != "yes":
            out("Aborted. Durable context unchanged.")
            return 0

    founder.owner_context_path.write_text(new_text, encoding="utf-8")

    import json as _json

    fb_path = FounderFeedbackStore(store.run_dir).path
    all_entries = FounderFeedbackStore(store.run_dir).load()
    for e in all_entries:
        e.promoted = True
    fb_path.write_text(
        _json.dumps([e.model_dump(mode="json") for e in all_entries], indent=2), encoding="utf-8"
    )

    updated = load_founder_context(config.driver_root)
    good(f"\nDurable founder context updated. New version: {updated.version}")
    return 0


async def cmd_stop(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    assert config.runs_dir is not None
    store = (
        EvidenceStore.open_run(config.runs_dir, args.run_id)
        if args.run_id
        else EvidenceStore.latest_run(config.runs_dir)
    )
    if store is None:
        out(f"No runs found under {config.runs_dir}")
        return 0

    store.request_stop(args.reason or "stop requested via CLI")
    state = store.load_state()
    if state is not None:
        state.stop_requested = True
        store.save_state(state)

    good(f"Stop requested for run {store.run_id}.")
    note("The driver halts before its next iteration; it does not kill a builder mid-turn.")
    return 0


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------


def _config_from_args(args: argparse.Namespace) -> DriverConfig:
    overrides: dict[str, Any] = {}
    for attr, key in (
        ("repo", "neyma_repo"),
        ("max_iterations", "max_iterations"),
        ("scenario", "scenario"),
        ("task", "task"),
    ):
        value = getattr(args, attr, None)
        if value is not None:
            overrides[key] = value
    if getattr(args, "builder_model", None):
        overrides["builder.model"] = args.builder_model
    if getattr(args, "evaluator_model", None):
        overrides["evaluator.model"] = args.evaluator_model
    if getattr(args, "browser", False):
        overrides["run.browser_enabled"] = True
    if getattr(args, "headed", False):
        overrides["run.headless"] = False
    return load_config(getattr(args, "config", None), **overrides)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m neyma_product_driver",
        description="Local product-driver for Neyma: drives a Claude Code builder "
        "session, operates the running product, and judges observed behaviour.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", help="path to a driver config YAML/JSON file")
        p.add_argument("--repo", help="path to the Neyma repository")

    run_p = sub.add_parser("run", help="run the full builder → test → correct loop")
    common(run_p)
    run_p.add_argument("--task", help="what the builder should do")
    run_p.add_argument("--scenario", help="scenario name or path")
    run_p.add_argument("--max-iterations", type=int, dest="max_iterations")
    run_p.add_argument("--builder-model")
    run_p.add_argument("--evaluator-model")
    run_p.add_argument("--browser", action="store_true", help="enable browser testing")
    run_p.add_argument("--headed", action="store_true", help="show the browser window")
    run_p.add_argument("--resume-run", help="resume a previous run id")
    run_p.add_argument("--resume-session", help="resume a specific Claude builder session id")
    run_p.add_argument(
        "--auto-scenarios",
        action="store_true",
        dest="auto_scenarios",
        help="generate an adaptive verification suite for this task (now the "
        "default; accepted so existing commands keep working)",
    )
    run_p.add_argument(
        "--no-auto-scenarios",
        action="store_true",
        dest="no_auto_scenarios",
        help="do not generate verification scenarios for this run",
    )
    run_p.add_argument(
        "--no-auto-review",
        action="store_true",
        dest="no_auto_review",
        help="do not take the independent review a task requires inside the run "
        "(the run stops at NEEDS_INDEPENDENT_REVIEW and reports what was owed instead)",
    )
    run_p.set_defaults(func=cmd_run)

    scen_p = sub.add_parser(
        "scenarios", help="plan, replay and promote generated verification scenarios"
    )
    scen_sub = scen_p.add_subparsers(dest="scenarios_command", required=True)

    plan_p = scen_sub.add_parser(
        "plan", help="generate a scenario plan for a task and print it (executes nothing)"
    )
    common(plan_p)
    plan_p.add_argument("--task", help="what the builder would be asked to do")
    plan_p.add_argument("--scenario", help="base scenario supplying services and app_url")
    plan_p.add_argument("--json", action="store_true", dest="as_json")
    plan_p.set_defaults(func=cmd_scenarios_plan)

    replay_p = scen_sub.add_parser(
        "run-generated", help="re-execute a run's generated scenarios (no builder, no evaluator)"
    )
    common(replay_p)
    replay_p.add_argument("--run", help="run id (defaults to the latest)")
    replay_p.add_argument("--scenario", help="base scenario supplying services and app_url")
    replay_p.add_argument("--browser", action="store_true")
    replay_p.add_argument("--headed", action="store_true")
    replay_p.set_defaults(func=cmd_scenarios_run_generated)

    cand_p = scen_sub.add_parser(
        "promotion-candidates",
        help="list generated scenarios a run suggests for permanent regression coverage",
    )
    common(cand_p)
    cand_p.add_argument("--run", help="run id (defaults to the latest)")
    cand_p.add_argument("--json", action="store_true", dest="as_json")
    cand_p.set_defaults(func=cmd_scenarios_promotion_candidates)

    promote_scen_p = scen_sub.add_parser(
        "promote",
        help="add one candidate to the permanent suite (shows the YAML, asks first)",
    )
    common(promote_scen_p)
    promote_scen_p.add_argument("--run", help="run id (defaults to the latest)")
    promote_scen_p.add_argument(
        "--scenario-id", required=True, dest="scenario_id", help="the candidate's scenario id"
    )
    promote_scen_p.add_argument(
        "--scenario", help="base scenario supplying services and app_url"
    )
    promote_scen_p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    promote_scen_p.set_defaults(func=cmd_scenarios_promote)

    doctor_p = sub.add_parser("doctor", help="verify the local environment")
    common(doctor_p)
    doctor_p.set_defaults(func=cmd_doctor)

    calibrate_p = sub.add_parser(
        "calibrate",
        help="read-only preflight: what the driver derives from the target repository",
    )
    common(calibrate_p)
    calibrate_p.add_argument("--json", action="store_true", dest="as_json")
    calibrate_p.set_defaults(func=cmd_calibrate)

    status_p = sub.add_parser("status", help="show the latest (or a given) run")
    common(status_p)
    status_p.add_argument("run_id", nargs="?", help="run id (defaults to the latest)")
    status_p.set_defaults(func=cmd_status)

    eval_p = sub.add_parser("evaluate", help="run a scenario and evaluate it once, no builder")
    common(eval_p)
    eval_p.add_argument("--scenario")
    eval_p.add_argument("--task")
    eval_p.add_argument("--browser", action="store_true")
    eval_p.add_argument("--headed", action="store_true")
    eval_p.set_defaults(func=cmd_evaluate)

    stop_p = sub.add_parser("stop", help="request that a run halt before its next iteration")
    common(stop_p)
    stop_p.add_argument("run_id", nargs="?")
    stop_p.add_argument("--reason", default="")
    stop_p.set_defaults(func=cmd_stop)

    audit_p = sub.add_parser(
        "audit", help="audit the repository's completion claims (no Claude session)"
    )
    common(audit_p)
    audit_p.add_argument("--report", help="path to a builder report to audit")
    audit_p.add_argument("--json", action="store_true", help="also emit the full audit as JSON")
    audit_p.set_defaults(func=cmd_audit)

    protocol_p = sub.add_parser(
        "protocol",
        help="diagnose repository protocol, commit topology and governance deadlocks",
    )
    common(protocol_p)
    protocol_p.add_argument("--run", help="run id to attach the resolution to (defaults to the latest)")
    protocol_p.add_argument("--baseline", help="override the authorized baseline commit")
    protocol_p.add_argument("--sources", action="store_true", help="list every rule discovered")
    protocol_p.add_argument("--json", action="store_true", help="also emit the full resolution as JSON")
    protocol_p.set_defaults(func=cmd_protocol)

    approve_p = sub.add_parser(
        "approve",
        help="approve one remediation option for one plan hash (executes nothing)",
    )
    common(approve_p)
    approve_p.add_argument("--run", help="run id (defaults to the latest)")
    approve_p.add_argument(
        "--baseline",
        help="the same --baseline the plan was reported with, if one was given",
    )
    approve_p.add_argument("--option", required=True, help="option id, e.g. A")
    approve_p.add_argument(
        "--confirmation", required=True, help="the exact approval phrase the plan requires"
    )
    approve_p.set_defaults(func=cmd_approve)

    investigate_p = sub.add_parser(
        "investigate",
        help="open-ended, read-only diagnosis of an unfamiliar failure",
    )
    common(investigate_p)
    investigate_p.add_argument("--run", help="run id to attach the investigation to (defaults to latest)")
    investigate_p.add_argument("--issue", default="", help="a short description of what to investigate")
    investigate_p.add_argument(
        "--max-iterations", type=int, default=8, dest="max_iterations",
        help="soft cap on investigation iterations (default 8)",
    )
    investigate_p.set_defaults(func=cmd_investigate)

    review_p = sub.add_parser(
        "review",
        help="launch a fresh read-only independent reviewer (requires your authorization)",
    )
    common(review_p)
    review_p.add_argument("--run", help="run id (defaults to the latest)")
    review_p.add_argument("--yes", action="store_true", help="skip the authorization prompt")
    review_p.set_defaults(func=cmd_review)

    fb_p = sub.add_parser(
        "feedback",
        help="record founder direction for a run (highest-priority context; this run only)",
    )
    common(fb_p)
    fb_p.add_argument("--run", help="run id (defaults to the latest)")
    fb_p.add_argument("--message", required=True, help="the product direction")
    fb_p.set_defaults(func=cmd_feedback)

    promote_p = sub.add_parser(
        "promote-feedback",
        help="promote a run's founder feedback into the durable context (asks first)",
    )
    common(promote_p)
    promote_p.add_argument("--run", help="run id (defaults to the latest)")
    promote_p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    promote_p.set_defaults(func=cmd_promote_feedback)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Callable[[argparse.Namespace], Awaitable[int]] = args.func
    try:
        return asyncio.run(func(args))
    except KeyboardInterrupt:
        out("\ninterrupted")
        return 130
    except ValidationError as exc:
        # Configuration is wrong. Say exactly what is missing and how to supply
        # it — a traceback here would read as a driver bug, when in fact the
        # driver is correctly refusing to guess.
        error("Configuration error\n")
        for item in exc.errors():
            location = ".".join(str(part) for part in item.get("loc", ())) or "(root)"
            out(f"  {location}: {item.get('msg', '')}")
        if any("neyma_repo" in str(e.get("loc", ())) for e in exc.errors()):
            out(
                "\n  The target repository is never inferred. Either:\n"
                "    - copy driver.config.example.yaml to driver.config.yaml and set "
                "neyma_repo, or\n"
                "    - pass --repo /path/to/the/repository\n"
                "  The driver does not fall back to a previously-used path."
            )
        return 2
    except RepositoryPathError as exc:
        error(f"Repository path error\n  {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
