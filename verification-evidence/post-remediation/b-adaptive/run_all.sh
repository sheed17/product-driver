#!/bin/zsh
# Drive the whole adaptive-divergence experiment.
#
#   ./run_all.sh <driver-root> <work-dir> <out-dir>
#
# Three seeded defects, three replicates each, plus one evidence-free control.
# Every invocation is written out explicitly: zsh does not word-split an
# unquoted parameter, so a "$defect $replicate" loop variable silently arrives
# as one argument and the fixture then runs with no recognised defect at all.
set -e

DRIVER="${1:?driver root}"
WORK="${2:?work dir}"
OUT="${3:?out dir}"
RUN="$(dirname "$0")/run_experiment.py"
PY="${PYBIN:-$DRIVER/.venv/bin/python}"

rm -rf "$WORK" "$OUT"
mkdir -p "$WORK" "$OUT"
cp -r "$(dirname "$0")/fixture" "$WORK/"

run() {
  local defect="$1" replicate="$2" port="$3"; shift 3
  echo "########## $defect-$replicate (port $port) ##########"
  "$PY" "$RUN" "$defect" "$port" "$OUT/$defect-$replicate" \
      --driver "$DRIVER" --work "$WORK" --real "$@" 2>&1 | tail -14
}

run nonidempotent a 8801
run nonidempotent b 8802
run nonidempotent c 8803
run ui_lies       a 8804
run ui_lies       b 8805
run ui_lies       c 8806
run uncertain     a 8807
run uncertain     b 8808
run uncertain     c 8809

echo "########## CONTROL (evidence withheld) ##########"
"$PY" "$RUN" none 8899 "$OUT/control" \
    --driver "$DRIVER" --work "$WORK" --real --no-evidence 2>&1 | tail -14
