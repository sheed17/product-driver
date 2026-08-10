"""Build a disposable fixture repository with one real defect.

  python make_fixture.py --dest <dir>

The fixture is a self-contained git repository shaped like a product the driver
can legitimately operate: repository authority (CLAUDE.md), an implementation
registry with exactly one READY unit, and a small HTTP service backed by a
durable JSON store.

**The defect is real and is not announced anywhere the builder can read it.**
`POST /api/invoices/<id>/approve` applies the payment effect on every call, so
approving twice pays twice. The task the driver is given asks for exactly-once
approval; nothing in the repository says the code is wrong.

No external effects are possible: the service binds loopback, the repository has
no remote, and the only dependencies are the standard library.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

CLAUDE_MD = """\
# Repository authority

This repository implements a small carrier-invoice approval service. This file
is the authority for how work is done here.

## Rules

- Every change must keep the service loopback-only. It must never make a network
  call to anything but 127.0.0.1, and it must never require a credential.
- `POST /api/invoices/<id>/approve` is the only write path. Approval is an
  effect: it records an approval and a payment.
- The durable store under `data/store.json` is the source of truth. An HTTP 200
  is not evidence that an effect happened; the store is.
- `scripts/state.py` is the read-only probe over the durable store. It is how
  anything outside the service checks what actually happened.
- Do not weaken or delete a test to obtain a green result.
- The tests are standard-library `unittest`. Run them with
  `python3 -m unittest discover -s tests`. Do not introduce pytest.
- Do not add dependencies. The standard library is the whole toolbox here.

## Scope

Work only on the unit marked READY in
`docs/implementation/IMPLEMENTATION-REGISTRY.yaml`.
"""

REGISTRY = """\
meta:
  name: fixture-approval-service
  note: >
    Disposable fixture repository used to exercise the Product Driver end to end.
    Not a product.
units:
  - unit_id: U-FIX-1
    name: exactly-once carrier invoice approval
    objective: >
      Approving a carrier invoice records the approval durably and pays exactly
      once, however many times the approval is submitted.
    status: READY
    execution_state: IN_PROGRESS
    checkpoint_state: NOT_STARTED
    dependencies: []
    unlocked_by: []
    blocks: []
    acceptance_contract: docs/implementation/U-FIX-1-ACCEPTANCE.yaml
    canonical_references:
      - docs/implementation/CURRENT.md
    allowed_scope:
      - the approval write path
      - the durable store
      - the state probe
    prohibited_scope:
      - new dependencies
      - network calls beyond loopback
      - deleting or weakening tests
    acceptance_criteria:
      - criterion: an approved invoice is paid exactly once
        weight: 3
        result: PENDING
      - criterion: an approval is recorded in the durable store
        weight: 3
        result: PENDING
      - criterion: approving an invoice that does not exist is refused
        weight: 2
        result: PENDING
      - criterion: two approvals that arrive at the same time still pay exactly once
        weight: 3
        result: PENDING
"""

ACCEPTANCE = """\
unit_id: U-FIX-1
criteria:
  - id: AC-FIX-001
    statement: an approved invoice is paid exactly once
    evidence: scripts/state.py reports payments=1 after any number of approvals
  - id: AC-FIX-002
    statement: an approval is recorded in the durable store
    evidence: scripts/state.py reports status=approved
  - id: AC-FIX-003
    statement: approving an invoice that does not exist is refused
    evidence: the service answers 404 and the store is unchanged
  - id: AC-FIX-004
    statement: two approvals that arrive at the same time still pay exactly once
    evidence: scripts/state.py reports payments=1 after concurrent approvals
"""

CURRENT = """\
# Current state

U-FIX-1 is READY. The approval endpoint exists and records approvals. Exactly-once
behaviour has not been verified.
"""

BUILD_STATUS = """\
phase: fixture
status: IN_PROGRESS
notes:
  - U-FIX-1 is the only READY unit.
