"""ADJ-G: the resume drop with the two scenario files that actually ship.

A run launched as `--scenario browser_generic` plans generated coverage that
names the service browser_generic declares ("site"). Resuming it as
`--resume-run <id>` without repeating `--scenario` re-reads the config default
(`scenario: backend_generic`), which declares no services. Every generated
scenario then fails to compile and is dropped.

No file is edited and nothing is sabotaged: the operator simply did not repeat a
flag, and `cli.py` loads the scenario from args/config before it opens the run,
never from the saved `state.scenario_name` — which it then overwrites.
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
    from neyma_product_driver.scenario_planner import ScenarioPlanner
    from neyma_product_driver.scenarios import load_scenario

    from scenario_fixtures import (
        FakeFounder,
        FakeUnit,
        ScriptedReasoner,
        raw_payload,
        raw_scenario,
    )

    browser = load_scenario(driver / "scenarios" / "browser_generic.yaml")
    backend = load_scenario(driver / "scenarios" / "backend_generic.yaml")

    props = []
    for i in range(6):
        p = raw_scenario(f"gen-site-{i}", risk_category="happy_path")
        p["title"] = f"the operator surface renders slice {i}"
        p["service_refs"] = ["site"]
        p["mode"] = "backend"
        p["setup"] = []
        p["cleanup"] = []
        p["persisted_state_checks"] = []
        p["isolation_note"] = "read-only: it issues GETs and mutates nothing"
        p["actions"] = [
            {
                "kind": "request",
                "name": "load the operator console",
                "request": {
                    "method": "GET",
                    "path": f"/operator/{i}/",
                    "expect_status": 200,
                },
            }
        ]
        p["expected_observations"] = ["Awaiting your approval"]
        p["forbidden_observations"] = ["Traceback"]
        props.append(p)

    cfg = ScenarioGenerationConfig(enabled=True, max_initial_scenarios=12)
    root = out / "g01shipped"
    if root.exists():
        shutil.rmtree(root)

    writer = ScenarioPlanner(
        repo=driver,
        config=cfg,
        reasoner=ScriptedReasoner([raw_payload(*props, risks=[])]),
        store=EvidenceStore(root / "runs", "run-1"),
        base_scenario=browser,
        permanent_scenarios=[backend, browser],
        founder=FakeFounder(),
        browser_enabled=True,
        emit=lambda _m: None,
    )
    writer.plan_initial(task="t", unit=FakeUnit(), run_id="run-1")
    before = len(writer.plan.scenarios)
    rejected = [
        f"{r.id}: {'; '.join(r.reasons)[:160]}" for w in writer.plan.waves for r in w.rejected
    ]
    writer.persist()

    reader = ScenarioPlanner(
        repo=driver,
        config=cfg,
        reasoner=ScriptedReasoner([]),
        store=EvidenceStore(root / "runs", "run-1"),
        base_scenario=backend,  # the config default, because --scenario was not repeated
        permanent_scenarios=[backend, browser],
        founder=FakeFounder(),
        browser_enabled=True,
    )
    emitted: list[str] = []
    reader.emit = emitted.append
    note = reader.restore_from_store()
    reader.note_executed(["backend_generic"])
    on_disk = json.loads((root / "runs" / "run-1" / "scenario-plan.json").read_text())

    report = {
        "base_at_plan_time": f"{browser.name} (services: {[s.name for s in browser.services]})",
        "base_at_resume_time": f"{backend.name} (services: {[s.name for s in backend.services]})",
        "planned": before,
        "plan_time_rejections": rejected,
        "after_resume": len(reader.plan.scenarios),
        "generation_problems": reader.generation_problems(),
        "restore_note": note,
        "console": [e[:220] for e in emitted],
        "plan_on_disk_after_next_persist": len(on_disk["scenarios"]),
    }
    print(json.dumps(report, indent=2, default=str))
    (out / "adj-g01-shipped.json").write_text(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
