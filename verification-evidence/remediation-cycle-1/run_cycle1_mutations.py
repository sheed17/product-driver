"""Cycle-1 mutation harness.

For each of the nine upheld blocking defects, remove *the fix* and confirm the
regression coverage added for it fails. A test that still passes with its own
fix reverted does not pin the defect, whatever its name says.

Run from the pre-remediation worktree, which holds the remediated source.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "pre-remediation"
PY = "/Users/sammyfammy/neyma-product-driver/.venv/bin/python"
SRC = ROOT / "neyma_product_driver"
TESTS = "tests/test_remediation_cycle_1.py"

# id, defect, [(file, old, new), ...], target test class
MUTATIONS = [
    (
        "X1", "CG-02 — gate skipped on the completion-audit terminal path",
        [(SRC / "cli.py",
          "        gate_overrode = False\n        if suite_result is not None:",
          "        gate_overrode = False\n        if suite_result is not None and False:")],
        "TestTheGatePrecedesEveryTerminalState",
    ),
    (
        "X2", "D-SAFETY-01 — fixture_content executes as code",
        [(SRC / "scenario_validation.py",
          "        if suffix not in FIXTURE_DATA_EXTENSIONS:",
          "        if False:")],
        "TestAFixtureCannotBeAProgram",
    ),
    (
        "X3", "D-SAFETY-02/F-4 — goto scheme mismatch escapes loopback",
        [(SRC / "scenario_validation.py",
          '    raw = goto or ""\n    control = _control_character_problem(raw)',
          '    raw = goto or ""\n    if True:\n'
          '        return (raw if raw.startswith("http") else _join_url(app_url, raw)), ""\n'
          "    control = _control_character_problem(raw)")],
        "TestBrowserNavigationIsAnAllowlist",
    ),
    (
        "X4", "R1 — redact_obj corrupts the plan via 'authorization'",
        [(SRC / "models.py",
          '    if isinstance(value, str):\n        return "[REDACTED]"',
          '    if True:\n        return "[REDACTED]"')],
        "TestRedactionPreservesTypes",
    ),
    (
        "X5", "R2 — unreadable plan fails open and destroys the record",
        [(SRC / "scenario_planner.py",
          '        detail = f"{type(exc).__name__}: {exc}"\n        preserved = path.with_name(',
          '        return PlanRestore(state="absent")\n'
          '        detail = f"{type(exc).__name__}: {exc}"\n        preserved = path.with_name(')],
        "TestAnUnreadablePlanFailsClosed",
    ),
    (
        "X6", "I1/G-SCALE-02 — evidence-directory collision credited as verified",
        [(SRC / "evidence.py",
          "    if cleaned != name or cleaned != cleaned.casefold():",
          "    if False:")],
        "TestEvidenceDirectoriesAreInjective",
    ),
    (
        "X7", "G-SCALE-01 — resume-time drops invisible to the gate",
        [(SRC / "scenario_planner.py",
          "        for record in self.plan.waves:\n            if record.stage != STAGE_RESUME:",
          "        for record in []:\n            if record.stage != STAGE_RESUME:")],
        "TestResumeTimeDropsAreVisible",
    ),
    (
        "X8", "F-1+F-2 — browser scenario that observed nothing PASSES",
        [(SRC / "scenarios.py",
          "        if not obs.page_loaded:\n            result.assertions.append(",
          "        if False:\n            result.assertions.append("),
         (SRC / "scenario_suite.py",
          "            or (result.browser is not None and not result.browser.page_loaded)",
          "            or False")],
        "TestABrowserScenarioMustHaveObservedSomething",
    ),
    (
        "X9", "F-3 — browser text replaced rather than accumulated",
        [(SRC / "scenarios.py",
          "            parts.extend(result.browser.observed_texts)",
          "            pass")],
        "TestEveryScreenTheRunLookedAtIsSearchable",
    ),
]


def main() -> int:
    results = []
    for mid, defect, edits, target in MUTATIONS:
        originals = {}
        applied = True
        for path, old, new in edits:
            text = path.read_text(encoding="utf-8")
            originals.setdefault(path, text)
            if old not in text:
                print(f"{mid}: ANCHOR NOT FOUND in {path.name} -> {old[:60]!r}")
                applied = False
                break
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
        if not applied:
            for path, text in originals.items():
                path.write_text(text, encoding="utf-8")
            results.append((mid, defect, target, "ANCHOR-ERROR", ""))
            continue
        proc = subprocess.run(
            [PY, "-m", "pytest", f"{TESTS}::{target}", "-q", "--no-header", "-p", "no:randomly"],
            cwd=ROOT, capture_output=True, text=True,
        )
        tail = proc.stdout.strip().splitlines()
        summary = tail[-1] if tail else "(no output)"
        status = "CAUGHT" if proc.returncode != 0 else "*** MISSED ***"
        results.append((mid, defect, target, status, summary))
        print(f"{mid}: {status:15} {target}  |  {summary}")
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")

    print("\n" + "=" * 78)
    caught = sum(1 for r in results if r[3] == "CAUGHT")
    print(f"CYCLE-1 MUTATION SCORE: {caught}/{len(results)}")
    for mid, defect, target, status, summary in results:
        print(f"  {mid} {status:15} {defect}")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
