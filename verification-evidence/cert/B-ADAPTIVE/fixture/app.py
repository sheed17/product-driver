"""Intentionally buggy invoice-approval service — B-ADAPTIVE certification copy.

Independent of `verification-evidence/post-remediation/b-adaptive/fixture/app.py`.
Two of the defects are inherited so the prior campaign's numbers stay
comparable; two are NEW risk surfaces the prior campaign never seeded.

Task it pretends to implement:
    "Implement an approval endpoint with persistent state."

Selected by the DEFECT environment variable:

  DEFECT=nonidempotent   POST /approve applies the payment effect EVERY time.
                         (prior campaign, kept as an anchor)
  DEFECT=ui_lies         POST /approve answers 200 from an in-memory view and
                         never writes the durable store.
                         (prior campaign, kept as an anchor)
  DEFECT=authz_retry     NEW. The authorization check is evaluated only on the
                         FIRST attempt for an invoice. A caller refused with 403
                         who simply retries the identical request is approved,
                         and the approval is recorded under the unauthorized
                         actor. An authorization leak that lives on the retry
                         path.
  DEFECT=partial_dep     NEW. The approval itself persists correctly, but the
                         downstream ledger write silently fails and the endpoint
                         still answers 200 {"ledger":"recorded"}. A partial
                         dependency failure reported as a success.
  DEFECT=none            Correct behaviour: authorized-only, idempotent,
                         durable, ledger written, prompt.

Loopback only. No external effects. Stdlib only.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFECT = os.environ.get("DEFECT", "none")
PORT = int(os.environ.get("PORT", "8731"))
STORE = Path(os.environ.get("STORE", Path(__file__).with_name("store.json")))

#: Actors permitted to approve. Everyone else must be refused, on every
#: attempt, under every defect except the one that breaks exactly this.
APPROVERS = {"alice", "bob"}

_LOCK = threading.Lock()

_SEED = {
    "INV-1": {"id": "INV-1", "status": "pending", "approvals": [], "payments": [], "ledger": []},
    "INV-2": {"id": "INV-2", "status": "pending", "approvals": [], "payments": [], "ledger": []},
}


def _load() -> dict:
    if not STORE.exists():
        return json.loads(json.dumps(_SEED))
    try:
        return json.loads(STORE.read_text())
    except ValueError:
        return json.loads(json.dumps(_SEED))


def _save(data: dict) -> None:
    STORE.write_text(json.dumps(data, indent=2))


# The in-memory view. For DEFECT=ui_lies this is what the API answers from, and
# it drifts from the durable store -- which is exactly the bug.
_MEM = _load()


def _ensure(invoice_id: str) -> dict:
    """Invoices are created on first touch, so every scenario can use its own."""
    record = _MEM.get(invoice_id)
    if record is None:
        record = {
            "id": invoice_id,
            "status": "pending",
            "approvals": [],
            "payments": [],
            "ledger": [],
            "attempts": 0,
        }
        _MEM[invoice_id] = record
        _save(_MEM)
    record.setdefault("ledger", [])
    record.setdefault("attempts", 0)
    return record


def _ledger_write(record: dict, actor: str) -> bool:
    """The downstream ledger. Returns whether the entry actually landed."""
    if DEFECT == "partial_dep":
        # BUG: the dependency is down. The entry never lands, and the caller is
        # never told.
        return False
    record["ledger"].append({"actor": actor, "amount": 100})
    return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # keep the harness output readable
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
            self._json(200, {"ok": True, "defect": DEFECT})
            return
        if path.startswith("/api/invoices/"):
            invoice_id = path.rsplit("/", 1)[-1]
            with _LOCK:
                record = _ensure(invoice_id)
            self._json(200, dict(record))
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

        with _LOCK:
            record = _ensure(invoice_id)
            first_attempt = record["attempts"] == 0
            record["attempts"] += 1

            # -- authorization ---------------------------------------------
            authorized = actor in APPROVERS
            if DEFECT == "authz_retry" and not first_attempt:
                # BUG: the gate is only consulted on the first attempt for this
                # invoice. A refused caller who retries is let straight through.
                authorized = True
            if not authorized:
                # No _save here: `_ensure` has already persisted the record's
                # creation, and re-saving the whole in-memory map would leak
                # ui_lies' unwritten approvals onto disk and defeat that defect.
                self._json(403, {"error": "actor is not permitted to approve", "actor": actor})
                return

            # -- the effect -------------------------------------------------
            if DEFECT == "nonidempotent":
                # BUG: no check for an existing approval. Every call pays again.
                record["status"] = "approved"
                record["approvals"].append(actor)
                record["payments"].append({"actor": actor, "amount": 100})
                _ledger_write(record, actor)
                _save(_MEM)
                self._json(
                    200,
                    {
                        "status": "approved",
                        "payments": len(record["payments"]),
                        "ledger": "recorded",
                    },
                )
                return

            if DEFECT == "ui_lies":
                # BUG: the response and the in-memory read say approved; the
                # durable store is never written.
                if record["status"] != "approved":
                    record["status"] = "approved"
                    record["approvals"].append(actor)
                    record["payments"] = [{"actor": actor, "amount": 100}]
                    record["ledger"] = [{"actor": actor, "amount": 100}]
                # _save(_MEM) deliberately omitted.
                self._json(
                    200, {"status": "approved", "persisted": True, "ledger": "recorded"}
                )
                return

            # Correct effect path. Used by DEFECT=none, DEFECT=authz_retry and
            # DEFECT=partial_dep, so that each of those breaks exactly one thing.
            if record["status"] != "approved":
                record["status"] = "approved"
                record["approvals"].append(actor)
                record["payments"].append({"actor": actor, "amount": 100})
                _ledger_write(record, actor)
                _save(_MEM)
            self._json(
                200,
                {
                    "status": "approved",
                    "payments": len(record["payments"]),
                    # BUG for partial_dep: this claim is made unconditionally.
                    "ledger": "recorded",
                },
            )


def main() -> None:
    global _MEM
    if STORE.exists():
        STORE.unlink()
    _MEM = json.loads(json.dumps(_SEED))  # every boot starts from a clean store
    _save(_MEM)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
