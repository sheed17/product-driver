"""Render divergence.json as the markdown tables the finding needs."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def fmt(v):
    return "n/a" if v is None else f"{v:.3f}"


def main() -> int:
    data = json.loads(Path(sys.argv[1]).read_text())
    out: list[str] = []
    for pop in ("raw", "accepted"):
        block = data[pop]
        out.append(f"### {block['population']}  (runs contributing: {block['runs_contributing']})\n")
        out.append(
            "| measure | REPLICATE | BETWEEN | CONTROL | BETWEEN-REPLICATE | perm p | NN same-defect | NN chance | verdict |"
        )
        out.append("|---|---|---|---|---|---|---|---|---|")
        for name, m in block["measures"].items():
            perm = m["permutation"]
            nn = m["nearest_neighbour_supplementary"]
            verdict = (
                "PASS" if m["verdict"].startswith("RESPONSIVE —")
                else "WEAK" if m["verdict"].startswith("RESPONSIVE but weak")
                else "FAIL" if m["verdict"].startswith("NOT RESPONSIVE")
                else "n/a"
            )
            out.append(
                f"| `{name}` | {fmt(m['REPLICATE']['mean'])} (n={m['REPLICATE']['pairs']}) "
                f"| {fmt(m['BETWEEN']['mean'])} (n={m['BETWEEN']['pairs']}) "
                f"| {fmt(m['CONTROL']['mean'])} (n={m['CONTROL']['pairs']}) "
                f"| {fmt(perm['observed_diff'])} | {fmt(perm['p'])} "
                f"| {nn['hits']}/{nn['n']} ({fmt(nn['rate'])}) | {fmt(nn['chance_rate'])} | {verdict} |"
            )
        out.append("")
    for key in ("targeting_raw", "targeting_accepted"):
        t = data[key]
        out.append(f"### {key}\n")
        out.append(
            f"- mean own-neighbourhood hit rate: **{fmt(t['mean_own_neighbourhood_hit_rate'])}**\n"
            f"- mean own-EXCLUSIVE hit rate: **{fmt(t['mean_own_exclusive_hit_rate'])}**\n"
            f"- runs reaching their own neighbourhood: "
            f"{t['runs_reaching_their_own_neighbourhood']}/{t['runs_scored']}\n"
            f"- runs reaching their own EXCLUSIVE neighbourhood: "
            f"{t['runs_reaching_their_own_EXCLUSIVE_neighbourhood']}/{t['runs_scored']}\n"
            f"- runs reaching ANOTHER defect's exclusive neighbourhood: "
            f"{t['runs_reaching_ANOTHER_defects_exclusive_neighbourhood']}/{t['runs_scored']}\n"
        )
        out.append("| run | defect | wave-2 categories | own hits | own-exclusive hits | other-defect exclusive hits |")
        out.append("|---|---|---|---|---|---|")
        for r in t["per_run"]:
            out.append(
                f"| {r['run']} | {r['defect']} | {', '.join(r['wave2_categories']) or '—'} "
                f"| {', '.join(r['own_neighbourhood_hits']) or '—'} "
                f"| {', '.join(r['own_exclusive_hits']) or '—'} "
                f"| {json.dumps(r['other_defect_EXCLUSIVE_hits']) if r['other_defect_EXCLUSIVE_hits'] else '—'} |"
            )
        out.append("")
    out.append("### runs\n")
    out.append("| run | defect | evidence withheld | wave-1 failures | proposed | accepted | refused | wave-2 s | infra |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for r in data["runs"]:
        infra = ", ".join(i["kind"] for i in (r.get("infrastructure_observations") or [])) or "—"
        out.append(
            f"| {r['name']} | {r['defect']} | {r['no_evidence']} | "
            f"{', '.join(r['wave1_failures'] or []) or '—'} | {r['wave2_proposed']} | "
            f"{r['wave2_accepted']} | {r.get('wave2_refused')} | {r.get('wave2_seconds')} | {infra} |"
        )
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
