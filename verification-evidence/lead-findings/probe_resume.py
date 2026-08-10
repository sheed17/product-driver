"""Does generated scenario state survive a resume?

`ScenarioPlanner.persist()` writes runs/<id>/scenario-plan.json, and
`_make_planner` builds a fresh planner on every invocation. If nothing reads the
plan back, a resumed run:
  * forgets every scenario it already generated (duplicates regenerate),
  * resets waves_used, so max_waves stops bounding the run across resumes.

Driven with the repo's own deterministic fixtures. No model involved.
"""
import sys
from pathlib import Path
import tempfile

sys.path.insert(0, "tests")

from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.scenario_planner import ScenarioPlanner
from scenario_fixtures import (  # type: ignore
    FakeFounder,
    FakeUnit,
    ScriptedReasoner,
    base_scenario,
    raw_payload,
    raw_scenario,
)

tmp = Path(tempfile.mkdtemp())
store = EvidenceStore(tmp / "runs", "run-resume-probe")

cfg = ScenarioGenerationConfig(enabled=True, max_waves=2)


def make_planner(payloads):
    return ScenarioPlanner(
        repo=tmp,
        config=ScenarioGenerationConfig(enabled=True, max_waves=2),
        reasoner=ScriptedReasoner(payloads),
        store=store,
        base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()],
        founder=FakeFounder(),
    )


# --- first process: generate, then exhaust the wave budget -----------------
p1 = make_planner(
    [
        raw_payload(raw_scenario("gen-a")),
        raw_payload(raw_scenario("gen-b")),
        raw_payload(raw_scenario("gen-c")),
    ]
)
p1.plan_initial(task="approval endpoint", unit=FakeUnit())
p1.expand_after_failures(task="approval endpoint", unit=FakeUnit(), failures=["gen-a failed"])
p1.persist()

print("--- before interruption ---")
print("scenarios generated :", [s.id for s in p1.plan.scenarios])
print("waves_used          :", p1.waves_used)
print("budget_exhausted    :", p1.budget_exhausted())
plan_file = store.run_dir / "scenario-plan.json"
print("plan persisted      :", plan_file.exists(), plan_file)

# --- simulate resume: same run dir, same store, new process ---------------
p2 = make_planner(
    [
        raw_payload(raw_scenario("gen-a")),  # the SAME scenario as before
        raw_payload(raw_scenario("gen-d")),
        raw_payload(raw_scenario("gen-e")),
    ]
)

print()
print("--- after resume (same run id, same store) ---")
print("scenarios restored  :", [s.id for s in p2.plan.scenarios])
print("waves_used          :", p2.waves_used)
print("budget_exhausted    :", p2.budget_exhausted())

# Does it regenerate a duplicate it already produced and persisted?
p2.plan_initial(task="approval endpoint", unit=FakeUnit())
regenerated = [s.id for s in p2.plan.scenarios]
print("after replanning    :", regenerated)

print()
if not [s.id for s in p2.plan.scenarios] or "gen-a" in regenerated:
    print("RESULT: resume does NOT restore generated scenario state.")
    print("        - prior scenarios are absent from the new planner")
    print("        - 'gen-a' was regenerated despite already existing in the persisted plan")
else:
    print("RESULT: state appears to be restored.")

print()
print("wave budget across resume:")
p2.expand_after_failures(task="t", unit=FakeUnit(), failures=["gen-d failed"])
print("  p2.waves_used   :", p2.waves_used, "(max_waves =", cfg.max_waves, ")")
print("  total waves actually run across both processes:", p1.waves_used + p2.waves_used)
if p1.waves_used + p2.waves_used > cfg.max_waves:
    print("  => max_waves was exceeded across the resume boundary")
