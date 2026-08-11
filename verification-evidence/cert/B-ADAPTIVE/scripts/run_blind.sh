#!/bin/zsh
# De-confounded ("blind") arm.
#
# The wave-2 brief quotes the failure artifact directory verbatim, so a run
# directory named after the seeded defect puts the string `partial_dep`,
# `authz_retry`, `ui_lies` or `nonidempotent` into the model's prompt. Divergence
# measured in the named arm could therefore be driven by the directory name
# rather than by the observed evidence.
#
# This arm re-runs the same experiment with neutral run directories (blind-01 …),
# so the only defect-correlated signal left in the brief is the observed failure
# itself. The mapping is written to blind-key.json AFTER the runs are launched.
#
#   ./run_blind.sh <driver-root> <work-dir> <out-dir>
set -u

DRIVER="${1:?driver root}"
WORK="${2:?work dir}"
OUT="${3:?out dir}"
RUN="$(dirname "$0")/run_experiment.py"
PY="${PYBIN:-$DRIVER/.venv/bin/python}"

mkdir -p "$WORK" "$OUT"
rm -rf "$WORK/fixture"
cp -r "$(dirname "$0")/../fixture" "$WORK/"

# Interleaved, so no defect occupies a contiguous stretch of wall clock.
ORDER=(
  "blind-01 nonidempotent 9601"
  "blind-02 ui_lies       9602"
  "blind-03 authz_retry   9603"
  "blind-04 partial_dep   9604"
  "blind-05 partial_dep   9605"
  "blind-06 authz_retry   9606"
  "blind-07 ui_lies       9607"
  "blind-08 nonidempotent 9608"
  "blind-09 ui_lies       9609"
  "blind-10 nonidempotent 9610"
  "blind-11 partial_dep   9611"
  "blind-12 authz_retry   9612"
)

for entry in "${ORDER[@]}"; do
  set -- ${=entry}
  label="$1"; defect="$2"; port="$3"
  if [[ -f "$OUT/$label/meta.json" ]]; then
    echo "########## $label already present, skipping ##########"
    continue
  fi
  echo "########## $label (port $port) $(date -u +%H:%M:%S) ##########"
  "$PY" "$RUN" "$defect" "$port" "$OUT/$label" \
      --driver "$DRIVER" --work "$WORK" --real --label "$label" 2>&1 | tail -8
done

echo "########## BLIND ARM DONE $(date -u +%H:%M:%S) ##########"
