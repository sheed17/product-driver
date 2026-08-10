#!/bin/zsh
source /Users/sammyfammy/neyma-product-driver/.claude/worktrees/agent-a9a34d7277b7dcdd7/r4work/env.sh
pkill -f "r4-scale/work/target/app.py" 2>/dev/null
sleep 0.4
rm -f /tmp/r4-target.sqlite /tmp/r4-target.sqlite-wal /tmp/r4-target.sqlite-shm
nohup "$PYBIN" "$R4_TARGET/app.py" > "$R4_EVIDENCE/target.log" 2>&1 &
sleep 1.5
echo "--- health ---"
curl -s http://127.0.0.1:$TARGET_PORT/health
echo
echo "--- item 11 (BAD=${TARGET_BAD_IDS}) ---"
curl -s http://127.0.0.1:$TARGET_PORT/item/11
echo
echo "--- item 87 status ---"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:$TARGET_PORT/item/87
echo "--- target.log ---"
tail -3 "$R4_EVIDENCE/target.log"
