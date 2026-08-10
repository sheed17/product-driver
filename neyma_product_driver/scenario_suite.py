"""Running many scenarios as one verification pass, and aggregating what happened.

A run used to centre on a single :class:`~neyma_product_driver.scenarios.Scenario`.
It now centres on a :class:`ScenarioSuite`: the permanent, human-reviewed
regression scenarios plus whatever the planner generated for this task. The two
kinds stay distinguishable everywhere — in ordering, in results, in evidence, and
in what an ACCEPT is allowed to ignore.

Execution is sequential by default and deliberately so. Scenarios start services,
bind ports, write databases and share a workspace; running two of those at once
produces failures that belong to the harness rather than the product, which is
the most expensive kind of false signal there is. The isolation model is
nonetheless explicit — every entry carries an ``isolation_key``, and
:func:`isolation_groups` already partitions the suite into sets that provably do
not contend — so safe parallelism can be switched on later without redesigning
anything. Until a run can *prove* isolation, the executor is sequential:
:attr:`SuiteExecutor.MAX_PARALLEL` is 1, and the constructor refuses any other
value rather than accepting a number it will not act on.

Aggregation summarizes. The evaluator is given counts, coverage by risk category,
clustered failures and evidence paths — not megabytes of raw output. Full
artifacts stay on disk under the run directory, exactly as they always have.
"""

from __future__ import annotations

import json
import time
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .evidence import sanitize_filename
from .failure_clustering import FailureCluster, FailureRecord, cluster_failures
from .models import ScenarioResult, redact, redact_obj, utcnow
from .scenario_plan import GeneratedScenario, Priority, RiskCategory, neighbours
from .scenarios import Scenario


class Origin(str, Enum):
    """Where a scenario came from, and therefore what authority it carries."""

    #: Human-reviewed, stored in the repository's scenario directory. The
    #: authoritative regression coverage; never modified by a run.
    PERMANENT = "permanent"
    #: Generated for this run, stored under the run's evidence directory.
    GENERATED = "generated"


