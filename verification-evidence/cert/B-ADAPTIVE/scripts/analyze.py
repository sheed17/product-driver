"""Quantify whether wave 2 responds to the failure evidence it was given.

  python analyze.py --root <dir with one subdirectory per run>

  BETWEEN   different seeded defects, same task, same wave 1
  REPLICATE the same seeded defect run twice (the model's own noise floor)
  CONTROL   wave 2 with the failure evidence removed entirely

Responsiveness requires BETWEEN similarity clearly LOWER than REPLICATE, with
CONTROL lower still.

Differences from the prior campaign's `analyze.py`:

  * pairwise Jaccard scores share runs and are NOT independent, so the margin is
    assessed with a PERMUTATION TEST on the defect labels rather than by eyeing
    two means;
  * `actions_and_oracles` is reported twice — once with the prior campaign's
    exact definition, for comparability, and once with arbitrary invoice
    identifiers normalised away and request bodies included, so the measure
    reflects the SHAPE and the ASSERTED SUBSTANCE rather than the model's free
    choice of an invoice id;
  * targeting is reported both plainly and EXCLUSIVELY (categories belonging to
    this defect's neighbourhood and to no other defect's);
  * a supplementary nearest-neighbour test (added after pre-registration, and
    labelled as such) asks the blunt question: is a run's most similar peer one
    of its own replicates?
"""

from __future__ import annotations

import argparse
import json
import random
import re
from itertools import combinations
from pathlib import Path

STOP = set(
    "a an the and or of to to is are be with that this for on in at it its must "
    "still after before when then than from into over under not no any all each "
    "one two both same other more most such very can could should would may".split()
)

#: Fixed in PREREGISTRATION.md before any wave-2 output of this campaign existed.
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
    "authz_retry": {
        "authorization",
        "approval_required",
        "cross_tenant",
        "retry_safety",
        "repeated_request",
    },
    "partial_dep": {
        "partial_failure",
        "dependency_failure",
        "service_unavailable",
        "ui_backend_disagreement",
    },
}

EXCLUSIVE = {
    d: cats - set().union(*(c for k, c in NEIGHBOURHOOD.items() if k != d))
    for d, cats in NEIGHBOURHOOD.items()
}

_ID = re.compile(r"/api/invoices/[^/]+/")
_STATE_ARG = re.compile(r"(fixture/state\.py)\s+\S+")


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


def _oracles(scenarios, *, normalise: bool) -> set[str]:
    out: set[str] = set()

    def path(p: str) -> str:
        return _ID.sub("/api/invoices/{id}/", p) if normalise else p

    def command(c: str) -> str:
        return _STATE_ARG.sub(r"\1 {id}", c) if normalise else c

    for s in scenarios:
        for action in s.get("actions", []) or []:
            kind = str(action.get("kind", "?"))
            out.add(f"kind:{kind}")
            requests = [r for r in [action.get("request")] if r] + list(
                action.get("requests") or []
            )
            for request in requests:
                out.add(f"path:{path(str(request.get('path','')))}")
                out.add(f"method:{request.get('method','')}")
                if normalise:
                    body = request.get("json_body")
                    if isinstance(body, dict):
                        for k, v in sorted(body.items()):
                            out.add(f"body:{k}={v}")
                    for needle in request.get("expect_contains") or []:
                        out.add(f"resp-expect:{needle}")
                    if request.get("expect_status") is not None:
                        out.add(f"status:{request['expect_status']}")
            if action.get("service"):
                out.add(f"service-op:{kind}:{action['service']}")
            check = action.get("state_check")
            if normalise and isinstance(check, dict):
                out.add(f"cmd:{command(str(check.get('command','')))}")
                for needle in list(check.get("contains") or []) + list(
                    check.get("not_contains") or []
                ):
                    out.add(f"oracle:{needle}")
        for check in s.get("persisted_state_checks", []) or []:
            if normalise:
                out.add(f"cmd:{command(str(check.get('command','')))}")
            for needle in list(check.get("contains") or []) + list(
                check.get("not_contains") or []
            ):
                out.add(f"oracle:{needle}")
        for needle in s.get("expected_observations", []) or []:
            out.add(f"expect:{needle}")
        for needle in s.get("forbidden_observations", []) or []:
            out.add(f"forbid:{needle}")
    return out


