"""ADJ-E R1: does an authorization-category scenario corrupt scenario-plan.json?

Independent of the reviewer's harness. Drives real product objects only:
EvidenceStore.write_json, GeneratedScenarioPlan, ScenarioPlanner.persist/restore.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import redact_obj
from neyma_product_driver.scenario_plan import (
    GeneratedScenario,
    GeneratedScenarioPlan,
    Priority,
    RiskCategory,
)

out: dict = {}

# --- 0. which RiskCategory values are masked by redact_obj as dict KEYS? ------
masked = []
for cat in RiskCategory:
    probe = redact_obj({cat.value: 3})
    if probe[cat.value] != 3:
        masked.append(cat.value)
out["risk_categories_masked_as_dict_keys"] = masked
out["risk_category_count"] = len(list(RiskCategory))

# --- 1. build a plan with one authorization scenario and one clean one -------
def make_plan(category: RiskCategory, sid: str) -> GeneratedScenarioPlan:
    plan = GeneratedScenarioPlan(run_id="adj-e", task="probe")
    plan.scenarios.append(
        GeneratedScenario(
            id=sid,
            title="probe",
            risk_category=category,
            priority=Priority.P1,
        )
    )
    plan.recompute_coverage()
    return plan


results = {}
for label, cat in (("clean_boundary", RiskCategory.BOUNDARY),
                   ("authorization", RiskCategory.AUTHORIZATION),
                   ("cross_tenant", RiskCategory.CROSS_TENANT),
                   ("approval_required", RiskCategory.APPROVAL_REQUIRED)):
    tmp = Path(tempfile.mkdtemp(prefix=f"adje-{label}-"))
    store = EvidenceStore(runs_dir=tmp, run_id="run")
    plan = make_plan(cat, f"gen-{label}")
    path = store.write_json("scenario-plan.json", plan.model_dump(mode="json"))
    raw = json.loads(path.read_text())
    entry = {
        "persisted_by_risk_category": raw["coverage_summary"]["by_risk_category"],
        "persisted_total": raw["coverage_summary"]["total_scenarios"],
    }
    try:
        back = GeneratedScenarioPlan.model_validate_json(path.read_text())
        entry["reread"] = "OK"
        entry["reread_scenarios"] = [s.id for s in back.scenarios]
    except Exception as exc:
        entry["reread"] = f"{type(exc).__name__}"
        entry["reread_error"] = str(exc).splitlines()[:6]
    results[label] = entry

out["per_category_roundtrip"] = results

# --- 2. same thing through the real planner persist/restore ------------------
from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.scenario_planner import ScenarioPlanner


class _NullReasoner:
    session_id = ""

    def propose(self, brief):  # pragma: no cover - not used
        return {}


def planner_cycle(cat: RiskCategory, label: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix=f"adje-planner-{label}-"))
    store = EvidenceStore(runs_dir=tmp, run_id="run")
    p1 = ScenarioPlanner(
        repo=Path.cwd(),
        config=ScenarioGenerationConfig(),
        reasoner=_NullReasoner(),
        store=store,
    )
    p1.plan = make_plan(cat, f"gen-{label}")
    p1.plan.executed_scenario_ids = [f"gen-{label}"]
    p1.plan.observed_failure_ids = [f"gen-{label}"]
    p1._wave = 1
    from neyma_product_driver.scenario_plan import WaveRecord

    p1.plan.waves.append(WaveRecord(wave=1, stage="initial"))
    p1.persist()
    size = (store.run_dir / "scenario-plan.json").stat().st_size

    emissions: list[str] = []
    p2 = ScenarioPlanner(
        repo=Path.cwd(),
        config=ScenarioGenerationConfig(),
        reasoner=_NullReasoner(),
        store=store,
        emit=emissions.append,
    )
    note = p2.restore_from_store()
    before = {"waves_used": p1.waves_used, "scenarios": [s.id for s in p1.plan.scenarios]}
    after = {
        "waves_used": p2.waves_used,
        "scenarios": [s.id for s in p2.plan.scenarios],
        "executed_scenario_ids": list(p2.plan.executed_scenario_ids),
        "restore_note": note,
        "emissions": emissions,
    }
    # what a subsequent persist() would do to the surviving record
    p2.persist()
    size_after = (store.run_dir / "scenario-plan.json").stat().st_size
    waves_dir = store.run_dir / "scenario-generation"
    return {
        "before": before,
        "after": after,
        "plan_bytes_before": size,
        "plan_bytes_after_resume_persist": size_after,
        "wave_files_surviving": sorted(p.name for p in waves_dir.glob("*.json")) if waves_dir.exists() else [],
        "run_dir": str(store.run_dir),
    }


out["planner_clean"] = planner_cycle(RiskCategory.BOUNDARY, "clean")
out["planner_authorization"] = planner_cycle(RiskCategory.AUTHORIZATION, "authorization")

print(json.dumps(out, indent=2))
