"""ADJ-G: a second, worse resume-time loss found while adjudicating G-SCALE-01.

`EvidenceStore.write_json` runs `redact_obj`, which masks any dict *key* matching
/authorization|token|secret|.../ . `CoverageSummary.by_risk_category` is keyed by
RiskCategory value, and `authorization` is one of the shipped enum members. A
plan containing one authorization-category scenario is therefore persisted with
`by_risk_category: {"authorization": "[REDACTED]"}` — an int field holding a
string — so the plan file no longer validates.

On resume `restore_from_store` cannot parse it, silently starts from an empty
plan, and the next `persist()` overwrites the file. Control: the same plan
without an authorization scenario round-trips fine.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


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

    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.scenario_plan import GeneratedScenarioPlan
    from neyma_product_driver.scenario_planner import ScenarioPlanner

    from scenario_fixtures import (
        FakeFounder,
        FakeUnit,
        ScriptedReasoner,
        base_scenario,
        raw_payload,
        raw_scenario,
    )

    def build(tag: str, categories: list[str]) -> dict:
        root = out / "g01auth" / tag
        if root.exists():
            shutil.rmtree(root)
        props = []
        for i, cat in enumerate(categories):
            p = raw_scenario(f"gen-{cat}-{i}", risk_category=cat)
            p["title"] = f"{cat} case {i}"
            p["actions"][0]["request"]["path"] = f"/invoices/{i}/approve"
            props.append(p)
        cfg = ScenarioGenerationConfig(enabled=True, max_initial_scenarios=12)
        writer = ScenarioPlanner(
            repo=root / "repo",
            config=cfg,
            reasoner=ScriptedReasoner([raw_payload(*props)]),
            store=EvidenceStore(root / "runs", "run-1"),
            base_scenario=base_scenario(),
            permanent_scenarios=[base_scenario()],
            founder=FakeFounder(),
            emit=lambda _m: None,
        )
        writer.plan_initial(task="t", unit=FakeUnit(), run_id="run-1")
        planned = len(writer.plan.scenarios)
        waves_used = writer.waves_used
        writer.persist()
        path = root / "runs" / "run-1" / "scenario-plan.json"
        on_disk = json.loads(path.read_text())
        try:
            GeneratedScenarioPlan.model_validate_json(path.read_text())
            parse = "parses"
        except Exception as exc:
            parse = f"{type(exc).__name__}: {str(exc).splitlines()[1].strip()}"

        reader = ScenarioPlanner(
            repo=root / "repo",
            config=cfg,
            reasoner=ScriptedReasoner([]),
            store=EvidenceStore(root / "runs", "run-1"),
            base_scenario=base_scenario(),
            permanent_scenarios=[base_scenario()],
            founder=FakeFounder(),
        )
        emitted: list[str] = []
        reader.emit = emitted.append
        note = reader.restore_from_store()
        restored = len(reader.plan.scenarios)
        # What the loop does next, whatever happened above.
        reader.note_executed(["backend_generic"])
        after_persist = json.loads(path.read_text())
        return {
            "planned": planned,
            "waves_used_before": waves_used,
            "by_risk_category_on_disk": on_disk["coverage_summary"]["by_risk_category"],
            "plan_file_parses": parse,
            "restore_note": note,
            "scenarios_after_restore": restored,
            "waves_used_after_restore": reader.waves_used,
            "console": [e[:140] for e in emitted],
            "plan_on_disk_after_next_persist": len(after_persist["scenarios"]),
            "wave_files_left": sorted(
                p.name for p in (root / "runs" / "run-1" / "scenario-generation").glob("*.json")
            )
            if (root / "runs" / "run-1" / "scenario-generation").exists()
            else [],
        }

    report = {
        "control_no_authorization": build("control", ["idempotency", "concurrency"]),
        "with_one_authorization_scenario": build(
            "auth", ["idempotency", "authorization", "concurrency"]
        ),
    }
    print(json.dumps(report, indent=2, default=str))
    (out / "adj-g01-authorization.json").write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
