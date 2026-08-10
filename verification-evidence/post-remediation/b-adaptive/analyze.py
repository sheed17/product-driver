"""Quantify whether wave 2 responds to the failure evidence it was given.

  python analyze.py --root <dir with one subdirectory per run>

r5's design and its decisive rule are kept:

  BETWEEN   different seeded defects, same task, same wave 1
  REPLICATE the same seeded defect run twice (the model's own noise floor)
  CONTROL   wave 2 with the failure evidence removed entirely

  Responsiveness requires BETWEEN similarity clearly LOWER than REPLICATE, with
  CONTROL lower still (a wave with no evidence has nothing to be similar to).

r5 measured only risk-category and title-token similarity. Because the finding
under re-test is specifically that *content* must respond, four more comparisons
are made here, over the same populations:

  * actions / oracles   — the shapes exercised and the state probes used
  * purpose + rationale — the stated reason each case exists
  * generating_risk     — the risk the model says the failure revealed
  * targeting           — does wave 2 reach the failure surface *neighbouring the
                          seeded defect*, using a per-defect expectation set
                          written from the fixture's own docstring before any
                          output was read?

Both populations r5 used are reported: ACCEPTED (what would actually execute)
and RAW (what the model proposed before validation), so a wave that produced
nothing still contributes evidence about responsiveness.
"""

from __future__ import annotations

import argparse
import json
import re
from itertools import combinations
from pathlib import Path

STOP = set(
    "a an the and or of to to is are be with that this for on in at it its must "
    "still after before when then than from into over under not no any all each "
    "one two both same other more most such very can could should would may".split()
)

#: What a verifier that had actually read each defect's evidence should be
#: steering towards. Taken from the fixture's own description of each defect
#: (`fixture/app.py`), fixed before any wave-2 output was inspected.
NEIGHBOURHOOD = {
    "nonidempotent": {
        "idempotency",
        "repeated_request",
        "retry_safety",
        "concurrency",
    },
    "ui_lies": {
        "persistence_failure",
        "ui_backend_disagreement",
        "restart_recovery",
        "stale_state",
        "crash_mid_workflow",
    },
    "uncertain": {
        "timeout_after_effect",
        "ambiguous_external_effect",
        "retry_safety",
        "partial_failure",
        "timeout_before_effect",
    },
}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 1.0


def tokens(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3 and w not in STOP
    }


def load(run_dir: Path) -> dict:
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    accepted_path = run_dir / "wave2-scenarios.json"
    accepted = json.loads(accepted_path.read_text()) if accepted_path.exists() else []
    raw_path = run_dir / "wave2-raw-payload.json"
    raw: list[dict] = []
    if raw_path.exists():
        payload = json.loads(raw_path.read_text())
        if isinstance(payload, dict):
            raw = [s for s in payload.get("scenarios", []) if isinstance(s, dict)]
    return {"name": run_dir.name, "meta": meta, "accepted": accepted, "raw": raw}


def categories(scenarios) -> set[str]:
    return {str(s.get("risk_category", "")) for s in scenarios if s.get("risk_category")}


def titles(scenarios) -> set[str]:
    return tokens(" ".join(f"{s.get('title','')} {s.get('purpose','')}" for s in scenarios))


def purposes(scenarios) -> set[str]:
    return tokens(
        " ".join(f"{s.get('purpose','')} {s.get('rationale','')}" for s in scenarios)
    )


def generating_risks(scenarios) -> set[str]:
    out = []
    for s in scenarios:
        risk = s.get("generating_risk") or (s.get("provenance") or {}).get(
            "generating_risk", ""
        )
        out.append(str(risk))
    return tokens(" ".join(out))


def oracles(scenarios) -> set[str]:
    """Action kinds and the substance of what each case asserts."""
    out: set[str] = set()
    for s in scenarios:
        for action in s.get("actions", []) or []:
            kind = str(action.get("kind", "?"))
            out.add(f"kind:{kind}")
            request = action.get("request") or {}
            if request:
                out.add(f"path:{request.get('path','')}")
                out.add(f"method:{request.get('method','')}")
            if action.get("service"):
                out.add(f"service-op:{kind}:{action['service']}")
        for check in s.get("persisted_state_checks", []) or []:
            for needle in list(check.get("contains") or []) + list(
                check.get("not_contains") or []
            ):
                out.add(f"oracle:{needle}")
        for needle in s.get("expected_observations", []) or []:
            out.add(f"expect:{needle}")
        for needle in s.get("forbidden_observations", []) or []:
            out.add(f"forbid:{needle}")
    return out


MEASURES = {
    "risk_categories": categories,
    "title_tokens": titles,
    "purpose_rationale_tokens": purposes,
    "generating_risk_tokens": generating_risks,
    "actions_and_oracles": oracles,
}


