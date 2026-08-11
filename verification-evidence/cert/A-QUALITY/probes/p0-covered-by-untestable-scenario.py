"""PROBE P0 — is a P0 risk in the candidate's own recorded plan 'covered' by a
scenario whose assertions cannot detect the failure the risk describes?

Reads only committed artifacts. No product is started, nothing is written.
Run:  .venv/bin/python verification-evidence/cert/A-QUALITY/probes/p0-covered-by-untestable-scenario.py
"""
import json
from pathlib import Path

PLAN = Path(__file__).resolve().parents[3] / "post-remediation/a-generation-quality/after-fix/plan-A2-diff.json"
plan = json.loads(PLAN.read_text())
s = next(x for x in plan["scenarios"] if x["id"] == "S1")
risk = next(r for r in plan["risks"] if "S1" in r["covered_by"])

print(f"artifact          : {PLAN}")
print(f"risk              : {risk['severity']} {risk['risk_category']}")
print(f"risk description  : {risk['description']}")
print(f"covered_by        : {risk['covered_by']}")
print(f"scenario S1       : {s['priority']} {s['risk_category']} — {s['title']}")
print(f"scenario purpose  : {s['purpose']}")
print()
print("EVERY assertion S1 can produce:")
n = 0
for a in s["actions"]:
    k = a["kind"]
    if k == "request":
        r = a["request"]
        if r.get("expect_status") is not None:
            n += 1; print(f"  {n}. HTTP {r['method']} {r['path']} status == {r['expect_status']}")
        for c in r.get("expect_contains") or []:
            n += 1; print(f"  {n}. HTTP body contains {c!r}")
    if k == "state_check":
        c = a["state_check"]
        for x in c.get("contains") or []:
            n += 1; print(f"  {n}. probe output contains {x!r}")
        for x in c.get("not_contains") or []:
            n += 1; print(f"  {n}. probe output does NOT contain {x!r}")
for c in s.get("persisted_state_checks") or []:
    for x in c.get("contains") or []:
        n += 1; print(f"  {n}. persisted probe contains {x!r}")
    for x in c.get("not_contains") or []:
        n += 1; print(f"  {n}. persisted probe does NOT contain {x!r}")
for x in s.get("expected_observations") or []:
    n += 1; print(f"  {n}. somewhere in observed output: {x!r}")
for x in s.get("forbidden_observations") or []:
    n += 1; print(f"  {n}. nowhere in observed output: {x!r}")
print()
print("COUNTERFACTUAL. Suppose the product is broken exactly as the P0 risk describes:")
print("the detail screen serves a cached snapshot and never reflects durable state.")
print("Then: both GETs still return 200; the two sqlite probes still run without error;")
print("no traceback or 'no such table' appears anywhere.")
print("=> every assertion above still PASSES. The scenario cannot fail in the way its")
print("   own purpose says it probes, yet the P0 risk is recorded as covered by it.")
