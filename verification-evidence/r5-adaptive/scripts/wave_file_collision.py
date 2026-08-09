"""Do refused waves overwrite each other's evidence file?

`_generate` builds `WaveRecord(wave=self._wave + 1, ...)` before checking the
wave budget, and a refused wave never increments `_wave`. `persist()` writes
`scenario-generation/wave-{record.wave:02d}.json`, so every refusal after the
budget is spent writes to the same filename.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import Founder, STATE_CMD, Unit, base_scenario  # noqa: E402
from neyma_product_driver.config import ScenarioGenerationConfig  # noqa: E402
from neyma_product_driver.evidence import EvidenceStore  # noqa: E402
from neyma_product_driver.scenario_planner import ScenarioPlanner  # noqa: E402


class Scripted:
    session_id = "scripted"

    def propose(self, brief):
        return {"risks": [], "scenarios": []}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="r5-waves-"))
    store = EvidenceStore(tmp, run_id="r5-waves")
    cfg = ScenarioGenerationConfig(enabled=True, approved_commands=[STATE_CMD], max_waves=2)
    base = base_scenario("none", 8999, tmp / "store.json")
    p = ScenarioPlanner(
        repo=ROOT, config=cfg, reasoner=Scripted(), store=store,
        base_scenario=base, permanent_scenarios=[base], founder=Founder(),
        emit=lambda _m: None,
    )
    p.plan_initial(task="t", unit=Unit())
    for _ in range(5):
        p.expand_after_failures(task="t", unit=Unit(), failures=["[FAIL] x"])

    files = sorted(f.name for f in (store.run_dir / "scenario-generation").glob("*.json"))
    plan = json.loads((store.run_dir / "scenario-plan.json").read_text())
    print(f"run dir: {store.run_dir}")
    print(f"wave records in scenario-plan.json: {len(plan['waves'])}")
    print(f"wave numbers recorded: {[w['wave'] for w in plan['waves']]}")
    print(f"per-wave evidence files written: {files}")
    if len(files) < len(plan["waves"]):
        print(
            f"\nFAIL: {len(plan['waves'])} wave records collapsed into {len(files)} files; "
            f"{len(plan['waves']) - len(files)} refusal record(s) overwrote each other."
        )
        return 1
    print("\nPASS: one file per wave record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
