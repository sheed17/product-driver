"""Reproduce every independently verified blocker, before and after remediation.

Run from the repository root:

    .venv/bin/python verification-evidence/remediation/reproduce_blockers.py

Each probe prints OPEN (the defect reproduces) or CLOSED (it does not). Nothing
here weakens a guard: the probes only observe what the production code decides.
Destructive payloads are limited to `echo`, and no external host is contacted.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

RESULTS: list[tuple[str, str, str]] = []


def record(probe: str, open_: bool, detail: str) -> None:
    RESULTS.append((probe, "OPEN" if open_ else "CLOSED", detail))
    print(f"[{'OPEN  ' if open_ else 'CLOSED'}] {probe}\n         {detail}\n")


# --------------------------------------------------------------------------
# B1 — the real reasoner cannot run through the async control loop
# --------------------------------------------------------------------------
def probe_b1() -> None:
    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.scenario_generator import LLMScenarioReasoner
    from neyma_product_driver.scenario_planner import ScenarioPlanner
    from scenario_fixtures import (  # type: ignore
        FakeFounder, FakeUnit, base_scenario, raw_payload, raw_scenario,
    )

    class Stub(LLMScenarioReasoner):
        """Real class, real propose(); only the model call itself is stubbed."""
        async def _session(self, prompt):  # type: ignore[override]
            return raw_payload(raw_scenario("gen-a"))

    tmp = Path(tempfile.mkdtemp())
    planner = ScenarioPlanner(
        repo=tmp, config=ScenarioGenerationConfig(enabled=True), reasoner=Stub(tmp),
        base_scenario=base_scenario(), permanent_scenarios=[base_scenario()],
        founder=FakeFounder(), emit=lambda _m: None,
    )

    async def as_the_driver_does():
        return planner.plan_initial(task="add supervised invoice approval", unit=FakeUnit())

    plan = asyncio.run(as_the_driver_does())
    err = plan.waves[0].reasoner_error if plan.waves else "(no wave)"
    record(
        "B1  real reasoner through the async control loop",
        not plan.scenarios,
        f"scenarios={[s.id for s in plan.scenarios]} reasoner_error={err!r}",
    )


# --------------------------------------------------------------------------
# B2.1 — control-character / shell composition bypass
# --------------------------------------------------------------------------
def probe_b21() -> None:
    from neyma_product_driver.scenario_validation import ApprovedCommands

    approved = ApprovedCommands(["echo payments"])
    vectors = {
        "newline": "echo payments\necho INJECTED",
        "carriage return": "echo payments\recho INJECTED",
        "vertical tab": "echo payments\x0becho INJECTED",
        "form feed": "echo payments\x0cecho INJECTED",
        "NUL": "echo payments\x00echo INJECTED",
        "chained &&": "echo payments && echo INJECTED",
        "substitution": "echo payments $(echo INJECTED)",
        "backtick": "echo payments `echo INJECTED`",
        "subshell": "echo payments; (echo INJECTED)",
        "pipe": "echo payments | tee /tmp/x",
        "redirect": "echo payments > /tmp/x",
        "quoted-then-escape": 'echo payments "" && echo INJECTED',
    }
    admitted = {name: cmd for name, cmd in vectors.items() if approved.approves(cmd)[0]}
    record(
        "B2.1 shell/control-character composition",
        bool(admitted),
        f"admitted {len(admitted)}/{len(vectors)}: {sorted(admitted)}" if admitted
        else f"all {len(vectors)} composition vectors refused",
    )


# --------------------------------------------------------------------------
# B2.2 — legitimate quoted probes must not be refused
# --------------------------------------------------------------------------
def probe_b22() -> None:
    from neyma_product_driver.scenario_validation import ApprovedCommands

    approved = ApprovedCommands(["sqlite3 data/workflow.sqlite3", "python3 -c"])
    legitimate = {
        "sql duplicate detection":
            'sqlite3 data/workflow.sqlite3 "SELECT key FROM grants GROUP BY key HAVING count(*) > 1"',
        "sql concat marker":
            "sqlite3 data/workflow.sqlite3 \"SELECT 'DUP:'||key FROM grants\"",
        "sql range compare":
            'sqlite3 data/workflow.sqlite3 "SELECT * FROM runs WHERE created < updated"',
        "json argument":
            'python3 -c "import json; print(json.dumps({\'a\': 1}))"',
        "regex argument":
            'python3 -c "import re; print(re.match(r\'^a|b$\', \'a\'))"',
    }
    refused = {n: approved.approves(c)[1] for n, c in legitimate.items() if not approved.approves(c)[0]}
    record(
        "B2.2 legitimate quoted state probes",
        bool(refused),
        f"wrongly refused {len(refused)}/{len(legitimate)}: {sorted(refused)}" if refused
        else f"all {len(legitimate)} quoted probes accepted",
    )


# --------------------------------------------------------------------------
# B2.3 — absolute / non-loopback URL through request.path
# --------------------------------------------------------------------------
def probe_b23() -> None:
    from neyma_product_driver.scenario_generator import parse_scenarios
    from neyma_product_driver.scenario_plan import ScenarioProvenance
    from neyma_product_driver.scenario_validation import (
        ApprovedCommands, ValidationContext, validate_scenario,
    )
    from scenario_fixtures import APPROVED_CLEANUP, APPROVED_STATE, raw_scenario  # type: ignore

    targets = {
        "absolute https": "https://api.stripe.com/v1/charges",
        "absolute http": "http://evil.example/exfil",
        "scheme-relative": "//evil.example/exfil",
        "userinfo trick": "http://127.0.0.1@evil.example/",
        "decimal IP": "http://2130706433/",
        "alternate scheme": "file:///etc/passwd",
        "non-loopback IPv6": "http://[2001:db8::1]/",
    }
    leaked = {}
    for name, target in targets.items():
        raw = raw_scenario(
            f"gen-{abs(hash(name))%9999}",
            actions=[{"kind": "request", "name": "probe",
                      "request": {"method": "POST", "path": target, "expect_status": 200}}],
            state_checks=[{"name": "s", "command": APPROVED_STATE, "contains": ["payments=1"]}],
        )
        parsed, _ = parse_scenarios({"scenarios": [raw]}, provenance=ScenarioProvenance())
        if not parsed:
            continue
        ctx = ValidationContext(
            approved_commands=ApprovedCommands([APPROVED_STATE, APPROVED_CLEANUP]),
            app_url="http://127.0.0.1:8931", declared_services={"api"},
            grounding_tokens={"U-042", "invoice", "approved", "paid", "once"},
            principle_tokens={"effect-truth", "ownership"},
        )
        reasons = validate_scenario(parsed[0], ctx)
        if not any(k in r.lower() for r in reasons for k in ("external", "loopback", "host", "scheme")):
            leaked[name] = parsed[0].actions[0].request.target()
    record(
        "B2.3 absolute/non-loopback URL via request.path",
        bool(leaked),
        f"ungated {len(leaked)}/{len(targets)}: {sorted(leaked)}" if leaked
        else f"all {len(targets)} off-target URLs refused",
    )


# --------------------------------------------------------------------------
# B4 — a required scenario that never executed must not permit ACCEPT
# --------------------------------------------------------------------------
def probe_b4() -> None:
    from neyma_product_driver.cli import _apply_suite_precedence
    from neyma_product_driver.models import Decision, EvaluatorDecision
    from neyma_product_driver.scenario_plan import Priority
    from neyma_product_driver.scenario_suite import (
        Origin, Outcome, ScenarioOutcome, SuiteResult,
    )

    outcomes = [
        ScenarioOutcome(scenario_id="backend_generic", scenario_name="backend_generic",
                        origin=Origin.PERMANENT, outcome=Outcome.SKIPPED, priority=Priority.P0,
                        required=True, skip_reason="execution budget exhausted"),
        ScenarioOutcome(scenario_id="gen-approve-twice", scenario_name="generated:gen-approve-twice",
                        origin=Origin.GENERATED, outcome=Outcome.SKIPPED, priority=Priority.P0,
                        required=True, skip_reason="execution budget exhausted"),
    ]
    suite = SuiteResult(outcomes=outcomes, full_run=True, selection_reason="all selected")
    final = _apply_suite_precedence(
        suite, EvaluatorDecision(decision=Decision.ACCEPT, summary="looks good"),
        "backend_generic", lambda _m: None,
    )
    record(
        "B4  required scenario skipped, zero executed",
        final.decision is Decision.ACCEPT,
        f"executed=0 skipped={suite.skipped} full_run={suite.full_run} decision={final.decision.value}",
    )


# --------------------------------------------------------------------------
# B5 — provenance must be enforced, not decorative
# --------------------------------------------------------------------------
def probe_b5() -> None:
    from neyma_product_driver.scenario_generator import parse_scenarios
    from neyma_product_driver.scenario_plan import ScenarioProvenance
    from neyma_product_driver.scenario_validation import (
        ApprovedCommands, ValidationContext, validate_scenario,
    )
    from scenario_fixtures import APPROVED_CLEANUP, APPROVED_STATE, raw_scenario  # type: ignore

    # An empty provenance stamp: no run identity, no wave, no basis, no source.
    parsed, _ = parse_scenarios(
        {"scenarios": [raw_scenario("gen-no-prov")]}, provenance=ScenarioProvenance()
    )
    ctx = ValidationContext(
        approved_commands=ApprovedCommands([APPROVED_STATE, APPROVED_CLEANUP]),
        app_url="http://127.0.0.1:8931", declared_services={"api"},
        grounding_tokens={"U-042", "invoice", "approved", "paid", "once"},
        principle_tokens={"effect-truth", "ownership"},
    )
    reasons = validate_scenario(parsed[0], ctx) if parsed else ["(unparseable)"]
    prov_reasons = [r for r in reasons if "provenance" in r.lower()]
    record(
        "B5  provenance enforcement",
        not prov_reasons,
        f"empty provenance produced reasons={reasons or '(none)'}",
    )


# --------------------------------------------------------------------------
# B6 — resume must not regenerate from wave zero
# --------------------------------------------------------------------------
def probe_b6() -> None:
    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.evidence import EvidenceStore
    from neyma_product_driver.scenario_planner import ScenarioPlanner
    from scenario_fixtures import (  # type: ignore
        FakeFounder, FakeUnit, ScriptedReasoner, base_scenario, raw_payload, raw_scenario,
    )

    tmp = Path(tempfile.mkdtemp())
    store = EvidenceStore(tmp / "runs", "run-resume-probe")

    def make(payloads):
        return ScenarioPlanner(
            repo=tmp, config=ScenarioGenerationConfig(enabled=True, max_waves=2),
            reasoner=ScriptedReasoner(payloads), store=store, base_scenario=base_scenario(),
            permanent_scenarios=[base_scenario()], founder=FakeFounder(), emit=lambda _m: None,
        )

    first = make([raw_payload(raw_scenario("gen-a"))])
    first.plan_initial(task="approval endpoint", unit=FakeUnit())
    first.persist()
    before = [s.id for s in first.plan.scenarios]

    resumed = _resume_planner(make, store)
    after = [s.id for s in resumed.plan.scenarios]
    # Guard against a vacuous pass: if the first planner generated nothing there
    # is nothing for resume to preserve, and the probe proves nothing.
    invalid = not before
    record(
        "B6  resume preserves generated state",
        invalid or after != before or resumed.waves_used != first.waves_used,
        (
            f"PROBE INVALID — nothing was generated to preserve (before={before})"
            if invalid
            else f"before={before} after_resume={after} "
            f"waves_used before={first.waves_used} after={resumed.waves_used}"
        ),
    )


def _resume_planner(make, store):
    """Use the product's own restore path if one exists; otherwise a fresh planner."""
    planner = make([])
    restore = getattr(planner, "restore_from_store", None)
    if callable(restore):
        restore()
    return planner


