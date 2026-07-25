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
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from .config import (
    DEFAULT_DRIVER_ROOT,
    DriverConfig,
    api_key_present,
    load_config,
)
from .completion_auditor import AuditDecision, CompletionAuditor
from .context import (
    ContextProvenance,
    ContextResolutionError,
    FounderFeedbackStore,
    RepositoryContextLoader,
    founder_dir,
    load_founder_context,
)
from .evidence import EvidenceStore, check_writable, new_run_id
from .models import (
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
from .prompts import (
    builder_correction_prompt,
    builder_task_prompt,
    evaluator_prompt,
    render_correction_for_builder,
    validate_correction_quality,
)
from .scenarios import Scenario, ScenarioExecutor, load_scenario

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


@dataclass
class LoopResult:
    status: RunStatus
    state: RunState
    final_decision: EvaluatorDecision | None = None
    audit: Any = None
    protocol: Any = None


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
) -> LoopResult:
    """Drive builder → observe → evaluate → correct, bounded by max_iterations.

    The order is: builder claim → completion auditor → protocol resolver →
    scenario runner → product evaluator → combine. Repository authority is
    re-read before every evaluator decision, so a phase or READY-unit change
    mid-run is picked up rather than served from cache. Returns as soon as a
    terminal decision is reached, the iteration budget is exhausted, or a stop
    is requested.
    """
    feedback_store = FounderFeedbackStore(store.run_dir)
    last_audit: dict[str, Any] = {"value": None}
    last_protocol: dict[str, Any] = {"value": None}

    def _terminate(status: RunStatus, decision: EvaluatorDecision, record: IterationRecord) -> LoopResult:
        record.decision = decision
        store.save_iteration(record)
        state.iterations.append(record)
        state.final_decision = decision
        state.status = status
        store.save_state(state)
        return LoopResult(status, state, decision, last_audit["value"], last_protocol["value"])

    # Resolve authority once up front so the builder's task is scoped correctly.
    active_unit_id = ""
    if repo_loader is not None:
        try:
            active_unit_id = repo_loader.resolve_active_unit().unit_id
        except ContextResolutionError as exc:
            emit(f"  cannot resolve current authority: {exc}")
            decision = EvaluatorDecision(
                decision=Decision.BLOCKED,
                summary=f"Cannot resolve Neyma's current authority: {exc}",
                problems=[str(exc)],
            )
            return _terminate(RunStatus.BLOCKED, decision, IterationRecord(iteration=0))

    next_prompt = builder_task_prompt(
        state.task, scenario.summary(), active_unit_id, feedback_store.render()
    )
    prior_problems: list[str] = []
    sent_corrections: list[str] = []

    for iteration in range(1, config.max_iterations + 1):
        if store.stop_requested():
            emit("\nStop requested — halting before the next iteration.")
            state.status = RunStatus.STOPPED
            store.save_state(state)
            return LoopResult(RunStatus.STOPPED, state)

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

        # 3. operate the product
        emit(f"→ running scenario '{scenario.name}' ({scenario.mode})...")
        executor = make_executor(store.iteration_dir(iteration))
        scenario_result = await executor.execute(scenario)
        record.scenario = scenario_result
        emit(
            f"  scenario {'PASSED' if scenario_result.passed else 'FAILED'}"
            + (f" — {scenario_result.error}" if scenario_result.error else "")
        )

        # 3b. audit the builder's completion claims BEFORE judging the product
        audit = None
        if auditor is not None:
            emit("→ auditing completion claims...")
            try:
                unit_now = repo_loader.resolve_active_unit() if repo_loader else None
                audit = auditor.audit(
                    record.builder_summary,
                    unit=unit_now,
                    run_commands=list(scenario_result.commands),
                    evidence_dir=str(store.iteration_dir(iteration)),
                )
            except ContextResolutionError as exc:
                emit(f"  cannot resolve current authority: {exc}")
                decision = EvaluatorDecision(
                    decision=Decision.BLOCKED,
                    summary=f"Cannot resolve Neyma's current authority: {exc}",
                    problems=[str(exc)],
                )
                return _terminate(RunStatus.BLOCKED, decision, record)

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
            resolution = protocol_resolver.resolve(run_commands=list(scenario_result.commands))
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
            triggered, reason = should_investigate(
                builder_report=record.builder_summary,
                audit=audit,
                protocol=resolution,
                scenario_passed=scenario_result.passed,
                prior_failures=prior,
            )
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

        # 4. re-read repository authority — never reuse stale phase context
        repo_context = None
        if repo_loader is not None:
            try:
                repo_context = repo_loader.load(topics=["product", "architecture", "acceptance"])
            except ContextResolutionError as exc:
                emit(f"  cannot resolve current authority: {exc}")
                decision = EvaluatorDecision(
                    decision=Decision.BLOCKED,
                    summary=f"Cannot resolve Neyma's current authority: {exc}",
                    problems=[str(exc)],
                )
                return _terminate(RunStatus.BLOCKED, decision, record)

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
            service_logs=getattr(executor, "service_logs", {}) or {},
            evidence_dir=str(store.iteration_dir(iteration)),
            paused_permission_requests=denied,
            prior_problems=prior_problems,
            founder=founder,
            repo_context=repo_context,
            founder_feedback=feedback_text,
            previous_corrections=sent_corrections,
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

        # 6a. combine, highest authority first: a repository-governance blocker
        #     outranks every product judgement. A green targeted suite cannot
        #     make an invalid commit topology valid.
        if resolution is not None:
            terminal, decision = _apply_protocol_precedence(
                resolution, decision, scenario.name, emit
            )
            if terminal is not None:
                record.decision = decision
                record.notes.append(f"protocol resolver: {resolution.status.value}")
                _print_decision(decision, emit)
                return _terminate(terminal, decision, record)

        # 6b. combine: a completion claim the repository does not support
        #     overrides an ACCEPT from the product evaluator.
        if audit is not None and audit.blocks_acceptance:
            if audit.decision is AuditDecision.REQUIRES_INDEPENDENT_REVIEW:
                if decision.decision is Decision.ACCEPT:
                    emit("  product evaluation ACCEPTed, but independent review is required.")
                    record.decision = decision
                    record.notes.append(audit.headline)
                    store.save_iteration(record)
                    state.iterations.append(record)
                    state.final_decision = decision
                    state.status = RunStatus.NEEDS_INDEPENDENT_REVIEW
                    store.save_state(state)
                    return LoopResult(RunStatus.NEEDS_INDEPENDENT_REVIEW, state, decision, audit)
                # Otherwise the evaluator's own verdict still routes below.
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
    return LoopResult(RunStatus.MAX_ITERATIONS, state, state.final_decision)


