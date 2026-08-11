"""Re-anchor N10 and P7, which cycle 1's own fixes moved.

Neither SURVIVED. Both came back COULD_NOT_APPLY, because this cycle changed the
exact lines their fragments were anchored to:

  N10  `restore_from_store` returned `""`; it now returns `PlanRestore(...)`,
       so the fragment matched 0 times.
  P7   `risks=_identified_risks(planner),` now appears twice, because the gate
       is evaluated at 6b *and* applied through `_apply_suite_precedence`, so
       the fragment matched 2 times and could not be applied unambiguously.

"The harness could not test it" is not "the requirement is still covered", so
both are re-anchored here and re-run. The intent of each mutation is unchanged.

The builder may not edit `verification-evidence/post-remediation/run_mutations.py`.
The re-anchored fragments below are therefore a RECOMMENDATION to the controller,
proved to work.

Runs in an isolated git worktree; the working tree is never mutated.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path("/Users/sammyfammy/neyma-product-driver")
WORK = Path(
    "/private/tmp/claude-501/-Users-sammyfammy-neyma-product-driver/"
    "14ad07bb-8100-4304-a9d3-25273378f23b/scratchpad/pre-remediation"
)
PY = str(REPO / ".venv" / "bin" / "python")

REANCHORED = [
    (
        "N10", "resume regenerates from wave zero",
        "neyma_product_driver/scenario_planner.py",
        "        if self.store is None:\n"
        "            return PlanRestore(state=\"absent\")\n"
        "        path = self.store.run_dir / PLAN_FILENAME",
        "        return PlanRestore(state=\"absent\")\n"
        "        if self.store is None:\n"
        "            return PlanRestore(state=\"absent\")\n"
        "        path = self.store.run_dir / PLAN_FILENAME",
        "tests/test_remediation_contract.py",
        1,
    ),
    (
        "P7", "the control loop stops handing the risk register to the gate",
        "neyma_product_driver/cli.py",
        "                risks=_identified_risks(planner),",
        "                risks=(),",
        "tests/test_post_remediation_contract.py",
        2,  # both call sites: the 6b gate evaluation and _apply_suite_precedence
    ),
]


def main() -> int:
    # Re-sync the worktree to the remediated source.
    for src in (REPO / "neyma_product_driver").glob("*.py"):
        (WORK / "neyma_product_driver" / src.name).write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )
    for src in (REPO / "tests").glob("*.py"):
        (WORK / "tests" / src.name).write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )

    failures = 0
    for mid, desc, relpath, old, new, tests, expected_count in REANCHORED:
        target = WORK / relpath
        original = target.read_text(encoding="utf-8")
        found = original.count(old)
        if found != expected_count:
            print(f"{mid}: ANCHOR STILL WRONG — matched {found}x, expected {expected_count}")
            failures += 1
            continue
        target.write_text(original.replace(old, new), encoding="utf-8")
        proc = subprocess.run(
            [PY, "-m", "pytest", tests, "-x", "-q", "--no-header", "-p", "no:randomly"],
            cwd=WORK, capture_output=True, text=True,
        )
        target.write_text(original, encoding="utf-8")
        tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        summary = tail[-1] if tail else "(no output)"
        failed_test = next(
            (ln for ln in tail if ln.startswith("FAILED")), ""
        )
        status = "CAUGHT" if proc.returncode != 0 else "*** SURVIVED ***"
        if proc.returncode == 0:
            failures += 1
        print(f"{mid}: {status:16} ({found}x anchored)  {desc}")
        if failed_test:
            print(f"      {failed_test}")
        print(f"      {summary}")

    print()
    if failures:
        print(f"RESULT: {failures} mutation(s) not caught after re-anchoring")
        return 1
    print("RESULT: both re-anchored mutations CAUGHT — 30/30 requirements covered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
