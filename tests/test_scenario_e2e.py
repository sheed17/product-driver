"""End-to-end: a real buggy app, generated coverage, adaptive expansion, acceptance.

This is the whole loop against a product that actually runs — a tiny HTTP
approval service started as a real subprocess, driven over real sockets, with a
real defect in it. The builder and evaluator are faked (nothing here consumes
Claude usage) and scenario generation is scripted, but the *execution* is
genuine: real processes, real HTTP, real restarts, real persisted state.

The defect is the one the design is built around: approving twice pays twice,
and an approval does not survive a restart. Acceptance is reached only after the
fix, and only when the evidence is actually green.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from neyma_product_driver.cli import run_control_loop
from neyma_product_driver.config import (
    DriverConfig,
    ScenarioGenerationConfig,
    ScenarioRunConfig,
)
from neyma_product_driver.context import ActiveUnit, RepositoryContext
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import Decision, EvaluatorDecision, RunState, RunStatus
from neyma_product_driver.scenario_planner import ScenarioPlanner
from neyma_product_driver.scenario_suite import Origin, Outcome
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor, ServiceSpec

from scenario_fixtures import FakeFounder, FakeUnit, ScriptedReasoner

# --------------------------------------------------------------------------
# The tiny product
# --------------------------------------------------------------------------

#: An approval service with two real defects when APPROVAL_FIXED is unset:
#:   1. approving twice records two payments (not idempotent)
#:   2. reads are served from memory, so a restarted process reports no
#:      approvals even though the payment ledger on disk still has them —
#:      the classic UI/backend disagreement after a recovery
#: The fixed build records each invoice once and reads back what was persisted.
APP_SOURCE = '''\
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

STATE = os.environ["APPROVAL_STATE"]
HERE = os.path.dirname(os.path.abspath(__file__))
MEMORY = {"payments": []}


def fixed():
    # Read per request rather than captured at import: the "builder" fixes the
    # product by writing this marker, and the running service must pick that up
    # the same way a real code change would after a restart.
    return os.path.exists(os.path.join(HERE, "FIXED"))


def load():
    # The buggy build answers from memory and never consults what it wrote, so
    # a restarted process disagrees with its own ledger.
    if fixed() and os.path.exists(STATE):
        with open(STATE) as fh:
            return json.load(fh)
    return MEMORY


def save(state):
    MEMORY.update(state)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, STATE)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._reply(200, {"ok": True})
        state = load()
        return self._reply(200, {"payments": state["payments"]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._reply(400, {"error": "malformed request"})
        invoice = body.get("invoice")
        if not invoice:
            return self._reply(400, {"error": "invoice is required"})

        state = load()
        payments = list(state["payments"])
        if fixed() and invoice in payments:
            # Already paid. The truthful answer is the existing outcome, not a
            # second effect.
            return self._reply(200, {"status": "already approved", "invoice": invoice})
        payments.append(invoice)
        save({"payments": payments})
        return self._reply(200, {"status": "approved", "invoice": invoice})


HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'''

#: A read-only state probe. This is the ORACLE: it reads what was persisted,
#: which is the only thing that can answer "did the effect actually happen".
PROBE_SOURCE = '''\
import json, os, sys

state = os.environ["APPROVAL_STATE"]
payments = []
if os.path.exists(state):
    with open(state) as fh:
        payments = json.load(fh).get("payments", [])
print("payments=%d" % len(payments))
print("invoices=%s" % ",".join(payments))
'''


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def buggy_app(tmp_path: Path):
    """A real, runnable app with a real defect, plus its approved commands."""
    repo = tmp_path / "product"
    repo.mkdir()
    (repo / "CLAUDE.md").write_text("# authority\n")
    (repo / "app.py").write_text(APP_SOURCE)
    (repo / "probe.py").write_text(PROBE_SOURCE)
    port = _free_port()
    state_file = repo / "approvals.json"

    reset = repo / "reset.py"
    reset.write_text(
        textwrap.dedent(
            '''\
            import os
            state = os.environ["APPROVAL_STATE"]
            if os.path.exists(state):
                os.remove(state)
            print("reset")
            '''
        )
    )

    # A real git checkout, so diff-aware refinement inspects a real working
    # tree rather than being skipped for want of one.
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "initial product"], cwd=repo, check=True)

    return {
        "repo": repo,
        "fix_marker": repo / "FIXED",
        "port": port,
        "state_file": state_file,
        "app_url": f"http://127.0.0.1:{port}",
        "env": {"APPROVAL_STATE": str(state_file)},
        "serve": f"{sys.executable} app.py {port}",
        "probe": f"{sys.executable} probe.py",
        "reset": f"{sys.executable} reset.py",
    }


def apply_the_fix(app: dict) -> None:
    """What the builder would do: make the product idempotent and durable."""
    app["fix_marker"].write_text("idempotent + reads back what it persisted\n")


def permanent_scenario(app: dict) -> Scenario:
    """The human-written regression scenario: one approval, one payment."""
    return Scenario(
        name="approval_backend",
        mode="backend",
        setup=[app["reset"]],
        services=[ServiceSpec(name="api", command=app["serve"])],
        readiness=[{"http": f"{app['app_url']}/health", "expect_status": 200}],
        app_url=app["app_url"],
        requests=[
            {
                "name": "approve once",
                "method": "POST",
                "path": "/approve",
                "json": {"invoice": "INV-1"},
                "expect_status": 200,
            }
        ],
        expect_state=[
            {"name": "one payment", "command": app["probe"], "contains": ["payments=1"]}
        ],
        teardown=[app["reset"]],
        env=dict(app["env"]),
    )


# --------------------------------------------------------------------------
# Scripted generation
# --------------------------------------------------------------------------


def _scenario(scenario_id: str, *, risk: str, actions: list, expected: list, **extra) -> dict:
    payload = {
        "id": scenario_id,
        "title": f"{scenario_id} exercises {risk}",
        "purpose": "verify the approval effect happens exactly as promised",
        "risk_category": risk,
        "priority": "P0",
        "requirement_reference": "U-042: an approved invoice is paid exactly once",
        "product_principle_reference": "effect-truth",
        "mode": "backend",
        "service_refs": ["api"],
        "actions": actions,
        "expected_observations": expected,
        "generating_risk": f"the approval effect may not be safe under {risk}",
        "isolation_key": "approval-state",
    }
    payload.update(extra)
    return payload


def initial_wave(app: dict) -> dict:
    probe_once = {
        "kind": "state_check",
        "name": "exactly one payment",
        "state_check": {"command": app["probe"], "contains": ["payments=1"]},
    }
    return {
        "risks": [
            {
                "description": "approving twice may pay twice",
                "risk_category": "idempotency",
                "severity": "P0",
                "basis": "the diff touched the approval endpoint",
            }
        ],
        "scenarios": [
            _scenario(
                "gen-happy",
                risk="happy_path",
                actions=[
                    {
                        "kind": "request",
                        "request": {
                            "method": "POST",
                            "path": "/approve",
                            "json_body": {"invoice": "INV-1"},
                            "expect_status": 200,
                        },
                    },
                    probe_once,
                ],
                expected=["payments=1"],
                cleanup=[app["reset"]],
            ),
            _scenario(
                "gen-approve-twice",
                risk="idempotency",
                actions=[
                    {
                        "kind": "request",
                        "name": "first approval",
                        "request": {
                            "method": "POST",
                            "path": "/approve",
                            "json_body": {"invoice": "INV-2"},
                            "expect_status": 200,
                        },
                    },
                    {
                        "kind": "request",
                        "name": "second approval of the same invoice",
                        "request": {
                            "method": "POST",
                            "path": "/approve",
                            "json_body": {"invoice": "INV-2"},
                        },
                    },
                    probe_once,
                ],
                expected=["payments=1"],
                forbidden_observations=["payments=2"],
                cleanup=[app["reset"]],
            ),
            _scenario(
                "gen-missing-invoice",
                risk="missing_data",
                actions=[
                    {
                        "kind": "request",
                        "request": {
                            "method": "POST",
                            "path": "/approve",
                            "json_body": {},
                            "expect_status": 400,
                            "expect_contains": ["invoice is required"],
                        },
                    }
                ],
                expected=["invoice is required"],
                cleanup=[app["reset"]],
            ),
        ],
        "assumptions": ["the approval endpoint is POST /approve"],
    }


def adaptive_wave(app: dict) -> dict:
    """What a generator would propose once duplicate approval has failed.

    Each case names the failure that caused it, which is what an adaptive
    proposal must do: without that link nothing records why the driver decided
    to test this, and validation refuses it.
    """
    probe_once = {
        "kind": "state_check",
        "name": "exactly one payment",
        "state_check": {"command": app["probe"], "contains": ["payments=1"]},
    }
    return {
        "risks": [],
        "scenarios": [
            _scenario(
                "gen-concurrent-approval",
                risk="concurrency",
                actions=[
                    {
                        "kind": "parallel_requests",
                        "name": "two operators approve the same invoice at once",
                        "requests": [
                            {
                                "method": "POST",
                                "path": "/approve",
                                "json_body": {"invoice": "INV-3"},
                                "name": "operator-a",
                            },
                            {
                                "method": "POST",
                                "path": "/approve",
                                "json_body": {"invoice": "INV-3"},
                                "name": "operator-b",
                            },
                        ],
                    },
                    probe_once,
                ],
                expected=["payments=1"],
                forbidden_observations=["payments=2"],
                cleanup=[app["reset"]],
                source_failures=["gen-approve-twice"],
            ),
            _scenario(
                "gen-restart-persistence",
                risk="restart_recovery",
                actions=[
                    {
                        "kind": "request",
                        "request": {
                            "method": "POST",
                            "path": "/approve",
                            "json_body": {"invoice": "INV-4"},
                            "expect_status": 200,
                        },
                    },
                    {"kind": "restart_service", "service": "api"},
                    {
                        "kind": "request",
                        "name": "read the approval back after the restart",
                        "request": {
                            "method": "GET",
                            "path": "/payments",
                            "expect_contains": ["INV-4"],
                        },
                    },
                    {
                        "kind": "state_check",
                        "state_check": {"command": app["probe"], "contains": ["INV-4"]},
                    },
                ],
                expected=["INV-4"],
                cleanup=[app["reset"]],
                source_failures=["gen-approve-twice"],
            ),
        ],
    }


# --------------------------------------------------------------------------
# Harness fakes
# --------------------------------------------------------------------------


@dataclass
class FakeTurn:
    text: str = "implemented.\n\nRUNNABLE CHECKPOINT: POST /approve"
    session_id: str | None = "builder-1"
    tool_uses: list[str] = field(default_factory=list)
    denied_requests: list[str] = field(default_factory=list)
    is_error: bool = False
    error_detail: str = ""


class FixingBuilder:
    """Simulates a builder: after the first correction, it fixes the defect."""

    def __init__(self, app: dict) -> None:
        self.session_id = "builder-1"
        self.app = app
        self.prompts: list[str] = []
        self.fixed = False

    async def send(self, prompt: str, timeout_s: int | None = None) -> FakeTurn:
        self.prompts.append(prompt)
        if len(self.prompts) > 1 and not self.fixed:
            apply_the_fix(self.app)
            self.fixed = True
        return FakeTurn()


class OptimisticEvaluator:
    """Always ACCEPTs. The point is that the suite, not the evaluator, gates."""

    def __init__(self) -> None:
        self.session_id = "evaluator-1"
        self.prompts: list[str] = []

    async def evaluate(self, prompt: str, timeout_s: int | None = None) -> EvaluatorDecision:
        self.prompts.append(prompt)
        return EvaluatorDecision(
            decision=Decision.ACCEPT,
            summary="the approval flow looked fine to me",
            observed_behavior=["an approval succeeded"],
            confidence=0.9,
        )


class StubReviewer:
    """A fresh read-only reviewer, stubbed. Never launches a Claude session."""

    def __call__(self) -> "StubReviewer":
        return self

    async def __aenter__(self) -> "StubReviewer":
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def review(self, prompt: str):
        from neyma_product_driver.reviewer import IndependentReview

        return IndependentReview(
            verdict="SUPPORTED",
            summary="the double-payment defect is fixed and the evidence shows it",
            confidence=0.9,
        )


class StubRepoLoader:
    def __init__(self) -> None:
        self.unit = FakeUnit()

    def resolve_active_unit_optional(self) -> FakeUnit:
        return self.resolve_active_unit()

    def resolve_active_unit(self) -> FakeUnit:
        return self.unit

    def load(self, topics: list[str] | None = None) -> RepositoryContext:
        return RepositoryContext(
            head_commit="e2e",
            branch="main",
            dirty_file_count=0,
            active_unit=ActiveUnit(unit_id="U-042", name=self.unit.name, status="READY"),
            authority_excerpt="",
            current_excerpt="",
            topic_excerpts={},
            files_consulted=[],
            fingerprint="fp",
        )


# --------------------------------------------------------------------------
# The end-to-end test
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_execute_learn_expand_rerun_then_accept(buggy_app, tmp_path):
    app = buggy_app
    base = permanent_scenario(app)

    config = DriverConfig(
        neyma_repo=app["repo"],
        driver_root=tmp_path / "driver",
        runs_dir=tmp_path / "driver" / "runs",
        scenarios_dir=tmp_path / "driver" / "scenarios",
        task="build supervised carrier invoice approval",
        max_iterations=3,
        run=ScenarioRunConfig(command_timeout_s=60, readiness_timeout_s=30, http_timeout_s=10),
        scenario_generation=ScenarioGenerationConfig(enabled=True, max_waves=3),
    )
    assert config.scenarios_dir is not None
    config.scenarios_dir.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(config.runs_dir, "20260809-e2e")
    state = RunState(run_id=store.run_id, task=config.task, max_iterations=3)

    planner = ScenarioPlanner(
        repo=app["repo"],
        config=config.scenario_generation,
        reasoner=ScriptedReasoner(
            [
                initial_wave(app),  # stage 1, before the builder has done anything
                adaptive_wave(app),  # stage 3, after the duplicate approval fails
                {"risks": [], "scenarios": []},  # stage 2 once the fix changed the tree
            ]
        ),
        store=store,
        base_scenario=base,
        permanent_scenarios=[base],
        founder=FakeFounder(),
    )

    builder = FixingBuilder(app)
    evaluator = OptimisticEvaluator()

    result = await run_control_loop(
        config=config,
        scenario=base,
        store=store,
        state=state,
        builder=builder,
        evaluator=evaluator,
        make_executor=lambda artifact_dir: ScenarioExecutor(
            app["repo"], config.run, artifact_dir
        ),
        emit=lambda _m: None,
        repo_loader=StubRepoLoader(),
        planner=planner,
        # Supervised invoice approval touches payment and approval authority, so
        # the driver's review policy calls for one focused independent review
        # before it accepts. A stub supplies the verdict; what this end-to-end
        # test is exercising is the generation-and-verification loop around it.
        reviewer_factory=StubReviewer(),
    )

    # --- 1-3. scenarios were generated, executed, and exposed the real defect
    first = state.iterations[0].suite
    assert first is not None
    first_outcomes = {o["scenario_id"]: o["outcome"] for o in first["outcomes"]}
    assert first_outcomes["gen-happy"] == "PASSED"
    assert first_outcomes["gen-missing-invoice"] == "PASSED"
    assert first_outcomes["gen-approve-twice"] == "FAILED", (
        "the buggy build pays twice; the generated scenario must catch it"
    )

    # --- the evaluator said ACCEPT and was overruled by the evidence
    assert evaluator.prompts, "the evaluator was consulted"
    assert state.iterations[0].decision.decision is Decision.FIX
    assert "gen-approve-twice" in state.iterations[0].decision.correction_prompt

    # --- 4. adaptive follow-up coverage was produced from the failure
    adaptive = [w for w in planner.plan.waves if w.stage == "adaptive"]
    assert adaptive, "a failure must produce an adaptive wave"
    assert set(adaptive[0].accepted_ids) == {
        "gen-concurrent-approval",
        "gen-restart-persistence",
    }
    # And the diff the fix produced was inspected in a later wave.
    assert any(
        w.stage == "diff_refinement" and w.basis.diff_files for w in planner.plan.waves
    ), "the builder's change must be inspected by a diff-aware wave"

    # --- 5-6. the defect was fixed and the relevant suite reran
    assert builder.fixed
    final = result.suite
    assert final is not None
    assert final.full_run is True, "acceptance must rest on the full required set"

    # --- 7. acceptance only after the evidence is actually green, and after the
    #        review this change's risk classification called for
    assert result.risk.level.value == "HIGH_CONSEQUENCE", result.risk.brief()
    assert [r.verdict for r in result.reviews] == ["SUPPORTED"]
    assert result.status is RunStatus.ACCEPTED
    assert final.everything_required_passed
    assert final.failed == 0 and final.blocked == 0
    executed = {o.scenario_id for o in final.outcomes}
    assert {
        "approval_backend",       # the permanent regression scenario
        "gen-approve-twice",      # the case that found the bug
        "gen-concurrent-approval",
        "gen-restart-persistence",
    } <= executed

    # --- the scenario that found the defect is a promotion candidate
    candidates = {c.scenario_id: c for c in result.promotion_candidates}
    assert "gen-approve-twice" in candidates
    assert candidates["gen-approve-twice"].discovered_in_iteration == 1
    assert candidates["gen-approve-twice"].promoted is False
    assert "payments=2" in candidates["gen-approve-twice"].bug_discovered or (
        "payments" in candidates["gen-approve-twice"].bug_discovered
    )

    # --- nothing was written into the permanent suite
    assert list(config.scenarios_dir.iterdir()) == []

    # --- evidence exists and is traceable back to scenario ids
    plan = json.loads((store.run_dir / "scenario-plan.json").read_text())
    assert {s["id"] for s in plan["scenarios"]} >= {"gen-approve-twice", "gen-restart-persistence"}
    assert (store.run_dir / "scenario-generation" / "wave-01.json").exists()
    for outcome in final.outcomes:
        assert Path(outcome.evidence_path).exists()

    # --- the coverage report describes coverage, not correctness
    summary = final.summary_block()
    assert "0 unresolved high-priority scenario failures" in summary
    assert "not a claim that all possible cases were verified" in summary
    assert "all possible cases verified" not in summary.replace(
        "not a claim that all possible cases were verified", ""
    )


@pytest.mark.asyncio
async def test_the_restart_scenario_really_restarts_a_real_service(buggy_app, tmp_path):
    """The restart primitive is genuine: state must survive a killed process.

    Run directly against the fixed build so the assertion is about the executor
    restarting a real subprocess, not about the loop.
    """
    app = buggy_app
    apply_the_fix(app)
    base = permanent_scenario(app)

    from neyma_product_driver.scenario_plan import compile_to_scenario
    from neyma_product_driver.scenario_generator import parse_scenarios
    from neyma_product_driver.scenario_plan import ScenarioProvenance

    parsed, malformed = parse_scenarios(
        adaptive_wave(app), provenance=ScenarioProvenance(wave=1, stage="adaptive")
    )
    assert not malformed
    model = next(s for s in parsed if s.id == "gen-restart-persistence")
    compiled = compile_to_scenario(
        model, base=base, approved_commands={app["probe"], app["reset"]}
    )
    assert [s.kind for s in compiled.steps] == [
        "request",
        "restart_service",
        "request",
        "state_check",
    ]

    executor = ScenarioExecutor(
        app["repo"],
        ScenarioRunConfig(command_timeout_s=60, readiness_timeout_s=30, http_timeout_s=10),
        tmp_path / "artifacts",
    )
    result = await executor.execute(compiled)

    assert result.error is None, result.error
    assert result.passed, [a.model_dump() for a in result.failed_assertions()]
    # The service really was stopped and started again mid-scenario.
    assert any("restart_service" in step for step in result.steps_performed)
    # And the approval was read back over HTTP from the new process.
    assert any("INV-4" in obs.body_text for obs in result.http)


@pytest.mark.asyncio
async def test_the_buggy_build_really_is_buggy(buggy_app, tmp_path):
    """Guards the test above: without the fix, the same scenario must fail.

    A test that passes against both builds would prove nothing about the
    scenario's ability to detect the defect.
    """
    app = buggy_app
    base = permanent_scenario(app)

    from neyma_product_driver.scenario_generator import parse_scenarios
    from neyma_product_driver.scenario_plan import ScenarioProvenance, compile_to_scenario

    parsed, _ = parse_scenarios(
        initial_wave(app), provenance=ScenarioProvenance(wave=1, stage="initial")
    )
    model = next(s for s in parsed if s.id == "gen-approve-twice")
    compiled = compile_to_scenario(
        model, base=base, approved_commands={app["probe"], app["reset"]}
    )

    executor = ScenarioExecutor(
        app["repo"],
        ScenarioRunConfig(command_timeout_s=60, readiness_timeout_s=30, http_timeout_s=10),
        tmp_path / "artifacts",
    )
    result = await executor.execute(compiled)

    assert not result.passed
    failed = " ".join(a.target + a.detail for a in result.failed_assertions())
    assert "payments=1" in failed
