#!/bin/bash
# G-SCALE baseline sweep. One OS process per scale point so ru_maxrss is a
# per-size peak rather than a cumulative one (the implementer's harness ran all
# four sizes in one process, which makes its RSS numbers monotone by
# construction and therefore uninformative).
set -u
CERT=/Users/sammyfammy/neyma-product-driver/verification-evidence/cert/G-SCALE
PY=/Users/sammyfammy/neyma-product-driver/.venv/bin/python
SCRATCH=/private/tmp/claude-501/-Users-sammyfammy-neyma-product-driver/14ad07bb-8100-4304-a9d3-25273378f23b/scratchpad

export TARGET_PORT=8791
export TARGET_DB=$SCRATCH/gscale.sqlite
export TARGET_BAD_IDS=12,66,87,150,183
export TARGET_DUP_IDS=11,74,176
export TARGET_500_IDS=

OUT=${OUT:-$CERT/raw/base}
EXTRA=${EXTRA:-}
for n in ${SIZES:-10 50 100 200}; do
  echo "=== n=$n ${EXTRA} ==="
  "$PY" "$CERT/gscale_one.py" --driver /Users/sammyfammy/neyma-product-driver \
      --target "$CERT/work" --out "$OUT" --n "$n" $EXTRA > "$OUT/stdout-$n.json" 2>"$OUT/stderr-$n.txt"
  rc=$?
  echo "rc=$rc"
  if [ $rc -ne 0 ]; then tail -20 "$OUT/stderr-$n.txt"; fi
done
