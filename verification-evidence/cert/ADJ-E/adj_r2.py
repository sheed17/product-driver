"""ADJ-E R2: is the fail-open response to an unreadable plan reachable other
than through R1, and what does it cost?

Four parts:
  A. sweep the WHOLE plan model for every redact_obj key collision, and which
     of them are fatal to re-parse (is `authorization` really the only one?)
  B. is the same collision fatal to state.json / the iteration record?
  C. truncation reachability: is scenario-plan.json written non-atomically, and
     does a truncated prefix produce the same fail-open?
  D. the wave-budget escape with max_waves=1, driven through the real planner.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.models import IterationRecord, RunState, redact_obj
from neyma_product_driver.scenario_generator import GenerationBrief
from neyma_product_driver.scenario_plan import (
    GeneratedAction,
    GeneratedRequest,
    GeneratedScenario,
    GeneratedScenarioPlan,
    GeneratedStateCheck,
    IdentifiedRisk,
    Priority,
    RejectedScenario,
    RiskCategory,
    WaveRecord,
)
from neyma_product_driver.scenario_planner import PLAN_FILENAME, ScenarioPlanner

out: dict = {}


# ---------------------------------------------------------------- A ----------
def rich_plan() -> GeneratedScenarioPlan:
    plan = GeneratedScenarioPlan(run_id="adj-e", task="probe task")
    for i, cat in enumerate(RiskCategory):
        plan.risks.append(
            IdentifiedRisk(
                id=f"R{i}", description=f"risk {cat.value}", risk_category=cat,
                severity=Priority.P1,
            )
        )
        plan.scenarios.append(
            GeneratedScenario(
                id=f"gen-{cat.value}",
                title=f"probe {cat.value}",
                risk_category=cat,
                priority=Priority.P1,
                actions=[
                    GeneratedAction(
                        kind="request",
                        request=GeneratedRequest(
                            method="POST",
                            path="/api/x",
                            headers={"Authorization": "Bearer abc", "X-Token": "t"},
                            json_body={"password": "hunter2", "count": 3},
                            expect_status=200,
                        ),
                    )
                ],
                persisted_state_checks=[GeneratedStateCheck(command="ls")],
            )
        )
    plan.waves.append(
        WaveRecord(
            wave=1,
            stage="initial",
            rejected=[
                RejectedScenario(
                    id="bad", title="bad", reasons=["no"],
                    raw={"authorization": 7, "secret": 1, "nested": {"token": 2}},
                )
            ],
        )
    )
    plan.executed_scenario_ids = [s.id for s in plan.scenarios]
    plan.recompute_coverage()
    return plan


def walk(a, b, path=""):
    """Yield JSON paths where redaction changed the value."""
    if type(a) is not type(b):
        yield path, a, b
        return
    if isinstance(a, dict):
        for k in a:
            yield from walk(a[k], b.get(k), f"{path}.{k}")
    elif isinstance(a, list):
        for i, (x, y) in enumerate(zip(a, b)):
            yield from walk(x, y, f"{path}[{i}]")
    elif a != b:
        yield path, a, b


plan = rich_plan()
raw = plan.model_dump(mode="json")
red = redact_obj(raw)
changes = [
    {"path": p, "before": str(x)[:40], "after": str(y)[:40]} for p, x, y in walk(raw, red)
]
out["A_redaction_changes_in_full_plan"] = changes
try:
    GeneratedScenarioPlan.model_validate(red)
    out["A_reparse_full_redacted_plan"] = "OK"
except Exception as exc:
    out["A_reparse_full_redacted_plan"] = str(exc).splitlines()[:8]

# which single category, on its own, is fatal?
fatal = []
for cat in RiskCategory:
    p = GeneratedScenarioPlan()
    p.scenarios.append(
        GeneratedScenario(id=f"gen-{cat.value}", title="t", risk_category=cat)
    )
    p.recompute_coverage()
    try:
        GeneratedScenarioPlan.model_validate(redact_obj(p.model_dump(mode="json")))
    except Exception:
        fatal.append(cat.value)
out["A_categories_fatal_to_reparse"] = fatal


# ---------------------------------------------------------------- B ----------
tmp = Path(tempfile.mkdtemp(prefix="adje-r2-state-"))
store = EvidenceStore(runs_dir=tmp, run_id="run")
state = RunState(run_id="run", task="t")
state.iterations.append(
    IterationRecord(
        iteration=1,
        suite={
            "coverage_by_risk_category": {
                "authorization": {"passed": 2, "failed": 1},
                "boundary": {"passed": 1, "failed": 0},
            }
        },
    )
)
store.save_state(state)
persisted = json.loads(store.state_path.read_text())
out["B_state_json_authorization_bucket"] = (
    persisted["iterations"][0]["suite"]["coverage_by_risk_category"]
)
out["B_state_reloads"] = store.load_state() is not None


# ---------------------------------------------------------------- C ----------
class _NullReasoner:
    session_id = ""

    def propose(self, brief: GenerationBrief):
        return {}


def fresh_planner(store: EvidenceStore, emit=None, **cfg) -> ScenarioPlanner:
    return ScenarioPlanner(
        repo=Path.cwd(),
        config=ScenarioGenerationConfig(**cfg),
        reasoner=_NullReasoner(),
        store=store,
        emit=emit or (lambda _m: None),
    )


tmp = Path(tempfile.mkdtemp(prefix="adje-r2-trunc-"))
store = EvidenceStore(runs_dir=tmp, run_id="run")
p = fresh_planner(store)
clean = rich_plan()
# strip the one authorization scenario so R1 is NOT in play
clean.scenarios = [s for s in clean.scenarios if s.risk_category is not RiskCategory.AUTHORIZATION]
clean.risks = [r for r in clean.risks if r.risk_category is not RiskCategory.AUTHORIZATION]
clean.recompute_coverage()
p.plan = clean
p._wave = 1
p.persist()
plan_path = store.run_dir / PLAN_FILENAME
full = plan_path.read_text()
out["C_clean_plan_bytes"] = len(full)

trunc_results = {}
for frac in (0.1, 0.5, 0.9, 0.999):
    plan_path.write_text(full[: int(len(full) * frac)], encoding="utf-8")
    emits: list[str] = []
    q = fresh_planner(store, emit=emits.append)
    note = q.restore_from_store()
    trunc_results[f"{frac:g}"] = {
        "restore_note": note,
        "emissions": emits,
        "waves_used_after": q.waves_used,
        "scenarios_after": len(q.plan.scenarios),
    }
plan_path.write_text(full, encoding="utf-8")
out["C_truncated_plan_restore"] = trunc_results


# is the plan write atomic? watch for a staging file / partial sizes
def _reader(path: str, stop, sizes, bad):
    p = Path(path)
    while not stop.is_set():
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        sizes.append(len(text))
        try:
            json.loads(text)
        except ValueError:
            bad.value += 1


if __name__ == "__main__":
    mgr = mp.Manager()
    stop = mgr.Event()
    sizes = mgr.list()
    bad = mgr.Value("i", 0)
    reader = mp.Process(target=_reader, args=(str(plan_path), stop, sizes, bad))
    reader.start()
    time.sleep(0.2)
    big = rich_plan()
    big.scenarios *= 40          # make the write long enough to observe
    big.recompute_coverage()
    writer = fresh_planner(store)
    writer.plan = big
    t0 = time.monotonic()
    while time.monotonic() - t0 < 3.0:
        writer.persist()
        writer.plan = clean
        writer.persist()
        writer.plan = big
    stop.set()
    reader.join(timeout=5)
    observed = list(sizes)
    out["D_atomicity_probe"] = {
        "reads": len(observed),
        "unparseable_reads": bad.value,
        "distinct_sizes_seen": len(set(observed)),
        "size_range": [min(observed), max(observed)] if observed else [],
        "staging_file_used_for_plan": any(
            f.name.startswith(".") for f in store.run_dir.iterdir()
        ),
    }

    # ------------------------------------------------------------- E --------
    # wave-budget escape with max_waves=1: clean vs unreadable plan
    def budget_case(label: str, corrupt: str | None) -> dict:
        t = Path(tempfile.mkdtemp(prefix=f"adje-r2-budget-{label}-"))
        s = EvidenceStore(runs_dir=t, run_id="run")
        one = ScenarioGenerationConfig(max_waves=1)
        a = ScenarioPlanner(
            repo=Path.cwd(), config=one, reasoner=_NullReasoner(), store=s
        )
        pl = GeneratedScenarioPlan(run_id="r")
        pl.scenarios.append(
            GeneratedScenario(
                id="gen-a", title="t",
                risk_category=RiskCategory.AUTHORIZATION if corrupt == "auth"
                else RiskCategory.BOUNDARY,
            )
        )
        pl.recompute_coverage()
        pl.waves.append(WaveRecord(wave=1, stage="initial"))
        a.plan = pl
        a._wave = 1
        a.persist()
        pp = s.run_dir / PLAN_FILENAME
        before_bytes = pp.stat().st_size
        if corrupt == "truncate":
            pp.write_text(pp.read_text()[: before_bytes // 2], encoding="utf-8")

        emits: list[str] = []
        b = ScenarioPlanner(
            repo=Path.cwd(), config=one, reasoner=_NullReasoner(),
            store=s, emit=emits.append,
        )
        b.restore_from_store()
        waves_at_restore = b.waves_used
        exhausted_at_restore = b.budget_exhausted()
        # what run_control_loop does next, unconditionally
        b.plan_initial(task="t", run_id="r")
        return {
            "waves_used_at_restore": waves_at_restore,
            "budget_exhausted_at_restore": exhausted_at_restore,
            "waves_used_after_plan_initial": b.waves_used,
            "wave_records_after_plan_initial": [
                {"wave": w.wave, "stage": w.stage, "notes": w.budget_notes}
                for w in b.plan.waves
            ],
            "plan_bytes_before": before_bytes,
            "plan_bytes_after": pp.stat().st_size,
            "scenarios_after": [x.id for x in b.plan.scenarios],
            "emissions": emits,
            "wave_files": sorted(
                f.name for f in (s.run_dir / "scenario-generation").glob("*.json")
            ),
        }

    out["E_budget_clean"] = budget_case("clean", None)
    out["E_budget_authorization"] = budget_case("auth", "auth")
    out["E_budget_truncated"] = budget_case("trunc", "truncate")

    print(json.dumps(out, indent=2, default=str))
