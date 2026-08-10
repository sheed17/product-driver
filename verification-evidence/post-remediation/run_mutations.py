#!/usr/bin/env python3
"""Mutation testing over the post-remediation system.

Reuses the remediation runner's machinery and its eighteen mutations verbatim —
re-running them is the point, since this pass changed the gate, the suite, the
executor and the plan models, and a previously-caught mutation that now survives
would be a regression in the tests rather than in the code.

Added here, one per residual closed in this pass:

  P1-P4   scenario id collision (D)
  P5-P8   uncovered required risk reaching the evaluator and the gate (C)
  P9      max_parallel honesty (E)
  P10     the run journal (F)
  P11-P12 browser expect_text as a real oracle (G)

Each mutation removes one requirement. A suite worth trusting fails for all of
them.

    .venv/bin/python verification-evidence/post-remediation/run_mutations.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

_spec = importlib.util.spec_from_file_location(
    "remediation_mutations", REPO / "verification-evidence" / "remediation" / "run_mutations.py"
)
assert _spec and _spec.loader
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)


# M5's anchor moved when `_apply_suite_precedence` began passing the risk
# register to the gate. The mutation itself is unchanged: skip the gate's
# verdict entirely and return the evaluator's ACCEPT.
ORIGINAL = [
    m
    if m[0] != "M5"
    else (
        "M5",
        "ignore generated scenario failure during acceptance",
        "neyma_product_driver/cli.py",
        "    verdict = evaluate_gate(\n"
        "        suite_result, generation_problems=generation_problems, risks=risks\n"
        "    )\n",
        "    verdict = evaluate_gate(\n"
        "        suite_result, generation_problems=generation_problems, risks=risks\n"
        "    )\n    return decision\n",
    )
    for m in base.MUTATIONS
]


NEW: list[tuple[str, str, str, str, str]] = [
    # ---- D: scenario id collision -------------------------------------
    (
        "P1",
        "scenario ids truncated again, so two long proposals become one",
        "neyma_product_driver/scenario_plan.py",
        "        identity = shorten_preserving_identity(cleaned, SCENARIO_ID_LIMIT)\n",
        "        identity = cleaned[:SCENARIO_ID_LIMIT]\n",
    ),
    (
        "P2",
        "evidence directory names truncated again, so two scenarios share one",
        "neyma_product_driver/evidence.py",
        "    return shorten_preserving_identity(cleaned, FILENAME_LIMIT)\n",
        "    return cleaned[:FILENAME_LIMIT]\n",
    ),
    (
        "P3",
        "a duplicate suite id is dropped silently again",
        "neyma_product_driver/scenario_suite.py",
        "        if self.by_id(entry.scenario_id) is not None:\n            return False\n",
        "        if self.by_id(entry.scenario_id) is not None:\n            return True\n",
    ),
    (
        "P4",
        "assembly conflicts never reach the acceptance gate",
        "neyma_product_driver/scenario_gate.py",
        "        problems += [p for p in result.assembly_problems if str(p).strip()]\n",
        "        problems += []\n",
    ),
    # ---- C: uncovered required risk -----------------------------------
    (
        "P5",
        "uncovered required risks never computed (the detector returns nothing)",
        "neyma_product_driver/scenario_gate.py",
        "    if not risks:\n        return []\n",
        "    return []\n    if not risks:\n        return []\n",
    ),
    (
        "P6",
        "a coverage gap no longer blocks acceptance",
        "neyma_product_driver/scenario_gate.py",
        "        if not unverified and not problems and not gaps\n",
        "        if not unverified and not problems\n",
    ),
    (
        "P7",
        "the control loop stops handing the risk register to the gate",
        "neyma_product_driver/cli.py",
        "                risks=_identified_risks(planner),\n",
        "                risks=(),\n",
    ),
    (
        "P8",
        "the evaluator is no longer shown the coverage gaps",
        "neyma_product_driver/cli.py",
        "            coverage_gaps=_coverage_gap_briefs(planner, suite_result),\n",
        "            coverage_gaps=[],\n",
    ),
    # ---- E: max_parallel honesty --------------------------------------
    (
        "P9",
        "the executor silently coerces max_parallel again instead of refusing",
        "neyma_product_driver/scenario_suite.py",
        "        if int(max_parallel) != self.MAX_PARALLEL:\n",
        "        if False:\n",
    ),
    # ---- F: the run journal -------------------------------------------
    (
        "P10",
        "the run journal reads a field IterationRecord does not have",
        "neyma_product_driver/cli.py",
        "    scenario = getattr(record, \"scenario\", None)\n    if scenario is None:\n        return []\n",
        "    scenario = record.commands\n    if scenario is None:\n        return []\n",
    ),
    # ---- G: browser expect_text ---------------------------------------
    (
        "P11",
        "browser expect_text goes back to narration nobody scores",
        "neyma_product_driver/scenarios.py",
        "                self._assert_browser_text(result, obs)\n",
        "",
    ),
    (
        "P12",
        "browser expect_text results are no longer recorded structurally",
        "neyma_product_driver/scenarios.py",
        "                obs.text_expectations.append(\n",
        "                [].append(\n",
    ),
]

MUTATIONS = ORIGINAL + NEW


#: Run the two contract files first, then the rest of the suite. A mutation the
#: contracts catch then costs seconds instead of a full suite run — the same
#: verdict, an order of magnitude less wall clock. `-x` still means the first
#: failure anywhere ends the run, so nothing is skipped when a mutation
#: survives the contracts.
CONTRACT_FIRST = [
    "tests/test_post_remediation_contract.py",
    "tests/test_remediation_contract.py",
]


def run_suite_fast(timeout: int = 1200) -> tuple[bool, str]:
    """Return (caught, tail). caught=True means the suite failed, as it should."""
    import subprocess
    import time

    started = time.time()
    tails: list[str] = []
    for stage in (CONTRACT_FIRST, []):
        proc = subprocess.run(
            [base.PYTHON, "-m", "pytest", "-x", "-q", "--no-header",
             "-p", "no:cacheprovider", *stage],
            cwd=base.WORK,
            env={**os.environ, "PYTHONPATH": str(base.WORK)},
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        lines = [ln for ln in output.strip().splitlines() if ln.strip()]
        failing = [ln for ln in lines if ln.startswith("FAILED") or " failed" in ln]
        tails.append("\n".join(failing[-4:] or lines[-3:]))
        if proc.returncode != 0:
            elapsed = time.time() - started
            where = "contracts" if stage else "full suite"
            return True, f"[{elapsed:.0f}s, caught by the {where}] {tails[-1]}"
    return False, f"[{time.time() - started:.0f}s] {tails[-1]}"


def main() -> int:
    only = set(sys.argv[1:])
    print(f"staging a clean copy under {base.SCRATCH} ...", flush=True)
    base.prepare_baseline()

    results = []
    for mid, description, path, old, new in MUTATIONS:
        if only and mid not in only:
            continue
        print(f"\n=== {mid}: {description} ===", flush=True)
        base.reset_work()
        problem = base.apply_mutation(path, old, new)
        if problem:
            print(f"    COULD NOT APPLY — {problem}", flush=True)
            results.append(
                {
                    "id": mid,
                    "description": description,
                    "status": "COULD_NOT_APPLY",
                    "detail": problem,
                }
            )
            continue
        try:
            caught, tail = run_suite_fast()
        except Exception as exc:
            caught, tail = False, f"{type(exc).__name__}: {exc}"
        print(f"    {'CAUGHT' if caught else 'SURVIVED — TESTS STAYED GREEN'}", flush=True)
        print("    " + tail.replace("\n", "\n    ")[:1200], flush=True)
        results.append(
            {
                "id": mid,
                "description": description,
                "file": path,
                "status": "CAUGHT" if caught else "SURVIVED",
                "detail": tail,
            }
        )

    caught = sum(1 for r in results if r["status"] == "CAUGHT")
    print(f"\n{caught}/{len(results)} mutations caught")
    (HERE / "mutation-results.json").write_text(
        json.dumps(
            {"caught": caught, "total": len(results), "results": results}, indent=2
        ),
        encoding="utf-8",
    )
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
