#!/bin/bash
# G-SCALE: start the loopback verification target. Loopback only, no network.
set -u
CERT=/Users/sammyfammy/neyma-product-driver/verification-evidence/cert/G-SCALE
SCRATCH=/private/tmp/claude-501/-Users-sammyfammy-neyma-product-driver/14ad07bb-8100-4304-a9d3-25273378f23b/scratchpad
PY=/Users/sammyfammy/neyma-product-driver/.venv/bin/python

export TARGET_PORT=${TARGET_PORT:-8791}
export TARGET_DB=${TARGET_DB:-$SCRATCH/gscale.sqlite}
export TARGET_BAD_IDS=${TARGET_BAD_IDS-12,66,87,150,183}
export TARGET_DUP_IDS=${TARGET_DUP_IDS-11,74,176}
export TARGET_500_IDS=${TARGET_500_IDS-}

mkdir -p "$CERT/raw"
rm -f "$TARGET_DB" "$TARGET_DB-wal" "$TARGET_DB-shm"
pkill -f "work/app.py" 2>/dev/null
sleep 0.3
nohup "$PY" "$CERT/work/app.py" > "$CERT/raw/target-$TARGET_PORT.log" 2>&1 &
echo "pid=$!"
for i in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:$TARGET_PORT/health" >/dev/null; then
    echo "ready on $TARGET_PORT db=$TARGET_DB"
    cat "$CERT/raw/target-$TARGET_PORT.log"
    exit 0
  fi
  sleep 0.25
done
echo "TARGET DID NOT COME UP"; cat "$CERT/raw/target-$TARGET_PORT.log"; exit 1
