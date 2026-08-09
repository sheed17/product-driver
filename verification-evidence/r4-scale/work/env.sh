#!/bin/zsh
# Shared environment for the R4 scale audit.
W=/Users/sammyfammy/neyma-product-driver/.claude/worktrees/agent-a9a34d7277b7dcdd7/r4work
E=/Users/sammyfammy/neyma-product-driver/verification-evidence/r4-scale
export R4_STAGE="$E/stage"
export R4_TARGET="$E/work/target"
export R4_EVIDENCE="$E/out"
export TARGET_DB=/tmp/r4-target.sqlite
export TARGET_PORT=8731
export PYBIN=/Users/sammyfammy/neyma-product-driver/.venv/bin/python
mkdir -p "$R4_EVIDENCE"
rsync -a --delete "$W/" "$E/work/"
