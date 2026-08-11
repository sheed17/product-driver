"""A-QUALITY independent analyzer.

Deliberately NOT the prior campaign's analyze.py. It reuses that file's rejection
classifier and its RELEVANT category sets (so the inherited-task numbers stay
comparable) and then applies this reviewer's own, stricter measures:

  * STRICT grounding — the prior analyzer's `grounded()` only checks that the two
    reference fields are non-empty strings. Here a reference must actually name
    the task's unit id, unit name, or one of the acceptance criteria the brief
    showed, and the principle must be a real founder rubric id.
  * BARE-SUITE, two ways — the prior definition (every command is a broad suite
    AND no product-driving action) and a stricter one (ANY broad-suite command
    appears anywhere in the scenario).
  * ORACLE VACUITY flags — mechanical detectors for oracles that pass whatever the
    product does, or that pass when the probe itself failed to run.
  * USEFUL — the pre-registered U1..U5 conjunction.

Usage: python analyze_cert.py --dir <raw dir> --driver <driver root>
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIOR_ANALYZE = HERE.parent.parent / "post-remediation" / "a-generation-quality" / "analyze.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


PRIOR = _load(PRIOR_ANALYZE, "prior_analyze")

# Pre-registered on-surface sets for the two NEW tasks (see PREREGISTERED-USEFUL.md).
RELEVANT = dict(PRIOR.RELEVANT)
RELEVANT["F"] = {
    "happy_path", "idempotency", "repeated_request", "retry_safety", "partial_failure",
    "service_unavailable", "dependency_failure", "timeout_before_effect",
    "timeout_after_effect", "ambiguous_external_effect", "safety_invariant", "regression",
    "persistence_failure", "restart_recovery", "crash_mid_workflow", "concurrency",
    "missing_data", "unexpected_state_transition", "stale_state",
}
RELEVANT["G"] = {
    "conflicting_evidence", "malformed_input", "missing_data", "boundary", "happy_path",
    "stale_state", "ui_backend_disagreement", "safety_invariant", "regression",
    "unexpected_state_transition", "idempotency", "repeated_request",
    "persistence_failure", "cross_tenant",
}

PRODUCT_DRIVING = {
    "request", "parallel_requests", "browser",
    "restart_service", "stop_service", "start_service",
}

NUMERIC = re.compile(r"^\d{1,3}$")


def all_checks(s: dict) -> list[dict]:
    out = []
    for a in s.get("actions", []):
        if a.get("state_check"):
            out.append(a["state_check"])
    out += list(s.get("persisted_state_checks") or [])
    return out


def vacuity_flags(s: dict) -> list[str]:
    flags: list[str] = []
    for c in all_checks(s):
        cont = c.get("contains") or []
        ncont = c.get("not_contains") or []
        if not cont and ncont:
            flags.append("V1:oracle-passes-if-probe-errors(only not_contains)")
        for n in ncont:
            if NUMERIC.match(str(n)):
                flags.append(f"V2:numeric-substring-trap(not_contains {n!r})")
        for n in cont:
            if NUMERIC.match(str(n)):
                flags.append(f"V3:numeric-substring-oracle(contains {n!r})")
        joined = " ".join(str(x) for x in list(cont) + list(ncont)).lower()
        if joined and all(
            k in joined for k in []
        ):
            pass
        if not cont and ncont and all(
            any(t in str(n).lower() for t in ("no such table", "no such column", "error", "traceback"))
            for n in ncont
        ):
            flags.append("V4:oracle-asserts-only-that-the-probe-did-not-error")
    for n in (s.get("expected_observations") or []):
        if NUMERIC.match(str(n)) or len(str(n)) <= 3:
            flags.append(f"V6:near-vacuous-global-observation({n!r})")
    for a in s.get("actions", []):
        if a.get("kind") == "request":
            r = a.get("request") or {}
            if r.get("expect_status") is None and not (r.get("expect_contains") or []):
                flags.append(f"V5:request-asserts-nothing({r.get('method','?')} {r.get('path','?')})")
        if a.get("kind") == "parallel_requests":
            for r in a.get("requests") or []:
                if r.get("expect_status") is None and not (r.get("expect_contains") or []):
                    flags.append(f"V5:parallel-request-asserts-nothing({r.get('path','?')})")
    return sorted(set(flags))



def surface_diversity(accepted: list[dict]) -> dict:
    """How much of the product does the accepted set actually touch?

    `redundant_shapes` counts identical action-kind signatures. Two scenarios that
    hit the same endpoint with the same oracle but differ by one `wait` action have
    different signatures and are counted distinct. This measures the thing that
    matters instead: distinct endpoints and distinct oracle assertions.
    """
    paths, oracle_cmds, oracle_asserts = set(), set(), set()
    for s in accepted:
        for a in s.get("actions", []):
            if a.get("kind") == "request":
                r = a.get("request") or {}
                paths.add(f"{r.get('method','?')} {str(r.get('path','')).split('?')[0]}")
            if a.get("kind") == "parallel_requests":
                for r in a.get("requests") or []:
                    paths.add(f"{r.get('method','?')} {str(r.get('path','')).split('?')[0]}")
        for c in all_checks(s):
            oracle_cmds.add(str(c.get("command", "")))
            oracle_asserts.add(
                str(sorted(c.get("contains") or [])) + "|" + str(sorted(c.get("not_contains") or []))
            )
    return {
        "distinct_request_endpoints": sorted(paths),
        "n_distinct_request_endpoints": len(paths),
        "n_distinct_oracle_commands": len(oracle_cmds),
        "n_distinct_oracle_assertions": len(oracle_asserts),
    }


def strict_grounding(s: dict, unit, principles: set[str]) -> tuple[bool, bool, str]:
    """(requirement_ok, principle_ok, note). No emptiness-only shortcuts."""
    ref = str(s.get("requirement_reference") or "").strip().lower()
    prin = str(s.get("product_principle_reference") or "").strip().lower()
    tokens: set[str] = set()
    if unit is not None:
        uid = str(getattr(unit, "unit_id", "") or "").lower()
        if uid:
            tokens.add(uid)
        nm = str(getattr(unit, "name", "") or "").lower()
        if len(nm) >= 4:
            tokens.add(nm)
        for c in getattr(unit, "acceptance_criteria", None) or []:
            if isinstance(c, dict):
                lab = str(c.get("criterion", "") or "").lower()
                if len(lab) >= 4:
                    tokens.add(lab)
    req_ok = bool(ref) and any(t in ref for t in tokens)
    note = ""
    if not req_ok and ref:
        m = re.search(r"\bAC-[A-Z]+-\d+\b", str(s.get("requirement_reference")))
        if m:
            req_ok = False
            note = f"cites AC id {m.group(0)!r} that is not among the criteria it was shown"
        else:
            note = "requirement_reference names nothing the brief supplied"
    prin_ok = bool(prin) and any(p in prin for p in principles)
    return req_ok, prin_ok, note



_STOP = {"never", "always", "every", "which", "there", "their", "other", "after",
         "before", "while", "shows", "state", "result", "results", "reason",
         "recorded", "record", "records", "changes", "change", "yields", "given"}


def task_vocabulary(spec) -> set[str]:
    """Words >=5 letters that the TASK STATEMENT and the unit itself use. Derived
    from the fixture, not chosen by the reviewer."""
    unit = spec.get("unit")
    text = str(spec.get("task", ""))
    if unit is not None:
        text += " " + str(getattr(unit, "name", "")) + " " + str(getattr(unit, "objective", ""))
    return {w for w in re.findall(r"[a-z]{5,}", text.lower())} - _STOP


def misattribution_terms(s: dict) -> list[str]:
    """Words of >=5 letters in the cited requirement that appear NOWHERE in what the
    scenario actually does. Objective proxy for 'the grounding reference names a
    concept this scenario never touches'. Conservative: a term is only counted when
    it is absent from title, purpose, rationale, every request path and body, every
    command, and every expectation literal.
    """
    ref = str(s.get("requirement_reference") or "").lower()
    terms = {w for w in re.findall(r"[a-z]{5,}", ref)} - _STOP
    if not terms:
        return []
    hay = " ".join([
        str(s.get("title", "")), str(s.get("purpose", "")), str(s.get("rationale", "")),
        " ".join(PRIOR.commands_of(s)),
        " ".join(str(x) for x in (s.get("expected_observations") or [])),
        " ".join(str(x) for x in (s.get("forbidden_observations") or [])),
    ])
    for a in s.get("actions", []):
        reqs = ([a.get("request")] if a.get("request") else []) + list(a.get("requests") or [])
        for r in reqs:
            hay += " " + str(r.get("path", "")) + " " + json.dumps(r.get("json_body") or {}) \
                   + " " + " ".join(str(x) for x in (r.get("expect_contains") or []))
    hay = hay.lower()
    return sorted(t for t in terms if t not in hay)


def useful(s: dict, key: str, seen_shapes: set[str]) -> tuple[bool, list[str]]:
    fails: list[str] = []
    if not PRIOR.mechanically_scorable(s):
        fails.append("U1 no executable oracle")
    kinds = set(PRIOR.action_kinds(s))
    if not (kinds & PRODUCT_DRIVING) and not any(
        c.get("command") for c in all_checks(s)
    ):
        fails.append("U3 does not drive the product")
    if PRIOR.is_bare_suite_invocation(s):
        fails.append("U3 bare suite invocation")
    rel = RELEVANT.get(key, set())
    if rel and s.get("risk_category") not in rel:
        fails.append(f"U4 off-surface category {s.get('risk_category')}")
    sig = PRIOR.shape(s)
    if sig in seen_shapes:
        fails.append("U5 duplicate shape")
    return (not fails), fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--driver", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    root = Path(args.dir).resolve()
    driver = Path(args.driver).resolve()
    sys.path.insert(0, str(driver))

    harness = _load(HERE / "run_cert_gen.py", "cert_harness")
    prior_gen = harness._load_prior()
    tasks = prior_gen.build_tasks()
    tasks.update(harness.new_tasks())

    from neyma_product_driver.context import load_founder_context
    from neyma_product_driver.scenario_validation import principle_tokens_from

    founder = load_founder_context(driver)
    principles = {p.lower() for p in principle_tokens_from(founder)}

    rows = []
    agg_reject: dict[str, int] = {}
    all_useful = 0
    all_accepted = 0
    all_proposed = 0
    all_flags: dict[str, int] = {}
    infra = {"waves_total": 0, "waves_failed": 0, "waves_empty_payload": 0,
             "waves_zero_scenarios": 0, "errors": []}

    for plan_path in sorted(root.glob("plan-*.json")):
        tag = plan_path.stem[len("plan-"):]
        key = tag.split("-")[0]
        unit = tasks.get(key, {}).get("unit")
        plan = json.loads(plan_path.read_text())
        raw_path = root / f"raw-{tag}.json"
        raw = json.loads(raw_path.read_text()) if raw_path.exists() else []
        tel_path = root / f"telemetry-{tag}.json"
        tel = json.loads(tel_path.read_text()) if tel_path.exists() else {}

        proposed = sum(len(p.get("scenarios", [])) for p in raw if isinstance(p, dict))
        accepted = plan["scenarios"]
        rejected = [r for w in plan["waves"] for r in w["rejected"]]

        for w in tel.get("waves", []):
            infra["waves_total"] += 1
            if w.get("exception"):
                infra["waves_failed"] += 1
                infra["errors"].append({"tag": tag, "wave": w["wave_index"], "error": w["exception"]})
            elif w.get("payload_kind") != "dict":
                infra["waves_empty_payload"] += 1
                infra["errors"].append({"tag": tag, "wave": w["wave_index"], "error": f"payload {w.get('payload_kind')}"})
            elif w.get("n_scenarios", 0) == 0:
                infra["waves_zero_scenarios"] += 1

        primary: dict[str, int] = {}
        full_reasons: list[dict] = []
        for r in rejected:
            kind = PRIOR.classify(r["reasons"][0]) if r["reasons"] else "unknown"
            primary[kind] = primary.get(kind, 0) + 1
            agg_reject[kind] = agg_reject.get(kind, 0) + 1
            full_reasons.append({"id": r.get("id"), "title": r.get("title"), "reasons": r.get("reasons")})

        seen: set[str] = set()
        per_scenario = []
        n_useful = 0
        for s in accepted:
            ok, fails = useful(s, key, seen)
            seen.add(PRIOR.shape(s))
            req_ok, prin_ok, note = strict_grounding(s, unit, principles)
            flags = vacuity_flags(s)
            for f in flags:
                base = f.split("(")[0]
                all_flags[base] = all_flags.get(base, 0) + 1
            if not (req_ok and prin_ok):
                ok = False
                fails.append("G1 fabricated/ungrounded reference: " + (note or f"principle {s.get('product_principle_reference')!r}"))
            if ok:
                n_useful += 1
            core_ok = not [f for f in fails if not f.startswith("U4")]
            per_scenario.append({
                "id": s["id"],
                "title": s["title"],
                "risk_category": s["risk_category"],
                "priority": s.get("priority"),
                "action_kinds": PRIOR.action_kinds(s),
                "requirement_reference": s.get("requirement_reference"),
                "product_principle_reference": s.get("product_principle_reference"),
                "strict_requirement_grounded": req_ok,
                "strict_principle_grounded": prin_ok,
                "grounding_note": note,
                "bare_suite_prior_definition": PRIOR.is_bare_suite_invocation(s),
                "contains_broad_suite_command": any(
                    PRIOR.BROAD_SUITE.search(c) for c in PRIOR.commands_of(s)
                ),
                "mechanically_scorable": PRIOR.mechanically_scorable(s),
                "inspects_persisted_state": PRIOR.inspects_persisted_state(s),
                "vacuity_flags": flags,
                "cited_terms_absent_from_scenario": misattribution_terms(s),
                "absent_terms_central_to_task": sorted(
                    set(misattribution_terms(s)) & task_vocabulary(tasks.get(key, {}))),
                "USEFUL": ok,
                "USEFUL_ignoring_U4": core_ok,
                "useful_failures": fails,
            })

        cats = [s["risk_category"] for s in accepted]
        rel = RELEVANT.get(key, set())
        off = [c for c in cats if rel and c not in rel]
        sigs = [PRIOR.shape(s) for s in accepted]
        eff = [s for s in accepted if s["risk_category"] in PRIOR.EFFECT_FAMILY]

        all_useful += n_useful
        all_accepted += len(accepted)
        all_proposed += proposed

        rows.append({
            "tag": tag,
            "task_key": key,
            "inherited": key in {"A", "A2", "B", "C", "D", "E"},
            "elapsed_s": tel.get("elapsed_s"),
            "proposed": proposed,
            "accepted": len(accepted),
            "USEFUL": n_useful,
            "USEFUL_ignoring_U4": sum(1 for r in per_scenario if r["USEFUL_ignoring_U4"]),
            "rejected": len(rejected),
            "zero_coverage": n_useful == 0,
            "rejection_reasons": primary,
            "accepted_categories": sorted(set(cats)),
            "off_topic_categories": sorted(set(off)),
            "redundant_shapes": len(sigs) - len(set(sigs)),
            **surface_diversity(accepted),
            "bare_suite_prior_definition": sum(1 for s in accepted if PRIOR.is_bare_suite_invocation(s)),
            "contains_broad_suite_command": sum(
                1 for s in accepted if any(PRIOR.BROAD_SUITE.search(c) for c in PRIOR.commands_of(s))
            ),
            "strict_grounded": sum(1 for r in per_scenario if r["strict_requirement_grounded"] and r["strict_principle_grounded"]),
            "prior_grounded_definition": sum(1 for s in accepted if PRIOR.grounded(s)),
            "mechanically_scorable": sum(1 for s in accepted if PRIOR.mechanically_scorable(s)),
            "scenarios_with_vacuity_flag": sum(1 for r in per_scenario if r["vacuity_flags"]),
            "scenarios_whose_cited_requirement_names_absent_concepts": sum(
                1 for r in per_scenario if r["cited_terms_absent_from_scenario"]),
            "scenarios_missing_a_TASK_CENTRAL_concept": sum(
                1 for r in per_scenario if r["absent_terms_central_to_task"]),
            "effect_family_cases": len(eff),
            "effect_family_with_state_oracle": sum(1 for s in eff if PRIOR.inspects_persisted_state(s)),
            "generation_problems": tel.get("generation_problems", []),
            "scenarios": per_scenario,
            "rejections_full": full_reasons,
        })

    report = {
        "aggregate": {
            "runs": len(rows),
            "proposed": all_proposed,
            "accepted": all_accepted,
            "useful": all_useful,
            "useful_ignoring_U4": sum(r["USEFUL_ignoring_U4"] for r in rows),
            "acceptance_rate": round(all_accepted / all_proposed, 3) if all_proposed else None,
            "useful_rate_of_accepted": round(all_useful / all_accepted, 3) if all_accepted else None,
            "zero_coverage_runs": [r["tag"] for r in rows if r["zero_coverage"]],
            "rejection_taxonomy": dict(sorted(agg_reject.items(), key=lambda kv: -kv[1])),
            "vacuity_flag_counts": dict(sorted(all_flags.items(), key=lambda kv: -kv[1])),
            "bare_suite_prior_definition": sum(r["bare_suite_prior_definition"] for r in rows),
            "contains_broad_suite_command": sum(r["contains_broad_suite_command"] for r in rows),
            "strict_grounded": sum(r["strict_grounded"] for r in rows),
            "cited_requirement_names_absent_concepts": sum(
                r["scenarios_whose_cited_requirement_names_absent_concepts"] for r in rows),
            "scenarios_missing_a_TASK_CENTRAL_concept": sum(
                r["scenarios_missing_a_TASK_CENTRAL_concept"] for r in rows),
            "prior_grounded_definition": sum(r["prior_grounded_definition"] for r in rows),
            "effect_family_cases": sum(r["effect_family_cases"] for r in rows),
            "effect_family_with_state_oracle": sum(r["effect_family_with_state_oracle"] for r in rows),
            "infrastructure": infra,
        },
        "per_run": rows,
    }
    text = json.dumps(report, indent=2)
    (Path(args.out) if args.out else HERE / "metrics-cert.json").write_text(text + "\n", encoding="utf-8")
    print(json.dumps(report["aggregate"], indent=2))
    for r in rows:
        print(f"{r['tag']:10s} proposed={r['proposed']:3d} accepted={r['accepted']:3d} "
              f"useful={r['USEFUL']:3d}(+U4-free {r['USEFUL_ignoring_U4']:2d}) rejected={r['rejected']:3d} "
              f"vacuity={r['scenarios_with_vacuity_flag']:3d} {r['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