def oracles(scenarios) -> set[str]:
    return _oracles(scenarios, normalise=False)


def oracles_norm(scenarios) -> set[str]:
    return _oracles(scenarios, normalise=True)


MEASURES = {
    "risk_categories": categories,
    "title_tokens": titles,
    "purpose_rationale_tokens": purposes,
    "generating_risk_tokens": generating_risks,
    "actions_and_oracles": oracles,
    "actions_and_oracles_normalised": oracles_norm,
}


def klass(a: dict, b: dict) -> str:
    da, db = a["meta"].get("defect", "?"), b["meta"].get("defect", "?")
    if a["meta"].get("no_evidence") or b["meta"].get("no_evidence"):
        return "CONTROL"
    return "REPLICATE" if da == db else "BETWEEN"


def _mean(values):
    return sum(values) / len(values) if values else None


def permutation_p(
    labels: list[str], sim: dict[tuple[int, int], float], trials: int, seed: int
) -> dict:
    """One-sided p for mean(BETWEEN) - mean(REPLICATE) being as low as observed."""
    n = len(labels)
    pairs = list(combinations(range(n), 2))

    def diff(assign: list[str]) -> float | None:
        rep = [sim[p] for p in pairs if assign[p[0]] == assign[p[1]]]
        bet = [sim[p] for p in pairs if assign[p[0]] != assign[p[1]]]
        if not rep or not bet:
            return None
        return _mean(bet) - _mean(rep)

    observed = diff(labels)
    if observed is None:
        return {"observed_diff": None, "p": None, "trials": 0}
    rng = random.Random(seed)
    shuffled = list(labels)
    hits = 0
    for _ in range(trials):
        rng.shuffle(shuffled)
        value = diff(shuffled)
        if value is not None and value <= observed + 1e-12:
            hits += 1
    return {
        "observed_diff": round(observed, 4),
        "p": round((hits + 1) / (trials + 1), 4),
        "trials": trials,
    }


def nearest_neighbour(
    labels: list[str], names: list[str], sim: dict[tuple[int, int], float]
) -> dict:
    n = len(labels)
    hits = 0
    rows = []
    for i in range(n):
        best_j, best = None, -1.0
        for j in range(n):
            if i == j:
                continue
            score = sim[(min(i, j), max(i, j))]
            if score > best:
                best, best_j = score, j
        same = labels[i] == labels[best_j]
        hits += same
        rows.append(
            {
                "run": names[i],
                "nearest": names[best_j],
                "jaccard": round(best, 3),
                "same_defect": bool(same),
            }
        )
    chance = _mean(
        [sum(1 for j in range(n) if j != i and labels[j] == labels[i]) / (n - 1) for i in range(n)]
    )
    return {
        "hits": hits,
        "n": n,
        "rate": round(hits / n, 3) if n else None,
        "chance_rate": round(chance, 3) if chance is not None else None,
        "detail": rows,
    }


def report(label: str, runs: list[dict], key: str, trials: int) -> dict:
    usable = [r for r in runs if r[key]]
    evidence_runs = [r for r in usable if not r["meta"].get("no_evidence")]
    labels = [r["meta"].get("defect", "?") for r in evidence_runs]
    names = [r["name"] for r in evidence_runs]
    out: dict = {
        "population": label,
        "runs_contributing": len(usable),
        "evidence_runs": len(evidence_runs),
        "measures": {},
    }
    for measure, extract in MEASURES.items():
        buckets: dict[str, list[float]] = {"REPLICATE": [], "BETWEEN": [], "CONTROL": []}
        pairs = []
        for a, b in combinations(usable, 2):
            score = jaccard(extract(a[key]), extract(b[key]))
            buckets[klass(a, b)].append(score)
            pairs.append(
                {"a": a["name"], "b": b["name"], "class": klass(a, b), "jaccard": round(score, 3)}
            )
        sim = {}
        sets = [extract(r[key]) for r in evidence_runs]
        for i, j in combinations(range(len(evidence_runs)), 2):
            sim[(i, j)] = jaccard(sets[i], sets[j])
        entry = {
            group: {
                "pairs": len(values),
                "mean": round(_mean(values), 3) if values else None,
            }
            for group, values in buckets.items()
        }
        entry["verdict"] = _verdict(buckets)
        entry["permutation"] = permutation_p(labels, sim, trials, seed=hash(measure) % 10_000)
        entry["nearest_neighbour_supplementary"] = nearest_neighbour(labels, names, sim)
        entry["pair_detail"] = pairs
        out["measures"][measure] = entry
    return out


