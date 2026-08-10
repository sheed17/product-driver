"""Read-only probe of the DURABLE store. This is the oracle a scenario uses.

Deliberately reads the file, not the running process -- an HTTP 200 is not
evidence, and neither is an in-memory view.
"""

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
    for invoice_id, record in sorted(data.items()):
        if wanted and invoice_id != wanted:
            continue
        print(
            f"{invoice_id} status={record['status']} "
            f"approvals={len(record['approvals'])} payments={len(record['payments'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
