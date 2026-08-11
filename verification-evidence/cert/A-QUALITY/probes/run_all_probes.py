"""A-QUALITY reproduction script — every deterministic probe behind the findings.

Read-only. Starts no product, writes nothing outside this directory, consults no
model. Uses the candidate's own validator, gate and artifacts, unmodified.

    .venv/bin/python verification-evidence/cert/A-QUALITY/probes/run_all_probes.py
    .venv/bin/python verification-evidence/cert/A-QUALITY/probes/run_all_probes.py G1

Probe ids: G1 (fabricated grounding), E1 (effect-family oracle substance),
R1 (regression category), C1 (category-string false coverage), P0 (untestable
scenario covering a P0 risk), V1 (oracle counterfactuals against real sqlite).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import os
from pathlib import Path

DRIVER = Path(__file__).resolve().parents[4]
CERT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DRIVER))

from neyma_product_driver.context import ActiveUnit, load_founder_context  # noqa: E402
from neyma_product_driver.scenario_gate import uncovered_required_risks  # noqa: E402
from neyma_product_driver.scenario_plan import (  # noqa: E402
    GeneratedScenario, IdentifiedRisk, ScenarioProvenance,
)
from neyma_product_driver.scenario_suite import ScenarioOutcome, SuiteResult  # noqa: E402
from neyma_product_driver.scenario_validation import (  # noqa: E402
    ApprovedCommands, ValidationContext, grounding_tokens_from,
    principle_tokens_from, validate_plan,
)

FOUNDER = load_founder_context(DRIVER)
UNIT = ActiveUnit(
    unit_id="U-042", name="supervised carrier invoice approval", status="READY",
    objective="x", acceptance_contract="c.md",
    acceptance_criteria=[{"criterion": "an approved invoice is paid exactly once",
                          "weight": 3, "result": "PENDING"}],
    allowed_scope=[], prohibited_scope=[],
)
CTX = ValidationContext(
    approved_commands=ApprovedCommands(["sqlite3 data/app.sqlite3"]),
    grounding_tokens=grounding_tokens_from(UNIT),
    principle_tokens=principle_tokens_from(FOUNDER),
    declared_services={"callback"}, app_url="http://127.0.0.1:8001",
)
PROV = ScenarioProvenance(task_hash="d", stage="initial", wave=1, model="opus",
                          session_id="probe", kind="ephemeral")


def scenario(sid, **over):
    base = dict(
        id=sid, title="probe " + sid,
        purpose="a purpose long enough to pass the minimum length rule",
        rationale="a rationale so the risk-basis rule is satisfied",
        risk_category="happy_path", priority="P1",
        requirement_reference="U-042", product_principle_reference="outcome_verification",
        mode="backend", service_refs=["callback"], provenance=PROV,
        actions=[{"kind": "request",
                  "request": {"method": "GET", "path": "/x", "expect_status": 200}}],
    )
    base.update(over)
    return GeneratedScenario(**base)


def verdict(s):
    acc, ref = validate_plan([s], CTX)
    return ("ACCEPTED" if acc else "REFUSED"), [r for _, rs in ref for r in rs]


# ---------------------------------------------------------------- G1
def probe_G1():
    print("PROBE G1 — can a scenario ground itself in a requirement that does not exist?")
    print(f"grounding tokens the validator accepts: {sorted(CTX.grounding_tokens)}\n")
    cases = [
        ("REAL unit id", "U-042", "outcome_verification"),
        ("REAL acceptance criterion", "an approved invoice is paid exactly once", "outcome_verification"),
        ("FABRICATED AC id", "AC-PAYMENT-417", "outcome_verification"),
        ("FABRICATED AC id, absurd", "AC-ZZZZ-99999 which exists nowhere in any repository", "outcome_verification"),
        ("FABRICATED free prose", "the invoice must be beautiful", "outcome_verification"),
        ("FABRICATED principle", "U-042", "invented_principle_id"),
    ]
    for i, (label, ref, prin) in enumerate(cases):
        v, why = verdict(scenario(f"S{i}", requirement_reference=ref,
                                  product_principle_reference=prin))
        print(f"{label:28s} ref={ref[:46]!r:50s} -> {v}")
        for r in why:
            print(f"      reason: {r}")
    print("\nValidationContext.grounds_requirement (scenario_validation.py:338) returns True")
    print("for ANY string matching AC-<AREA>-<nnn>, on shape alone, never on existence.")
    print("Free prose is caught; a fabricated AC id is not. The refusal message that does")
    print("fire claims 'a scenario may not invent a product requirement' — it can.")


# ---------------------------------------------------------------- E1
def probe_E1():
    print("PROBE E1 — does the EFFECT_FAMILY rule require an oracle that can FAIL?\n")
    cases = [
        ("EMPTY oracle: command, no contains, no not_contains",
         [{"command": 'sqlite3 data/app.sqlite3 "SELECT count(*) FROM t"',
           "contains": [], "not_contains": []}]),
        ("ONLY not_contains 'Error' (passes unless the probe crashes)",
         [{"command": 'sqlite3 data/app.sqlite3 "SELECT count(*) FROM t"',
           "contains": [], "not_contains": ["Error"]}]),
        ("A REAL oracle: contains 'PAID_ONCE'",
         [{"command": "sqlite3 data/app.sqlite3 \"SELECT 'PAID_ONCE'\"",
           "contains": ["PAID_ONCE"], "not_contains": []}]),
        ("NO persisted_state_checks at all (control — must be refused)", []),
    ]
    for i, (label, psc) in enumerate(cases):
        v, why = verdict(scenario(f"S{i}", risk_category="restart_recovery",
                                  priority="P0", persisted_state_checks=psc))
        print(f"{label:58s} -> {v}")
        for r in why:
            print(f"      reason: {r[:120]}")
    print("\nThe rule (scenario_validation.py:615) tests inspects_persisted_state() — the")
    print("PRESENCE of a state check. A P0 effect-family scenario whose state check asserts")
    print("nothing at all is ACCEPTED, and the prior campaign's metric")
    print("'effect_family_with_state_oracle 9/9' counts exactly that as an oracle.")


# ---------------------------------------------------------------- R1
def probe_R1():
    print("PROBE R1 — can a `regression` scenario ever be accepted in an INITIAL wave?\n")
    initial = ScenarioProvenance(task_hash="d", stage="initial", wave=1, model="opus",
                                 session_id="s", kind="ephemeral", diff_files_consulted=[])
    diffw = ScenarioProvenance(task_hash="d", stage="diff_refinement", wave=2, model="opus",
                               session_id="s", kind="ephemeral",
                               diff_files_consulted=["src/freight_recon/governed_approval.py"])
    for label, prov in [("INITIAL wave (plan_initial: basis has no diff files)", initial),
                        ("DIFF wave    (refine_for_diff: basis has diff files)", diffw)]:
        v, why = verdict(scenario("S1", risk_category="regression", provenance=prov))
        print(f"{label}: -> {v}")
        for r in why:
            print(f"    reason: {r}")
    print("\nThe brief and the system prompt both advertise `regression` as an available")
    print("category. Neither says it is unsatisfiable in an initial wave. The rule keys off")
    print("provenance.diff_files_consulted, which the PLANNER stamps — no field the model")
    print("writes can satisfy it. The refusal blames the scenario for the stage it is in.")


# ---------------------------------------------------------------- C1
def _plan(name):
    return json.loads((CERT / "raw" / name).read_text())


def _gate_both_ways(plan, label):
    risks = [IdentifiedRisk.model_validate(r) for r in plan["risks"]]
    scen = plan["scenarios"]
    print(f"{label} risks:")
    for r in risks:
        print(f"  {r.severity.value:3s} {r.risk_category.value:26s} covered_by={r.covered_by}")
    print(f"{label} scenarios:")
    for s in scen:
        paths = [a["request"]["path"] for a in s["actions"] if a["kind"] == "request"]
        paths += [r["path"] for a in s["actions"] if a["kind"] == "parallel_requests"
                  for r in a["requests"]]
        print(f"  {s['id']:12s} {s['risk_category']:22s} drives={sorted(set(p.split('?')[0] for p in paths))}")
        print(f"               cites: {s['requirement_reference']}")
    result = SuiteResult(outcomes=[
        ScenarioOutcome(scenario_id=s["id"], scenario_name=s["title"], origin="generated",
                        outcome="PASSED", priority=s["priority"],
                        risk_category=s["risk_category"], required=True,
                        evidence_path=f"/tmp/{s['id']}", evidence_verified=True)
        for s in scen])
    n_none = len(uncovered_required_risks(risks, None))
    gaps = uncovered_required_risks(risks, result)
    print(f"\n  nothing executed            -> {n_none} gaps")
    print(f"  all scenarios PASS w/evidence -> {len(gaps)} gaps")
    for g in gaps:
        print(f"      {g.severity} {g.risk_category}")
    verified = [r.risk_category.value for r in risks
                if r.severity.blocks_acceptance
                and r.risk_category.value not in {g.risk_category for g in gaps}]
    print(f"  risks then treated as VERIFIED: {verified}")
    return verified


def probe_C1():
    print("PROBE C1 — risk coverage is decided by RISK-CATEGORY STRING EQUALITY only.")
    print("(scenario_gate.py:110 matches outcome.risk_category == risk.risk_category;")
    print(" nothing compares what the scenario does with what the risk describes.)\n")
    print("=" * 78)
    v = _gate_both_ways(_plan("plan-G-r1.json"), "TASK G (live, this session)")
    print()
    print("Every task-G scenario drives HTTP token/body handling. No document, no")
    print("extraction, no reconciliation appears anywhere in the plan, and all three cite")
    print("the same acceptance criterion about unparseable DOCUMENTS. Yet on a passing run")
    print(f"the gate marks {v} verified.")
    print()
    print("=" * 78)
    _gate_both_ways(_plan("plan-F-r1.json"), "TASK F (live, this session)")
    print()
    print("Task F is the control: the scenario categories do not intersect the risk")
    print("categories at all, so all four risks stay gaps even on a fully passing run.")
    print("The acceptance-tightening works — when the labels happen to disagree.")


# ---------------------------------------------------------------- P0
def probe_P0():
    print("PROBE P0 — a P0 risk in the candidate's OWN committed plan is 'covered' by a")
    print("scenario whose assertions cannot detect the failure the risk describes.\n")
    p = DRIVER / "verification-evidence/post-remediation/a-generation-quality/after-fix/plan-A2-diff.json"
    plan = json.loads(p.read_text())
    s = next(x for x in plan["scenarios"] if x["id"] == "S1")
    risk = next(r for r in plan["risks"] if "S1" in r["covered_by"])
    print(f"artifact         : {p.relative_to(DRIVER)}")
    print(f"risk             : {risk['severity']} {risk['risk_category']}, covered_by {risk['covered_by']}")
    print(f"risk description : {risk['description'][:200]}")
    print(f"scenario S1      : {s['priority']} — {s['title']}")
    print(f"purpose          : {s['purpose']}")
    print("\nEVERY assertion S1 can produce:")
    n = 0
    for a in s["actions"]:
        if a["kind"] == "request":
            r = a["request"]
            if r.get("expect_status") is not None:
                n += 1; print(f"  {n}. HTTP {r['method']} {r['path']} status == {r['expect_status']}")
            for c in r.get("expect_contains") or []:
                n += 1; print(f"  {n}. HTTP body contains {c!r}")
        if a["kind"] == "state_check":
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
    print("\nCOUNTERFACTUAL: the screen serves a permanently cached snapshot (exactly the")
    print("P0 risk). Both GETs still return 200; both sqlite probes still run cleanly; no")
    print("traceback appears. EVERY assertion still PASSES, and the P0 risk is booked covered.")


# ---------------------------------------------------------------- V1
def probe_V1():
    print("PROBE V1 — counterfactual EXECUTION of oracles the prior campaign ACCEPTED.")
    print("Uses the driver's own state-check semantics (scenarios.py:705-728): a PASS per")
    print("`contains` needle found in stdout+stderr and per `not_contains` needle absent.")
    print("The exit code is never asserted.\n")
    work = tempfile.mkdtemp()

    def mkdb(name, tables=True, n=0):
        db = os.path.join(work, name)
        c = sqlite3.connect(db)
        if tables:
            c.execute("CREATE TABLE audit_events (actor TEXT, payload_json TEXT, to_state TEXT, event_type TEXT)")
            for _ in range(n):
                c.execute("INSERT INTO audit_events VALUES (?,?,?,?)",
                          ("sup-probe-s1", "%OP-PROBE-S6%", "RELEASED", "release"))
        c.commit(); c.close()
        return db

    def run(db, sql, contains, not_contains):
        r = subprocess.run(["sqlite3", db, sql], capture_output=True, text=True)
        out = f"{r.stdout}\n{r.stderr}"
        ok = all(x in out for x in contains) and all(x not in out for x in not_contains)
        return out.strip()[:52], r.returncode, ok

    print("ORACLE — after-fix/plan-E.json S1 (accepted, 'every refusal is recorded'):")
    SQL = "SELECT count(*) FROM audit_events WHERE actor='sup-probe-s1'"
    for label, db in [("CORRECT: 1 audit row", mkdb("a1.db", n=1)),
                      ("BROKEN: audit never written (0 rows)", mkdb("a2.db", n=0)),
                      ("BROKEN: audit table absent entirely", mkdb("a3.db", tables=False)),
                      ("CORRECT but busy: 10 audit rows", mkdb("a4.db", n=10))]:
        out, rc, ok = run(db, SQL, [], ["0"])
        print(f"  {label:38s} rc={rc} out={out!r:48s} -> {'PASS' if ok else 'FAIL'}")
    print("  => passes when the table is missing entirely; FAILS on a correct product with")
    print("     10 rows, because '10' contains the substring '0'.\n")

    print("ORACLE — after-fix/plan-E.json S6 (accepted, concurrency 'single releaser wins'):")
    SQL6 = ("SELECT count(*) FROM audit_events WHERE payload_json LIKE '%OP-PROBE-S6%' "
            "AND (to_state='RELEASED' OR event_type LIKE '%releas%')")
    for label, db in [("CORRECT: exactly 1 release", mkdb("b1.db", n=1)),
                      ("BROKEN: double release (2)", mkdb("b2.db", n=2)),
                      ("BROKEN: 11 releases", mkdb("b3.db", n=11)),
                      ("BROKEN: table absent entirely", mkdb("b5.db", tables=False))]:
        out, rc, ok = run(db, SQL6, [], ["2", "3"])
        print(f"  {label:38s} rc={rc} out={out!r:48s} -> {'PASS' if ok else 'FAIL'}")
    print("  => catches exactly 2 and 3 releases; 11 concurrent releases pass, and so does")
    print("     a completely absent audit table.")




# ---------------------------------------------------------------- U1
def probe_U1():
    print("PROBE U1 — where does the generator's own statement that the acceptance criteria")
    print("are UNVERIFIABLE actually go?\n")
    import subprocess as sp
    for tag in ("A-r1", "F-r1", "G-r1"):
        plan = _plan(f"plan-{tag}.json")
        qs = plan.get("unresolved_questions") or []
        print(f"{tag}: {len(qs)} unresolved_question(s). First:")
        for q in qs[:1]:
            print(f"    {q[:300]}")
    print()
    src = DRIVER / "neyma_product_driver"
    hits = sp.run(["grep", "-rn", "unresolved_questions", str(src)],
                  capture_output=True, text=True).stdout.splitlines()
    consumers = [h for h in hits if "scenario_generator.py" not in h]
    print("Every reference to unresolved_questions outside the generator:")
    for h in consumers:
        print("   ", h.replace(str(DRIVER) + "/", ""))
    print()
    print("None of these is evaluator_prompt(), evaluate_gate(), SuiteResult or cli.")
    print("The field is written into GeneratedScenarioPlan and rendered to the terminal.")
    print("This is the same shape as residual C, which POST-DYNAMIC-REMEDIATION §4C")
    print("describes closing for uncovered_risks: 'recompute_coverage() computed")
    print("uncovered_risks and only the terminal ever saw them'. It is still open here,")
    print("and what it hides is the generator saying two weight-3 acceptance criteria")
    print("could not be verified this wave.")


PROBES = {"G1": probe_G1, "E1": probe_E1, "R1": probe_R1, "C1": probe_C1,
          "P0": probe_P0, "V1": probe_V1, "U1": probe_U1}

if __name__ == "__main__":
    wanted = sys.argv[1:] or list(PROBES)
    for i, name in enumerate(wanted):
        if i:
            print("\n" + "=" * 78 + "\n")
        PROBES[name]()
