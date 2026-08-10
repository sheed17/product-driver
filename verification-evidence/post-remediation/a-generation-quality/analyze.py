"""Measure real-model generation quality across r2's five representative tasks.

  python analyze.py --dir <evidence dir> [--compare <r2 metrics.json>]

r2's rejection taxonomy and counting rules are kept verbatim so the numbers are
comparable. The measures r2 did not make are added below, one per question the
re-measurement was asked to answer:

  * tasks producing zero usable scenarios
  * category relevance          — is the risk category one the task's diff or
                                  acceptance criteria could plausibly implicate?
  * duplicate / redundant       — accepted scenarios sharing a signature, and
                                  accepted scenarios sharing a category *and* an
                                  action shape
  * bare-suite invocation       — the r2 finding that 67% of accepted scenarios
                                  merely ran an existing broad test suite
  * grounding quality           — does every accepted scenario name a real unit /
                                  acceptance criterion and a real principle?
  * mechanically executable     — does every accepted scenario carry at least one
                                  assertion the executor can actually score?
  * effect-family state oracles — does every accepted EFFECT_FAMILY scenario
                                  inspect persisted state?
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

TAGS = ["A", "A2-diff", "B-diff", "C", "D", "E"]

#: Categories whose whole point is a persisted effect. A scenario in one of these
#: that never inspects persisted state has not verified the thing it exists for.
EFFECT_FAMILY = {
    "idempotency",
    "repeated_request",
    "retry_safety",
    "timeout_after_effect",
    "restart_recovery",
    "crash_mid_workflow",
    "partial_failure",
    "persistence_failure",
    "unexpected_state_transition",
    "ambiguous_external_effect",
}

#: Commands that run somebody else's whole suite rather than probing a behaviour.
BROAD_SUITE = re.compile(r"\b(pytest|unittest|tox|nox|run_diagnostics|check_env)\b")

#: What each task could plausibly be about, from its own acceptance criteria and
#: diff — written from the task definitions, before any output was read.
RELEVANT: dict[str, set[str]] = {
    "A": {
        "happy_path", "missing_data", "stale_state", "ui_backend_disagreement",
        "conflicting_evidence", "malformed_input", "boundary", "regression",
        "authorization", "safety_invariant",
    },
    "A2-diff": {
        "happy_path", "missing_data", "stale_state", "ui_backend_disagreement",
        "conflicting_evidence", "malformed_input", "boundary", "regression",
        "authorization", "cross_tenant", "approval_required", "safety_invariant",
        "persistence_failure", "restart_recovery", "crash_mid_workflow",
        "partial_failure", "concurrency", "unexpected_state_transition",
    },
    "B-diff": {
        "happy_path", "idempotency", "repeated_request", "retry_safety",
        "concurrency", "authorization", "approval_required", "cross_tenant",
        "restart_recovery", "persistence_failure", "timeout_after_effect",
        "ambiguous_external_effect", "partial_failure", "crash_mid_workflow",
        "safety_invariant", "regression", "boundary", "malformed_input",
        "missing_data", "unexpected_state_transition",
    },
    "C": {
        "happy_path", "missing_data", "boundary", "malformed_input",
        "safety_invariant", "regression", "stale_state", "authorization",
        "cross_tenant", "concurrency",
    },
    "D": {
        "persistence_failure", "restart_recovery", "crash_mid_workflow",
        "partial_failure", "stale_state", "idempotency", "retry_safety",
        "concurrency", "unexpected_state_transition", "timeout_after_effect",
        "ambiguous_external_effect", "safety_invariant", "regression",
        "happy_path", "repeated_request",
    },
    "E": {
        "authorization", "approval_required", "cross_tenant", "safety_invariant",
        "regression", "happy_path", "boundary", "malformed_input", "missing_data",
        "concurrency", "stale_state",
    },
}


def classify(reason: str) -> str:
    """r2's taxonomy, unchanged, plus the classes this pass observed."""
    if "unknown risk_category" in reason:
        return "invented-risk-category"
    if "shell composition" in reason:
        return "sql-blocked-by-shell-composition"
    if "control character" in reason:
        return "control-character-refused"
    if "not in the approved set" in reason:
        m = re.search(r"not in the approved set: (.+?)\. Generated", reason, re.S)
        text = (m.group(1) if m else "").strip("'\" ")
        words = text.split()
        if len(words) >= 4 and not text.startswith((".", "/", "sqlite3", "python", ".venv")):
            return "prose-written-into-setup/cleanup"
        return "unapproved-command"
    if "inspects no persisted state" in reason:
        return "effect-family-without-oracle"
    if "regression scenario must name" in reason:
        return "regression-without-scope"
    if "persisted_state_checks" in reason:
        return "state-check-schema-mismatch"
    if "Field required" in reason or "Extra inputs" in reason:
        return "schema-mismatch"
    if "mutates local state" in reason:
        return "no-cleanup/isolation"
    if "duplicate" in reason:
        return "duplicate"
    if "does not name the active unit" in reason or "does not name a founder" in reason:
        return "ungrounded-reference"
    if "already used in this run" in reason:
        return "id-collision"
    if "wave budget" in reason:
        return "wave-budget"
    if "external" in reason or "loopback" in reason:
        return "external-host-refused"
    return "other: " + reason[:70]


