#!/usr/bin/env python3
"""C-GATE attack set C — precedence ordering, evidence attribution, widening.

All three drive real product code:

  * ``run_control_loop`` with a real ``CompletionAudit`` object (auditor faked,
    audit verdict real) to test whether the completion-audit precedence branch
    can terminate a run *before* the suite gate is consulted;
  * the real ``SuiteExecutor`` + ``write_case_evidence`` / ``verify_case_evidence``
    against a real filesystem, to test wrong-run / wrong-iteration /
    wrong-scenario / corrupt / deleted evidence;
  * a real two-iteration run to test whether a narrowed rerun widens before
    acceptance.

    .venv/bin/python verification-evidence/cert/C-GATE/attack_precedence_evidence.py
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from scenario_fixtures import (  # noqa: E402
    FakeFounder,
    ScriptedReasoner,
    base_scenario,
    raw_payload,
    raw_scenario,
)

import attack_loop_e2e as e2e  # noqa: E402  (reuse the fakes and scaffolding)

from neyma_product_driver.cli import run_control_loop  # noqa: E402
from neyma_product_driver.completion_auditor import (  # noqa: E402
    AuditDecision,
    CompletionAudit,
)
from neyma_product_driver.config import (  # noqa: E402
    DriverConfig,
    ScenarioGenerationConfig,
)
from neyma_product_driver.evidence import EvidenceStore  # noqa: E402
from neyma_product_driver.models import (  # noqa: E402
    AssertionResult,
    RunState,
    RunStatus,
    ScenarioResult,
)
from neyma_product_driver.scenario_gate import evaluate_gate  # noqa: E402
from neyma_product_driver.scenario_planner import ScenarioPlanner  # noqa: E402
from neyma_product_driver.scenario_suite import (  # noqa: E402
    CASE_RECORD_FILENAME,
    Outcome,
    SuiteExecutor,
    build_suite,
    verify_case_evidence,
    write_case_evidence,
)
from neyma_product_driver.scenarios import Scenario  # noqa: E402

RESULTS: list[dict] = []


def record(ident: str, what: str, observed: Any, expected: Any, verdict: str) -> None:
    RESULTS.append(
        {"id": ident, "attack": what, "observed": observed, "expected": expected, "OUTCOME": verdict}
    )
    print(f"[{verdict:>16}] {ident}: {what}")
    print(f"                    observed: {observed}")


# ===========================================================================
# C1 — precedence: can the completion-audit branch terminate before the gate?
# ===========================================================================
class FakeAuditor:
    """Returns a fixed CompletionAudit. The audit MODEL is the real one."""

    def __init__(self, decision: AuditDecision) -> None:
        self.audit_obj = CompletionAudit(
            decision=decision,
            headline=f"completion audit: {decision.value}",
            confidence=0.9,
            correction_prompt=(
                "The registry says the unit is not complete. Bring the status surface "
                "back in line with the evidence before claiming completion."
            ),
        )

    def audit(self, *a: Any, **kw: Any) -> CompletionAudit:
        return self.audit_obj


async def c1(ident: str, audit_decision: AuditDecision, what: str) -> None:
    """A run whose suite is provably unverified, with a hostile ACCEPT evaluator."""
    root = Path(tempfile.mkdtemp(prefix=f"cgate-{ident}-"))
    try:
        repo = e2e.make_repo(root)
        config = e2e.make_config(root, repo, enabled=True, execution_budget_s=0)
        config.max_iterations = 1
        assert config.runs_dir is not None
        store = EvidenceStore(config.runs_dir, f"2026-cgate-{ident}")
        state = RunState(run_id=store.run_id, task=config.task, max_iterations=1)
        planner = e2e.make_planner(
            config, store, ScriptedReasoner([raw_payload(raw_scenario("gen-idem"))])
        )
        log: list[str] = []
        result = await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=e2e.FakeBuilder(),
            evaluator=e2e.HostileEvaluator(),
            make_executor=lambda d: e2e.ScriptedScenarioExecutor(d, {}, log),
            emit=lambda _m: None,
            repo_loader=e2e.FakeRepoLoader(),
            auditor=FakeAuditor(audit_decision),
            planner=planner,
        )
        # LoopResult.suite is dropped on some early-return paths, so recover the
        # suite the iteration actually recorded and ask the gate about THAT.
        from neyma_product_driver.scenario_suite import SuiteResult

        recorded = None
        if state.iterations and state.iterations[-1].suite:
            recorded = SuiteResult.model_validate(state.iterations[-1].suite)
        gate = (
            evaluate_gate(
                recorded,
                generation_problems=planner.generation_problems(),
                risks=list(planner.plan.risks),
            )
            if recorded is not None
            else None
        )
        observed = {
            "LoopResult.suite_present": result.suite is not None,
            "iteration_record_suite_present": recorded is not None,
            "run_status": result.status.value,
            "exit_code": {
                "ACCEPTED": 0, "BLOCKED": 11, "MAX_ITERATIONS": 12,
                "NEEDS_INDEPENDENT_REVIEW": 14, "NEEDS_USER": 10,
                "REQUIRES_APPROVAL": 15, "STOPPED": 13, "ERROR": 1, "RUNNING": 1,
            }[result.status.value],
            "persisted_final_decision": result.final_decision.decision.value if result.final_decision else None,
            "scenarios_executed": log,
            "gate_would_have_said": gate.status.value if gate else "no suite",
            "gate_unverified_count": len(gate.unverified) if gate else 0,
        }
        # A run that ends on a non-ACCEPTED status is not an acceptance; but a
        # terminal state that records ACCEPT as the run's final decision while
        # the gate says NOT_VERIFIED is a precedence hole worth naming.
        bypassed = (
            observed["persisted_final_decision"] == "ACCEPT"
            and observed["gate_would_have_said"] == "NOT_VERIFIED"
        )
        record(
            ident,
            what,
            observed,
            "the suite gate is consulted before any terminal state records ACCEPT",
            "GATE BYPASSED" if bypassed else "EXPECTED",
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ===========================================================================
# C2 — evidence attribution, through the REAL SuiteExecutor
# ===========================================================================
class PassingExecutor:
    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = artifact_dir
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        return ScenarioResult(
            scenario_name=scenario.name,
            assertions=[AssertionResult(kind="expect_state", target="x", passed=True)],
        )


async def c2() -> None:
    root = Path(tempfile.mkdtemp(prefix="cgate-C2-"))
    try:
        suite = build_suite(permanent=[("perm", Scenario(name="perm"))])

        # C2a — a genuine execution: evidence must resolve.
        ex = SuiteExecutor(
            make_executor=PassingExecutor,
            artifact_root=root / "iter1",
            run_id="RUN-A",
            iteration=1,
        )
        result = await ex.run(suite)
        o = result.outcomes[0]
        record(
            "C2a",
            "CONTROL: a real execution writes and verifies its own evidence",
            {"outcome": o.outcome.value, "evidence_verified": o.evidence_verified,
             "problem": o.evidence_problem},
            "PASSED with evidence_verified=True",
            "EXPECTED" if o.outcome is Outcome.PASSED and o.evidence_verified else "FALSE REFUSAL",
        )
        evidence_dir = Path(o.evidence_path)

        # C2b — delete the record: the pass must stop being provable.
        (evidence_dir / CASE_RECORD_FILENAME).unlink()
        problem = verify_case_evidence(str(evidence_dir), scenario_id="perm",
                                       run_id="RUN-A", iteration=1)
        record("C2b", "evidence record DELETED after the run", problem or "(accepted!)",
               "a refusal string", "EXPECTED" if problem else "FALSE ACCEPTANCE")

        # C2c — corrupt JSON.
        (evidence_dir / CASE_RECORD_FILENAME).write_text("{not json", encoding="utf-8")
        problem = verify_case_evidence(str(evidence_dir), scenario_id="perm",
                                       run_id="RUN-A", iteration=1)
        record("C2c", "evidence record CORRUPTED (unparseable JSON)", problem or "(accepted!)",
               "a refusal string", "EXPECTED" if problem else "FALSE ACCEPTANCE")

        # C2d — empty file.
        (evidence_dir / CASE_RECORD_FILENAME).write_text("", encoding="utf-8")
        problem = verify_case_evidence(str(evidence_dir), scenario_id="perm",
                                       run_id="RUN-A", iteration=1)
        record("C2d", "evidence record EMPTY", problem or "(accepted!)",
               "a refusal string", "EXPECTED" if problem else "FALSE ACCEPTANCE")

        # C2e — a record belonging to another scenario.
        write_case_evidence(evidence_dir, ScenarioResult(scenario_name="other"),
                            scenario_id="some-other-scenario", run_id="RUN-A", iteration=1)
        problem = verify_case_evidence(str(evidence_dir), scenario_id="perm",
                                       run_id="RUN-A", iteration=1)
        record("C2e", "evidence belongs to ANOTHER SCENARIO", problem or "(accepted!)",
               "a refusal string", "EXPECTED" if problem else "FALSE ACCEPTANCE")

        # C2f — a record from another run (the stale-directory attack).
        write_case_evidence(evidence_dir, ScenarioResult(scenario_name="perm"),
                            scenario_id="perm", run_id="RUN-OLD", iteration=1)
        problem = verify_case_evidence(str(evidence_dir), scenario_id="perm",
                                       run_id="RUN-A", iteration=1)
        record("C2f", "evidence belongs to ANOTHER RUN (stale leftover directory)",
               problem or "(accepted!)", "a refusal string",
               "EXPECTED" if problem else "FALSE ACCEPTANCE")

        # C2g — a record from another iteration of the same run.
        write_case_evidence(evidence_dir, ScenarioResult(scenario_name="perm"),
                            scenario_id="perm", run_id="RUN-A", iteration=1)
        problem = verify_case_evidence(str(evidence_dir), scenario_id="perm",
                                       run_id="RUN-A", iteration=2)
        record("C2g", "evidence written in ANOTHER ITERATION", problem or "(accepted!)",
               "a refusal string", "EXPECTED" if problem else "FALSE ACCEPTANCE")

        # C2h — a record claiming iteration 0 while the run is at iteration 3.
        #       `if iteration and ...` means iteration 0 is never compared, so a
        #       record stamped 0 is accepted by any iteration.
        write_case_evidence(evidence_dir, ScenarioResult(scenario_name="perm"),
                            scenario_id="perm", run_id="RUN-A", iteration=0)
        problem = verify_case_evidence(str(evidence_dir), scenario_id="perm",
                                       run_id="RUN-A", iteration=3)
        record("C2h", "evidence stamped iteration=0 checked against iteration 3",
               problem or "(ACCEPTED — iteration 0 is never compared)",
               "a refusal string, or a documented reason it cannot happen",
               "EXPECTED" if problem else "WEAKNESS")

        # C2i — a record with no run_id at all, checked against a real run.
        write_case_evidence(evidence_dir, ScenarioResult(scenario_name="perm"),
                            scenario_id="perm", run_id="", iteration=1)
        problem = verify_case_evidence(str(evidence_dir), scenario_id="perm",
                                       run_id="RUN-A", iteration=1)
        record("C2i", "evidence with an EMPTY run_id checked against run RUN-A",
               problem or "(accepted!)", "a refusal string",
               "EXPECTED" if problem else "FALSE ACCEPTANCE")

        # C2j — the whole evidence directory removed.
        shutil.rmtree(evidence_dir)
        problem = verify_case_evidence(str(evidence_dir), scenario_id="perm",
                                       run_id="RUN-A", iteration=1)
        record("C2j", "evidence DIRECTORY removed entirely", problem or "(accepted!)",
               "a refusal string", "EXPECTED" if problem else "FALSE ACCEPTANCE")

        # C2k — does an unprovable pass actually reach the gate as unverified?
        #       Re-run, then delete the record and re-evaluate the gate.
        ex2 = SuiteExecutor(make_executor=PassingExecutor, artifact_root=root / "iter2",
                            run_id="RUN-A", iteration=2)
        result2 = await ex2.run(suite)
        o2 = result2.outcomes[0]
        o2.evidence_verified = False  # what the executor sets when evidence fails
        o2.evidence_problem = "the cited evidence directory does not exist"
        verdict = evaluate_gate(result2)
        record("C2k", "a PASSED required outcome whose evidence stopped resolving",
               {"gate": verdict.status.value, "unverified": [c.brief() for c in verdict.unverified]},
               "NOT_VERIFIED",
               "EXPECTED" if verdict.status.value == "NOT_VERIFIED" else "FALSE ACCEPTANCE")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ===========================================================================
# C3 — narrowed rerun: does the run widen before accepting?
# ===========================================================================
class IterationScriptedExecutor:
    """Fails a named scenario only on the first iteration."""

    STATE: dict[str, int] = {}

    def __init__(self, artifact_dir: Path, fail_first: set[str], log: list[tuple[int, str]]) -> None:
        self.artifact_dir = artifact_dir
        self.fail_first = fail_first
        self.log = log
        self.service_logs: dict[str, str] = {}

    async def execute(self, scenario: Scenario) -> ScenarioResult:
        key = scenario.name.split(":", 1)[-1]
        n = IterationScriptedExecutor.STATE.get("iteration", 1)
        self.log.append((n, key))
        passing = not (n == 1 and key in self.fail_first)
        return ScenarioResult(
            scenario_name=scenario.name,
            assertions=[AssertionResult(kind="expect_state", target=key, passed=passing,
                                        detail="" if passing else "got 2, want 1")],
        )


async def c3() -> None:
    root = Path(tempfile.mkdtemp(prefix="cgate-C3-"))
    try:
        repo = e2e.make_repo(root)
        config = e2e.make_config(root, repo, enabled=True)
        config.max_iterations = 2
        assert config.runs_dir is not None
        store = EvidenceStore(config.runs_dir, "2026-cgate-C3")
        state = RunState(run_id=store.run_id, task=config.task, max_iterations=2)
        planner = e2e.make_planner(
            config,
            store,
            ScriptedReasoner(
                [
                    raw_payload(
                        raw_scenario("gen-a"),
                        raw_scenario(
                            "gen-b",
                            risk_category="boundary",
                            actions=[
                                {
                                    "kind": "request",
                                    "name": "approve at the boundary",
                                    "request": {
                                        "method": "POST",
                                        "path": "/approve?amount=0",
                                        "expect_status": 400,
                                    },
                                }
                            ],
                            state_checks=[
                                {
                                    "name": "no payment at the boundary",
                                    "command": "./probe.sh payments",
                                    "contains": ["payments=0"],
                                }
                            ],
                            expected_observations=["payments=0"],
                            forbidden_observations=["payments=1"],
                        ),
                        raw_scenario(
                            "gen-c",
                            risk_category="authorization",
                            actions=[
                                {
                                    "kind": "request",
                                    "name": "approve without authority",
                                    "request": {
                                        "method": "POST",
                                        "path": "/approve",
                                        "headers": {"X-Actor": "unauthorized"},
                                        "expect_status": 403,
                                    },
                                }
                            ],
                            state_checks=[
                                {
                                    "name": "unauthorized approval pays nothing",
                                    "command": "./probe.sh payments",
                                    "contains": ["payments=0"],
                                }
                            ],
                            expected_observations=["payments=0"],
                            forbidden_observations=["payments=1"],
                        ),
                    ),
                    {"risks": [], "scenarios": []},
                    {"risks": [], "scenarios": []},
                    {"risks": [], "scenarios": []},
                    {"risks": [], "scenarios": []},
                ]
            ),
        )
        log: list[tuple[int, str]] = []
        IterationScriptedExecutor.STATE["iteration"] = 1

        class Ev(e2e.HostileEvaluator):
            async def evaluate(self, prompt, timeout_s=None):
                d = await super().evaluate(prompt, timeout_s)
                IterationScriptedExecutor.STATE["iteration"] = self.calls + 1
                return d

        result = await run_control_loop(
            config=config,
            scenario=base_scenario(),
            store=store,
            state=state,
            builder=e2e.FakeBuilder(),
            evaluator=Ev(),
            make_executor=lambda d: IterationScriptedExecutor(d, {"gen-a"}, log),
            emit=lambda _m: None,
            repo_loader=e2e.FakeRepoLoader(),
            planner=planner,
        )
        it2 = [k for n, k in log if n == 2]
        suite = result.suite
        observed = {
            "run_status": result.status.value,
            "iteration_1_executed": [k for n, k in log if n == 1],
            "iteration_2_executed": it2,
            "final_full_run": suite.full_run if suite else None,
            "final_selection_reason": suite.selection_reason if suite else None,
            "gate": evaluate_gate(suite, risks=list(planner.plan.risks)).status.value if suite else None,
        }
        # The accepted suite result must be a FULL execution, not a narrowed one.
        ok = (
            result.status is not RunStatus.ACCEPTED
            or (suite is not None and suite.full_run and len(suite.outcomes) == 4)
        )
        record("C3", "narrowed rerun after a failure: is the ACCEPTed suite a full execution?",
               observed, "acceptance rests on a full 4-scenario execution",
               "EXPECTED" if ok else "FALSE ACCEPTANCE")
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def main() -> None:
    await c1("C1a", AuditDecision.REQUIRES_INDEPENDENT_REVIEW,
             "audit=REQUIRES_INDEPENDENT_REVIEW + hostile ACCEPT + a suite that ran NOTHING")
    await c1("C1b", AuditDecision.VERIFIED,
             "CONTROL: audit=VERIFIED + hostile ACCEPT + a suite that ran NOTHING")
    await c1("C1c", AuditDecision.CONTRADICTED,
             "CONTROL: audit=CONTRADICTED + hostile ACCEPT + a suite that ran NOTHING")
    await c2()
    await c3()

    out_path = Path(__file__).with_name("attack_precedence_evidence.json")
    out_path.write_text(json.dumps(RESULTS, indent=2, default=str), encoding="utf-8")
    bad = [r for r in RESULTS if r["OUTCOME"] != "EXPECTED"]
    print("\n" + "=" * 72)
    print(f"{len(RESULTS)} attacks; deviations: {len(bad)}")
    for r in bad:
        print(f"  {r['OUTCOME']}: {r['id']} — {r['attack']}")
    print(f"raw: {out_path}")


asyncio.run(main())