# --------------------------------------------------------------------------
# B7 — per-case evidence must exist where it is cited
# --------------------------------------------------------------------------
def probe_b7() -> None:
    from neyma_product_driver.config import ScenarioRunConfig
    from neyma_product_driver.scenario_suite import SuiteExecutor, build_suite
    from neyma_product_driver.scenarios import Scenario, ScenarioExecutor

    tmp = Path(tempfile.mkdtemp())
    scenario = Scenario(
        name="evidence_probe", mode="backend",
        commands=[{"name": "smoke", "run": "echo hello-evidence"}],
    )
    suite = build_suite(permanent=[("evidence_probe", scenario)])
    executor = SuiteExecutor(
        make_executor=lambda d: ScenarioExecutor(tmp, ScenarioRunConfig(), d),
        artifact_root=tmp / "iteration-01", emit=lambda _m: None,
    )
    result = asyncio.run(executor.run(suite))
    outcome = result.outcomes[0]
    cited = Path(outcome.evidence_path) if outcome.evidence_path else None
    files = sorted(p.name for p in cited.rglob("*") if p.is_file()) if cited and cited.exists() else []
    substantive = [f for f in files if (cited / f).stat().st_size > 0] if cited else []
    record(
        "B7  per-case evidence exists where cited",
        not substantive,
        f"outcome={outcome.outcome.value} cited={cited} files={files} non_empty={substantive}",
    )