"""

# The defect: `_approve` appends a payment unconditionally.
APP = '''\
"""Carrier invoice approval service. Loopback only, standard library only.

The invoice record and the payment ledger are separate durable artifacts:
`data/store.json` holds status and approvals, `data/payments.log` is an
append-only record of money actually moved. That separation is deliberate and
ordinary — a ledger you only ever append to is easier to audit than one you
rewrite — and it is what makes "paid exactly once" a property of the ledger
rather than of the record.
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "__PORT__"))
DATA = Path(os.environ.get("DATA", Path(__file__).resolve().parents[1] / "data"))
STORE = Path(os.environ.get("STORE", DATA / "store.json"))
LEDGER = Path(os.environ.get("LEDGER", DATA / "payments.log"))
#: Widens the read-modify-write window so a race is observable rather than
#: merely possible. Real services have this window too; here it is made
#: reliable so a verification run measures the same thing every time.
WRITE_DELAY_S = float(os.environ.get("WRITE_DELAY_S", "0.05"))

SEED = {
    "INV-1": {"id": "INV-1", "status": "pending", "approvals": []},
    "INV-2": {"id": "INV-2", "status": "pending", "approvals": []},
}


def load() -> dict:
    if not STORE.exists():
        return json.loads(json.dumps(SEED))
    try:
        return json.loads(STORE.read_text())
    except ValueError:
        return json.loads(json.dumps(SEED))


def save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, indent=2))


def pay(invoice_id: str, actor: str) -> None:
    """Move the money. Appends one line to the durable ledger."""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as handle:
        handle.write(f"{invoice_id} {actor} 100" + chr(10))


def payments_for(invoice_id: str) -> int:
    if not LEDGER.exists():
        return 0
    return sum(
        1 for line in LEDGER.read_text().splitlines() if line.startswith(invoice_id + " ")
    )


