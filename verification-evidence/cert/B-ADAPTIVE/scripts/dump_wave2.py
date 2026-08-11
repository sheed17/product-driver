"""Dump every run's RAW wave-2 population in readable form, for hand-reading.

  python dump_wave2.py <runs-root>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def render_actions(scenario: dict) -> list[str]:
    lines: list[str] = []
    for action in scenario.get("actions") or []:
        kind = action.get("kind")
        if kind == "request" and action.get("request"):
            r = action["request"]
            lines.append(
                f"    - {r.get('method')} {r.get('path')} body={json.dumps(r.get('json_body'))} "
                f"expect_status={r.get('expect_status')} expect_contains={r.get('expect_contains') or []}"
            )
        elif kind == "parallel_requests":
            for r in action.get("requests") or []:
                lines.append(
                    f"    - [parallel] {r.get('method')} {r.get('path')} "
                    f"body={json.dumps(r.get('json_body'))} expect_status={r.get('expect_status')}"
                )
        elif kind == "state_check" and action.get("state_check"):
            c = action["state_check"]
            lines.append(
                f"    - STATE `{c.get('command')}` contains={c.get('contains')} "
                f"not_contains={c.get('not_contains')}"
            )
        else:
            lines.append(
                f"    - {kind} service={action.get('service') or '-'} "
                f"wait_ms={action.get('wait_ms')} command={action.get('command') or '-'}"
            )
    for c in scenario.get("persisted_state_checks") or []:
        lines.append(
            f"    ORACLE `{c.get('command')}` contains={c.get('contains')} "
            f"not_contains={c.get('not_contains')}"
        )
    if scenario.get("expected_observations"):
        lines.append(f"    EXPECT {scenario['expected_observations']}")
    if scenario.get("forbidden_observations"):
        lines.append(f"    FORBID {scenario['forbidden_observations']}")
    return lines


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    out: list[str] = []
    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue
        meta_path = run_dir / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        raw_path = run_dir / "wave2-raw-payload.json"
        payload = json.loads(raw_path.read_text()) if raw_path.exists() else None
        accepted_ids = {
            s["id"] for s in json.loads((run_dir / "wave2-scenarios.json").read_text())
        } if (run_dir / "wave2-scenarios.json").exists() else set()
        out.append(
            f"\n## {run_dir.name}  —  defect={meta.get('defect')} "
            f"evidence_withheld={meta.get('no_evidence')} "
            f"wave1_failures={meta.get('wave1_failures')} "
            f"accepted={meta.get('wave2_accepted')} refused={meta.get('wave2_refused')}"
        )
        if meta.get("wave2_reasoner_error"):
            out.append(f"  REASONER ERROR: {meta['wave2_reasoner_error']}")
        if not isinstance(payload, dict):
            out.append("  (no raw payload — the generation session produced nothing)")
            continue
        for s in payload.get("scenarios", []):
            mark = "ACCEPTED" if s.get("id") in accepted_ids else "REFUSED/renamed"
            out.append(
                f"\n  [{s.get('priority')} {s.get('risk_category')}] {s.get('id')} ({mark})\n"
                f"    title: {s.get('title')}\n"
                f"    generating_risk: {s.get('generating_risk')}\n"
                f"    source_failures: {s.get('source_failures')} "
                f"source_clusters: {s.get('source_clusters')}\n"
                f"    purpose: {s.get('purpose')}\n"
                f"    rationale: {s.get('rationale')}"
            )
            out.extend(render_actions(s))
    text = "\n".join(out)
    (root.parent / "WAVE2-POPULATIONS.md").write_text(
        "# Raw wave-2 populations (pre-validation), every run\n" + text + "\n"
    )
    print(f"wrote {root.parent / 'WAVE2-POPULATIONS.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