def _apply_protocol_precedence(
    resolution: Any,
    decision: EvaluatorDecision,
    scenario_name: str,
    emit: Callable[[str], None],
) -> tuple[RunStatus | None, EvaluatorDecision]:
    """Apply repository governance to the product evaluator's verdict.

    Precedence, highest first:

        1. authority conflict
        2. destructive-action approval required
        3. repository deadlock
        4. protocol violation           (an ACCEPT can never override one)
        5. environmental blocker        (never a product failure, never a PASS)

    Returns ``(terminal_status, decision)``. A terminal status ends the run;
    ``None`` means the loop continues with the returned decision, which may
    have been replaced.
    """
    from .protocol_resolver import ProtocolStatus

    status = resolution.status

    if status is ProtocolStatus.BLOCKED_AUTHORITY:
        emit("  repository protocol is self-contradictory; the driver cannot choose between rules.")
        return RunStatus.BLOCKED, EvaluatorDecision(
            decision=Decision.BLOCKED,
            summary="Repository protocol authority is unresolvable: " + resolution.next_safe_action,
            problems=[c.description for c in resolution.conflicts]
            or [v.observed_state for v in resolution.violations],
            evidence_paths=list(resolution.sources_read)[:12],
            observed_behavior=decision.observed_behavior,
        )

    if status is ProtocolStatus.REQUIRES_APPROVAL:
        primary = resolution.cause("PRIMARY")
        emit("  repository governance requires an explicit human approval before anything else.")
        return RunStatus.REQUIRES_APPROVAL, EvaluatorDecision(
            decision=Decision.ASK_USER,
            summary=(primary.summary if primary else "")
            or "A repository-governance repair needs your approval.",
            problems=[v.detail or v.observed_state for v in resolution.violations],
            observed_behavior=decision.observed_behavior,
            evidence_paths=list(resolution.sources_read)[:12],
            confidence=0.9,
        )

    if status is ProtocolStatus.DEADLOCK:
        emit("  repository governance is deadlocked; retrying the blocked gate cannot clear it.")
        return RunStatus.BLOCKED, EvaluatorDecision(
            decision=Decision.BLOCKED,
            summary=resolution.deadlocks[0].root_cause if resolution.deadlocks else "deadlock",
            problems=[resolution.blocker_chain()] if resolution.blocker_chain() else [],
            observed_behavior=decision.observed_behavior,
            evidence_paths=list(resolution.sources_read)[:12],
        )

    if status is ProtocolStatus.VIOLATION:
        first = resolution.violations[0] if resolution.violations else None
        if decision.decision is Decision.ACCEPT:
            emit("  product evaluation ACCEPTed, but the repository protocol is violated.")
            return None, EvaluatorDecision(
                decision=Decision.FIX,
                summary=f"Repository protocol violation: {first.detail if first else ''}",
                problems=[v.render() for v in resolution.violations],
                observed_behavior=decision.observed_behavior,
                evidence_paths=[p for v in resolution.violations for p in v.evidence_paths][:12],
                correction_prompt=_protocol_correction(resolution),
                requirement_reference=(
                    f"{first.rule_id} ({first.rule_citation})" if first else "repository protocol"
                ),
                product_principle_reference=(
                    "a green targeted suite cannot make an invalid repository state valid"
                ),
                scenario=scenario_name,
                observed_result=first.observed_state if first else resolution.current_graph,
                expected_result=first.expected_state if first else resolution.expected_graph,
                preserve=(
                    "All implementation code, all commits, all receipts and every archival ref. "
                    "Do not rewrite history and do not hand-edit derived status."
                ),
                retest=(
                    "Re-run this driver's protocol resolver; it must report CONSISTENT before "
                    "any finalizer or reviewer is launched."
                ),
                confidence=0.85,
            )
        # A product defect the evaluator found outranks a protocol violation for
        # what to send the builder next, but the violation is still recorded.
        decision.problems.extend(v.detail or v.observed_state for v in resolution.violations)
        return None, decision

    if status is ProtocolStatus.BLOCKED_ENVIRONMENT and decision.decision is Decision.ACCEPT:
        emit("  an environmental gate failure cannot be counted as a pass.")
        return RunStatus.BLOCKED, EvaluatorDecision(
            decision=Decision.BLOCKED,
            summary=(
                "A required gate could not run for environmental reasons. This is not a product "
                "failure — and it is not a PASS either."
            ),
            problems=[b.render() for b in resolution.environment_blockers],
            observed_behavior=decision.observed_behavior,
            evidence_paths=list(resolution.sources_read)[:12],
        )

    return None, decision


