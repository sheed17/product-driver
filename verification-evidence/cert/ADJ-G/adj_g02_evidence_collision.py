"""ADJ-G independent reproduction of G-SCALE-02, plus the second route.

Route A (reviewer's): a permanent scenario whose *name* contains a space folds
onto a generated id. Needs operator-chosen text.

Route B (mine): two GENERATED ids that differ only in case. Nothing is folded,
nothing is shortened, validation's duplicate-id check is exact and case
sensitive, the suite holds two entries — and on a case-insensitive filesystem
(APFS, the default on macOS) the two evidence directories are one directory.
No operator text is involved at all.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def fs_is_case_insensitive(where: Path) -> bool:
    d = Path(tempfile.mkdtemp(dir=str(where)))
    (d / "Aa").mkdir()
    result = (d / "aa").exists()
    shutil.rmtree(d)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    driver = Path(args.driver).resolve()
    sys.path.insert(0, str(driver))
    sys.path.insert(0, str(driver / "tests"))
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    from neyma_product_driver import cli as driver_cli
    from neyma_product_driver.evidence import sanitize_filename
    from neyma_product_driver.scenario_suite import verify_case_evidence
    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.models import Decision, EvaluatorDecision, ScenarioResult
    from neyma_product_driver.scenario_gate import evaluate_gate
    from neyma_product_driver.scenario_planner import ScenarioPlanner
    from neyma_product_driver.scenario_suite import SuiteExecutor, build_suite
    from neyma_product_driver.scenarios import Scenario

    from scenario_fixtures import (
        FakeFounder,
        FakeUnit,
        ScriptedReasoner,
        base_scenario,
        raw_payload,
        raw_scenario,
    )

    root = out / "g02"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    report: dict = {
        "filesystem_case_insensitive": fs_is_case_insensitive(root),
        "sanitize_pure_function_collisions": {
            "'case 001' vs 'case/001'": [
                sanitize_filename("case 001"),
                sanitize_filename("case/001"),
                sanitize_filename("case 001") == sanitize_filename("case/001"),
            ],
            "'approve twice' vs 'approve-twice'": [
                sanitize_filename("approve twice"),
                sanitize_filename("approve-twice"),
                sanitize_filename("approve twice") == sanitize_filename("approve-twice"),
            ],
            "'Auth-01' vs 'auth-01' (distinct strings)": [
                sanitize_filename("Auth-01"),
                sanitize_filename("auth-01"),
                sanitize_filename("Auth-01") == sanitize_filename("auth-01"),
            ],
        },
    }

    # ---- Route B: two generated ids differing only in case -----------------
    a = raw_scenario("gen-AUTH-01", risk_category="authorization")
    a["title"] = "an unauthorised approval is refused"
    a["actions"][0]["request"]["path"] = "/invoices/1/approve"
    b = raw_scenario("gen-auth-01", risk_category="authorization")
    b["title"] = "an approval by the wrong tenant is refused"
    b["actions"][0]["request"]["path"] = "/invoices/2/approve"
    b["actions"][1]["request"]["path"] = "/invoices/2/approve"

    cfg = ScenarioGenerationConfig(enabled=True, max_initial_scenarios=8)
    planner = ScenarioPlanner(
        repo=root / "repo",
        config=cfg,
        reasoner=ScriptedReasoner(
            [
                raw_payload(
                    a,
                    b,
                    risks=[
                        {
                            "id": "R1",
                            "description": "an unauthorised caller may approve",
                            "risk_category": "authorization",
                            "severity": "P0",
                            "basis": "the diff touched the approval path",
                        }
                    ],
                )
            ]
        ),
        store=EvidenceStore(root / "runs", "run-1"),
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
        emit=lambda _m: None,
    )
    planner.plan_initial(task="t", unit=FakeUnit(), run_id="run-1")
    ids = [s.id for s in planner.plan.scenarios]
    report["route_b_admitted_ids"] = ids
    report["route_b_rejected"] = [
        f"{r.id}: {'; '.join(r.reasons)}" for w in planner.plan.waves for r in w.rejected
    ]

    suite = build_suite(
        permanent=[("backend_generic", base_scenario())],
        generated=[
            (m, planner.compiled[m.id]) for m in planner.plan.scenarios if m.id in planner.compiled
        ],
    )
    report["route_b_suite_entries"] = [e.scenario_id for e in suite.entries]
    report["route_b_assembly_conflicts"] = list(suite.assembly_conflicts)

    class AlwaysPass:
        service_logs: dict[str, str] = {}

        def __init__(self, d: Path) -> None:
            self.d = d

        async def execute(self, scenario: Scenario) -> ScenarioResult:
            return ScenarioResult(scenario_name=scenario.name, readiness_ok=True)

    ex = SuiteExecutor(
        make_executor=lambda d: AlwaysPass(d),
        artifact_root=root / "artifacts",
        run_id="run-1",
        iteration=1,
    )
    result = asyncio.run(ex.run(suite, selection_reason="full"))
    paths = {o.scenario_id: o.evidence_path for o in result.outcomes}
    report["route_b_evidence_paths"] = paths
    report["route_b_distinct_paths"] = len(set(paths.values()))
    report["route_b_distinct_paths_casefolded"] = len(
        {p.casefold() for p in paths.values()}
    )
    scen_dir = root / "artifacts" / "scenarios"
    report["route_b_dirs_on_disk"] = sorted(os.listdir(scen_dir))
    report["route_b_evidence_verified"] = {
        o.scenario_id: o.evidence_verified for o in result.outcomes
    }
    report["route_b_outcomes"] = {o.scenario_id: o.outcome.value for o in result.outcomes}
    # Re-verify after the whole suite has run, which is when a reader would look.
    report["route_b_reverification_after_run"] = {
        o.scenario_id: verify_case_evidence(
            o.evidence_path, scenario_id=o.scenario_id, run_id="run-1", iteration=1
        )
        or "OK"
        for o in result.outcomes
    }
    verdict = evaluate_gate(result, generation_problems=[], risks=list(planner.plan.risks))
    report["route_b_gate"] = (
        f"{verdict.status.value} {verdict.required_passed}/{verdict.required_total}"
    )
    final = driver_cli._apply_suite_precedence(
        result,
        EvaluatorDecision(decision=Decision.ACCEPT, summary="ok", confidence=0.9),
        "backend_generic",
        lambda _m: None,
        generation_problems=[],
        risks=list(planner.plan.risks),
    )
    report["route_b_hostile_accept_becomes"] = final.decision.value

    # ---- Route A: permanent name with a space ------------------------------
    perm = base_scenario()
    perm2 = Scenario(**{**perm.model_dump(), "name": "approve twice"})
    a2 = raw_scenario("approve-twice")
    a2["actions"][0]["request"]["path"] = "/invoices/9/approve"
    planner2 = ScenarioPlanner(
        repo=root / "repo2",
        config=cfg,
        reasoner=ScriptedReasoner([raw_payload(a2)]),
        store=EvidenceStore(root / "runs2", "run-2"),
        base_scenario=perm2,
        permanent_scenarios=[perm2],
        founder=FakeFounder(),
        emit=lambda _m: None,
    )
    planner2.plan_initial(task="t", unit=FakeUnit(), run_id="run-2")
    suite2 = build_suite(
        permanent=[("approve twice", perm2)],
        generated=[
            (m, planner2.compiled[m.id])
            for m in planner2.plan.scenarios
            if m.id in planner2.compiled
        ],
    )
    ex2 = SuiteExecutor(
        make_executor=lambda d: AlwaysPass(d),
        artifact_root=root / "artifacts2",
        run_id="run-2",
        iteration=1,
    )
    result2 = asyncio.run(ex2.run(suite2, selection_reason="full"))
    paths2 = {o.scenario_id: o.evidence_path for o in result2.outcomes}
    verdict2 = evaluate_gate(result2, generation_problems=[], risks=list(planner2.plan.risks))
    report["route_a"] = {
        "suite_entries": [e.scenario_id for e in suite2.entries],
        "assembly_conflicts": list(suite2.assembly_conflicts),
        "evidence_paths": paths2,
        "distinct_paths": len(set(paths2.values())),
        "dirs_on_disk": sorted(os.listdir(root / "artifacts2" / "scenarios")),
        "evidence_verified": {o.scenario_id: o.evidence_verified for o in result2.outcomes},
        "reverification_after_run": {
            o.scenario_id: verify_case_evidence(
                o.evidence_path, scenario_id=o.scenario_id, run_id="run-2", iteration=1
            )
            or "OK"
            for o in result2.outcomes
        },
        "gate": f"{verdict2.status.value} {verdict2.required_passed}/{verdict2.required_total}",
    }

    print(json.dumps(report, indent=2, default=str))
    (out / "adj-g02.json").write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
