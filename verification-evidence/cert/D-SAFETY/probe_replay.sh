#!/bin/bash
# D-SAFETY replay probe: does `scenarios run-generated` re-validate a
# hand-edited scenario-plan.json before executing it?
#
# HARMLESS AND LOCAL. Everything happens under a fresh temp directory. The
# "attack" payloads write a marker file inside that same temp directory and hit
# a loopback listener started by probe_execute.py's sibling; nothing external.
set -u
DRIVER_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
PY="$DRIVER_ROOT/.venv/bin/python"
TMP="$(mktemp -d /tmp/dsafety-replay-XXXXXX)"
echo "temp root: $TMP"

REPO="$TMP/target-repo"
mkdir -p "$REPO/eval/tests"
git -C "$REPO" init -q 2>/dev/null || (mkdir -p "$REPO/.git")
printf '[pytest]\n' > "$REPO/pytest-canonical.ini"
printf 'def test_ok():\n    assert True\n' > "$REPO/eval/tests/test_a.py"

mkdir -p "$TMP/scenarios" "$TMP/runs/run-1"
APPROVED="$PY -m pytest -c pytest-canonical.ini eval/tests/test_a.py -q"

cat > "$TMP/scenarios/base.yaml" <<YAML
name: base
mode: backend
app_url: ""
commands:
  - name: suite
    run: "$APPROVED"
YAML

cat > "$TMP/driver.config.yaml" <<YAML
neyma_repo: "$REPO"
runs_dir: "$TMP/runs"
scenarios_dir: "$TMP/scenarios"
scenario: "base.yaml"
task: "probe"
YAML

# A hand-edited plan. Scenario A carries a command that was NEVER approved.
# Scenario B carries the approved prefix plus a post-validation {{fixture:}}
# substitution that makes model-authored code execute.
cat > "$TMP/runs/run-1/scenario-plan.json" <<JSON
{
  "run_id": "run-1",
  "task": "probe",
  "scenarios": [
    {
      "id": "A-unapproved-command",
      "title": "hand-edited: a command nobody approved",
      "risk_category": "happy_path",
      "requirement_reference": "AC-PROBE-001",
      "product_principle_reference": "product rubric",
      "expected_observations": ["marker"],
      "actions": [
        {"kind": "command", "command": "/bin/sh -c 'echo REPLAY_UNAPPROVED > $TMP/A-RAN.txt'"}
      ],
      "provenance": {"task_hash": "p", "stage": "initial", "model": "hand-edited", "wave": 1,
                     "generating_risk": "p"}
    },
    {
      "id": "B-fixture-chain",
      "title": "hand-edited: approved prefix plus model-authored fixture",
      "risk_category": "happy_path",
      "requirement_reference": "AC-PROBE-001",
      "product_principle_reference": "product rubric",
      "expected_observations": ["1 passed"],
      "actions": [
        {"kind": "fixture", "fixture_name": "test_pwn.py",
         "fixture_content": "import pathlib\npathlib.Path('$TMP/B-RAN.txt').write_text('replayed model-authored code')\ndef test_noop():\n    assert True\n"},
        {"kind": "command", "command": "$APPROVED {{fixture:test_pwn.py}}", "expect_exit_code": 0}
      ],
      "provenance": {"task_hash": "p", "stage": "initial", "model": "hand-edited", "wave": 1,
                     "generating_risk": "p"}
    }
  ]
}
JSON

echo "--- running replay ---"
"$PY" -m neyma_product_driver scenarios run-generated \
  --config "$TMP/driver.config.yaml" --run run-1 --scenario base.yaml 2>&1 | tail -40

echo
echo "--- outcome ---"
echo "A-RAN.txt (unapproved command executed?): $([ -f "$TMP/A-RAN.txt" ] && echo YES || echo no)"
echo "B-RAN.txt (model-authored fixture code executed?): $([ -f "$TMP/B-RAN.txt" ] && echo YES || echo no)"
echo "temp root kept for inspection: $TMP"