def action_kinds(scenario: dict) -> list[str]:
    return [a.get("kind", "?") for a in scenario.get("actions", [])]


def commands_of(scenario: dict) -> list[str]:
    out = list(scenario.get("setup") or []) + list(scenario.get("cleanup") or [])
    for action in scenario.get("actions", []):
        if action.get("command"):
            out.append(str(action["command"]))
        check = action.get("state_check") or {}
        if check.get("command"):
            out.append(str(check["command"]))
    for check in scenario.get("persisted_state_checks", []) or []:
        if check.get("command"):
            out.append(str(check["command"]))
    return out


def inspects_persisted_state(scenario: dict) -> bool:
    if scenario.get("persisted_state_checks"):
        return True
    return any(a.get("kind") == "state_check" for a in scenario.get("actions", []))


def is_bare_suite_invocation(scenario: dict) -> bool:
    """Does this scenario only run a broad existing suite, probing nothing itself?

    r2's headline quality finding. A scenario that runs pytest and asserts pytest
    passed tells you the suite is green; it does not exercise a situation.
    """
    commands = commands_of(scenario)
    if not commands:
        return False
    if not all(BROAD_SUITE.search(c) for c in commands):
        return False
    kinds = set(action_kinds(scenario))
    # If it also issues a request, drives a browser, restarts a service or races
    # something, it is doing more than running the suite.
    return not (kinds - {"command", "state_check"})


def mechanically_scorable(scenario: dict) -> bool:
    """Can the executor score at least one thing this scenario claims?"""
    if scenario.get("expected_observations") or scenario.get("forbidden_observations"):
        return True
    if scenario.get("persisted_state_checks"):
        return True
    for action in scenario.get("actions", []):
        kind = action.get("kind")
        if kind == "command" and (
            action.get("expect_contains") or action.get("expect_exit_code") is not None
        ):
            return True
        if kind == "request":
            request = action.get("request") or {}
            if request.get("expect_status") is not None or request.get("expect_contains"):
                return True
        if kind == "parallel_requests":
            for request in action.get("requests", []) or []:
                if request.get("expect_status") is not None or request.get("expect_contains"):
                    return True
        if kind == "state_check":
            check = action.get("state_check") or {}
            if check.get("contains") or check.get("not_contains"):
                return True
        if kind == "browser":
            if any(s.get("expect_text") for s in action.get("browser_steps", []) or []):
                return True
    return False


def grounded(scenario: dict) -> bool:
    return bool(
        str(scenario.get("requirement_reference") or "").strip()
        and str(scenario.get("product_principle_reference") or "").strip()
    )


