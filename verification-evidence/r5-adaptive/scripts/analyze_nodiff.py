"""No-diff family: the diff hint removed, so ONLY the failure evidence differs.

In the main family the generator was told the builder changed r5/fixture/app.py
and, having Read/Grep/Glob, it read that file -- its wave-2 rationales quote the
source. That confounds the responsiveness question. Here `diff_files` is empty,
so the failure evidence is the only thing that varies between runs.
"""

from __future__ import annotations

import json
import re
from itertools import combinations
from pathlib import Path

RUNS = {
    "nodiff1-nonidempotent": ("r5/raw/nodiff1", "exp1"),
    "nodiff3-uncertain": ("r5/raw/nodiff3", "exp3"),
    "nodiff-CONTROL-no-evidence": ("r5/raw/nodiff-control", "CONTROL"),
}

STOP = set("a an the and or of to is are be with that this for on in at it its must still".split())


def load(path: str) -> list[dict]:
    p = Path(path) / "wave2-scenarios.json"
    return json.loads(p.read_text()) if p.exists() else []


def categories(s) -> set[str]:
    return {x["risk_category"] for x in s}


def tokens(s) -> set[str]:
    out: set[str] = set()
    for x in s:
        for w in re.findall(r"[a-z]+", f"{x.get('title','')} {x.get('purpose','')}".lower()):
            if len(w) > 3 and w not in STOP:
                out.add(w)
    return out


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 1.0


def main() -> int:
    data = {k: load(v[0]) for k, v in RUNS.items()}
    print("=" * 78)
    print("NO-DIFF FAMILY -- only the failure evidence differs between these runs")
    print("=" * 78)
    for name, (path, tag) in RUNS.items():
        print(f"\n{name}   (seeded defect group: {tag})   [{path}]")
        for s in data[name]:
            print(f"    [{s['priority']} {s['risk_category']:<24}] {s['title']}")

    print()
    print("=" * 78)
    print("PAIRWISE SIMILARITY")
    print("=" * 78)
    for a, b in combinations(RUNS, 2):
        rel = "CONTROL" if "CONTROL" in (RUNS[a][1], RUNS[b][1]) else "BETWEEN-DEFECTS"
        print(
            f"[{rel:<15}] {a:<28} vs {b:<28} "
            f"cats={jaccard(categories(data[a]), categories(data[b])):.3f} "
            f"titles={jaccard(tokens(data[a]), tokens(data[b])):.3f}"
        )
    print()
    print("If the run driven by the TIMEOUT failures is no closer to timeout/ambiguity")
    print("territory than the run that saw NO failures at all, the evidence is not")
    print("steering generation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
