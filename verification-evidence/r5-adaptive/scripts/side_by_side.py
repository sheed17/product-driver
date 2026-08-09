"""Render wave 1 and wave 2 side by side per experiment, for the adjudicator."""

from __future__ import annotations

import json
from pathlib import Path

RUNS = [
    ("EXPERIMENT 1 -- seeded defect: approval is NOT idempotent", "r5/raw/real1"),
    ("EXPERIMENT 1 -- REPLICATE a (identical inputs)", "r5/raw/real1b"),
    ("EXPERIMENT 1 -- REPLICATE b (identical inputs)", "r5/raw/real1c"),
    ("EXPERIMENT 2 -- seeded defect: UI/API says success, durable store never written",
     "r5/raw/real2"),
    ("EXPERIMENT 2 -- REPLICATE a", "r5/raw/real2b"),
    ("EXPERIMENT 2 -- REPLICATE b", "r5/raw/real2c"),
    ("EXPERIMENT 3 -- seeded defect: effect lands, then the call times out (uncertain outcome)",
     "r5/raw/real3"),
    ("EXPERIMENT 3 -- REPLICATE a", "r5/raw/real3b"),
    ("EXPERIMENT 3 -- REPLICATE b", "r5/raw/real3c"),
    ("EXPERIMENT 3 -- REPLICATE c", "r5/raw/real3d"),
    ("CONTROL -- identical task and wave 1, FAILURE EVIDENCE REMOVED", "r5/raw/control"),
]


def render_scenario(s: dict, indent: str = "    ") -> list[str]:
    lines = [f"{indent}[{s.get('priority')} {s.get('risk_category')}] {s.get('id')}",
             f"{indent}  title    : {s.get('title')}",
             f"{indent}  purpose  : {s.get('purpose','')}"]
    if s.get("rationale"):
        lines.append(f"{indent}  rationale: {s['rationale']}")
    for a in s.get("actions", []):
        kind = a.get("kind")
        if kind == "request" and a.get("request"):
            r = a["request"]
            lines.append(f"{indent}  action   : {r.get('method')} {r.get('path')} "
                         f"expect {r.get('expect_status')} timeout={r.get('timeout_s')}")
        elif kind == "parallel_requests":
            for r in a.get("requests", []):
                lines.append(f"{indent}  action   : PARALLEL {r.get('method')} {r.get('path')}")
        elif kind == "state_check" and a.get("state_check"):
            c = a["state_check"]
            lines.append(f"{indent}  action   : state_check `{c.get('command')}` "
                         f"contains={c.get('contains')} not_contains={c.get('not_contains')}")
        else:
            lines.append(f"{indent}  action   : {kind} {a.get('service','')}{a.get('wait_ms','')}")
    for c in s.get("persisted_state_checks", []):
        lines.append(f"{indent}  oracle   : `{c.get('command')}` contains={c.get('contains')} "
                     f"not_contains={c.get('not_contains')}")
    prov = s.get("provenance") or {}
    if prov:
        lines.append(f"{indent}  because  : generating_risk={prov.get('generating_risk')!r} "
                     f"stage={prov.get('stage')} wave={prov.get('wave')}")
    return lines


def main() -> int:
    out: list[str] = []
    for label, path in RUNS:
        base = Path(path)
        out += ["", "=" * 78, label, f"evidence: {base.resolve()}", "=" * 78]

        failures = base / "wave1-failures.txt"
        out += ["", "-- WAVE 1 (identical scripted batch in every run) --"]
        w1 = base / "wave1-scenarios.json"
        if w1.exists():
            for s in json.loads(w1.read_text()):
                out += render_scenario(s)
        else:
            out += ["    (control run: wave 1 planned but not executed)"]

        out += ["", "-- WHAT WAVE 1 OBSERVED (the evidence handed to wave 2) --"]
        out += ["    " + (failures.read_text().strip() or "(no failures)")
                if failures.exists() else "    (no failures -- evidence deliberately withheld)"]

        clusters = base / "wave1-clusters.txt"
        if clusters.exists() and clusters.read_text().strip():
            out += ["", "-- FAILURE CLUSTERS SHOWN TO WAVE 2 --"]
            out += ["    " + line for line in clusters.read_text().splitlines()]

        out += ["", "-- WAVE 2 (adaptive, real model) ACCEPTED --"]
        w2 = base / "wave2-scenarios.json"
        accepted = json.loads(w2.read_text()) if w2.exists() else []
        if accepted:
            for s in accepted:
                out += render_scenario(s)
        else:
            out += ["    NONE ACCEPTED -- every proposal was refused by validation."]

        rawp = base / "wave2-raw-payload.json"
        if rawp.exists():
            payload = json.loads(rawp.read_text())
            proposed = payload.get("scenarios", []) if isinstance(payload, dict) else []
            if len(proposed) != len(accepted):
                out += ["", f"-- WAVE 2 RAW MODEL PROPOSALS ({len(proposed)}) --"]
                for s in proposed:
                    out += [f"    [{s.get('risk_category')}] {s.get('title')}"]

    text = "\n".join(out)
    Path("r5/raw/wave1-vs-wave2-side-by-side.txt").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
