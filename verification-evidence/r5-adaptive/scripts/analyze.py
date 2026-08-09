"""Quantify how different wave 2 is across different seeded failures.

Three comparisons matter, and only their ordering is decisive:

  BETWEEN   different seeded defects, same task, same wave 1
  REPLICATE the same seeded defect run twice (the model's own noise floor)
  CONTROL   wave 2 with the failure evidence removed entirely

Adaptive generation is real only if BETWEEN similarity is clearly LOWER than
REPLICATE similarity, and CONTROL is lower still.

Two populations are measured. ACCEPTED is what the run would actually execute.
RAW is what the model proposed before validation refused anything -- measuring
that removes the validation-yield confound, so a wave that produced nothing
still contributes evidence about responsiveness.
"""

from __future__ import annotations

import json
import re
from itertools import combinations
from pathlib import Path

# name -> (directory, seeded defect)
RUNS = {
    "exp1a-nonidem": ("r5/raw/real1", "exp1"),
    "exp1b-nonidem": ("r5/raw/real1b", "exp1"),
    "exp1c-nonidem": ("r5/raw/real1c", "exp1"),
    "exp2a-ui_lies": ("r5/raw/real2", "exp2"),
    "exp2b-ui_lies": ("r5/raw/real2b", "exp2"),
    "exp2c-ui_lies": ("r5/raw/real2c", "exp2"),
    "exp3a-uncertain": ("r5/raw/real3", "exp3"),
    "exp3b-uncertain": ("r5/raw/real3b", "exp3"),
    "exp3c-uncertain": ("r5/raw/real3c", "exp3"),
    "exp3d-uncertain": ("r5/raw/real3d", "exp3"),
    "CONTROL-no-evidence": ("r5/raw/control", "CONTROL"),
}

STOP = set("a an the and or of to is are be with that this for on in at it its must still".split())


def accepted(path: str) -> list[dict]:
    p = Path(path) / "wave2-scenarios.json"
    return json.loads(p.read_text()) if p.exists() else []


def raw(path: str) -> list[dict]:
    p = Path(path) / "wave2-raw-payload.json"
    if not p.exists():
        return []
    payload = json.loads(p.read_text())
    if not isinstance(payload, dict):
        return []
    return [s for s in payload.get("scenarios", []) if isinstance(s, dict)]


def categories(scenarios) -> set[str]:
    return {str(s.get("risk_category", "")) for s in scenarios if s.get("risk_category")}


def title_tokens(scenarios) -> set[str]:
    out: set[str] = set()
    for s in scenarios:
        text = f"{s.get('title','')} {s.get('purpose','')}"
        for w in re.findall(r"[a-z]+", text.lower()):
            if len(w) > 3 and w not in STOP:
                out.add(w)
    return out


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 1.0


def kind(a: str, b: str) -> str:
    if "CONTROL" in (RUNS[a][1], RUNS[b][1]):
        return "CONTROL"
    return "REPLICATE" if RUNS[a][1] == RUNS[b][1] else "BETWEEN"


def report(label: str, data: dict[str, list[dict]]) -> None:
    usable = [k for k in RUNS if data[k]]
    print()
    print("=" * 78)
    print(f"{label}  ({len(usable)} of {len(RUNS)} runs contributed scenarios)")
    print("=" * 78)
    rows = []
    for a, b in combinations(usable, 2):
        rows.append(
            (
                a,
                b,
                jaccard(categories(data[a]), categories(data[b])),
                jaccard(title_tokens(data[a]), title_tokens(data[b])),
            )
        )
    for group in ("REPLICATE", "BETWEEN", "CONTROL"):
        sel = [r for r in rows if kind(r[0], r[1]) == group]
        if not sel:
            print(f"{group:<12} no pairs")
            continue
        n = len(sel)
        print(
            f"{group:<12} pairs={n:<3} mean risk-category Jaccard {sum(r[2] for r in sel)/n:.3f}"
            f"   mean title-token Jaccard {sum(r[3] for r in sel)/n:.3f}"
        )
    print("\n  pair detail:")
    for a, b, jc, jt in sorted(rows, key=lambda r: kind(r[0], r[1])):
        print(f"    [{kind(a,b):<9}] {a:<20} vs {b:<20} cats={jc:.3f} titles={jt:.3f}")


def main() -> int:
    acc = {k: accepted(v[0]) for k, v in RUNS.items()}
    rawd = {k: raw(v[0]) for k, v in RUNS.items()}

    print("=" * 78)
    print("WAVE-2 ACCEPTED OUTPUT PER RUN")
    print("=" * 78)
    for name, (path, _) in RUNS.items():
        scenarios = acc[name]
        print(f"\n{name}  accepted={len(scenarios)}  proposed={len(rawd[name])}   [{path}]")
        for s in scenarios:
            print(f"    [{s['priority']} {s['risk_category']:<26}] {s['title']}")
        if not scenarios and rawd[name]:
            print("    (all proposals refused by validation; proposed titles were:)")
            for s in rawd[name]:
                print(f"      - [{s.get('risk_category','?')}] {s.get('title','?')}")

    report("SIMILARITY OF ACCEPTED WAVE-2 COVERAGE", acc)
    report("SIMILARITY OF RAW MODEL PROPOSALS (pre-validation)", rawd)

    print()
    print("VERDICT RULE: responsiveness requires BETWEEN < REPLICATE and CONTROL lowest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