def _approve(data: dict, invoice_id: str, actor: str) -> None:
    """Apply the approval effect to one invoice."""
    record = data[invoice_id]
    record["status"] = "approved"
    record["approvals"].append(actor)
    pay(invoice_id, actor)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/health":
            self._json(200, {"ok": True})
            return
        if path.startswith("/api/invoices/"):
            invoice_id = path.rsplit("/", 1)[-1]
            record = load().get(invoice_id)
            if record is None:
                self._json(404, {"error": "no such invoice"})
                return
            self._json(200, {**record, "payments": payments_for(invoice_id)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if not (path.startswith("/api/invoices/") and path.endswith("/approve")):
            self._json(404, {"error": "not found"})
            return
        invoice_id = path.split("/")[3]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        actor = str(body.get("actor") or "unknown")

        data = load()
        if invoice_id not in data:
            self._json(404, {"error": "no such invoice"})
            return
        _approve(data, invoice_id, actor)
        time.sleep(WRITE_DELAY_S)
        save(data)
        self._json(200, {"status": "approved", "payments": payments_for(invoice_id)})


def main() -> None:
    # Seeded only when absent. Wiping the store on every boot would make a
    # restart destructive, and "an approval survives a restart" is exactly what
    # this service is supposed to promise.
    if not STORE.exists():
        save(json.loads(json.dumps(SEED)))
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
'''

STATE = '''\
"""Read-only probe over the durable artifacts. The oracle for what happened."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DATA = Path(os.environ.get("DATA", Path(__file__).resolve().parents[1] / "data"))
STORE = Path(os.environ.get("STORE", DATA / "store.json"))
LEDGER = Path(os.environ.get("LEDGER", DATA / "payments.log"))


def payments_for(invoice_id: str) -> int:
    if not LEDGER.exists():
        return 0
    return sum(
        1 for line in LEDGER.read_text().splitlines() if line.startswith(invoice_id + " ")
    )


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
            f"approvals={len(record['approvals'])} payments={payments_for(invoice_id)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

TESTS = '''\
"""The repository's own tests. They pass against the defective code.

That is the point: a green suite is not evidence that approval is exactly-once,
because nothing here approves the same invoice twice, and nothing here approves
concurrently. Standard library only — run with
`python3 -m unittest discover -s tests`.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import app  # noqa: E402


class ApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._store, self._ledger = app.STORE, app.LEDGER
        app.STORE = root / "store.json"
        app.LEDGER = root / "payments.log"

    def tearDown(self) -> None:
        app.STORE, app.LEDGER = self._store, self._ledger
        self._tmp.cleanup()

    def test_approving_records_the_approval(self) -> None:
        data = json.loads(json.dumps(app.SEED))
        app._approve(data, "INV-1", "alice")
        app.save(data)
        reloaded = json.loads(app.STORE.read_text())
        self.assertEqual(reloaded["INV-1"]["status"], "approved")
        self.assertEqual(reloaded["INV-1"]["approvals"], ["alice"])

    def test_approving_moves_money_once(self) -> None:
        data = json.loads(json.dumps(app.SEED))
        app._approve(data, "INV-1", "alice")
        self.assertEqual(app.payments_for("INV-1"), 1)

    def test_the_store_round_trips(self) -> None:
        app.save(json.loads(json.dumps(app.SEED)))
        self.assertEqual(app.load()["INV-2"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
'''

SCENARIO = """\
name: fixture_backend
phase: "U-FIX-1"
mode: backend
description: >
  Permanent regression scenario for the approval service: the service starts, an
  invoice can be approved, and the durable store records the approval.

services:
  - name: api
    command: "python3 src/app.py"
    env:
      PORT: "8791"

readiness:
  - http: "http://127.0.0.1:8791/health"
    expect_status: 200
    timeout_s: 10

app_url: "http://127.0.0.1:8791"

requests:
  - name: approve the smoke invoice
    method: POST
    path: /api/invoices/INV-2/approve
    json: {"actor": "smoke"}
    expect_status: 200
    timeout_s: 5

expect_state:
  - name: the store records the approval
    command: "python3 scripts/state.py INV-2"
    contains: ["status=approved"]

forbidden:
  - "Traceback"
"""

README = """\
# fixture-approval-service

A disposable fixture repository. Not a product. Loopback only, standard library
only, no remote, no credentials.
"""


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True)
    ap.add_argument("--port", type=int, default=8791)
    args = ap.parse_args()
    root = Path(args.dest).resolve()
    if root.exists():
        import shutil

        shutil.rmtree(root)
    root.mkdir(parents=True)

    write(root, "CLAUDE.md", CLAUDE_MD)
    write(root, "README.md", README)
    write(root, "docs/implementation/IMPLEMENTATION-REGISTRY.yaml", REGISTRY)
    write(root, "docs/implementation/U-FIX-1-ACCEPTANCE.yaml", ACCEPTANCE)
    write(root, "docs/implementation/CURRENT.md", CURRENT)
    write(root, "docs/implementation/BUILD-STATUS.yaml", BUILD_STATUS)
    write(root, "src/app.py", APP.replace("__PORT__", str(args.port)))
    write(root, "scripts/state.py", STATE)
    write(root, "tests/test_app.py", TESTS)
    write(root, "scenarios/fixture_backend.yaml", SCENARIO.replace("8791", str(args.port)))
    # `.pytest_tmp/` and `*.db` exist so the driver's doctor has an ignored
    # scratch path to prove the builder can write without touching a tracked
    # file. Ordinary repository hygiene, not a relaxed guard.
    write(
        root,
        ".gitignore",
        "data/\n__pycache__/\n.pytest_cache/\n.pytest_tmp/\n*.db\n",
    )

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@localhost",
         "commit", "-q", "-m", "Fixture approval service"],
        cwd=root,
        check=True,
    )
    # No remote is configured, so nothing here can be published even by accident.
    remotes = subprocess.run(
        ["git", "remote"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert remotes == "", f"the fixture must have no remote, found {remotes!r}"

    print(json.dumps({"fixture": str(root), "port": args.port, "remotes": remotes or "(none)"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