def _protocol_correction(resolution: Any) -> str:
    """A correction the builder may safely act on. Never a history rewrite."""
    lines = [
        "REPOSITORY PROTOCOL VIOLATION — the repository's own rules reject the current state.",
        "",
        "CURRENT GRAPH:",
        resolution.current_graph,
        "",
        "EXPECTED GRAPH:",
        resolution.expected_graph,
        "",
        "VIOLATIONS:",
    ]
    lines += [f"  {i}. {v.render()}" for i, v in enumerate(resolution.violations, 1)]
    lines += [
        "",
        "REQUIRED ACTION:",
        f"  {resolution.next_safe_action}",
        "",
        "PROHIBITED:",
        "  - rewriting history (reset, rebase, squash, amend, cherry-pick, force-update):",
        "    those need explicit founder approval and are never yours to perform here",
        "  - writing or editing derived status by hand",
        "  - deleting or weakening any guard, test or receipt to obtain a green result",
        "",
        "If the only way forward is one of the prohibited operations, stop and say so.",
    ]
    return "\n".join(lines)


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

    try:
        scenario = load_scenario(config.scenario_path(args.scenario))
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

    # Resume or start a run.
    assert config.runs_dir is not None
    if args.resume_run:
        store = EvidenceStore.open_run(config.runs_dir, args.resume_run)
        state = store.load_state()
        if state is None:
            error(f"No resumable state found for run {args.resume_run}")
            return 2
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
    repo_loader = RepositoryContextLoader(config.neyma_repo)
    try:
        unit = repo_loader.resolve_active_unit()
        out(f"unit:     {unit.unit_id} ({unit.status}) — {unit.name}")
    except ContextResolutionError as exc:
        error(f"BLOCKED — {exc}")
        return 11

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
                        config.neyma_repo, config.run, artifact_dir
                    ),
                    founder=founder,
                    repo_loader=repo_loader,
                    auditor=CompletionAuditor(config.neyma_repo),
                    protocol_resolver=ProtocolResolver(config.neyma_repo),
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

    _report_outcome(result, store)
    return _exit_code_for(result.status)


