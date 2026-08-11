#!/bin/zsh
# B-ADAPTIVE certification campaign.
#
#   ./run_all.sh <driver-root> <work-dir> <out-dir>
#
# Four seeded defects (two of them on risk surfaces the prior campaign never
# used), four replicates each, plus two evidence-free controls.
#
# The invocation order is deliberately ROUND-ROBIN across defects rather than
# grouped by defect. The prior campaign ran all replicates of a defect
# consecutively, which makes REPLICATE pairs temporally adjacent while BETWEEN
# pairs are not: any drift in model behaviour over the campaign would then
# inflate replicate similarity and manufacture the very result being tested.
# Interleaving removes that confound.
set -u

DRIVER="${1:?driver root}"
WORK="${2:?work dir}"
OUT="${3:?out dir}"
RUN="$(dirname "$0")/run_experiment.py"
PY="${PYBIN:-$DRIVER/.venv/bin/python}"

mkdir -p "$WORK" "$OUT"
rm -rf "$WORK/fixture"
cp -r "$(dirname "$0")/../fixture" "$WORK/"

run() {
  local defect="$1" replicate="$2" port="$3"; shift 3
  local dir="$OUT/$defect-$replicate"
  if [[ -f "$dir/meta.json" ]]; then
    echo "########## $defect-$replicate already present, skipping ##########"
    return
  fi
  echo "########## $defect-$replicate (port $port) $(date -u +%H:%M:%S) ##########"
  "$PY" "$RUN" "$defect" "$port" "$dir" \
      --driver "$DRIVER" --work "$WORK" --real "$@" 2>&1 | tail -12
}

control() {
  local replicate="$1" port="$2"
  local dir="$OUT/control-$replicate"
  if [[ -f "$dir/meta.json" ]]; then
    echo "########## control-$replicate already present, skipping ##########"
    return
  fi
  echo "########## CONTROL-$replicate (evidence withheld) $(date -u +%H:%M:%S) ##########"
  "$PY" "$RUN" none "$port" "$dir" \
      --driver "$DRIVER" --work "$WORK" --real --no-evidence 2>&1 | tail -12
}

run partial_dep    a 9501   # already executed as the timing pilot; skipped if present
run nonidempotent  a 9511
run ui_lies        a 9521
run authz_retry    a 9531
control            1 9591

run partial_dep    b 9502
run nonidempotent  b 9512
run ui_lies        b 9522
run authz_retry    b 9532

run partial_dep    c 9503
run nonidempotent  c 9513
run ui_lies        c 9523
run authz_retry    c 9533
control            2 9592

run partial_dep    d 9504
run nonidempotent  d 9514
run ui_lies        d 9524
run authz_retry    d 9534

echo "########## DONE $(date -u +%H:%M:%S) ##########"
