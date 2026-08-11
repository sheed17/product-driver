"""Reviewer probe: can an adaptive scenario be accepted on FABRICATED grounding?

No product code is modified. The real `ScenarioPlanner` and the real validation
run; only the reasoner is scripted, exactly as the driver's own test suite
scripts it, so that no model quota is spent and the payload is controlled.

Two conditions:

  A. evidence-free wave (the CONTROL condition): `expand_after_failures` is
     called with no failures and no clusters, and the scripted wave 2 names
     source failures that this run never observed.
  B. same payload, but the run HAS observed a failure first, so the planner's
     known-failure set is non-empty.

If B rejects the fabricated citation and A accepts it, the "cites an id this run
never observed" check has a hole exactly where the run has nothing to check
against — which is the case where fabricated grounding is most dangerous.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import harness  # noqa: E402


def adaptive_payload() -> dict:
    def case(cid: str, sources: list[str], clusters: list[str], title: str) -> dict:
        return {
            "id": cid,
            "title": title,
            "purpose": "Re-approve the invoice and read the durable store to see what landed.",
            "risk_category": "persistence_failure",
            "priority": "P0",
            "rationale": "the observed failure suggests the durable write may not have happened",
            "requirement_reference": "AC-APPROVAL-001",
            "product_principle_reference": "product rubric",
            "service_refs": ["api"],
            "isolation_key": "store",
            "isolation_note": "uses its own invoice id",
            "generating_risk": "the approval may never reach the durable store",
            "source_failures": sources,
            "source_clusters": clusters,
            "actions": [
                {
                    "kind": "request",
                    "name": "approve",
                    "request": {
                        "method": "POST",
                        "path": f"/api/invoices/{cid.upper()}/approve",
                        "json_body": {"actor": "alice"},
                        "expect_status": 200,
                        "timeout_s": 4,
                    },
                }
            ],
            "persisted_state_checks": [
                {
                    "name": "durable store shows approved",
                    "command": f"{harness.STATE_CMD} {cid.upper()}",
                    "contains": ["status=approved"],
                }
            ],
        }

    return {
        "risks": [],
        "scenarios": [
            case(
                "fab-nonexistent-id",
                ["FAILURE-THAT-NEVER-HAPPENED"],
                [],
                "Adaptive case citing a failure id this run never produced",
            ),
            case(
                "fab-passing-scenario",
                ["gen-happy-approve"],
                [],
                "Adaptive case citing a wave-1 scenario that PASSED",
            ),
            case(
                "fab-nonexistent-cluster",
                [],
                ["C99"],
                "Adaptive case citing a failure cluster this run never produced",
            ),
        ],
        "assumptions": [],
        "unresolved_questions": [],
    }


class Scripted:
    def __init__(self) -> None:
        self.calls = 0
        self.session_id = "scripted"

    def propose(self, brief):
        self.calls += 1
        if self.calls == 1:
            return harness.WAVE1_PAYLOAD
        return adaptive_payload()


def observed_failure_stub():
    """The shape `expand_after_failures` reads: `.render()`, `.scenario_id`, `.cluster_id`."""
    return SimpleNamespace(
        render=lambda: (
            "[gen-persist-approve] generated:gen-persist-approve\n"
            "  risk: persistence_failure (P0)  cluster: C01\n"
            "  FAILED: durable store shows approved: contains 'status=approved' — not found"
        ),
        scenario_id="gen-persist-approve",
        cluster_id="C01",
    )


def condition(name: str, *, with_evidence: bool, work: Path) -> dict:
    planner = harness.make_planner(Scripted(), "none", 9999, work / "store.json", work)
    planner.plan_initial(task=harness.TASK, unit=harness.Unit(), run_id=f"probe-{name}")
    before = {s.id for s in planner.plan.scenarios}
    planner.expand_after_failures(
        task=harness.TASK,
        unit=harness.Unit(),
        failures=[observed_failure_stub()] if with_evidence else [],
        clusters=[],
        investigation_findings=[],
        evaluator_requests=[] if with_evidence else ["Please verify more thoroughly."],
        diff_files=["fixture/app.py"],
    )
    new = [s for s in planner.plan.scenarios if s.id not in before]
    wave2 = [w for w in planner.plan.waves if w.wave == 2]
    return {
        "condition": name,
        "with_evidence": with_evidence,
        "accepted": [
            {
                "id": s.id,
                "title": s.title,
                "source_failures": list(s.provenance.source_failures),
                "source_clusters": list(s.provenance.source_clusters),
            }
            for s in new
        ],
        "rejected": [
            {"id": r.id, "reasons": list(r.reasons)} for w in wave2 for r in w.rejected
        ],
    }


def main() -> int:
    driver = sys.argv[1]
    work = Path(sys.argv[2]).resolve()
    harness.load_driver(driver)
    out = [
        condition("A-evidence-free", with_evidence=False, work=work),
        condition("B-evidence-present", with_evidence=True, work=work),
    ]
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
