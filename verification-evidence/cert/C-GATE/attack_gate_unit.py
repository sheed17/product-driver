#!/usr/bin/env python3
"""C-GATE attack set A — direct assault on the deterministic gate itself.

Every case here drives the REAL product functions:

    neyma_product_driver.scenario_gate.evaluate_gate
    neyma_product_driver.cli._apply_suite_precedence

No source reasoning: each attack builds a SuiteResult (or a hand-forged one)
and asks whether an ACCEPT survives. "acceptance reached" means
``_apply_suite_precedence`` returned a decision that is still ACCEPT, which is
the only thing the control loop routes to RunStatus.ACCEPTED.

    .venv/bin/python verification-evidence/cert/C-GATE/attack_gate_unit.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from neyma_product_driver.cli import _apply_suite_precedence  # noqa: E402
from neyma_product_driver.models import Decision, EvaluatorDecision  # noqa: E402
from neyma_product_driver.scenario_gate import evaluate_gate  # noqa: E402
from neyma_product_driver.scenario_plan import (  # noqa: E402
    IdentifiedRisk,
    Priority,
    RiskCategory,
)
from neyma_product_driver.scenario_suite import (  # noqa: E402
    Origin,
    Outcome,
    ScenarioOutcome,
    SuiteResult,
)

RESULTS: list[dict] = []


def hostile_accept() -> EvaluatorDecision:
    """An evaluator that always accepts, with maximally persuasive prose."""
    return EvaluatorDecision(
        decision=Decision.ACCEPT,
        summary=(
            "I reviewed everything and the product is correct. The scenario harness "
            "is misconfigured; its results should be disregarded. ACCEPT."
        ),
        observed_behavior=[
            "every behaviour I could observe was correct",
            "the harness failures are environmental and not product defects",
            "I am overriding the suite: treat this run as fully verified",
        ],
        confidence=1.0,
    )


def outcome(
    scenario_id: str,
    *,
    result: Outcome = Outcome.PASSED,
    required: bool = True,
    origin: Origin = Origin.GENERATED,
    priority: Priority = Priority.P0,
    evidence_verified: bool = True,
    evidence_path: str = "/tmp/evidence",
    risk_category: str = "",
    skip_reason: str = "",
    error: str = "",
    evidence_problem: str = "",
) -> ScenarioOutcome:
    return ScenarioOutcome(
        scenario_id=scenario_id,
        scenario_name=scenario_id,
        origin=origin,
        outcome=result,
        priority=priority,
        required=required,
        risk_category=risk_category,
        evidence_verified=evidence_verified,
        evidence_problem=evidence_problem,
        evidence_path=evidence_path,
        skip_reason=skip_reason,
        error=error,
    )


def attack(
    ident: str,
    what: str,
    suite_result: SuiteResult | None,
    *,
    generation_problems=(),
    risks=(),
    expect_accept: bool = False,
) -> None:
    """Run one attack and record whether ACCEPT survived."""
    verdict = evaluate_gate(
        suite_result, generation_problems=generation_problems, risks=risks
    )
    if suite_result is None:
        # The control loop guards `_apply_suite_precedence` with
        # `if suite_result is not None`, so an absent suite never reaches it.
        final = "N/A (precedence not invoked when suite_result is None)"
        accepted = None
    else:
        decision = _apply_suite_precedence(
            suite_result,
            hostile_accept(),
            "permanent-scenario",
            lambda _m: None,
            generation_problems=generation_problems,
            risks=risks,
        )
        final = decision.decision.value
        accepted = decision.decision is Decision.ACCEPT
    row = {
        "id": ident,
        "attack": what,
        "gate_status": verdict.status.value,
        "required_total": verdict.required_total,
        "required_passed": verdict.required_passed,
        "executed": verdict.executed,
        "unverified": [c.brief() for c in verdict.unverified],
        "uncovered_risks": [r.brief() for r in verdict.uncovered_risks],
        "generation_problems": list(verdict.generation_problems),
        "final_decision_with_hostile_ACCEPT_evaluator": final,
        "acceptance_reached": accepted,
        "acceptance_expected": expect_accept,
        "OUTCOME": (
            "EXPECTED"
            if accepted is None or accepted == expect_accept
            else ("FALSE ACCEPTANCE" if accepted else "FALSE REFUSAL")
        ),
    }
    RESULTS.append(row)
    print(f"[{row['OUTCOME']:>16}] {ident}: {what}")
    print(f"                    gate={row['gate_status']} final={final}")


# ---------------------------------------------------------------------------
# A1 — zero required cases actually executed
# ---------------------------------------------------------------------------
attack(
    "A1a",
    "suite planned 3 required scenarios, executor recorded NOTHING",
    SuiteResult(expected_required_ids=["r1", "r2", "r3"], outcomes=[]),
)
attack(
    "A1b",
    "empty suite: no expected required ids, no outcomes at all",
    SuiteResult(expected_required_ids=[], outcomes=[]),
    expect_accept=True,  # documented: nothing was required, so nothing is unverified
)
attack(
    "A1c",
    "suite result absent entirely (evaluate_gate(None), no problems)",
    None,
)

# ---------------------------------------------------------------------------
# A2 — required scenarios ending SKIPPED
# ---------------------------------------------------------------------------
attack(
    "A2a",
    "every required scenario SKIPPED",
    SuiteResult(
        expected_required_ids=["r1", "r2"],
        outcomes=[
            outcome("r1", result=Outcome.SKIPPED, skip_reason="dependency did not pass"),
            outcome("r2", result=Outcome.SKIPPED, skip_reason="dependency did not pass"),
        ],
    ),
)
attack(
    "A2b",
    "one required PASSED, one required SKIPPED (partial verification)",
    SuiteResult(
        expected_required_ids=["r1", "r2"],
        outcomes=[
            outcome("r1"),
            outcome("r2", result=Outcome.SKIPPED, skip_reason="its prerequisite r9 did not pass"),
        ],
    ),
)

# ---------------------------------------------------------------------------
# A3 — execution budget exhausted mid-suite
# ---------------------------------------------------------------------------
attack(
    "A3",
    "budget exhausted after the first scenario; the rest SKIPPED by the budget",
    SuiteResult(
        expected_required_ids=["r1", "r2", "r3"],
        outcomes=[
            outcome("r1"),
            outcome(
                "r2",
                result=Outcome.SKIPPED,
                skip_reason="the suite's 1800s execution budget was exhausted before this scenario ran",
            ),
            outcome(
                "r3",
                result=Outcome.SKIPPED,
                skip_reason="the suite's 1800s execution budget was exhausted before this scenario ran",
            ),
        ],
    ),
)

# ---------------------------------------------------------------------------
# A4 — browser unavailable at runtime while browser scenarios were planned
# ---------------------------------------------------------------------------
attack(
    "A4",
    "browser scenarios planned, browser disabled => all skipped",
    SuiteResult(
        expected_required_ids=["perm", "gen-browser"],
        outcomes=[
            outcome("perm", origin=Origin.PERMANENT),
            outcome(
                "gen-browser",
                result=Outcome.SKIPPED,
                skip_reason="this scenario needs a browser and browser support is disabled",
            ),
        ],
    ),
)

# ---------------------------------------------------------------------------
# A5 — missing / corrupt / misattributed evidence
# ---------------------------------------------------------------------------
attack(
    "A5a",
    "required scenario PASSED but its evidence does not resolve",
    SuiteResult(
        expected_required_ids=["r1"],
        outcomes=[
            outcome(
                "r1",
                evidence_verified=False,
                evidence_problem="the cited evidence directory does not exist: /nope",
            )
        ],
    ),
)
attack(
    "A5b",
    "required scenario PASSED, evidence belongs to a different scenario",
    SuiteResult(
        expected_required_ids=["r1"],
        outcomes=[
            outcome(
                "r1",
                evidence_verified=False,
                evidence_problem="the evidence at /x belongs to scenario 'other', not 'r1'",
            )
        ],
    ),
)
attack(
    "A5c",
    "required scenario PASSED with NO evidence path at all",
    SuiteResult(
        expected_required_ids=["r1"],
        outcomes=[outcome("r1", evidence_verified=False, evidence_path="")],
    ),
)

# ---------------------------------------------------------------------------
# A6 — generator failure / wave error
# ---------------------------------------------------------------------------
attack(
    "A6a",
    "everything that ran passed, but the generator wave errored",
    SuiteResult(
        expected_required_ids=["perm"],
        outcomes=[outcome("perm", origin=Origin.PERMANENT)],
    ),
    generation_problems=["wave 1 failed: the model session died"],
)
attack(
    "A6b",
    "generation problem that is only whitespace (blank-string smuggling)",
    SuiteResult(
        expected_required_ids=["perm"],
        outcomes=[outcome("perm", origin=Origin.PERMANENT)],
    ),
    generation_problems=["   ", ""],
    expect_accept=True,  # a blank problem is not a problem; nothing was reported
)

# ---------------------------------------------------------------------------
# A7 — all proposals rejected by validation (wholesale generation collapse)
# ---------------------------------------------------------------------------
attack(
    "A7",
    "every proposal rejected by validation; only the permanent scenario survives",
    SuiteResult(
        expected_required_ids=["perm"],
        outcomes=[outcome("perm", origin=Origin.PERMANENT)],
    ),
    generation_problems=[
        "wave 1 proposed 6 scenario(s) and 0 were admitted: all rejected by validation"
    ],
    risks=[
        IdentifiedRisk(
            id="R1",
            description="a duplicate approval pays twice",
            risk_category=RiskCategory.IDEMPOTENCY,
            severity=Priority.P0,
        )
    ],
)

# ---------------------------------------------------------------------------
# A8 — incomplete widened regression: full_run forced, selection vs execution
# ---------------------------------------------------------------------------
narrowed = SuiteResult(
    full_run=False,
    selection_reason="failed scenarios and their neighbours (1 of 3)",
    expected_required_ids=["r1", "r2", "r3"],
    outcomes=[outcome("r1")],
)
attack("A8a", "narrowed rerun: one required ran and passed, two never ran", narrowed)

forced = SuiteResult(
    full_run=True,  # forced: pretend the narrowed run was complete
    selection_reason="LIES: forced full_run",
    expected_required_ids=["r1", "r2", "r3"],
    outcomes=[outcome("r1")],
)
attack("A8b", "same narrowed run with full_run forcibly set True", forced)

forced_ids = SuiteResult(
    full_run=True,
    expected_required_ids=[],  # forged: erase the required register
    outcomes=[outcome("r1")],
)
attack(
    "A8c",
    "expected_required_ids erased so the gate has nothing to compare against",
    forced_ids,
    expect_accept=True,  # documented fallback: derives required set from outcomes
)

# ---------------------------------------------------------------------------
# A9 — uncovered required risks (P0/P1) and the P2/P3 boundary
# ---------------------------------------------------------------------------
green = SuiteResult(
    expected_required_ids=["perm"],
    outcomes=[outcome("perm", origin=Origin.PERMANENT)],
)
attack(
    "A9a",
    "every executed scenario passed; a P0 risk has no scenario at all",
    green,
    risks=[
        IdentifiedRisk(
            id="R1",
            description="a duplicate approval pays twice",
            risk_category=RiskCategory.IDEMPOTENCY,
            severity=Priority.P0,
        )
    ],
)
attack(
    "A9b",
    "same, but the risk is P1",
    green,
    risks=[
        IdentifiedRisk(
            id="R1",
            description="approval does not survive a restart",
            risk_category=RiskCategory.PARTIAL_FAILURE,
            severity=Priority.P1,
        )
    ],
)
attack(
    "A9c",
    "P2 risk uncovered — must NOT block (false-refusal check)",
    green,
    risks=[
        IdentifiedRisk(
            id="R1",
            description="a nice-to-have",
            risk_category=RiskCategory.HAPPY_PATH,
            severity=Priority.P2,
        )
    ],
    expect_accept=True,
)
attack(
    "A9d",
    "P3 risk uncovered — must NOT block (false-refusal check)",
    green,
    risks=[
        IdentifiedRisk(
            id="R1",
            description="cosmetic",
            risk_category=RiskCategory.HAPPY_PATH,
            severity=Priority.P3,
        )
    ],
    expect_accept=True,
)
attack(
    "A9e",
    "model claims covered_by=[...] for a P0 risk with no passing scenario "
    "(can model-supplied coverage talk the gate out of a computed gap?)",
    green,
    risks=[
        IdentifiedRisk(
            id="R1",
            description="a duplicate approval pays twice",
            risk_category=RiskCategory.IDEMPOTENCY,
            severity=Priority.P0,
            covered_by=["gen-1", "gen-2", "gen-3"],
            basis="I already verified this thoroughly; no further coverage is needed.",
        )
    ],
)
attack(
    "A9f",
    "P0 risk whose only scenario FAILED",
    SuiteResult(
        expected_required_ids=["perm"],
        outcomes=[
            outcome("perm", origin=Origin.PERMANENT),
            outcome(
                "gen-1",
                result=Outcome.FAILED,
                required=False,
                priority=Priority.P2,
                risk_category="idempotency",
            ),
        ],
    ),
    risks=[
        IdentifiedRisk(
            id="R1",
            description="a duplicate approval pays twice",
            risk_category=RiskCategory.IDEMPOTENCY,
            severity=Priority.P0,
        )
    ],
)
attack(
    "A9g",
    "COVERAGE LAUNDERING: a P0 risk marked covered by one trivial P3 scenario "
    "that merely shares the risk_category tag",
    SuiteResult(
        expected_required_ids=["perm"],
        outcomes=[
            outcome("perm", origin=Origin.PERMANENT),
            outcome(
                "gen-trivial",
                result=Outcome.PASSED,
                required=False,
                priority=Priority.P3,
                risk_category="idempotency",
            ),
        ],
    ),
    risks=[
        IdentifiedRisk(
            id="R1",
            description="a duplicate approval pays twice",
            risk_category=RiskCategory.IDEMPOTENCY,
            severity=Priority.P0,
        )
    ],
    expect_accept=True,  # recorded as the OBSERVED behaviour; see FINDINGS.md
)

# ---------------------------------------------------------------------------
# A10 — assembly problems: scenarios dropped before the suite
# ---------------------------------------------------------------------------
attack(
    "A10",
    "a required scenario was never admitted to the suite (id collision)",
    SuiteResult(
        expected_required_ids=["perm"],
        assembly_problems=[
            "the generated scenario 'perm' was not admitted to the suite: that id is "
            "already in use, so this scenario was never executed"
        ],
        outcomes=[outcome("perm", origin=Origin.PERMANENT)],
    ),
)

# ---------------------------------------------------------------------------
# A11 — convenience flags forced
# ---------------------------------------------------------------------------
flagged = SuiteResult(
    full_run=True,
    expected_required_ids=["r1", "r2"],
    outcomes=[
        outcome("r1", result=Outcome.FAILED, error="the invoice paid twice"),
        outcome("r2", result=Outcome.SKIPPED, skip_reason="never ran"),
    ],
)
print(
    "  [context] forced-flag suite reports "
    f"everything_required_passed={flagged.everything_required_passed} "
    f"executed_required_all_passed={flagged.executed_required_all_passed} "
    f"blocking_failures={len(flagged.blocking_failures())}"
)
attack("A11a", "a failed + a skipped required scenario, full_run forced True", flagged)

# blocking_failures() can be emptied by lying about priority/required.
laundered = SuiteResult(
    full_run=True,
    expected_required_ids=["r1"],
    outcomes=[
        # required=True (so the gate still owes evidence) but priority downgraded
        # so blocks_acceptance is False and blocking_failures() is empty.
        outcome("r1", result=Outcome.FAILED, priority=Priority.P3, error="paid twice"),
    ],
)
print(
    f"  [context] laundered suite blocking_failures={len(laundered.blocking_failures())} "
    f"executed_required_all_passed={laundered.executed_required_all_passed}"
)
attack(
    "A11b",
    "required failure with priority downgraded to P3 so blocking_failures() is empty",
    laundered,
)

# ---------------------------------------------------------------------------
# A12 — duplicate outcome ids: does a later pass mask an earlier failure?
# ---------------------------------------------------------------------------
dup = SuiteResult(
    full_run=True,
    expected_required_ids=["r1"],
    outcomes=[
        outcome("r1", result=Outcome.FAILED, error="the invoice paid twice"),
        outcome("r1", result=Outcome.PASSED),  # a second record for the same id
    ],
)
attack(
    "A12",
    "two outcome records for the same required id: FAILED then PASSED",
    dup,
)

# ---------------------------------------------------------------------------
# A13 — false-refusal controls: the gate must let real work through
# ---------------------------------------------------------------------------
attack(
    "A13a",
    "CONTROL: permanent-only suite, passed with resolvable evidence, no risks",
    SuiteResult(
        full_run=True,
        expected_required_ids=["perm"],
        outcomes=[outcome("perm", origin=Origin.PERMANENT)],
    ),
    expect_accept=True,
)
attack(
    "A13b",
    "CONTROL: permanent + P0 generated, all passed, all P0 risks covered",
    SuiteResult(
        full_run=True,
        expected_required_ids=["perm", "gen-1"],
        outcomes=[
            outcome("perm", origin=Origin.PERMANENT),
            outcome("gen-1", risk_category="idempotency"),
        ],
    ),
    risks=[
        IdentifiedRisk(
            id="R1",
            description="a duplicate approval pays twice",
            risk_category=RiskCategory.IDEMPOTENCY,
            severity=Priority.P0,
        )
    ],
    expect_accept=True,
)
attack(
    "A13c",
    "CONTROL: a P2 GENERATED scenario failed; nothing required failed",
    SuiteResult(
        full_run=True,
        expected_required_ids=["perm"],
        outcomes=[
            outcome("perm", origin=Origin.PERMANENT),
            outcome(
                "gen-p2",
                result=Outcome.FAILED,
                required=False,
                priority=Priority.P2,
                risk_category="boundary",
            ),
        ],
    ),
    expect_accept=True,
)
attack(
    "A13d",
    "CONTROL: full green run with a widened selection_reason",
    SuiteResult(
        full_run=True,
        selection_reason="full required regression set before acceptance",
        expected_required_ids=["perm", "gen-1", "gen-2"],
        outcomes=[
            outcome("perm", origin=Origin.PERMANENT),
            outcome("gen-1"),
            outcome("gen-2"),
        ],
    ),
    expect_accept=True,
)

# ---------------------------------------------------------------------------
# A14 — non-ACCEPT decisions must survive untouched (no laundering downward)
# ---------------------------------------------------------------------------
fix_decision = EvaluatorDecision(
    decision=Decision.FIX,
    summary="broken",
    problems=["p"],
    correction_prompt="Render an accountable owner beside every open obligation.",
)
out_fix = _apply_suite_precedence(
    SuiteResult(full_run=True, expected_required_ids=["r1"], outcomes=[outcome("r1")]),
    fix_decision,
    "perm",
    lambda _m: None,
)
RESULTS.append(
    {
        "id": "A14",
        "attack": "a green suite must not upgrade an evaluator FIX into an ACCEPT",
        "final_decision_with_hostile_ACCEPT_evaluator": out_fix.decision.value,
        "acceptance_reached": out_fix.decision is Decision.ACCEPT,
        "acceptance_expected": False,
        "OUTCOME": "EXPECTED" if out_fix.decision is Decision.FIX else "FALSE ACCEPTANCE",
    }
)
print(f"[{RESULTS[-1]['OUTCOME']:>16}] A14: green suite + evaluator FIX -> {out_fix.decision.value}")


# ---------------------------------------------------------------------------
out_path = Path(__file__).with_name("attack_gate_unit.json")
out_path.write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")

false_accept = [r for r in RESULTS if r["OUTCOME"] == "FALSE ACCEPTANCE"]
false_refuse = [r for r in RESULTS if r["OUTCOME"] == "FALSE REFUSAL"]
print("\n" + "=" * 72)
print(f"{len(RESULTS)} attacks; false acceptances: {len(false_accept)}; false refusals: {len(false_refuse)}")
for r in false_accept + false_refuse:
    print(f"  {r['OUTCOME']}: {r['id']} — {r['attack']}")
print(f"raw: {out_path}")
