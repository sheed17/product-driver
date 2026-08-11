"""E-RESUME — leftovers, on a CLEAN plan (no `authorization` scenario), so the
`authorization` corruption does not mask the behaviour under test.

H. HEAD moved between processes: is the plan restored AND flagged?
I. evidence directory deleted between processes: what does resume say?
J. long ids: does `proposed_id` survive the resume reload?
K. ids colliding only after sanitisation: is the loser visibly refused?
L. which RiskCategory values trip the redaction key-name filter?
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for p in (str(REPO), str(REPO / "tests"), str(HERE)):
    sys.path.insert(0, p)
PY = str(REPO / ".venv" / "bin" / "python")

import resume_probe as rp  # noqa: E402
import resume_probe2 as rp2  # noqa: E402
from neyma_product_driver.evidence import EvidenceStore  # noqa: E402
from neyma_product_driver.models import redact_obj  # noqa: E402
from neyma_product_driver.scenario_plan import RiskCategory  # noqa: E402
from scenario_fixtures import FakeFounder, FakeUnit, base_scenario, raw_payload  # noqa: E402

RUN_ID = rp.RUN_ID
LONG_A = "gen-" + "m" * 90 + "-alpha"
LONG_B = "gen-" + "m" * 90 + "-beta"


def child_generate_long(work: Path) -> None:
    config, store, planner = rp2.make_planner(
        work,
        max_waves=3,
        payloads=[
            raw_payload(
                rp.probe_scenario(LONG_A, "alpha"),
                rp.probe_scenario(LONG_B, "beta", risk_category="boundary"),
            )
        ],
    )
    planner.plan_initial(task=config.task, unit=FakeUnit(), run_id=RUN_ID)
    (work / "expected.json").write_text(
        json.dumps(rp.snapshot(planner, store, "process1"), indent=2), encoding="utf-8"
    )
    sys.stdout.flush()
    os._exit(9)


def child_collide(work: Path) -> None:
    """Two ids that are distinct until sanitisation maps them together."""
    config, store, planner = rp2.make_planner(
        work,
        max_waves=3,
        payloads=[
            raw_payload(
                rp.probe_scenario("gen/alpha", "alpha"),
                rp.probe_scenario("gen:alpha", "beta", risk_category="boundary"),
            )
        ],
    )
    planner.plan_initial(task=config.task, unit=FakeUnit(), run_id=RUN_ID)
    snap = rp.snapshot(planner, store, "collide")
    (work / "collide.json").write_text(json.dumps(snap, indent=2), encoding="utf-8")


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="e-resume-part1c-"))
    out: dict[str, Any] = {"workdir": str(root)}

    # H — HEAD moved, clean plan
    work = rp2.fresh(root, "head-moved")
    rp2.run(work, "generate", "boundary", "3")
    note = rp.mutate_move_head(work)
    rp2.run(work, "resume", "restored.json")
    restored = json.loads((work / "restored.json").read_text())
    out["H_head_moved"] = {
        "mutation": note,
        "emissions": restored["restore_emissions"],
        "scenarios_restored": restored["plan"]["scenario_ids"],
        "waves_used": restored["waves_used"],
        "flagged": any("repository moved" in m for m in restored["restore_emissions"]),
    }

    # I — evidence directories deleted between processes, clean plan
    work = rp2.fresh(root, "evidence-deleted")
    subprocess.run([PY, str(HERE / "resume_probe2.py"), "--child", str(work), "loop", "first"],
                   capture_output=True, text=True, cwd=str(REPO))
    before = sorted(
        str(p.relative_to(work)) for p in (work / "driver" / "runs" / RUN_ID).rglob("result.json")
    )
    note = rp.mutate_delete_evidence(work)
    rp2.run(work, "resume", "restored.json")
    restored = json.loads((work / "restored.json").read_text())
    out["I_evidence_deleted"] = {
        "mutation": note,
        "evidence_before": before,
        "evidence_after": sorted(restored["evidence_records"]),
        "emissions": restored["restore_emissions"],
        "executed_scenario_ids_still_claimed": restored["plan"]["executed_scenario_ids"],
        "resume_noticed_the_loss": any(
            "evidence" in m.lower() for m in restored["restore_emissions"]
        ),
    }

    # J — proposed_id across the resume reload
    work = rp2.fresh(root, "long-ids")
    subprocess.run([PY, __file__, "--child", str(work), "generate_long"],
                   capture_output=True, text=True, cwd=str(REPO))
    rp2.run(work, "resume", "restored.json")
    expected = json.loads((work / "expected.json").read_text())
    restored = json.loads((work / "restored.json").read_text())
    plan_raw = json.loads(
        (work / "driver" / "runs" / RUN_ID / "scenario-plan.json").read_text()
    )
    out["J_proposed_id_across_resume"] = {
        "originals": [LONG_A, LONG_B],
        "ids_p1": expected["plan"]["scenario_ids"],
        "ids_p2": restored["plan"]["scenario_ids"],
        "proposed_p1": expected["plan"]["proposed_ids"],
        "proposed_p2": restored["plan"]["proposed_ids"],
        "in_plan_file": [s.get("proposed_id") for s in plan_raw["scenarios"]],
        "survived": expected["plan"]["proposed_ids"] == restored["plan"]["proposed_ids"]
        == [LONG_A, LONG_B],
    }

    # K — post-sanitisation id collision through the real planner
    work = rp2.fresh(root, "collide")
    subprocess.run([PY, __file__, "--child", str(work), "collide"],
                   capture_output=True, text=True, cwd=str(REPO))
    snap = json.loads((work / "collide.json").read_text())
    wave = snap["plan"]["waves"][0]
    plan_raw = json.loads((work / "driver" / "runs" / RUN_ID / "scenario-plan.json").read_text())
    out["K_post_sanitisation_collision"] = {
        "accepted_ids": wave["accepted_ids"],
        "rejected_ids": wave["rejected"],
        "rejection_reasons": [
            r["reasons"] for w in plan_raw["waves"] for r in w["rejected"]
        ],
        "proposed_ids_kept": snap["plan"]["proposed_ids"],
        "second_scenario_lost_silently": not wave["rejected"],
    }

    # L — which risk-category names trip the redaction key filter?
    tripped = []
    for cat in RiskCategory:
        probe = {cat.value: 3}
        if redact_obj(probe)[cat.value] != 3:
            tripped.append(cat.value)
    out["L_categories_tripping_redaction"] = {
        "categories": tripped,
        "of_total": len(list(RiskCategory)),
    }

    path = HERE / "resume_probe3.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2)[:9000])
    print(f"\nwrote {path}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        work = Path(sys.argv[2])
        if sys.argv[3] == "generate_long":
            child_generate_long(work)
        elif sys.argv[3] == "collide":
            child_collide(work)
    else:
        main()