def klass(a: dict, b: dict) -> str:
    da, db = a["meta"].get("defect", "?"), b["meta"].get("defect", "?")
    if a["meta"].get("no_evidence") or b["meta"].get("no_evidence"):
        return "CONTROL"
    return "REPLICATE" if da == db else "BETWEEN"


def report(label: str, runs: list[dict], key: str) -> dict:
    usable = [r for r in runs if r[key]]
    out: dict = {"population": label, "runs_contributing": len(usable), "measures": {}}
    for measure, extract in MEASURES.items():
        buckets: dict[str, list[float]] = {"REPLICATE": [], "BETWEEN": [], "CONTROL": []}
        pairs = []
        for a, b in combinations(usable, 2):
            score = jaccard(extract(a[key]), extract(b[key]))
            buckets[klass(a, b)].append(score)
            pairs.append(
                {"a": a["name"], "b": b["name"], "class": klass(a, b), "jaccard": round(score, 3)}
            )
        out["measures"][measure] = {
            group: {
                "pairs": len(values),
                "mean": round(sum(values) / len(values), 3) if values else None,
            }
            for group, values in buckets.items()
        }
        out["measures"][measure]["verdict"] = _verdict(buckets)
        out["measures"][measure]["pair_detail"] = pairs
    return out


def _verdict(buckets: dict[str, list[float]]) -> str:
    def mean(values):
        return sum(values) / len(values) if values else None

    replicate, between, control = (
        mean(buckets["REPLICATE"]),
        mean(buckets["BETWEEN"]),
        mean(buckets["CONTROL"]),
    )
    if replicate is None or between is None:
        return "INSUFFICIENT DATA"
    if between >= replicate:
        return "NOT RESPONSIVE — different evidence is no more different than a replicate"
    if control is not None and control >= between:
        return (
            "RESPONSIVE but weak — different evidence diverges, yet a wave with no "
            "evidence is not the most different of all"
        )
    return "RESPONSIVE — different evidence produces more different coverage than a replicate"


def targeting(runs: list[dict], key: str) -> dict:
    """Does each wave 2 reach the failure surface neighbouring its own defect?"""
    rows = []
    for run in runs:
        defect = run["meta"].get("defect", "?")
        if run["meta"].get("no_evidence"):
            defect = "CONTROL"
        found = categories(run[key])
        own = NEIGHBOURHOOD.get(defect, set())
        others = {
            d: NEIGHBOURHOOD[d] for d in NEIGHBOURHOOD if d != defect
        }
        rows.append(
            {
                "run": run["name"],
                "defect": defect,
                "wave2_categories": sorted(found),
                "own_neighbourhood_hits": sorted(found & own),
                "own_neighbourhood_hit_rate": (
                    round(len(found & own) / len(found), 3) if found else None
                ),
                "other_defect_neighbourhood_hits": {
                    d: sorted(found & cats) for d, cats in others.items() if found & cats
                },
            }
        )
    scored = [r for r in rows if r["defect"] != "CONTROL" and r["own_neighbourhood_hit_rate"] is not None]
    return {
        "per_run": rows,
        "mean_own_neighbourhood_hit_rate": (
            round(sum(r["own_neighbourhood_hit_rate"] for r in scored) / len(scored), 3)
            if scored
            else None
        ),
        "runs_reaching_their_own_neighbourhood": sum(
            1 for r in scored if r["own_neighbourhood_hits"]
        ),
        "runs_scored": len(scored),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    runs = [load(d) for d in sorted(root.iterdir()) if d.is_dir()]
    runs = [r for r in runs if r["meta"]]

    result = {
        "runs": [
            {
                "name": r["name"],
                "defect": r["meta"].get("defect"),
                "no_evidence": r["meta"].get("no_evidence"),
                "wave1_failures": r["meta"].get("wave1_failures"),
                "wave2_accepted": len(r["accepted"]),
                "wave2_proposed": len(r["raw"]),
                "wave2_titles": [s.get("title") for s in (r["accepted"] or r["raw"])],
            }
            for r in runs
        ],
        "accepted": report("ACCEPTED wave-2 coverage", runs, "accepted"),
        "raw": report("RAW model proposals (pre-validation)", runs, "raw"),
        "targeting_accepted": targeting(runs, "accepted"),
        "targeting_raw": targeting(runs, "raw"),
        "rule": (
            "Responsiveness requires BETWEEN < REPLICATE, with CONTROL lowest. "
            "Provenance links alone are not evidence: the content must move."
        ),
    }
    text = json.dumps(result, indent=2)
    print(text)
    (Path(args.out) if args.out else root / "divergence.json").write_text(
        text + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
