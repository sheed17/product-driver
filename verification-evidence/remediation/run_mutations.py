#!/usr/bin/env python3
"""Mutation testing over the remediated system.

Each mutation removes one architectural requirement. A suite worth trusting
fails for every one of them. Runs against an isolated copy of the repository, so
nothing here can disturb the working tree.

M1-M8 are the original eight from the independent verification (6/8 were caught
then). N1-N10 cover the boundaries this remediation repaired.

    .venv/bin/python verification-evidence/remediation/run_mutations.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path(os.environ.get("MUTATION_SCRATCH", "/tmp/npd-mutations"))
BASELINE = SCRATCH / "baseline"
WORK = SCRATCH / "work"
PYTHON = str(REPO / ".venv" / "bin" / "python")

# (id, description, file, old_fragment, new_fragment)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    # ---- the original eight -------------------------------------------
    (
        "M1", "disable adaptive generation (expand_after_failures becomes a no-op)",
        "neyma_product_driver/scenario_planner.py",
        '        """Stage 3 — a failure implies a family of situations worth exercising."""\n',
        '        """Stage 3 — a failure implies a family of situations worth exercising."""\n        return self.plan\n',
    ),
    (
        "M2", "duplicate detection always reports 'not a duplicate'",
        "neyma_product_driver/scenario_plan.py",
        '        """Stable identity for duplicate detection. Ignores prose."""\n',
        '        """Stable identity for duplicate detection. Ignores prose."""\n        return f"unique:{id(self)}"\n',
    ),
    (
        "M3", "failed scenarios report PASSED",
        "neyma_product_driver/scenario_suite.py",
        "        elif result.passed:\n            outcome = Outcome.PASSED\n        else:\n            outcome = Outcome.FAILED\n",
        "        else:\n            outcome = Outcome.PASSED\n",
    ),
    (
        "M4", "remove safety validation of generated scenarios",
        "neyma_product_driver/scenario_validation.py",
        "def _check_safety(generated: GeneratedScenario, context: ValidationContext) -> list[str]:\n    reasons: list[str] = []\n",
        "def _check_safety(generated: GeneratedScenario, context: ValidationContext) -> list[str]:\n    reasons: list[str] = []\n    return reasons\n",
    ),
    (
        "M5", "ignore generated scenario failure during acceptance",
        "neyma_product_driver/cli.py",
        "    verdict = evaluate_gate(suite_result, generation_problems=generation_problems)\n",
        "    verdict = evaluate_gate(suite_result, generation_problems=generation_problems)\n    return decision\n",
    ),
    (
        "M6", "remove provenance derivation from generated scenarios",
        "neyma_product_driver/scenario_generator.py",
        '    """Build the provenance stamp every scenario in a wave shares."""\n    return ScenarioProvenance(\n',
        '    """Build the provenance stamp every scenario in a wave shares."""\n    return ScenarioProvenance()\n    return ScenarioProvenance(\n',
    ),
    (
        "M7", "no failure ever blocks acceptance (blocking_failures always empty)",
        "neyma_product_driver/scenario_suite.py",
        '        """Failures that must prevent an ACCEPT."""\n        return [o for o in self.outcomes if o.blocks_acceptance]\n',
        '        """Failures that must prevent an ACCEPT."""\n        return []\n',
    ),
    (
        "M8", "everything_required_passed forced True",
        "neyma_product_driver/scenario_suite.py",
        "        from .scenario_gate import evaluate_gate\n\n        return not evaluate_gate(self).blocks_acceptance\n",
        "        return True\n",
    ),
    # ---- the repaired boundaries --------------------------------------
    (
        "N1", "real reasoner never invoked (propose returns nothing)",
        "neyma_product_driver/scenario_generator.py",
        "        return run_coroutine_blocking(\n            self._session(brief.render()), timeout_s=self.timeout_s + 60\n        )\n",
        "        return None\n",
    ),
    (
        "N2", "reasoner exception silently converted to an empty generation",
        "neyma_product_driver/scenario_generator.py",
        "        return run_coroutine_blocking(\n            self._session(brief.render()), timeout_s=self.timeout_s + 60\n        )\n",
        "        try:\n            return run_coroutine_blocking(\n                self._session(brief.render()), timeout_s=self.timeout_s + 60\n            )\n        except Exception:\n            return None\n",
    ),
    (
        "N3", "control characters accepted (newline shell composition)",
        "neyma_product_driver/scenario_validation.py",
        "        control = _control_character_problem(command)\n        if control:\n            return False, control\n\n",
        "",
    ),
    (
        "N4", "absolute URL in a generated request path accepted",
        "neyma_product_driver/scenario_validation.py",
        "    if _HAS_SCHEME.match(path):\n",
        "    if False:\n",
    ),
    (
        "N5", "shell scanner made quote-blind (legitimate SQL probes rejected)",
        "neyma_product_driver/scenario_validation.py",
        "        if not in_single and not in_double and char in _OPERATOR_CHARS:\n",
        "        if char in _OPERATOR_CHARS:\n",
    ),
    (
        "N6", "adaptive generation receives no failure evidence",
        "neyma_product_driver/scenario_planner.py",
        "            prior_failures=rendered,\n",
        "            prior_failures=[],\n",
    ),
    (
        "N7", "adaptive provenance no longer linked to a source failure",
        "neyma_product_driver/scenario_validation.py",
        '    if provenance.stage == "adaptive":\n',
        "    if False:\n",
    ),
    (
        "N8", "required SKIPPED treated as success by the gate",
        "neyma_product_driver/scenario_gate.py",
        "        if outcome.outcome is Outcome.PASSED and outcome.evidence_verified:\n            passed += 1\n            continue\n",
        "        if outcome.outcome in (Outcome.PASSED, Outcome.SKIPPED):\n            passed += 1\n            continue\n",
    ),
    (
        "N9", "evidence reference accepted without any artifact",
        "neyma_product_driver/scenario_suite.py",
        '    if not evidence_path:\n        return "the result cites no evidence directory"\n',
        '    return ""\n    if not evidence_path:\n        return "the result cites no evidence directory"\n',
    ),
    (
        "N10", "resume regenerates from wave zero",
        "neyma_product_driver/scenario_planner.py",
        '        if self.store is None:\n            return ""\n        path = self.store.run_dir / PLAN_FILENAME\n',
        '        return ""\n        if self.store is None:\n            return ""\n        path = self.store.run_dir / PLAN_FILENAME\n',
    ),
]


def prepare_baseline() -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if BASELINE.exists():
        shutil.rmtree(BASELINE)
    subprocess.run(
        [
            "rsync", "-a",
            "--exclude", ".git", "--exclude", ".venv", "--exclude", "runs",
            "--exclude", ".pytest_cache", "--exclude", "verification-evidence",
            "--exclude", "__pycache__",
            f"{REPO}/", f"{BASELINE}/",
        ],
        check=True,
    )


def reset_work() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    shutil.copytree(BASELINE, WORK, symlinks=True)


def apply_mutation(path: str, old: str, new: str) -> str:
    target = WORK / path
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        return f"fragment matched {count} times in {path}"
    target.write_text(text.replace(old, new, 1))
    return ""


def run_suite(timeout: int = 900) -> tuple[bool, str]:
    """Return (caught, tail). caught=True means the suite failed, as it should."""
    started = time.time()
    proc = subprocess.run(
        [PYTHON, "-m", "pytest", "-x", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=WORK,
        env={**os.environ, "PYTHONPATH": str(WORK)},
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.time() - started
    output = (proc.stdout or "") + (proc.stderr or "")
    lines = [ln for ln in output.strip().splitlines() if ln.strip()]
    failing = [ln for ln in lines if ln.startswith("FAILED") or " failed" in ln]
    tail = "\n".join(failing[-6:] or lines[-6:])
    return proc.returncode != 0, f"[{elapsed:.0f}s] {tail}"


def main() -> int:
    only = set(sys.argv[1:])
    print(f"staging a clean copy under {SCRATCH} ...")
    prepare_baseline()

    results = []
    for mid, description, path, old, new in MUTATIONS:
        if only and mid not in only:
            continue
        print(f"\n=== {mid}: {description} ===", flush=True)
        reset_work()
        problem = apply_mutation(path, old, new)
        if problem:
            print(f"    COULD NOT APPLY — {problem}")
            results.append({"id": mid, "description": description, "status": "COULD_NOT_APPLY",
                            "detail": problem})
            continue
        try:
            caught, tail = run_suite()
        except subprocess.TimeoutExpired:
            caught, tail = False, "TIMEOUT"
        print(f"    {'CAUGHT' if caught else 'SURVIVED — TESTS STAYED GREEN'}")
        print("    " + tail.replace("\n", "\n    ")[:1200], flush=True)
        results.append({"id": mid, "description": description, "file": path,
                        "status": "CAUGHT" if caught else "SURVIVED", "detail": tail})

    out = Path(__file__).parent / "mutation-results.json"
    out.write_text(json.dumps(results, indent=2))

    print("\n\n===== MUTATION SUMMARY =====")
    for r in results:
        print(f"{r['id']:<5} {r['status']:<15} {r['description']}")
    survived = [r for r in results if r["status"] != "CAUGHT"]
    print(f"\n{len(results) - len(survived)}/{len(results)} caught")
    for r in survived:
        print(f"  SURVIVED: {r['id']} — {r['description']}")
    print(f"written: {out}")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