# --------------------------------------------------------------------------
# B8 — per-wave scenario budget must actually bound a wave
# --------------------------------------------------------------------------
def probe_b8() -> None:
    from neyma_product_driver.config import ScenarioGenerationConfig
    from neyma_product_driver.scenario_planner import ScenarioPlanner
    from scenario_fixtures import (  # type: ignore
        APPROVED_STATE, FakeFounder, FakeUnit, ScriptedReasoner, base_scenario,
        raw_payload, raw_scenario,
    )

    many = [
        raw_scenario(
            f"gen-{i:02d}",
            risk_category=["idempotency", "concurrency", "restart_recovery",
                           "retry_safety", "persistence_failure"][i % 5],
            actions=[{"kind": "request", "name": f"r{i}",
                      "request": {"method": "POST", "path": f"/approve/{i}", "expect_status": 200}}],
            state_checks=[{"name": "s", "command": APPROVED_STATE, "contains": [f"payments={i}"]}],
            expected_observations=[f"payments={i}"], forbidden_observations=[f"payments={i+1}"],
        )
        for i in range(10)
    ]
    tmp = Path(tempfile.mkdtemp())
    planner = ScenarioPlanner(
        repo=tmp,
        config=ScenarioGenerationConfig(
            enabled=True, max_initial_scenarios=2, max_total_scenarios=30,
            max_scenarios_per_risk_category=6,
        ),
        reasoner=ScriptedReasoner([raw_payload(*many)]), base_scenario=base_scenario(),
        permanent_scenarios=[base_scenario()], founder=FakeFounder(), emit=lambda _m: None,
    )
    plan = planner.plan_initial(task="t", unit=FakeUnit())
    # The bound must actually bind: more than 2 means unenforced, but 0 means the
    # wave produced nothing at all and the bound was never exercised.
    accepted = len(plan.scenarios)
    record(
        "B8  per-wave scenario budget",
        accepted != 2,
        f"max_initial_scenarios=2, model returned 10, accepted={accepted}"
        + ("" if accepted == 2 else "  (expected exactly 2)"),
    )


def main() -> int:
    for probe in (probe_b1, probe_b21, probe_b22, probe_b23,
                  probe_b4, probe_b5, probe_b6, probe_b7, probe_b8):
        try:
            probe()
        except Exception as exc:  # a probe that cannot run is reported, never hidden
            record(probe.__name__, True, f"probe raised {type(exc).__name__}: {exc}")

    print("=" * 74)
    open_ = [r for r in RESULTS if r[1] == "OPEN"]
    for name, status, _ in RESULTS:
        print(f"  {status:<7} {name}")
    print(f"\n{len(open_)} of {len(RESULTS)} blockers OPEN")
    out = Path(__file__).parent / "blocker-status.json"
    out.write_text(json.dumps(
        [{"probe": n, "status": s, "detail": d} for n, s, d in RESULTS], indent=2
    ))
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
