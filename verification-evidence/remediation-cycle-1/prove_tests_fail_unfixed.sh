#!/usr/bin/env bash
# Show that tests/test_remediation_cycle_1.py fails on the UNFIXED candidate.
#
# Builds a pristine tree from a given commit (default: the certification
# candidate 537ae0b), drops the new test file into it, and runs it there.
# Nothing in the working tree is touched and no git history command is used —
# `git archive` only reads.
#
# Two module-level imports are rewritten in the *copy* only, because the two
# symbols they name (`cli._recorded_suite_gate`, `scenario_validation.
# resolve_browser_target`) do not exist on the unfixed candidate and their
# absence would abort collection before any test ran. The tests themselves are
# byte-identical; a test that then fails with AttributeError on a missing
# symbol is still a test that fails on the unfixed code.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
BASE="${1:-537ae0b}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

git -C "$REPO" archive "$BASE" | tar -x -C "$WORK"
cp "$REPO/tests/test_remediation_cycle_1.py" "$WORK/tests/"

python3 - "$WORK/tests/test_remediation_cycle_1.py" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); t = p.read_text()
t = t.replace("""from neyma_product_driver.cli import (
    LoopResult,
    _recorded_suite_gate,
    _report_outcome,
    run_control_loop,
)""", """from neyma_product_driver.cli import LoopResult, _report_outcome, run_control_loop

_recorded_suite_gate = getattr(driver_cli, "_recorded_suite_gate", None)""")
t = t.replace("""from neyma_product_driver.scenario_validation import (
    ApprovedCommands,
    resolve_browser_target,
    validate_scenario,
)""", """from neyma_product_driver import scenario_validation as _sv
from neyma_product_driver.scenario_validation import ApprovedCommands, validate_scenario

resolve_browser_target = getattr(_sv, "resolve_browser_target", None)""")
p.write_text(t)
PY

cd "$WORK"
"$REPO/.venv/bin/python" -m pytest tests/test_remediation_cycle_1.py -p no:randomly -v --no-header -rN
