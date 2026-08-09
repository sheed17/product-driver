"""Intentionally buggy invoice-approval service.

Task it pretends to implement:
    "Implement an approval endpoint with persistent state."

Three seeded defects, selected by the DEFECT environment variable. Each is a
different *kind* of wrongness, so an adaptive verifier that actually reads the
evidence should steer somewhere different in each case.

  DEFECT=nonidempotent   POST /approve applies the payment effect EVERY time.
                         Approving twice pays twice. Persisted state is honest.
  DEFECT=ui_lies         POST /approve returns 200 {"status":"approved"} and the
                         in-memory read reflects it, but nothing is written to
                         the durable store. A fresh process (or the state CLI,
                         which reads the file) still sees "pending".
  DEFECT=uncertain       POST /approve applies the effect, then stalls past any
                         sane client timeout before answering. The caller times
                         out and cannot tell whether the effect happened.
  DEFECT=none            Correct behaviour (control).

Loopback only. No external effects. Stdlib only.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFECT = os.environ.get("DEFECT", "nonidempotent")
PORT = int(os.environ.get("PORT", "8731"))
STORE = Path(os.environ.get("STORE", Path(__file__).with_name("store.json")))

_LOCK = threading.Lock()

_SEED = {
    "INV-1": {"id": "INV-1", "status": "pending", "approvals": [], "payments": []},
    "INV-2": {"id": "INV-2", "status": "pending", "approvals": [], "payments": []},
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
        record = {"id": invoice_id, "status": "pending", "approvals": [], "payments": []}
        _MEM[invoice_id] = record
        _save(_MEM)
    return record


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
        stall = 0.0

        with _LOCK:
            record = _ensure(invoice_id)

            if DEFECT == "nonidempotent":
                # BUG: no check for an existing approval. Every call pays again.
                record["status"] = "approved"
                record["approvals"].append(actor)
                record["payments"].append({"actor": actor, "amount": 100})
                _save(_MEM)
                self._json(200, {"status": "approved", "payments": len(record["payments"])})
                return

            if DEFECT == "ui_lies":
                # BUG: the response and the in-memory read say approved; the
                # durable store is never written.
                record["status"] = "approved"
                record["approvals"].append(actor)
                record["payments"] = [{"actor": actor, "amount": 100}]
                # _save(_MEM) deliberately omitted.
                self._json(200, {"status": "approved", "persisted": True})
                return

            if DEFECT == "uncertain":
                # BUG: the effect lands, then the response stalls past the
                # client's timeout. The caller cannot tell what happened.
                if record["status"] != "approved":
                    record["status"] = "approved"
                    record["approvals"].append(actor)
                    record["payments"].append({"actor": actor, "amount": 100})
                    _save(_MEM)
                stall = float(os.environ.get("STALL_S", "8"))

        if DEFECT == "uncertain":
            time.sleep(stall)
            try:
                self._json(200, {"status": "approved"})
            except OSError:
                pass
            return

        # DEFECT=none -- correct, idempotent, durable.
        with _LOCK:
            record = _MEM[invoice_id]
            if record["status"] != "approved":
                record["status"] = "approved"
                record["approvals"].append(actor)
                record["payments"].append({"actor": actor, "amount": 100})
                _save(_MEM)
            self._json(200, {"status": "approved", "payments": len(record["payments"])})


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
