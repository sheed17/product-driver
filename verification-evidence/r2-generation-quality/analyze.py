"""Measure the generation runs: proposal counts, rejection taxonomy, action mix."""

from __future__ import annotations

import json
import re
from pathlib import Path

OUT = Path(__file__).parent
TAGS = ["A", "A2-diff", "B-diff", "C", "D", "E"]


def classify(reason: str) -> str:
    if "unknown risk_category" in reason:
        return "invented-risk-category"
    if "shell composition" in reason:
        return "sql-blocked-by-shell-composition"
    if "not in the approved set" in reason:
        # Prose reads as English: contains a space-separated sentence, not a path.
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
    if "Field required" in reason or "Extra inputs" in reason:
        return "schema-mismatch"
    if "mutates local state" in reason:
        return "no-cleanup/isolation"
    if "duplicate" in reason:
        return "duplicate"
    if "does not name the active unit" in reason or "does not name a founder" in reason:
        return "ungrounded-reference"
    return "other: " + reason[:60]


rows = []
totals: dict[str, int] = {}
grand_proposed = grand_accepted = 0

for tag in TAGS:
    plan = json.loads((OUT / f"plan-{tag}.json").read_text())
    raw = json.loads((OUT / f"raw-{tag}.json").read_text())
    proposed = sum(len(p.get("scenarios", [])) for p in raw if isinstance(p, dict))
    accepted = len(plan["scenarios"])
    rejected = [r for w in plan["waves"] for r in w["rejected"]]
    grand_proposed += proposed
    grand_accepted += accepted

    # each rejected scenario is counted once, by its FIRST (primary) reason
    primary: dict[str, int] = {}
    for r in rejected:
        kind = classify(r["reasons"][0]) if r["reasons"] else "unknown"
        primary[kind] = primary.get(kind, 0) + 1
        totals[kind] = totals.get(kind, 0) + 1

    risks = plan["risks"]
    uncovered = [r for r in risks if not r["covered_by"]]
    p0p1_uncovered = [r for r in uncovered if r["severity"] in ("P0", "P1")]

    rows.append(
        {
            "task": tag,
            "proposed": proposed,
            "accepted": accepted,
            "rejected": len(rejected),
            "risks_identified": len(risks),
            "risks_uncovered": len(uncovered),
            "P0/P1_risks_uncovered": len(p0p1_uncovered),
            "rejection_reasons": primary,
            "accepted_categories": sorted({s["risk_category"] for s in plan["scenarios"]}),
        }
    )

# action mix across everything accepted
kinds: dict[str, int] = {}
state_checked = 0
for tag in TAGS:
    plan = json.loads((OUT / f"plan-{tag}.json").read_text())
    for s in plan["scenarios"]:
        for a in s["actions"]:
            kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
        if s["persisted_state_checks"] or any(a["kind"] == "state_check" for a in s["actions"]):
            state_checked += 1

report = {
    "grand_proposed": grand_proposed,
    "grand_accepted": grand_accepted,
    "acceptance_rate": round(grand_accepted / grand_proposed, 3) if grand_proposed else 0,
    "rejection_taxonomy": dict(sorted(totals.items(), key=lambda kv: -kv[1])),
    "accepted_action_kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
    "accepted_with_persisted_state_check": state_checked,
    "per_task": rows,
}
print(json.dumps(report, indent=2))
(OUT / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