def shape(scenario: dict) -> str:
    """A coarse redundancy key: same category, same action shape, same oracle."""
    return "|".join(
        [
            str(scenario.get("risk_category")),
            ",".join(action_kinds(scenario)),
            ",".join(sorted(scenario.get("expected_observations") or [])),
            ",".join(
                sorted(
                    str(c.get("command", "")) for c in scenario.get("persisted_state_checks") or []
                )
            ),
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--compare", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    root = Path(args.dir).resolve()

    rows = []
    totals: dict[str, int] = {}
    grand_proposed = grand_accepted = 0
    all_accepted: list[dict] = []

    for tag in TAGS:
        plan_path = root / f"plan-{tag}.json"
        raw_path = root / f"raw-{tag}.json"
        if not plan_path.exists():
            rows.append({"task": tag, "MISSING": True})
            continue
        plan = json.loads(plan_path.read_text())
        raw = json.loads(raw_path.read_text()) if raw_path.exists() else []
        proposed = sum(
            len(p.get("scenarios", [])) for p in raw if isinstance(p, dict)
        )
        accepted = plan["scenarios"]
        rejected = [r for w in plan["waves"] for r in w["rejected"]]
        grand_proposed += proposed
        grand_accepted += len(accepted)
        all_accepted += accepted

        primary: dict[str, int] = {}
        for r in rejected:
            kind = classify(r["reasons"][0]) if r["reasons"] else "unknown"
            primary[kind] = primary.get(kind, 0) + 1
            totals[kind] = totals.get(kind, 0) + 1

        risks = plan["risks"]
        uncovered = [r for r in risks if not r["covered_by"]]
        p0p1 = [r for r in uncovered if r["severity"] in ("P0", "P1")]

        categories = [s["risk_category"] for s in accepted]
        relevant = RELEVANT.get(tag, set())
        off_topic = [c for c in categories if relevant and c not in relevant]

        signatures = [shape(s) for s in accepted]
        effect_cases = [s for s in accepted if s["risk_category"] in EFFECT_FAMILY]

        rows.append(
            {
                "task": tag,
                "proposed": proposed,
                "accepted": len(accepted),
                "rejected": len(rejected),
                "produced_zero_usable_scenarios": len(accepted) == 0,
                "risks_identified": len(risks),
                "risks_uncovered": len(uncovered),
                "P0/P1_risks_uncovered": len(p0p1),
                "rejection_reasons": primary,
                "accepted_categories": sorted(set(categories)),
                "off_topic_categories": sorted(set(off_topic)),
                "category_relevance": (
                    round(1 - len(off_topic) / len(categories), 3) if categories else None
                ),
                "redundant_shapes": len(signatures) - len(set(signatures)),
                "bare_suite_invocations": sum(
                    1 for s in accepted if is_bare_suite_invocation(s)
                ),
                "bare_suite_proportion": (
                    round(
                        sum(1 for s in accepted if is_bare_suite_invocation(s)) / len(accepted), 3
                    )
                    if accepted
                    else None
                ),
                "grounded": sum(1 for s in accepted if grounded(s)),
                "mechanically_scorable": sum(1 for s in accepted if mechanically_scorable(s)),
                "effect_family_cases": len(effect_cases),
                "effect_family_with_state_oracle": sum(
                    1 for s in effect_cases if inspects_persisted_state(s)
                ),
            }
        )

    kinds: dict[str, int] = {}
    for scenario in all_accepted:
        for kind in action_kinds(scenario):
            kinds[kind] = kinds.get(kind, 0) + 1

    effect_all = [s for s in all_accepted if s["risk_category"] in EFFECT_FAMILY]
    report = {
        "grand_proposed": grand_proposed,
        "grand_accepted": grand_accepted,
        "acceptance_rate": (
            round(grand_accepted / grand_proposed, 3) if grand_proposed else 0
        ),
        "tasks_with_zero_usable_scenarios": [
            r["task"] for r in rows if r.get("produced_zero_usable_scenarios")
        ],
        "rejection_taxonomy": dict(sorted(totals.items(), key=lambda kv: -kv[1])),
        "accepted_action_kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "accepted_with_persisted_state_check": sum(
            1 for s in all_accepted if inspects_persisted_state(s)
        ),
        "quality": {
            "category_relevance": (
                round(
                    sum(
                        1
                        for r in rows
                        if not r.get("MISSING")
                        for c in r["accepted_categories"]
                        if c in RELEVANT.get(r["task"], set())
                    )
                    / max(
                        1,
                        sum(
                            len(r["accepted_categories"])
                            for r in rows
                            if not r.get("MISSING")
                        ),
                    ),
                    3,
                )
            ),
            "bare_suite_invocations": sum(
                1 for s in all_accepted if is_bare_suite_invocation(s)
            ),
            "bare_suite_proportion": (
                round(
                    sum(1 for s in all_accepted if is_bare_suite_invocation(s))
                    / len(all_accepted),
                    3,
                )
                if all_accepted
                else None
            ),
            "grounded": sum(1 for s in all_accepted if grounded(s)),
            "grounded_proportion": (
                round(sum(1 for s in all_accepted if grounded(s)) / len(all_accepted), 3)
                if all_accepted
                else None
            ),
            "mechanically_scorable": sum(
                1 for s in all_accepted if mechanically_scorable(s)
            ),
            "mechanically_scorable_proportion": (
                round(
                    sum(1 for s in all_accepted if mechanically_scorable(s))
                    / len(all_accepted),
                    3,
                )
                if all_accepted
                else None
            ),
            "effect_family_cases": len(effect_all),
            "effect_family_with_state_oracle": sum(
                1 for s in effect_all if inspects_persisted_state(s)
            ),
            "redundant_shapes_total": sum(
                r.get("redundant_shapes", 0) for r in rows if not r.get("MISSING")
            ),
        },
        "per_task": rows,
    }

    if args.compare:
        before = json.loads(Path(args.compare).read_text())
        report["comparison_with_r2"] = {
            "proposed": [before["grand_proposed"], grand_proposed],
            "accepted": [before["grand_accepted"], grand_accepted],
            "acceptance_rate": [before["acceptance_rate"], report["acceptance_rate"]],
            "tasks_with_zero_usable_scenarios": [
                [t["task"] for t in before["per_task"] if t["accepted"] == 0],
                report["tasks_with_zero_usable_scenarios"],
            ],
            "r2_rejection_taxonomy": before["rejection_taxonomy"],
            "now_rejection_taxonomy": report["rejection_taxonomy"],
        }

    text = json.dumps(report, indent=2)
    print(text)
    (Path(args.out) if args.out else root / "metrics.json").write_text(
        text + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
