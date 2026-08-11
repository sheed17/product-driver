"""Emit the final per-task table: mechanical metrics + this reviewer's hand U2 verdicts."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
m = json.loads((HERE / "metrics-cert.json").read_text())

# Hand U2 verdicts (see hand-counterfactuals.md). SOUND/PARTIAL pass U2; UNTESTABLE and
# UNSATISFIABLE fail it.
U2_FAIL = {
    ("A-r1", "EX-05"): "UNTESTABLE for its stated purpose",
    ("E-r1", "AUTHZ-S1"): "UNTESTABLE for its cited requirement",
    ("C-r1", "S1"): "UNSATISFIABLE (no /shipments route, no shipments table)",
    ("C-r1", "S2"): "UNSATISFIABLE", ("C-r1", "S3"): "UNSATISFIABLE",
    ("C-r1", "S4"): "UNSATISFIABLE", ("C-r1", "S5"): "UNSATISFIABLE",
    ("C-r1", "S6"): "UNSATISFIABLE", ("C-r1", "S7"): "UNSATISFIABLE",
}
SURFACE = {"A": "UI / stale state", "A2": "UI + unrelated diff",
           "B": "approval, idempotency, cross-tenant", "C": "read-only view",
           "D": "persistence / restart", "E": "authorization / release",
           "F": "dependency / partial failure (NEW)",
           "G": "malformed-missing-conflicting evidence (NEW)"}

print("| task | risk surface | src | prop | acc | rej | USEFUL (U1-U5,G1,U2) | U4-free | own P0 risks left uncovered | trap oracles | endpoints | distinct oracles |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
tot = {"prop": 0, "acc": 0, "rej": 0, "u": 0, "u4f": 0}
for r in sorted(m["per_run"], key=lambda x: x["tag"]):
    key = r["task_key"]
    u = sum(1 for s in r["scenarios"] if s["USEFUL"] and (r["tag"], s["id"]) not in U2_FAIL)
    u4f = sum(1 for s in r["scenarios"]
              if s["USEFUL_ignoring_U4"] and (r["tag"], s["id"]) not in U2_FAIL)
    traps = sum(1 for s in r["scenarios"]
                if any(f.startswith("V3") for f in s["vacuity_flags"]))
    plan = json.loads((HERE / "raw" / f"plan-{r['tag']}.json").read_text())
    p0 = [x for x in plan["risks"] if x["severity"] == "P0"]
    p0_uncov = [x for x in p0 if not x["covered_by"]]
    zc = f"{len(p0_uncov)}/{len(p0)}" if p0 else "no P0"
    tot["prop"] += r["proposed"]; tot["acc"] += r["accepted"]; tot["rej"] += r["rejected"]
    tot["u"] += u; tot["u4f"] += u4f
    print(f"| {r['tag']} | {SURFACE.get(key,'?')} | {'inherited' if r['inherited'] else 'NEW'} "
          f"| {r['proposed']} | {r['accepted']} | {r['rejected']} | {u} | {u4f} "
          f"| {zc} | {traps} "
          f"| {r['n_distinct_request_endpoints']} | {r['n_distinct_oracle_assertions']} |")
print(f"| **total** | | | **{tot['prop']}** | **{tot['acc']}** | **{tot['rej']}** "
      f"| **{tot['u']}** | **{tot['u4f']}** | | | | |")
print()
a = m["aggregate"]
print(f"acceptance rate            : {tot['acc']}/{tot['prop']} = {tot['acc']/tot['prop']:.1%}")
print(f"USEFUL (with U4) of accepted: {tot['u']}/{tot['acc']} = {tot['u']/tot['acc']:.1%}")
print(f"USEFUL (U4-free) of accepted: {tot['u4f']}/{tot['acc']} = {tot['u4f']/tot['acc']:.1%}")
print(f"strict grounding (real unit/criterion + real rubric id): {a['strict_grounded']}/{tot['acc']}")
print(f"scenarios containing ANY broad-suite command           : {a['contains_broad_suite_command']}/{tot['acc']}")
print(f"bare-suite invocations (prior campaign's definition)   : {a['bare_suite_prior_definition']}/{tot['acc']}")
print(f"effect-family cases / with a state oracle             : {a['effect_family_cases']}/{a['effect_family_with_state_oracle']}")
print(f"waves run / failed / empty                            : {a['infrastructure']['waves_total']}"
      f" / {a['infrastructure']['waves_failed']} / {a['infrastructure']['waves_empty_payload']}")
