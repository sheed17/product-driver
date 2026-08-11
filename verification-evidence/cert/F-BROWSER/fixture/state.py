"""Read-only probe of the fixture's durable store — the persisted-state oracle.

    python3 fixture/state.py EX-1
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

STORE = Path(os.environ.get("STORE", "store.json"))


def main() -> int:
    record_id = sys.argv[1] if len(sys.argv) > 1 else "EX-1"
    try:
        data = json.loads(STORE.read_text())
    except Exception as exc:
        print(f"store unreadable: {exc}")
        return 2
    record = data.get(record_id)
    if record is None:
        print(f"no record {record_id}")
        return 1
    print(
        f"id={record['id']} status={record['status']} "
        f"owner={record['owner'] or 'none'} next_action={record['next_action'] or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
