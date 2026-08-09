"""Deterministic tests of the planner's bounds, termination, dedup and provenance.

A scripted reasoner is injected, so nothing here consults a model. Each test
tries to EXCEED a bound and reports what the planner actually did.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import Founder, STATE_CMD, Unit, base_scenario  # noqa: E402
from neyma_product_driver.config import ScenarioGenerationConfig  # noqa: E402
from neyma_product_driver.scenario_planner import ScenarioPlanner  # noqa: E402

TASK = "Implement an approval endpoint with persistent state."
RESULTS: list[tuple[str, str, str]] = []


def record(name: str, verdict: str, detail: str) -> None:
    RESULTS.append((name, verdict, detail))
    print(f"[{verdict}] {name}\n        {detail}")


def scenario(sid: str, category: str, path: str = "/api/invoices/X/approve",
             expected=("approved",), title: str | None = None, purpose: str | None = None,
             risk: str = "R1") -> dict:
    return {
        "id": sid,
        "title": title or f"scenario {sid}",
        "purpose": purpose or "A purpose long enough to satisfy the minimum length rule.",
        "risk_category": category,
        "priority": "P1",
        "requirement_reference": "AC-APPROVAL-001",
        "product_principle_reference": "product rubric",
        "service_refs": ["api"],
        "isolation_key": "store",
        "isolation_note": "uses its own invoice id",
        "generating_risk": risk,
        "actions": [
            {
                "kind": "request",
                "name": "approve",
                "request": {
                    "method": "POST",
                    "path": path,
                    "json_body": {"actor": "alice"},
                    "expect_status": 200,
                    "timeout_s": 4,
                },
            }
        ],
        "expected_observations": list(expected),
    }


class Scripted:
    def __init__(self, payloads) -> None:
        self.payloads = list(payloads)
        self.session_id = "scripted"
        self.calls = 0

    def propose(self, brief):
        self.calls += 1
        return self.payloads.pop(0) if self.payloads else {"risks": [], "scenarios": []}


def planner_for(reasoner, **overrides) -> ScenarioPlanner:
    cfg = ScenarioGenerationConfig(enabled=True, approved_commands=[STATE_CMD], **overrides)
    base = base_scenario("none", 8999, Path("/tmp/r5-structural-store.json"))
    return ScenarioPlanner(
        repo=ROOT,
        config=cfg,
        reasoner=reasoner,
        store=None,
        base_scenario=base,
        permanent_scenarios=[base],
        founder=Founder(),
        emit=lambda _m: None,
    )


CATS = [
    "boundary", "missing_data", "malformed_input", "conflicting_evidence",
    "authorization", "cross_tenant", "approval_required", "safety_invariant",
    "stale_state", "service_unavailable",
]


# -- 1. per-wave limits -----------------------------------------------------

def test_initial_wave_limit() -> None:
    payload = {"risks": [], "scenarios": [
        scenario(f"s{i}", CATS[i], path=f"/api/invoices/A{i}/approve") for i in range(10)
    ]}
    p = planner_for(Scripted([payload]), max_initial_scenarios=2, max_total_scenarios=50)
    p.plan_initial(task=TASK, unit=Unit())
    n = len(p.plan.scenarios)
    record(
        "BOUND max_initial_scenarios=2, reasoner returned 10 valid scenarios",
        "FAIL" if n > 2 else "PASS",
        f"accepted {n}; the per-wave limit is only sent to the model in the brief "
        f"(scenario_planner.py:332-344) and is never enforced on the returned batch",
    )


def test_adaptive_wave_limit() -> None:
    payload = {"risks": [], "scenarios": [
        scenario(f"a{i}", CATS[i], path=f"/api/invoices/B{i}/approve") for i in range(10)
    ]}
    p = planner_for(
        Scripted([{"risks": [], "scenarios": []}, payload]),
        max_adaptive_scenarios_per_wave=2,
        max_total_scenarios=50,
    )
    p.plan_initial(task=TASK, unit=Unit())
    p.expand_after_failures(task=TASK, unit=Unit(), failures=["[FAIL] something broke"])
    n = len(p.plan.scenarios)
    record(
        "BOUND max_adaptive_scenarios_per_wave=2, reasoner returned 10",
        "FAIL" if n > 2 else "PASS",
        f"accepted {n} in the adaptive wave",
    )


# -- 2. totals and per-category --------------------------------------------

def test_total_limit() -> None:
    payload = {"risks": [], "scenarios": [
        scenario(f"t{i}", CATS[i], path=f"/api/invoices/C{i}/approve") for i in range(10)
    ]}
    p = planner_for(Scripted([payload]), max_initial_scenarios=10, max_total_scenarios=3)
    p.plan_initial(task=TASK, unit=Unit())
    n = len(p.plan.scenarios)
    record(
        "BOUND max_total_scenarios=3, reasoner returned 10",
        "PASS" if n == 3 else "FAIL",
        f"accepted {n}; refusals recorded: "
        f"{[note for w in p.plan.waves for note in w.budget_notes][:2]}",
    )


def test_per_category_limit() -> None:
    payload = {"risks": [], "scenarios": [
        scenario(f"c{i}", "boundary", path=f"/api/invoices/D{i}/approve") for i in range(8)
    ]}
    p = planner_for(
        Scripted([payload]),
        max_initial_scenarios=10,
        max_total_scenarios=50,
        max_scenarios_per_risk_category=2,
    )
    p.plan_initial(task=TASK, unit=Unit())
    n = len(p.plan.scenarios)
    record(
        "BOUND max_scenarios_per_risk_category=2, reasoner returned 8 in one category",
        "PASS" if n == 2 else "FAIL",
        f"accepted {n}",
    )


# -- 3. waves and termination ----------------------------------------------

def test_max_waves() -> None:
    payloads = [
        {"risks": [], "scenarios": [scenario(f"w{w}-{i}", CATS[i], path=f"/api/invoices/E{w}{i}/approve")
                                    for i in range(2)]}
        for w in range(8)
    ]
    p = planner_for(Scripted(payloads), max_waves=2, max_total_scenarios=50)
    p.plan_initial(task=TASK, unit=Unit())
    for _ in range(6):
        p.expand_after_failures(task=TASK, unit=Unit(), failures=["[FAIL] still broken"])
    waves_that_generated = sum(1 for w in p.plan.waves if w.accepted_ids)
    refusals = [n for w in p.plan.waves for n in w.budget_notes]
    record(
        "BOUND max_waves=2, expansion requested 6 more times after the initial wave",
        "PASS" if p.waves_used == 2 and waves_that_generated == 2 else "FAIL",
        f"waves_used={p.waves_used}, waves that produced scenarios={waves_that_generated}, "
        f"wave records={len(p.plan.waves)}, budget refusals recorded={len(refusals)}",
    )


def test_termination() -> None:
    """Drive expansion in a loop until the planner says stop. Must terminate."""
    payloads = [
        {"risks": [], "scenarios": [scenario(f"z{w}-{i}", CATS[i % len(CATS)],
                                             path=f"/api/invoices/F{w}{i}/approve")
                                    for i in range(6)]}
        for w in range(200)
    ]
    p = planner_for(Scripted(payloads), max_waves=3, max_total_scenarios=12)
    p.plan_initial(task=TASK, unit=Unit())
    iterations = 0
    while not p.budget_exhausted() and iterations < 500:
        iterations += 1
        p.expand_after_failures(task=TASK, unit=Unit(), failures=["[FAIL] still broken"])
    record(
        "TERMINATION loop expanding until budget_exhausted()",
        "PASS" if iterations < 500 else "FAIL",
        f"terminated after {iterations} expansion request(s); "
        f"waves_used={p.waves_used}, scenarios={len(p.plan.scenarios)}",
    )


def test_no_useful_verification() -> None:
    p = planner_for(Scripted([{"risks": [], "scenarios": []}]))
    p.plan_initial(task=TASK, unit=Unit())
    waves_before = p.waves_used
    p.expand_after_failures(task=TASK, unit=Unit(), failures=[], evaluator_requests=[])
    record(
        "RECOGNITION no failures and no evaluator requests -> no wave is spent",
        "PASS" if p.waves_used == waves_before else "FAIL",
        f"waves_used stayed at {p.waves_used}",
    )

    p2 = planner_for(Scripted([{"risks": [], "scenarios": []}, {"risks": [], "scenarios": []}]))
    p2.plan_initial(task=TASK, unit=Unit())
    p2.expand_after_failures(task=TASK, unit=Unit(), failures=["[FAIL] x"])
    record(
        "RECOGNITION reasoner answers 'nothing more is useful' (empty scenario list)",
        "PASS" if len(p2.plan.scenarios) == 0 and p2.waves_used == 2 else "FAIL",
        f"scenarios={len(p2.plan.scenarios)}, waves_used={p2.waves_used} "
        f"(the empty answer still consumes a wave, which bounds the loop)",
    )


# -- 4. duplicate detection -------------------------------------------------

def test_dedup() -> None:
    original = scenario("dup-a", "boundary", path="/api/invoices/G1/approve",
                        expected=("the invoice is approved",))

    exact = dict(original, id="dup-b")
    reworded_prose = dict(
        original,
        id="dup-c",
        title="A COMPLETELY DIFFERENT TITLE about approval edge cases",
        purpose="Reworded purpose text that shares no vocabulary with the first one at all.",
        risk_category="missing_data",
    )
    reworded_expectation = dict(
        original,
        id="dup-d",
        expected_observations=["The invoice ends up approved."],
    )

    p = planner_for(Scripted([{"risks": [], "scenarios": [
        original, exact, reworded_prose, reworded_expectation,
    ]}]), max_initial_scenarios=10, max_total_scenarios=50)
    p.plan_initial(task=TASK, unit=Unit())
    kept = [s.id for s in p.plan.scenarios]
    refused = {r.id: r.reasons for w in p.plan.waves for r in w.rejected}

    record(
        "DEDUP byte-identical operations and expectations (different id)",
        "PASS" if "dup-b" not in kept else "FAIL",
        f"dup-b refused: {refused.get('dup-b', ['(NOT REFUSED)'])[0][:100]}",
    )
    record(
        "DEDUP identical operations, reworded title/purpose AND relabelled risk category",
        "PASS" if "dup-c" not in kept else "FAIL",
        f"dup-c refused: {refused.get('dup-c', ['(NOT REFUSED)'])[0][:100]}",
    )
    record(
        "DEDUP identical operations, only the expectation TEXT reworded "
        "('the invoice is approved' -> 'The invoice ends up approved.')",
        "FAIL" if "dup-d" in kept else "PASS",
        f"dup-d {'ACCEPTED as new coverage' if 'dup-d' in kept else 'refused'}; "
        f"coverage_signature (scenario_plan.py:878-897) hashes the normalized expectation "
        f"strings, so re-wording an expectation mints a fresh signature for identical actions",
    )


# -- 5. provenance ----------------------------------------------------------

def test_provenance() -> None:
    p = planner_for(Scripted([
        {"risks": [{"id": "R1", "description": "may double-pay", "risk_category": "idempotency",
                    "severity": "P0", "basis": "AC-APPROVAL-001"}],
         "scenarios": [scenario("prov-1", "boundary", path="/api/invoices/H1/approve")]},
        {"risks": [], "scenarios": [
            scenario("prov-2", "missing_data", path="/api/invoices/H2/approve",
                     risk="R-DOES-NOT-EXIST"),
        ]},
    ]), max_total_scenarios=50)
    p.plan_initial(task=TASK, unit=Unit())
    failures = [
        "[FAIL] P0 generated gen-idempotent-approve  (idempotency)  — payments=2, expected 1",
        "[FAIL] P0 generated gen-persist-approve  (persistence_failure)  — store still pending",
    ]
    p.expand_after_failures(task=TASK, unit=Unit(), failures=failures,
                            investigation_findings=["approval writes are never flushed"])

    s2 = p.plan.by_id("prov-2")
    prov = s2.provenance
    persisted = json.loads(json.dumps(p.plan.model_dump(mode="json")))
    wave2 = [w for w in persisted["waves"] if w["wave"] == 2][0]

    record(
        "PROVENANCE the adaptive scenario records stage, wave and the failures consulted",
        "PASS" if prov.stage == "adaptive" and prov.wave == 2
        and prov.prior_failures_consulted == failures else "FAIL",
        f"stage={prov.stage!r} wave={prov.wave} "
        f"prior_failures_consulted={len(prov.prior_failures_consulted)} entries",
    )
    record(
        "PROVENANCE the wave record persists the whole basis (failures + findings)",
        "PASS" if wave2["basis"]["prior_failures"] == failures
        and wave2["basis"]["investigation_findings"] else "FAIL",
        f"wave-2 basis carries {len(wave2['basis']['prior_failures'])} failure(s) and "
        f"{len(wave2['basis']['investigation_findings'])} investigation finding(s)",
    )
    record(
        "PROVENANCE which SPECIFIC failure/cluster caused THIS scenario",
        "FAIL",
        f"generating_risk={prov.generating_risk!r} -- copied verbatim from the model's own "
        f"'generating_risk' field (scenario_generator.py:400-402) with no check that it names "
        f"a real risk, failure or cluster. Every scenario in a wave shares the same flat "
        f"prior_failures_consulted list, so a wave with N failures and M scenarios records "
        f"no failure->scenario edge. Here the model named a risk that does not exist and it "
        f"was accepted.",
    )
    record(
        "PROVENANCE cluster ids are never carried into the plan",
        "FAIL",
        "FailureCluster.cluster_id (C01, C02...) is rendered into the prompt text only "
        "(scenario_planner.py:342) and appears nowhere in GenerationBasis, WaveRecord or "
        "ScenarioProvenance, so 'which cluster produced this scenario' is unanswerable "
        "from the persisted plan.",
    )


def main() -> int:
    for test in (
        test_initial_wave_limit, test_adaptive_wave_limit, test_total_limit,
        test_per_category_limit, test_max_waves, test_termination,
        test_no_useful_verification, test_dedup, test_provenance,
    ):
        print()
        test()
    print("\n" + "=" * 78)
    failed = [r for r in RESULTS if r[1] == "FAIL"]
    print(f"{len(RESULTS)} checks, {len(failed)} FAILED")
    for name, _v, _d in failed:
        print(f"  FAIL  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
