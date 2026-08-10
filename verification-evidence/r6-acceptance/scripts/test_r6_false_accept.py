"""REVIEWER 6 — false-confidence / acceptance attacks against the REAL loop.

Nothing here weakens or strengthens a gate. Every case constructs a situation
and records what run_control_loop ACTUALLY decides.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from neyma_product_driver.cli import _apply_suite_precedence, run_control_loop
from neyma_product_driver.config import DriverConfig, ScenarioGenerationConfig
from neyma_product_driver.context import ActiveUnit, RepositoryContext
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import (
    AssertionResult,
    CommandResult,
    Decision,
    EvaluatorDecision,
    RunState,
    RunStatus,
    ScenarioResult,
)
from neyma_product_driver.scenario_plan import Priority
from neyma_product_driver.scenario_planner import ScenarioPlanner
from neyma_product_driver.scenario_suite import (
    Origin,
    Outcome,
    ScenarioOutcome,
    SuiteResult,
)
from neyma_product_driver.scenarios import Scenario, ServiceSpec

from scenario_fixtures import (
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    base_scenario,
    raw_payload,
    raw_scenario,
)

RESULTS: list[dict] = []


def record(case: str, constructed: str, status, decision, correct: bool, note: str = "") -> None:
    RESULTS.append(
        {
            "case": case,
            "constructed": constructed,
            "status": getattr(status, "value", str(status)),
            "decision": getattr(getattr(decision, "decision", None), "value", str(decision)),
            "correct": correct,
            "note": note,
        }
    )


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


@dataclass
class FakeTurn:
    text: str = "done.\n\nRUNNABLE CHECKPOINT: run `make demo`."
    session_id: str | None = "builder-1"
    tool_uses: list[str] = field(default_factory=list)
    denied_requests: list[str] = field(default_factory=list)
    is_error: bool = False
    error_detail: str = ""


class FakeBuilder:
    def __init__(self, text: str | None = None) -> None:
        self.session_id = "builder-1"
        self.prompts: list[str] = []
        self.text = text

    async def send(self, prompt, timeout_s=None):
        self.prompts.append(prompt)
        return FakeTurn() if self.text is None else FakeTurn(text=self.text)


class FakeRepoLoader:
    def __init__(self, unit=None):
        self.unit = unit or FakeUnit()

    def resolve_active_unit(self):
        return self.unit

    def load(self, topics=None):
        return RepositoryContext(
            head_commit="abc1234",
            branch="main",
            dirty_file_count=0,
            active_unit=ActiveUnit(
                unit_id=self.unit.unit_id,
                name=self.unit.name,
                status="READY",
                acceptance_criteria=self.unit.acceptance_criteria,
            ),
            authority_excerpt="",
            current_excerpt="",
            topic_excerpts={},
            files_consulted=[],
            fingerprint="fp",
        )


class FakeEvaluator:
    """Always ACCEPTs — the false-confidence source under attack."""

    def __init__(self, decisions=None):
        self.session_id = "evaluator-1"
        self.decisions = list(decisions or [])
        self.prompts: list[str] = []

    async def evaluate(self, prompt, timeout_s=None):
        self.prompts.append(prompt)
        if self.decisions:
            return self.decisions.pop(0)
        return accept()


def accept(**kw) -> EvaluatorDecision:
    return EvaluatorDecision(
        decision=Decision.ACCEPT, summary="looks good to me", observed_behavior=["saw it"], **kw
    )


class ScriptedExecutor:
    """Executes a per-scenario script. Keys are bare scenario ids."""

    def __init__(self, artifact_dir: Path, script: dict, log: list[str]) -> None:
        self.artifact_dir = artifact_dir
        self.script = script
        self.log = log
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        key = scenario.name.split(":", 1)[-1]
        self.log.append(key)
        spec = self.script.get(key, {"pass": True})
        if spec.get("raise"):
            raise RuntimeError(spec["raise"])
        return ScenarioResult(
            scenario_name=scenario.name,
            commands=[
                CommandResult(command=c, exit_code=0, stdout="ok", stderr="", duration_s=0.0)
                for c in spec.get("commands", [])
            ],
            readiness_ok=spec.get("readiness_ok", True),
            readiness_detail=spec.get("readiness_detail", ""),
            error=spec.get("error"),
            assertions=[
                AssertionResult(
                    kind="expect_state",
                    target=f"{key}: {a.get('target','state')}",
                    passed=a["passed"],
                    detail=a.get("detail", ""),
                )
                for a in spec.get("assertions", [{"passed": spec.get("pass", True)}])
            ],
        )


def make_planner(config: DriverConfig, store: EvidenceStore, payloads: list) -> ScenarioPlanner:
    config.scenario_generation = ScenarioGenerationConfig(enabled=True)
    return ScenarioPlanner(
        repo=config.neyma_repo,
        config=config.scenario_generation,
        reasoner=ScriptedReasoner(payloads),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )


@pytest.fixture
def loop_bits(driver_config: DriverConfig, request):
    assert driver_config.runs_dir is not None
    store = EvidenceStore(driver_config.runs_dir, f"r6-{request.node.name[:40]}")
    state = RunState(
        run_id=store.run_id,
        task="build supervised carrier invoice approval",
        max_iterations=driver_config.max_iterations,
    )
    return driver_config, store, state


async def drive(config, store, state, scenario, script, *, planner=None, evaluator=None,
                auditor=None, protocol_resolver=None, builder=None, log=None):
    log = log if log is not None else []
    return await run_control_loop(
        config=config,
        scenario=scenario,
        store=store,
        state=state,
        builder=builder or FakeBuilder(),
        evaluator=evaluator or FakeEvaluator(),
        make_executor=lambda d: ScriptedExecutor(d, script, log),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        auditor=auditor,
        protocol_resolver=protocol_resolver,
        planner=planner,
    )


# ==========================================================================
# CASE 1 — unit tests pass, the UI is wrong
# ==========================================================================


@pytest.mark.asyncio
async def test_case1_no_planner_failing_scenario_plus_accept(loop_bits):
    """Legacy path: no suite. Unit-test commands exit 0, the UI assertion fails,
    the evaluator ACCEPTs anyway."""
    config, store, state = loop_bits
    script = {
        "backend_generic": {
            "commands": ["pytest -q"],
            "assertions": [{"passed": False, "target": "owner column rendered",
                            "detail": "the owner column is absent from the approval table"}],
        }
    }
    result = await drive(config, store, state, base_scenario(), script)
    record(
        "1 (no planner / legacy single-scenario path)",
        "unit-test commands exit 0; the scenario's UI assertion FAILS; evaluator returns ACCEPT",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
        note="suite_result is None so _apply_suite_precedence never runs",
    )


@pytest.mark.asyncio
async def test_case1_with_planner(loop_bits):
    config, store, state = loop_bits
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-ui"))])
    script = {
        "backend_generic": {
            "commands": ["pytest -q"],
            "assertions": [{"passed": False, "target": "owner column rendered",
                            "detail": "absent"}],
        },
        "gen-ui": {"pass": True},
    }
    result = await drive(config, store, state, base_scenario(), script, planner=planner)
    record(
        "1 (with planner / suite path)",
        "same, but a suite runs: permanent scenario FAILS its UI assertion, generated pass, evaluator ACCEPTs",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
    )


# ==========================================================================
# CASE 2 — UI looks correct, persisted state is wrong
# ==========================================================================


@pytest.mark.asyncio
async def test_case2_persisted_state_wrong(loop_bits):
    config, store, state = loop_bits
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])
    script = {
        "backend_generic": {"pass": True},
        # the generated persisted-state check is the thing that catches it
        "gen-dup": {"assertions": [{"passed": False, "target": "payments row count",
                                    "detail": "got 2, want 1"}]},
    }
    result = await drive(config, store, state, base_scenario(), script, planner=planner)
    record(
        "2",
        "UI/permanent scenario passes; generated persisted-state check FAILS (payments=2); evaluator ACCEPTs",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
    )


@pytest.mark.asyncio
async def test_case2_no_planner(loop_bits):
    config, store, state = loop_bits
    script = {"backend_generic": {"pass": True}}
    result = await drive(config, store, state, base_scenario(), script)
    record(
        "2b (no planner)",
        "UI passes, no persisted-state coverage exists at all, evaluator ACCEPTs",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
        note="with generation off there is no state coverage; nothing forces any",
    )


# ==========================================================================
# CASE 3 — generated all pass, a required PERMANENT regression fails
# ==========================================================================


@pytest.mark.asyncio
async def test_case3_permanent_regression_fails(loop_bits):
    config, store, state = loop_bits
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-a"), raw_scenario("gen-b"))])
    script = {
        "backend_generic": {"assertions": [{"passed": False, "target": "regression",
                                            "detail": "the permanent regression broke"}]},
        "gen-a": {"pass": True},
        "gen-b": {"pass": True},
    }
    result = await drive(config, store, state, base_scenario(), script, planner=planner)
    record(
        "3",
        "all generated scenarios PASS; the permanent P0 regression scenario FAILS; evaluator ACCEPTs",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
    )


# ==========================================================================
# CASE 4 — evaluator ACCEPTs, completion auditor contradicts
# ==========================================================================


class FakeAudit:
    def __init__(self, decision_name: str, blocks: bool):
        from neyma_product_driver.completion_auditor import AuditDecision

        self.decision = getattr(AuditDecision, decision_name)
        self.blocks_acceptance = blocks
        self.headline = "the registry says COMPLETE but no receipt supports it"
        self.contradictions = []
        self.missing_evidence = ["no test receipt bound to HEAD"]
        self.evidence_paths = ["/runs/x/audit.json"]
        self.correction_prompt = (
            "The unit registry records U-042 as complete, but no receipt at HEAD supports "
            "that. Restore the registry status to the state the evidence supports, and do "
            "not hand-edit derived status."
        )
        self.confidence = 0.9

        class _S:
            active_unit_id = "U-042"

            class progress:
                independent_pending = ["independent review"]

        self.observed_state = _S()

    def model_dump(self, mode="json"):
        return {"decision": self.decision.value, "headline": self.headline}

    def summary_block(self):
        return "AUDIT: contradicted"


class FakeAuditor:
    def __init__(self, audit):
        self.audit_obj = audit

    def audit(self, *a, **kw):
        return self.audit_obj


@pytest.mark.asyncio
async def test_case4_auditor_contradicts(loop_bits):
    config, store, state = loop_bits
    script = {"backend_generic": {"pass": True}}
    result = await drive(
        config, store, state, base_scenario(), script,
        auditor=FakeAuditor(FakeAudit("CONTRADICTED", True)),
    )
    record(
        "4 (CONTRADICTED)",
        "scenario passes, evaluator ACCEPTs, completion auditor reports CONTRADICTED/blocks_acceptance",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
    )


@pytest.mark.asyncio
async def test_case4b_requires_independent_review(loop_bits):
    config, store, state = loop_bits
    script = {"backend_generic": {"pass": True}}
    result = await drive(
        config, store, state, base_scenario(), script,
        auditor=FakeAuditor(FakeAudit("REQUIRES_INDEPENDENT_REVIEW", True)),
    )
    record(
        "4b (REQUIRES_INDEPENDENT_REVIEW)",
        "same, auditor requires an independent reviewer",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
    )


# ==========================================================================
# CASE 5 — evaluator ACCEPTs, protocol resolver reports a violation
# ==========================================================================


def _resolution(status_name: str):
    from neyma_product_driver.protocol_resolver import ProtocolStatus

    class V:
        rule_id = "R-TOPO-1"
        rule_citation = "CLAUDE.md#commit-topology"
        detail = "the status commit precedes the content commit"
        observed_state = "status@HEAD, content@HEAD~1"
        expected_state = "content then status"
        evidence_paths = ["/runs/x/topology.json"]

        def render(self):
            return "R-TOPO-1: status commit precedes content commit"

    class B:
        def render(self):
            return "chromium is not installed; the browser gate could not run"

    class R:
        status = getattr(ProtocolStatus, status_name)
        violations = [V()] if status_name == "VIOLATION" else []
        conflicts = []
        deadlocks = []
        environment_blockers = [B()] if status_name == "BLOCKED_ENVIRONMENT" else []
        sources_read = ["/repo/CLAUDE.md"]
        current_graph = "status -> content"
        expected_graph = "content -> status"
        next_safe_action = "re-create the status commit on top of the content commit"
        approval_prompt = ""

        def cause(self, _kind):
            return None

        def blocker_chain(self):
            return ""

        def model_dump(self, mode="json"):
            return {"status": self.status.value}

        def summary_block(self):
            return f"PROTOCOL: {self.status.value}"

        def render_report(self, run_id=""):
            return "report"

    return R()


class FakeProtocolResolver:
    def __init__(self, resolution):
        self.resolution = resolution

    def resolve(self, run_commands=None):
        return self.resolution


@pytest.mark.asyncio
async def test_case5_protocol_violation(loop_bits):
    config, store, state = loop_bits
    config.max_iterations = 1
    script = {"backend_generic": {"pass": True}}
    result = await drive(
        config, store, state, base_scenario(), script,
        protocol_resolver=FakeProtocolResolver(_resolution("VIOLATION")),
    )
    record(
        "5 (VIOLATION)",
        "scenario passes, evaluator ACCEPTs, protocol resolver reports a repository VIOLATION",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
    )


@pytest.mark.asyncio
async def test_case5b_protocol_env_blocker(loop_bits):
    config, store, state = loop_bits
    config.max_iterations = 1
    script = {"backend_generic": {"pass": True}}
    result = await drive(
        config, store, state, base_scenario(), script,
        protocol_resolver=FakeProtocolResolver(_resolution("BLOCKED_ENVIRONMENT")),
    )
    record(
        "5b (BLOCKED_ENVIRONMENT)",
        "evaluator ACCEPTs, protocol resolver reports an environmental blocker",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
    )


# ==========================================================================
# CASE 6 — malformed generated scenario output
# ==========================================================================


@pytest.mark.asyncio
async def test_case6_malformed_generation(loop_bits):
    config, store, state = loop_bits
    # Every proposal is garbage: bad json shape, unknown risk category, an
    # ungrounded requirement, and a scenario that runs an unapproved command.
    malformed = {
        "risks": "not-a-list",
        "scenarios": [
            {"id": "broken"},  # missing everything
            raw_scenario("gen-evil", actions=[{"kind": "command", "command": "rm -rf /"}]),
            raw_scenario("gen-ungrounded", requirement="U-999: a requirement nobody wrote"),
        ],
    }
    planner = make_planner(config, store, [malformed])
    script = {"backend_generic": {"pass": True}}
    log: list[str] = []
    result = await drive(config, store, state, base_scenario(), script, planner=planner, log=log)
    record(
        "6",
        "the generator returns MALFORMED payloads (bad shape, unapproved command, ungrounded requirement)",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
        note=f"scenarios actually executed: {log}; suite={result.suite.headline() if result.suite else None}",
    )


@pytest.mark.asyncio
async def test_case6b_generator_raises(loop_bits):
    from scenario_fixtures import ExplodingReasoner

    config, store, state = loop_bits
    config.scenario_generation = ScenarioGenerationConfig(enabled=True)
    planner = ScenarioPlanner(
        repo=config.neyma_repo,
        config=config.scenario_generation,
        reasoner=ExplodingReasoner(),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )
    script = {"backend_generic": {"pass": True}}
    log: list[str] = []
    result = await drive(config, store, state, base_scenario(), script, planner=planner, log=log)
    record(
        "6b",
        "the generator session DIES (raises) during planning",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
        note=f"executed: {log}",
    )


# ==========================================================================
# CASE 7 — execution crashes / times out
# ==========================================================================


@pytest.mark.asyncio
async def test_case7_executor_raises(loop_bits):
    config, store, state = loop_bits
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-crash"))])
    script = {"backend_generic": {"pass": True}, "gen-crash": {"raise": "subprocess died"}}
    status = None
    decision = None
    note = ""
    try:
        result = await drive(config, store, state, base_scenario(), script, planner=planner)
        status, decision = result.status, result.final_decision
    except Exception as exc:  # noqa: BLE001
        note = f"propagated {type(exc).__name__}: {exc}"
        status = "EXCEPTION_PROPAGATED"
    record(
        "7 (executor raises)",
        "a generated scenario's executor raises mid-run; evaluator would ACCEPT",
        status,
        decision,
        correct=status != RunStatus.ACCEPTED,
        note=note,
    )


@pytest.mark.asyncio
async def test_case7b_executor_timeout(loop_bits):
    config, store, state = loop_bits
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-slow"))])
    script = {
        "backend_generic": {"pass": True},
        # a timeout: an error with no assertions -> "never observed"
        "gen-slow": {"error": "scenario timed out after 300s", "assertions": []},
    }
    result = await drive(config, store, state, base_scenario(), script, planner=planner)
    record(
        "7b (timeout / error, no assertions)",
        "a generated scenario times out (error set, zero assertions); evaluator ACCEPTs",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
    )


@pytest.mark.asyncio
async def test_case7c_readiness_failed(loop_bits):
    config, store, state = loop_bits
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-x"))])
    script = {
        "backend_generic": {"pass": True},
        "gen-x": {"readiness_ok": False, "readiness_detail": "the service never bound 8931",
                  "assertions": []},
    }
    result = await drive(config, store, state, base_scenario(), script, planner=planner)
    record(
        "7c (service never came up)",
        "a generated scenario's product never became ready; evaluator ACCEPTs",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
    )


# ==========================================================================
# CASE 8 — a REQUIRED scenario is SKIPPED for environmental reasons
# ==========================================================================


@pytest.mark.asyncio
async def test_case8_permanent_browser_scenario_skipped(loop_bits):
    """The authoritative P0 permanent scenario needs a browser; the browser is
    disabled. It is SKIPPED. Everything else is green. Evaluator ACCEPTs."""
    config, store, state = loop_bits
    config.run.browser_enabled = False
    browser_scenario = Scenario(
        name="browser_generic",
        mode="browser",
        app_url="http://127.0.0.1:8931",
        services=[ServiceSpec(name="api", command="./serve.sh")],
    )
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-ok"))])
    script = {"gen-ok": {"pass": True}}
    log: list[str] = []
    result = await drive(config, store, state, browser_scenario, script, planner=planner, log=log)
    skipped = [o.scenario_id for o in (result.suite.outcomes if result.suite else []) if o.outcome is Outcome.SKIPPED]
    record(
        "8 (required PERMANENT scenario skipped: no browser)",
        "the P0 permanent browser scenario cannot run (browser disabled) -> SKIPPED; generated pass; evaluator ACCEPTs",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
        note=f"skipped={skipped}; executed={log}",
    )


@pytest.mark.asyncio
async def test_case8b_execution_budget_exhausted(loop_bits):
    """Zero execution budget: every scenario is SKIPPED with a reason."""
    config, store, state = loop_bits
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-ok"))])
    planner.config.execution_budget_s = 0
    config.scenario_generation.execution_budget_s = 0
    script = {"backend_generic": {"pass": True}, "gen-ok": {"pass": True}}
    log: list[str] = []
    result = await drive(config, store, state, base_scenario(), script, planner=planner, log=log)
    outcomes = [(o.scenario_id, o.outcome.value) for o in (result.suite.outcomes if result.suite else [])]
    record(
        "8b (whole suite skipped: execution budget exhausted)",
        "execution_budget_s=0 so every required scenario is SKIPPED; evaluator ACCEPTs",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
        note=f"outcomes={outcomes}; executed={log}",
    )


@pytest.mark.asyncio
async def test_case8c_dependency_skip(loop_bits):
    """A required scenario skipped because its prerequisite did not pass."""
    config, store, state = loop_bits
    suite = SuiteResult(
        outcomes=[
            ScenarioOutcome(scenario_id="perm", scenario_name="perm", origin=Origin.PERMANENT,
                            outcome=Outcome.SKIPPED, priority=Priority.P0, required=True,
                            skip_reason="its prerequisite did not pass"),
        ]
    )
    decision = _apply_suite_precedence(suite, accept(), "perm", lambda _m: None)
    record(
        "8c (unit: SKIPPED required P0 outcome)",
        "_apply_suite_precedence given a suite whose only required P0 outcome is SKIPPED",
        "n/a (unit)",
        decision,
        correct=decision.decision is not Decision.ACCEPT,
        note=f"everything_required_passed={suite.everything_required_passed}",
    )


# ==========================================================================
# CASE 9 — coverage gaps around a high-risk change
# ==========================================================================


@pytest.mark.asyncio
async def test_case9_coverage_gap(loop_bits):
    """The generator produces exactly one trivial low-risk scenario for a change
    that touched payments and auth. Nothing covers the risky surface."""
    config, store, state = loop_bits
    planner = make_planner(
        config,
        store,
        [
            {
                "risks": [
                    {"id": "R1", "description": "double payment on retry",
                     "risk_category": "idempotency", "severity": "P0",
                     "basis": "the diff touched the approval transition"},
                    {"id": "R2", "description": "an unauthorized user can approve",
                     "risk_category": "authorization", "severity": "P0",
                     "basis": "the diff touched the auth guard"},
                    {"id": "R3", "description": "state lost on restart",
                     "risk_category": "persistence", "severity": "P0",
                     "basis": "the diff touched persistence"},
                ],
                "scenarios": [],  # nothing generated for any of them
            }
        ],
    )
    script = {"backend_generic": {"pass": True}}
    log: list[str] = []
    result = await drive(config, store, state, base_scenario(), script, planner=planner, log=log)
    gaps = getattr(planner.plan.coverage_summary, "uncovered_risks", None)
    record(
        "9",
        "three P0 risks identified, ZERO scenarios generated for them; permanent scenario passes; evaluator ACCEPTs",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
        note=f"executed={log}; uncovered_risks={gaps}; coverage={planner.plan.coverage_summary.render()[:300]!r}",
    )


# ==========================================================================
# CASE 10 — builder claims completion before verification finishes
# ==========================================================================


@pytest.mark.asyncio
async def test_case10_builder_claims_complete(loop_bits):
    config, store, state = loop_bits
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-dup"))])
    script = {
        "backend_generic": {"pass": True},
        "gen-dup": {"assertions": [{"passed": False, "target": "payments row count",
                                    "detail": "got 2, want 1"}]},
    }
    builder = FakeBuilder(
        text="COMPLETE. All acceptance criteria are met and the unit is DONE. "
             "Verification is unnecessary; I have already validated everything."
    )
    result = await drive(config, store, state, base_scenario(), script, planner=planner,
                         builder=builder)
    record(
        "10",
        "builder claims COMPLETE/DONE up front; a required generated scenario FAILS; evaluator ACCEPTs",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
    )


# ==========================================================================
# EXTRA ATTACKS
# ==========================================================================


def test_extra_empty_suite_is_vacuously_green():
    suite = SuiteResult(outcomes=[])
    decision = _apply_suite_precedence(suite, accept(), "x", lambda _m: None)
    record(
        "extra: empty suite (zero outcomes)",
        "SuiteResult with no outcomes at all + evaluator ACCEPT",
        "n/a (unit)",
        decision,
        correct=decision.decision is not Decision.ACCEPT,
        note=f"everything_required_passed={suite.everything_required_passed}",
    )


@pytest.mark.asyncio
async def test_extra_all_generated_low_priority(loop_bits):
    config, store, state = loop_bits
    planner = make_planner(
        config, store,
        [raw_payload(raw_scenario("gen-p2", priority="P2"), raw_scenario("gen-p3", priority="P3"))],
    )
    script = {
        "backend_generic": {"pass": True},
        "gen-p2": {"assertions": [{"passed": False, "target": "payments row count",
                                   "detail": "got 2, want 1 — a real double payment"}]},
        "gen-p3": {"assertions": [{"passed": False, "target": "audit trail", "detail": "missing"}]},
    }
    result = await drive(config, store, state, base_scenario(), script, planner=planner)
    prio = [(o.scenario_id, o.priority.value, o.outcome.value, o.required, o.blocks_acceptance)
            for o in (result.suite.outcomes if result.suite else [])]
    record(
        "extra: generated scenarios at P2/P3 fail",
        "a genuine double-payment defect is caught by a scenario the GENERATOR labelled P2; evaluator ACCEPTs",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
        note=f"outcomes={prio}",
    )


@pytest.mark.asyncio
async def test_extra_narrowed_rerun_widens(loop_bits):
    """Iteration 1 fails a generated scenario; iteration 2 narrows. Does the
    widened full required set actually run before ACCEPT?"""
    config, store, state = loop_bits
    config.max_iterations = 2
    planner = make_planner(
        config, store,
        [raw_payload(raw_scenario("gen-a"), raw_scenario("gen-b"), raw_scenario("gen-c")), None],
    )

    calls = {"n": 0}
    log: list[str] = []

    class TwoPhase(ScriptedExecutor):
        async def execute(self, scenario):
            key = scenario.name.split(":", 1)[-1]
            calls["n"] += 1
            # iteration 1: gen-a fails. iteration 2: everything passes.
            failing = state.iteration == 1 and key == "gen-a"
            log.append(f"i{state.iteration}:{key}")
            return ScenarioResult(
                scenario_name=scenario.name,
                assertions=[AssertionResult(kind="expect_state", target=key,
                                            passed=not failing,
                                            detail="" if not failing else "got 2, want 1")],
            )

    result = await run_control_loop(
        config=config,
        scenario=base_scenario(),
        store=store,
        state=state,
        builder=FakeBuilder(),
        evaluator=FakeEvaluator(),
        make_executor=lambda d: TwoPhase(d, {}, log),
        emit=lambda _m: None,
        repo_loader=FakeRepoLoader(),
        planner=planner,
    )
    ran_i2 = sorted({e.split(":", 1)[1] for e in log if e.startswith("i2:")})
    record(
        "extra: narrowed rerun -> ACCEPT",
        "iteration 1 fails gen-a; iteration 2 narrows then must widen before ACCEPT",
        result.status,
        result.final_decision,
        correct=(result.status is not RunStatus.ACCEPTED)
        or (result.suite is not None and result.suite.full_run
            and {"backend_generic", "gen-a", "gen-b", "gen-c"} <= set(ran_i2)),
        note=f"iteration-2 scenarios executed={ran_i2}; full_run={result.suite.full_run if result.suite else None}",
    )


@pytest.mark.asyncio
async def test_extra_vacuous_assertion_scenario(loop_bits):
    """A generated scenario that asserts nothing at all 'passes'."""
    config, store, state = loop_bits
    planner = make_planner(config, store, [raw_payload(raw_scenario("gen-vacuous"))])
    script = {
        "backend_generic": {"pass": True},
        "gen-vacuous": {"assertions": []},  # ran nothing, asserted nothing
    }
    result = await drive(config, store, state, base_scenario(), script, planner=planner)
    outcomes = [(o.scenario_id, o.outcome.value, o.assertions_total)
                for o in (result.suite.outcomes if result.suite else [])]
    record(
        "extra: vacuous scenario (zero assertions)",
        "a generated scenario returns zero assertions and no error; evaluator ACCEPTs",
        result.status,
        result.final_decision,
        correct=all(o[2] > 0 for o in outcomes if o[0] != "backend_generic") or result.status is not RunStatus.ACCEPTED,
        note=f"outcomes={outcomes} (a zero-assertion result is ScenarioResult.passed==True)",
    )


@pytest.mark.asyncio
async def test_extra_suite_none_bypass(loop_bits):
    """Direct check: with generation off (the DEFAULT), does a hard scenario
    failure plus an ACCEPT reach ACCEPTED?"""
    config, store, state = loop_bits
    script = {"backend_generic": {"error": "the app crashed on startup", "assertions": []}}
    result = await drive(config, store, state, base_scenario(), script)
    record(
        "extra: suite_result is None bypass",
        "generation OFF (default). Scenario errors out entirely. Evaluator ACCEPTs.",
        result.status,
        result.final_decision,
        correct=result.status is not RunStatus.ACCEPTED,
        note="no suite -> _apply_suite_precedence at cli.py:605 is skipped entirely",
    )


def test_zz_write_report():
    dest = Path("/Users/sammyfammy/neyma-product-driver/verification-evidence/r6-acceptance")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "results.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    lines = ["CASE | STATUS | DECISION | CORRECT | NOTE", "-" * 100]
    for r in RESULTS:
        lines.append(
            f"{r['case']} | {r['status']} | {r['decision']} | "
            f"{'yes' if r['correct'] else 'NO -- FALSE ACCEPT'} | {r['note']}"
        )
    (dest / "results.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