def _indent(msg: str) -> str:
    return "\n".join("  " + ln for ln in msg.rstrip().splitlines()) if msg.strip() else ""


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
        warn("IMPLEMENTED — AWAITING INDEPENDENT REVIEW\n")
        out(
            "The implementation stands and the product evaluation passed, but the\n"
            "repository requires criteria that this session may not award itself."
        )
        audit = result.audit
        if audit is not None:
            pending = audit.observed_state.progress.independent_pending
            if pending:
                out("\nRequires a session other than the implementing one:")
                for name in pending:
                    out(f"  - {name}")
        out(
            "\nThis is neither a failure nor a completion. To authorize the transition\n"
            "from implementer to independent reviewer, run:\n"
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

        try:
            unit = RepositoryContextLoader(config.neyma_repo).resolve_active_unit()
        except ContextResolutionError as exc:
            check("exactly one READY unit resolvable", False, str(exc))
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
    """Run the scenario and evaluate it once, without touching the builder."""
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

    executor = ScenarioExecutor(config.neyma_repo, config.run, store.iteration_dir(1))
    out("→ running scenario...")
    result = await executor.execute(scenario)
    out(f"  scenario {'PASSED' if result.passed else 'FAILED'}")

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

    record = IterationRecord(
        iteration=1,
        git=git,
        scenario=result,
        decision=decision,
        evaluator_session_id=state.evaluator_session_id,
        context_provenance=provenance.model_dump(mode="json"),
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
    return 0


async def cmd_audit(args: argparse.Namespace) -> int:
    """Audit the repository's current completion claims. No Claude session used."""
    config = _config_from_args(args)
    problems = config.validate_repo()
    if problems:
        for p in problems:
            error(p)
        return 2

    auditor = CompletionAuditor(config.neyma_repo)
    try:
        unit = RepositoryContextLoader(config.neyma_repo).resolve_active_unit()
    except ContextResolutionError as exc:
        error(f"BLOCKED — {exc}")
        return 11

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


async def cmd_review(args: argparse.Namespace) -> int:
    """Launch a fresh, read-only independent reviewer. Explicitly human-authorized."""
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

    try:
        unit = RepositoryContextLoader(config.neyma_repo).resolve_active_unit()
    except ContextResolutionError as exc:
        error(f"BLOCKED — {exc}")
        return 11

    last = state.iterations[-1] if state.iterations else None
    builder_report = last.builder_summary if last else ""
    iteration = last.iteration if last else 1

    auditor = CompletionAuditor(config.neyma_repo)
    audit = auditor.audit(builder_report, unit=unit, evidence_dir=str(store.iteration_dir(iteration)))

    header("INDEPENDENT REVIEW")
    out(f"run:  {store.run_id}")
    out(f"unit: {unit.unit_id} ({unit.status})")
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
    note(
        "\nThis launches a FRESH Claude session. It does not resume or inherit the\n"
        "builder conversation, it is read-only (Read/Grep/Glob only), and it will not\n"
        "write any status file."
    )

    if not args.yes:
        if not sys.stdin.isatty():
            error("\nRefusing to launch a reviewer non-interactively. Re-run with --yes.")
            return 3
        reply = input("\nAuthorize the transition from implementer to independent reviewer? Type 'yes': ")
        if reply.strip().lower() != "yes":
            out("Aborted. No reviewer was launched.")
            return 0

    from .reviewer import IndependentReviewerSession, review_prompt

    prompt = review_prompt(
        unit=unit,
        audit=audit,
        builder_report=builder_report,
        evidence_dir=str(store.iteration_dir(iteration)),
    )

    out("\n→ independent reviewer working...")
    async with IndependentReviewerSession(
        config.neyma_repo,
        model=config.evaluator.model,
        on_progress=lambda m: out(_indent(m)),
    ) as reviewer:
        review = await reviewer.review(prompt)

    store.save_independent_review(iteration, review.model_dump(mode="json"))
    if last is not None:
        last.independent_review = review.model_dump(mode="json")
        store.save_iteration(last)
        store.save_state(state)

    header("REVIEW VERDICT")
    colour = {"SUPPORTED": good, "NOT_SUPPORTED": error}.get(review.verdict, warn)
    colour(f"{review.verdict}  (confidence {review.confidence:.2f})")
    out(review.summary)

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
        "\nThis review is advisory evidence. It does not mark any status file.\n"
        "Recording an adjudication in the repository remains a human decision,\n"
        "made under the repository's own rules."
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
    run_p.set_defaults(func=cmd_run)

    doctor_p = sub.add_parser("doctor", help="verify the local environment")
    common(doctor_p)
    doctor_p.set_defaults(func=cmd_doctor)

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


if __name__ == "__main__":
    raise SystemExit(main())
