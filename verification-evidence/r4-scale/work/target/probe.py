"""Persisted-state probe: reads the target's SQLite directly."""

from __future__ import annotations

import os
import sqlite3
import sys

DB = os.environ.get("TARGET_DB", "/tmp/r4-target.sqlite")

if __name__ == "__main__":
    item = int(sys.argv[1])
    with sqlite3.connect(DB, timeout=10) as c:
        n = c.execute("SELECT COUNT(*) FROM approvals WHERE item=?", (item,)).fetchone()[0]
    print(f"item={item} approval_rows={n}")
    sys.exit(0)