def _verdict(buckets: dict[str, list[float]]) -> str:
    replicate, between, control = (
        _mean(buckets["REPLICATE"]),
        _mean(buckets["BETWEEN"]),
        _mean(buckets["CONTROL"]),
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
    rows = []
    for run in runs:
        defect = run["meta"].get("defect", "?")
        if run["meta"].get("no_evidence"):
            defect = "CONTROL"
        found = categories(run[key])
        own = NEIGHBOURHOOD.get(defect, set())
        own_excl = EXCLUSIVE.get(defect, set())
        others = {d: NEIGHBOURHOOD[d] for d in NEIGHBOURHOOD if d != defect}
        rows.append(
            {
                "run": run["name"],
                "defect": defect,
                "wave2_categories": sorted(found),
                "own_neighbourhood_hits": sorted(found & own),
                "own_neighbourhood_hit_rate": (
                    round(len(found & own) / len(found), 3) if found else None
                ),
                "own_exclusive_hits": sorted(found & own_excl),
                "own_exclusive_hit_rate": (
                    round(len(found & own_excl) / len(found), 3) if found else None
                ),
                "other_defect_neighbourhood_hits": {
                    d: sorted(found & cats) for d, cats in others.items() if found & cats
                },
                "other_defect_EXCLUSIVE_hits": {
                    d: sorted(found & EXCLUSIVE[d])
                    for d in others
                    if found & EXCLUSIVE[d]
                },
            }
        )
    scored = [
        r for r in rows if r["defect"] != "CONTROL" and r["own_neighbourhood_hit_rate"] is not None
    ]
    return {
        "per_run": rows,
        "mean_own_neighbourhood_hit_rate": (
            round(sum(r["own_neighbourhood_hit_rate"] for r in scored) / len(scored), 3)
            if scored
            else None
        ),
        "mean_own_exclusive_hit_rate": (
            round(sum(r["own_exclusive_hit_rate"] for r in scored) / len(scored), 3)
            if scored
            else None
        ),
        "runs_reaching_their_own_neighbourhood": sum(
            1 for r in scored if r["own_neighbourhood_hits"]
        ),
        "runs_reaching_their_own_EXCLUSIVE_neighbourhood": sum(
            1 for r in scored if r["own_exclusive_hits"]
        ),
        "runs_reaching_ANOTHER_defects_exclusive_neighbourhood": sum(
            1 for r in scored if r["other_defect_EXCLUSIVE_hits"]
        ),
        "runs_scored": len(scored),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--trials", type=int, default=20000)
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
                "wave2_refused": r["meta"].get("wave2_refused"),
                "wave2_rejections": r["meta"].get("wave2_rejections"),
                "wave2_reasoner_error": r["meta"].get("wave2_reasoner_error"),
                "infrastructure_observations": r["meta"].get("infrastructure_observations"),
                "wave2_seconds": r["meta"].get("wave2_seconds"),
                "wave2_titles": [s.get("title") for s in (r["accepted"] or r["raw"])],
            }
            for r in runs
        ],
        "accepted": report("ACCEPTED wave-2 coverage", runs, "accepted", args.trials),
        "raw": report("RAW model proposals (pre-validation)", runs, "raw", args.trials),
        "targeting_accepted": targeting(runs, "accepted"),
        "targeting_raw": targeting(runs, "raw"),
        "rule": (
            "Responsiveness requires BETWEEN < REPLICATE, with CONTROL lowest, and the "
            "margin must survive a permutation test on the defect labels. Provenance "
            "links alone are not evidence: the content must move."
        ),
    }
    text = json.dumps(result, indent=2)
    (Path(args.out) if args.out else root.parent / "divergence.json").write_text(
        text + "\n", encoding="utf-8"
    )
    print(text[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