class Outcome(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    #: Could not be judged: the product never became ready, or setup failed. Not
    #: a pass, and not a product failure either.
    BLOCKED = "BLOCKED"
    #: Deliberately not run, always with a stated reason.
    SKIPPED = "SKIPPED"


class SuiteEntry(BaseModel):
    """One scenario in the suite, with everything the runner needs to order it."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    scenario_id: str
    scenario: Scenario
    origin: Origin
    priority: Priority = Priority.P2
    risk_category: RiskCategory | None = None
    #: Scenarios sharing a key contend for the same resource and never overlap.
    isolation_key: str = "default"
    #: ids that must run (and pass) before this one is attempted.
    depends_on: list[str] = Field(default_factory=list)
    #: The generated model, when this entry came from a plan. Kept so results can
    #: be traced back to the risk that caused the scenario to exist.
    generated: GeneratedScenario | None = None
    #: True when a failure here must prevent acceptance.
    required: bool = True

    @property
    def is_generated(self) -> bool:
        return self.origin is Origin.GENERATED


class ScenarioOutcome(BaseModel):
    """What happened to one scenario, plus where its full evidence lives."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    scenario_name: str
    origin: Origin
    outcome: Outcome
    priority: Priority = Priority.P2
    risk_category: str = ""
    required: bool = True
    duration_s: float = 0.0
    assertions_total: int = 0
    assertions_failed: int = 0
    failed_assertions: list[str] = Field(default_factory=list)
    error: str = ""
    skip_reason: str = ""
    evidence_path: str = ""
    #: Whether ``evidence_path`` was confirmed to hold this scenario's own
    #: artifacts from this execution. A cited path that does not resolve is a
    #: dangling claim, and a result that cannot show its evidence does not count
    #: as verified — see :func:`verify_case_evidence`.
    evidence_verified: bool = False
    evidence_problem: str = ""
    #: Present only for generated scenarios: why this situation was exercised.
    generated_because: str = ""
    requirement_reference: str = ""

    @property
    def blocks_acceptance(self) -> bool:
        return (
            self.required
            and self.priority.blocks_acceptance
            and self.outcome in (Outcome.FAILED, Outcome.BLOCKED)
        )

    def brief(self) -> str:
        mark = {
            Outcome.PASSED: "PASS",
            Outcome.FAILED: "FAIL",
            Outcome.BLOCKED: "BLOCKED",
            Outcome.SKIPPED: "SKIP",
        }[self.outcome]
        line = f"[{mark}] {self.priority.value} {self.origin.value:<9} {self.scenario_id}"
        if self.risk_category:
            line += f"  ({self.risk_category})"
        if self.outcome is Outcome.SKIPPED and self.skip_reason:
            line += f"  — skipped: {self.skip_reason}"
        elif self.outcome is not Outcome.PASSED:
            detail = self.error or (
                self.failed_assertions[0] if self.failed_assertions else "no detail"
            )
            line += f"  — {detail[:180]}"
        return line


class SuiteResult(BaseModel):
    """The aggregate a run reasons about, and the evaluator is shown."""

    model_config = ConfigDict(extra="forbid")

    started_at: str = Field(default_factory=utcnow)
    duration_s: float = 0.0
    #: True when every entry in the suite was attempted. A narrowed rerun sets
    #: this False, which is what stops a partial pass from becoming an ACCEPT.
    full_run: bool = True
    selection_reason: str = ""
    #: Every required scenario this suite set out to verify, recorded before
    #: execution. The gate compares against this rather than against whatever
    #: produced a result, so a required scenario that never ran is visible as a
    #: gap instead of simply being absent from the counts.
    expected_required_ids: list[str] = Field(default_factory=list)
    #: Scenarios that never entered the suite because their id was already taken.
    #: Carried onto the result so the acceptance gate can refuse a run whose
    #: coverage was quietly reduced before a single scenario executed.
    assembly_problems: list[str] = Field(default_factory=list)
    outcomes: list[ScenarioOutcome] = Field(default_factory=list)
    clusters: list[FailureCluster] = Field(default_factory=list)

    # -- counts ------------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.outcomes)

    def _count(self, outcome: Outcome) -> int:
        return sum(1 for o in self.outcomes if o.outcome is outcome)

    @property
    def passed(self) -> int:
        return self._count(Outcome.PASSED)

    @property
    def failed(self) -> int:
        return self._count(Outcome.FAILED)

    @property
    def blocked(self) -> int:
        return self._count(Outcome.BLOCKED)

    @property
    def skipped(self) -> int:
        return self._count(Outcome.SKIPPED)

    # -- queries -----------------------------------------------------------

    def by_id(self, scenario_id: str) -> ScenarioOutcome | None:
        return next((o for o in self.outcomes if o.scenario_id == scenario_id), None)

    def failures(self) -> list[ScenarioOutcome]:
        return [o for o in self.outcomes if o.outcome in (Outcome.FAILED, Outcome.BLOCKED)]

    def blocking_failures(self) -> list[ScenarioOutcome]:
        """Failures that must prevent an ACCEPT."""
        return [o for o in self.outcomes if o.blocks_acceptance]

    def permanent_failures(self) -> list[ScenarioOutcome]:
        return [f for f in self.failures() if f.origin is Origin.PERMANENT]

    def coverage_by_risk_category(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for outcome in self.outcomes:
            if not outcome.risk_category:
                continue
            bucket = out.setdefault(
                outcome.risk_category, {"passed": 0, "failed": 0, "blocked": 0, "skipped": 0}
            )
            bucket[outcome.outcome.value.lower()] += 1
        return out

    @property
    def executed_required_all_passed(self) -> bool:
        """Did everything required that actually ran come back green?

        A narrower question than the acceptance gate, and a different one. This
        asks about the scenarios in *this* execution — which is what decides
        whether a narrowed rerun has earned the widening pass over the full
        required set. It deliberately says nothing about scenarios that were not
        selected; the gate is what refuses to accept while those are unproven.
        """
        return all(
            o.outcome is Outcome.PASSED and o.evidence_verified
            for o in self.outcomes
            if o.required
        )

    @property
    def everything_required_passed(self) -> bool:
        """Convenience view of the authoritative gate. Never the gate itself.

        Acceptance is decided by :func:`scenario_gate.evaluate_gate`, which
        recomputes from the outcome records. This property exists for display
        and for callers that want a quick answer; forcing it True cannot make an
        unverified run acceptable, because nothing on the acceptance path reads
        it.
        """
        from .scenario_gate import evaluate_gate

        return not evaluate_gate(self).blocks_acceptance

    # -- rendering ---------------------------------------------------------

    def headline(self) -> str:
        permanent = sum(1 for o in self.outcomes if o.origin is Origin.PERMANENT)
        generated = self.total - permanent
        return (
            f"{generated} generated case(s) + {permanent} permanent regression scenario(s): "
            f"{self.passed} passed, {self.failed} failed, {self.blocked} blocked, "
            f"{self.skipped} skipped"
        )

    def summary_block(self) -> str:
        """Compact, evidence-referencing summary. This is what the evaluator gets.

        Deliberately not a dump: counts, coverage, the failures that matter and
        the paths where the full artifacts live. Raw command output, response
        bodies, screenshots and traces stay on disk.
        """
        lines = [self.headline()]
        if not self.full_run:
            lines.append(
                f"NARROWED RUN — not every scenario was executed ({self.selection_reason}). "
                "This cannot support an acceptance on its own."
            )
        coverage = self.coverage_by_risk_category()
        if coverage:
            lines.append("risk categories exercised: " + ", ".join(sorted(coverage)))

        skipped = [o for o in self.outcomes if o.outcome is Outcome.SKIPPED]
        if skipped:
            lines.append("")
            lines.append("SKIPPED (with reason):")
            lines += [f"  {o.scenario_id}: {o.skip_reason}" for o in skipped]

        failures = self.failures()
        if failures:
            lines.append("")
            lines.append("FAILURES:")
            for outcome in failures:
                lines.append(f"  {outcome.brief()}")
                if outcome.generated_because:
                    lines.append(f"      generated because: {outcome.generated_because}")
                if outcome.requirement_reference:
                    lines.append(f"      verifies: {outcome.requirement_reference}")
                for assertion in outcome.failed_assertions[:4]:
                    lines.append(f"      failed: {assertion}")
                if outcome.evidence_path:
                    lines.append(f"      evidence: {outcome.evidence_path}")
        grouped = [c for c in self.clusters if not c.singleton]
        if grouped:
            lines.append("")
            lines.append(
                "FAILURE CLUSTERS — these appear to share one underlying cause, so "
                "correct the cause once rather than each symptom:"
            )
            lines += [f"  {line}" for c in grouped for line in c.render().splitlines()]

        blocking = self.blocking_failures()
        lines.append("")
        lines.append(
            f"{len(blocking)} unresolved high-priority scenario failure(s)"
            if blocking
            else "0 unresolved high-priority scenario failures"
        )
        lines.append(
            "This states verified coverage. It is not a claim that all possible cases "
            "were verified."
        )
        return "\n".join(lines)


# --------------------------------------------------------------------------
# The suite
# --------------------------------------------------------------------------


class ScenarioSuite(BaseModel):
    """Permanent regression coverage plus this run's generated coverage."""

    model_config = ConfigDict(extra="forbid")

    entries: list[SuiteEntry] = Field(default_factory=list)
    #: Scenarios that were offered to this suite and refused because their id was
    #: already taken. Recorded rather than discarded: a scenario that is not in
    #: the suite is not run, not evidenced and not counted, and a run that cannot
    #: say so is claiming coverage it does not have.
    assembly_conflicts: list[str] = Field(default_factory=list)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Any:
        return iter(self.entries)

    def by_id(self, scenario_id: str) -> SuiteEntry | None:
        return next((e for e in self.entries if e.scenario_id == scenario_id), None)

    def permanent(self) -> list[SuiteEntry]:
        return [e for e in self.entries if e.origin is Origin.PERMANENT]

    def generated(self) -> list[SuiteEntry]:
        return [e for e in self.entries if e.origin is Origin.GENERATED]

    def add(self, entry: SuiteEntry) -> bool:
        """Admit ``entry``. Returns False when an entry with that id already exists.

        The return value is not decoration. Dropping a scenario because its id
        was already taken is a decision with consequences — that scenario is not
        run, not evidenced, and not counted by the gate — and a caller that
        cannot see the decision cannot report it. Silence here is what let a
        required scenario disappear between the plan and the acceptance
        decision.
        """
        if self.by_id(entry.scenario_id) is not None:
            return False
        self.entries.append(entry)
        return True

    def execution_order(self, only: Sequence[str] | None = None) -> list[SuiteEntry]:
        """Deterministic order: dependencies first, then priority, then origin.

        Permanent scenarios sort ahead of generated ones at equal priority: if
        the change broke authoritative regression coverage, that is the thing to
        learn first, before spending time on situations we invented.
        """
        selected = [e for e in self.entries if only is None or e.scenario_id in set(only)]
        ranked = sorted(
            selected,
            key=lambda e: (
                e.priority.rank,
                0 if e.origin is Origin.PERMANENT else 1,
                e.scenario_id,
            ),
        )
        return _topological(ranked)

    def isolation_groups(self, only: Sequence[str] | None = None) -> list[list[SuiteEntry]]:
        """Partition into sets that provably do not contend for a resource.

        Everything sharing an ``isolation_key`` lands in one group. Nothing here
        runs those groups concurrently today — the runner is sequential — but the
        partition is what a future parallel runner would consume, and computing
        it now keeps the claim honest: we know what *could* be parallelised, and
        we have chosen not to.
        """
        groups: dict[str, list[SuiteEntry]] = {}
        for entry in self.execution_order(only):
            groups.setdefault(entry.isolation_key or "default", []).append(entry)
        return [groups[key] for key in sorted(groups)]


def _topological(entries: Sequence[SuiteEntry]) -> list[SuiteEntry]:
    """Stable dependency ordering. A cycle degrades to the input order."""
    remaining = list(entries)
    known = {e.scenario_id for e in remaining}
    emitted: set[str] = set()
    ordered: list[SuiteEntry] = []
    while remaining:
        progressed = False
        for entry in list(remaining):
            unmet = [d for d in entry.depends_on if d in known and d not in emitted]
            if unmet:
                continue
            ordered.append(entry)
            emitted.add(entry.scenario_id)
            remaining.remove(entry)
            progressed = True
        if not progressed:
            # Circular or unsatisfiable dependencies: emit the rest in the order
            # given rather than dropping scenarios on the floor.
            ordered.extend(remaining)
            break
    return ordered


# --------------------------------------------------------------------------
# Selection — what to rerun after a fix
# --------------------------------------------------------------------------


def select_rerun(
    suite: ScenarioSuite,
    previous: SuiteResult | None,
) -> tuple[list[str], str]:
    """Narrow the next pass to what a fix could plausibly have affected.

    The narrowing is deliberately asymmetric. **Every permanent scenario always
    reruns** — it is the authoritative regression set, a human decided each case
    matters, and there is no impact analysis trustworthy enough to skip one.
    Generated scenarios narrow to the ones that failed plus their risk
    neighbours, because those are what a correction to the shared cause would
    move.

    Returns ``(scenario_ids, reason)``. With no previous result, everything runs.
    """
    if previous is None:
        return [e.scenario_id for e in suite.entries], "first pass: the full suite"

    failed_ids = {o.scenario_id for o in previous.failures()}
    failed_categories: set[RiskCategory] = set()
    for outcome in previous.failures():
        entry = suite.by_id(outcome.scenario_id)
        if entry is not None and entry.risk_category is not None:
            failed_categories |= set(neighbours(entry.risk_category))

    selected: list[str] = []
    for entry in suite.entries:
        if entry.origin is Origin.PERMANENT:
            selected.append(entry.scenario_id)
        elif entry.scenario_id in failed_ids:
            selected.append(entry.scenario_id)
        elif entry.risk_category is not None and entry.risk_category in failed_categories:
            selected.append(entry.scenario_id)
        elif previous.by_id(entry.scenario_id) is None:
            # Never executed yet — a scenario generated during this wave.
            selected.append(entry.scenario_id)

    reason = (
        f"failed scenarios, their risk neighbours, newly generated cases, and the full "
        f"permanent regression set ({len(selected)} of {len(suite)})"
    )
    return selected, reason


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


class ScenarioRunnerLike(Protocol):
    service_logs: dict[str, str]

    async def execute(self, scenario: Scenario) -> ScenarioResult: ...


class SuiteExecutor:
    """Runs a suite and aggregates the results.

    ``make_executor`` is the same factory the single-scenario loop already uses,
    called once per scenario with that scenario's own artifact directory, so
    every scenario's evidence lands somewhere distinct and traceable.

    **Execution is sequential, and that is a property of this class rather than a
    setting.** :attr:`MAX_PARALLEL` states the only degree of concurrency ``run``
    implements, and the constructor refuses any other value instead of accepting
    it and ignoring it.
    """

    #: The only concurrency ``run`` implements. Not a default — a fact about the
    #: code below, which awaits each scenario before starting the next.
    MAX_PARALLEL = 1

    def __init__(
        self,
        *,
        make_executor: Callable[[Path], ScenarioRunnerLike],
        artifact_root: Path,
        browser_enabled: bool = False,
        execution_budget_s: float | None = None,
        max_parallel: int = 1,
        run_id: str = "",
        iteration: int = 0,
        emit: Callable[[str], None] = lambda _m: None,
    ) -> None:
        self.make_executor = make_executor
        self.artifact_root = Path(artifact_root)
        self.browser_enabled = browser_enabled
        self.execution_budget_s = execution_budget_s
        #: Stamped into every per-case evidence record, so a result can prove its
        #: evidence belongs to this run and this iteration rather than a leftover.
        self.run_id = run_id
        self.iteration = iteration
        # Mechanically wired to the only value this executor implements. It used
        # to be coerced with max(1, …), stored, and then never read again: a
        # caller that did not come through the config validator asked for eight
        # and was told it had eight, while `run` executed one at a time. A
        # parameter that reports a capability the code does not have is worse
        # than no parameter, because it is believed.
        if int(max_parallel) != self.MAX_PARALLEL:
            raise ValueError(
                f"max_parallel must be {self.MAX_PARALLEL}: this executor runs scenarios "
                "sequentially. Scenarios share services, ports, databases and a filesystem "
                "workspace, and nothing yet proves a given pair isolated. The isolation "
                "partition is computed (see ScenarioSuite.isolation_groups) so parallel "
                "execution can be added once it can be proven safe rather than assumed; "
                "until then this refuses rather than silently ignoring the request."
            )
        self.max_parallel = self.MAX_PARALLEL
        self.emit = emit
        self.service_logs: dict[str, str] = {}
        self.results: dict[str, ScenarioResult] = {}

    async def run(
        self,
        suite: ScenarioSuite,
        only: Sequence[str] | None = None,
        selection_reason: str = "",
    ) -> SuiteResult:
        started = time.monotonic()
        order = suite.execution_order(only)
        result = SuiteResult(
            full_run=only is None or len(order) == len(suite),
            selection_reason=selection_reason,
            # Recorded from the suite, not from the selection: a narrowed rerun
            # still owes evidence for every required scenario before acceptance.
            expected_required_ids=[e.scenario_id for e in suite.entries if e.required],
            assembly_problems=list(suite.assembly_conflicts),
        )

        budget_exhausted = False
        for entry in order:
            skip = self._skip_reason(entry, result, budget_exhausted)
            if skip:
                result.outcomes.append(self._skipped(entry, skip))
                continue

            if self.execution_budget_s is not None:
                if time.monotonic() - started >= self.execution_budget_s:
                    budget_exhausted = True
                    result.outcomes.append(
                        self._skipped(
                            entry,
                            f"the suite's {self.execution_budget_s:.0f}s execution budget was "
                            "exhausted before this scenario ran",
                        )
                    )
                    continue

            self.emit(f"  → {entry.scenario_id} ({entry.origin.value})")
            artifact_dir = self.artifact_root / "scenarios" / sanitize_filename(entry.scenario_id)
            artifact_dir.mkdir(parents=True, exist_ok=True)
            executor = self.make_executor(artifact_dir)
            scenario_result = await executor.execute(entry.scenario)
            scenario_result.scenario_id = entry.scenario_id
            scenario_result.origin = entry.origin.value  # type: ignore[assignment]
            self.results[entry.scenario_id] = scenario_result
            self.service_logs.update(getattr(executor, "service_logs", {}) or {})

            # The evidence is written before the outcome is judged, and the
            # outcome then carries whether that evidence actually resolves. A
            # result may not cite a directory it cannot show.
            try:
                write_case_evidence(
                    artifact_dir,
                    scenario_result,
                    scenario_id=entry.scenario_id,
                    run_id=self.run_id,
                    iteration=self.iteration,
                )
            except OSError as exc:
                self.emit(f"    could not persist evidence for {entry.scenario_id}: {exc}")

            outcome = self._outcome(entry, scenario_result, artifact_dir, time.monotonic())
            problem = verify_case_evidence(
                outcome.evidence_path,
                scenario_id=entry.scenario_id,
                run_id=self.run_id,
                iteration=self.iteration,
            )
            outcome.evidence_verified = not problem
            outcome.evidence_problem = problem
            if problem and outcome.outcome is Outcome.PASSED:
                # A pass that cannot show its evidence is not a pass anyone can
                # check. It becomes BLOCKED rather than being quietly believed.
                outcome.outcome = Outcome.BLOCKED
                outcome.error = (
                    outcome.error or ""
                ) + f" (evidence integrity: {problem})".strip()
                self.emit(f"    evidence integrity failure: {problem}")
            result.outcomes.append(outcome)

        result.duration_s = time.monotonic() - started
        result.clusters = cluster_failures(
            [
                FailureRecord.from_result(
                    self.results[o.scenario_id],
                    scenario_id=o.scenario_id,
                    origin=o.origin.value,
                    risk_category=_category(o.risk_category),
                    priority=o.priority,
                    evidence_path=o.evidence_path,
                )
                for o in result.failures()
                if o.scenario_id in self.results
            ]
        )
        return result

    # -- helpers ----------------------------------------------------------

    def _skip_reason(
        self, entry: SuiteEntry, result: SuiteResult, budget_exhausted: bool
    ) -> str:
        if budget_exhausted:
            return "the suite's execution budget was exhausted before this scenario ran"
        if entry.scenario.mode == "browser" and not self.browser_enabled:
            return (
                "this scenario needs a browser and browser support is disabled "
                "(set run.browser_enabled: true or pass --browser)"
            )
        for dependency in entry.depends_on:
            prior = result.by_id(dependency)
            if prior is not None and prior.outcome is not Outcome.PASSED:
                return f"its prerequisite {dependency} did not pass"
        return ""

    @staticmethod
    def _skipped(entry: SuiteEntry, reason: str) -> ScenarioOutcome:
        return ScenarioOutcome(
            scenario_id=entry.scenario_id,
            scenario_name=entry.scenario.name,
            origin=entry.origin,
            outcome=Outcome.SKIPPED,
            priority=entry.priority,
            risk_category=entry.risk_category.value if entry.risk_category else "",
            required=entry.required,
            skip_reason=reason,
            generated_because=_because(entry),
            requirement_reference=(
                entry.generated.requirement_reference if entry.generated else ""
            ),
        )

    @staticmethod
    def _outcome(
        entry: SuiteEntry,
        result: ScenarioResult,
        artifact_dir: Path,
        _now: float,
    ) -> ScenarioOutcome:
        # "Blocked" is reserved for never having observed the product at all:
        # readiness failed, or setup failed before anything was exercised. A
        # scenario that ran and disagreed with expectations is a FAILURE.
        never_observed = not result.readiness_ok or (
            result.error is not None and not result.assertions
        )
        if never_observed:
            outcome = Outcome.BLOCKED
        elif result.passed:
            outcome = Outcome.PASSED
        else:
            outcome = Outcome.FAILED
        failed = result.failed_assertions()
        return ScenarioOutcome(
            scenario_id=entry.scenario_id,
            scenario_name=result.scenario_name,
            origin=entry.origin,
            outcome=outcome,
            priority=entry.priority,
            risk_category=entry.risk_category.value if entry.risk_category else "",
            required=entry.required,
            assertions_total=len(result.assertions),
            assertions_failed=len(failed),
            failed_assertions=[f"{a.kind}: {a.target} — {a.detail}".strip(" —") for a in failed],
            error=result.error or ("" if result.readiness_ok else result.readiness_detail),
            evidence_path=str(artifact_dir),
            generated_because=_because(entry),
            requirement_reference=entry.generated.requirement_reference if entry.generated else "",
        )


def _because(entry: SuiteEntry) -> str:
    if entry.generated is None:
        return ""
    provenance = entry.generated.provenance
    return provenance.generating_risk or entry.generated.rationale or entry.generated.purpose


def _category(value: str) -> RiskCategory | None:
    try:
        return RiskCategory(value)
    except ValueError:
        return None


#: The per-case record every executed scenario writes into its own evidence
#: directory. Its presence is what makes ``evidence_path`` a fact rather than a
#: claim, and its contents are what the evaluator and the builder are invited to
#: read when a summary is not enough.
CASE_RECORD_FILENAME = "result.json"


def write_case_evidence(
    artifact_dir: Path,
    result: ScenarioResult,
    *,
    scenario_id: str,
    run_id: str = "",
    iteration: int = 0,
) -> None:
    """Persist a scenario's own result beside its artifacts.

    Without this the directory exists, is cited to the evaluator and to the
    builder as ``evidence:``, and contains nothing. Stamping the run, iteration
    and scenario id is what lets :func:`verify_case_evidence` tell this
    execution's evidence from a leftover directory belonging to another run,
    another iteration or another scenario.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)
    record = result.model_dump(mode="json")
    record["scenario_id"] = scenario_id
    record["run_id"] = run_id
    record["iteration"] = iteration
    record["written_at"] = utcnow()
    path = artifact_dir / CASE_RECORD_FILENAME
    # Written whole, then moved into place: an interrupted write must not leave
    # a half-parsed record that verification would read as corrupt evidence.
    staging = artifact_dir / f".{CASE_RECORD_FILENAME}.partial"
    staging.write_text(json.dumps(redact_obj(record), indent=2), encoding="utf-8")
    staging.replace(path)


def verify_case_evidence(
    evidence_path: str,
    *,
    scenario_id: str,
    run_id: str = "",
    iteration: int = 0,
) -> str:
    """Return why this evidence cannot be trusted, or "" when it can.

    Deterministic and cheap. Checks that the cited directory exists, holds this
    execution's record, and that the record belongs to *this* scenario, run and
    iteration — a stale directory from an earlier run is worse than no evidence,
    because it reads as proof.
    """
    if not evidence_path:
        return "the result cites no evidence directory"
    directory = Path(evidence_path)
    if not directory.exists():
        return f"the cited evidence directory does not exist: {evidence_path}"
    if not directory.is_dir():
        return f"the cited evidence path is not a directory: {evidence_path}"

    record_path = directory / CASE_RECORD_FILENAME
    if not record_path.exists():
        return (
            f"the cited evidence directory holds no {CASE_RECORD_FILENAME}, so nothing "
            f"records what this scenario actually did: {evidence_path}"
        )
    if record_path.stat().st_size == 0:
        return f"the evidence record is empty: {record_path}"

    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"the evidence record could not be read ({type(exc).__name__}): {record_path}"
    if not isinstance(record, dict):
        return f"the evidence record is not an object: {record_path}"

    if str(record.get("scenario_id", "")) != scenario_id:
        return (
            f"the evidence at {evidence_path} belongs to scenario "
            f"{record.get('scenario_id', '(unnamed)')!r}, not {scenario_id!r}"
        )
    if run_id and str(record.get("run_id", "")) != run_id:
        return (
            f"the evidence at {evidence_path} belongs to run "
            f"{record.get('run_id', '(unnamed)')!r}, not {run_id!r}"
        )
    if iteration and int(record.get("iteration", 0) or 0) != iteration:
        return (
            f"the evidence at {evidence_path} was written in iteration "
            f"{record.get('iteration')}, not {iteration}"
        )
    return ""


class FailureEvidence(BaseModel):
    """What a failure actually showed, in the shape adaptive generation needs.

    ``ScenarioOutcome.brief()`` exists for humans and for clustering: one
    assertion, truncated, with digit runs normalised away so that ``payments=2``
    and ``payments=7`` cluster together. That normalisation is right for grouping
    and wrong for generating — it deletes the one fact that says what went wrong.
    A generator told "expected payments=<n>, got payments=<n>" has been told
    nothing. This carries the observed values through intact.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    scenario_name: str = ""
    risk_category: str = ""
    priority: Priority = Priority.P2
    requirement_reference: str = ""
    generated_because: str = ""
    #: What the scenario asserted, and what it forbade.
    expected: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    #: Every assertion that failed — not just the first.
    failed_assertions: list[str] = Field(default_factory=list)
    error: str = ""
    #: A bounded excerpt of what the product actually produced. The
    #: discriminating fact: "payments=2" rather than "an expectation failed".
    observed: str = ""
    evidence_path: str = ""
    cluster_id: str = ""
    affected_components: list[str] = Field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"[{self.scenario_id}] {self.scenario_name or ''}".rstrip(),
            f"  risk: {self.risk_category or 'unclassified'} ({self.priority.value})"
            + (f"  cluster: {self.cluster_id}" if self.cluster_id else ""),
        ]
        if self.requirement_reference:
            lines.append(f"  verifies: {self.requirement_reference}")
        if self.generated_because:
            lines.append(f"  generated because: {self.generated_because}")
        if self.expected:
            lines.append("  expected: " + "; ".join(self.expected[:6]))
        if self.forbidden:
            lines.append("  must not appear: " + "; ".join(self.forbidden[:6]))
        for assertion in self.failed_assertions[:8]:
            lines.append(f"  FAILED: {assertion}")
        if self.error:
            lines.append(f"  error: {self.error}")
        if self.observed:
            lines.append(f"  observed output: {self.observed}")
        if self.affected_components:
            lines.append("  changed files in scope: " + ", ".join(self.affected_components[:10]))
        if self.evidence_path:
            lines.append(f"  evidence: {self.evidence_path}")
        return "\n".join(lines)


#: How much real output travels with a failure. Enough to name the value that
#: was wrong; far short of pasting a log into a prompt.
OBSERVED_EXCERPT_CHARS = 700


def _observed_excerpt(result: ScenarioResult | None) -> str:
    """The product's own output for a failed scenario, bounded and redacted."""
    if result is None:
        return ""
    parts: list[str] = []
    for command in result.commands:
        text = f"{command.stdout}\n{command.stderr}".strip()
        if text:
            parts.append(f"$ {command.command}\n{text}")
    for observation in result.http:
        body = (observation.body_text or "").strip()
        status = observation.status if observation.status is not None else observation.error
        parts.append(f"{observation.name or 'request'} -> {status}\n{body}")
    if result.browser is not None and result.browser.visible_text:
        parts.append(f"browser: {result.browser.visible_text}")
    joined = redact("\n".join(p for p in parts if p).strip())
    if len(joined) <= OBSERVED_EXCERPT_CHARS:
        return joined
    return joined[:OBSERVED_EXCERPT_CHARS] + " …(truncated; full output in the evidence directory)"


def build_failure_evidence(
    suite: "ScenarioSuite",
    result: SuiteResult,
    results: dict[str, ScenarioResult],
    *,
    diff_files: Sequence[str] = (),
) -> list[FailureEvidence]:
    """Assemble the structured brief adaptive generation is given."""
    cluster_of: dict[str, str] = {}
    for cluster in result.clusters:
        for scenario_id in cluster.affected_scenarios:
            cluster_of[scenario_id] = cluster.cluster_id

    evidence: list[FailureEvidence] = []
    for outcome in result.failures():
        entry = suite.by_id(outcome.scenario_id)
        scenario = entry.scenario if entry is not None else None
        evidence.append(
            FailureEvidence(
                scenario_id=outcome.scenario_id,
                scenario_name=outcome.scenario_name,
                risk_category=outcome.risk_category,
                priority=outcome.priority,
                requirement_reference=outcome.requirement_reference,
                generated_because=outcome.generated_because,
                expected=list(scenario.expect_visible) if scenario else [],
                forbidden=list(scenario.forbidden) if scenario else [],
                failed_assertions=list(outcome.failed_assertions),
                error=outcome.error,
                observed=_observed_excerpt(results.get(outcome.scenario_id)),
                evidence_path=outcome.evidence_path,
                cluster_id=cluster_of.get(outcome.scenario_id, ""),
                affected_components=list(diff_files)[:20],
            )
        )
    return evidence


def build_suite(
    *,
    permanent: Iterable[tuple[str, Scenario]] = (),
    generated: Iterable[tuple[GeneratedScenario, Scenario]] = (),
) -> ScenarioSuite:
    """Assemble a suite from permanent scenarios and compiled generated ones."""
    suite = ScenarioSuite()

    def admit(entry: SuiteEntry, what: str) -> None:
        if not suite.add(entry):
            suite.assembly_conflicts.append(
                f"{what} {entry.scenario_id!r} was not admitted to the suite: that id is "
                "already in use, so this scenario was never executed and nothing about it "
                "has been verified"
            )

    for scenario_id, scenario in permanent:
        admit(
            SuiteEntry(
                scenario_id=scenario_id,
                scenario=scenario,
                origin=Origin.PERMANENT,
                # A human wrote it down; a failure in it always blocks.
                priority=Priority.P0,
                isolation_key="default",
                required=True,
            ),
            "the permanent scenario",
        )
    for model, compiled in generated:
        admit(
            SuiteEntry(
                scenario_id=model.id,
                scenario=compiled,
                origin=Origin.GENERATED,
                priority=model.priority,
                risk_category=model.risk_category,
                isolation_key=model.isolation_key or "default",
                generated=model,
                # `required` means required. A P2/P3 generated case is worth
                # running and reporting but was never meant to hold the run, and
                # marking it required-but-ignorable made the flag lie: the gate
                # would have had to consult priority to know what "required"
                # meant. The judgement still lives in the priority the generator
                # assigned; it is simply recorded honestly here.
                required=model.priority.blocks_acceptance,
            ),
            "the generated scenario",
        )
    return suite


__all__ = [
    "Origin",
    "Outcome",
    "ScenarioOutcome",
    "ScenarioSuite",
    "SuiteEntry",
    "SuiteExecutor",
    "SuiteResult",
    "build_suite",
    "select_rerun",
]
