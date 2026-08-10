"""Read-only probe of the durable exception store. The oracle a scenario uses."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

STORE = Path(os.environ.get("STORE", Path(__file__).with_name("store.json")))


def main() -> int:
    if not STORE.exists():
        print("STORE MISSING")
        return 1
    data = json.loads(STORE.read_text())
    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    for exception_id, record in sorted(data.items()):
        if wanted and exception_id != wanted:
            continue
        print(f"{exception_id} status={record['status']} owner={record['owner']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
