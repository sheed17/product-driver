"""A disposable local web app for the generated-browser-scenario proof.

Loopback only, stdlib only, no credentials, no external hosts. It renders an
exception-detail page whose content is driven by a durable JSON store, so a
browser scenario has something real to assert and a state probe has something
real to read.

DEFECT=stale_read  the page keeps serving the status it rendered first, so the
                   screen and the durable store disagree after a resolution.
DEFECT=none        correct.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFECT = os.environ.get("DEFECT", "none")
PORT = int(os.environ.get("PORT", "8851"))
STORE = Path(os.environ.get("STORE", Path(__file__).with_name("store.json")))

_LOCK = threading.Lock()
_SEED = {
    "EX-1": {
        "id": "EX-1",
        "status": "open",
        "reason": "proof of delivery missing",
        "owner": "Neyma",
        "next_action": "chase the carrier for proof of delivery",
    }
}
_CACHE: dict[str, dict] = {}


def _load() -> dict:
    if not STORE.exists():
        return json.loads(json.dumps(_SEED))
    try:
        return json.loads(STORE.read_text())
    except ValueError:
        return json.loads(json.dumps(_SEED))


def _save(data: dict) -> None:
    STORE.write_text(json.dumps(data, indent=2))


PAGE = """<!doctype html>
<html><head><title>Exception {id}</title></head><body>
<h1 id="heading">Exception {id}</h1>
<p id="status">status: {status}</p>
<p id="reason">why: {reason}</p>
<p id="owner">owner: {owner}</p>
<p id="next">next action: {next_action}</p>
<form method="post" action="/api/exceptions/{id}/resolve">
  <button id="resolve" type="submit">Resolve</button>
</form>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: int, text: str) -> None:
        self._send(status, text.encode(), "text/html; charset=utf-8")

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health":
            self._json(200, {"ok": True, "defect": DEFECT})
            return
        if path in ("/", "/exceptions/EX-1"):
            with _LOCK:
                data = _load()
                record = data.get("EX-1", _SEED["EX-1"])
                if DEFECT == "stale_read":
                    # BUG: the first render is cached forever, so the page can
                    # keep claiming "open" after the store says "resolved".
                    record = _CACHE.setdefault("EX-1", dict(record))
            self._html(200, PAGE.format(**record))
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if not path.endswith("/resolve"):
            self._json(404, {"error": "not found"})
            return
        with _LOCK:
            data = _load()
            record = data.setdefault("EX-1", dict(_SEED["EX-1"]))
            record["status"] = "resolved"
            record["next_action"] = "none; the exception is closed"
            _save(data)
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()


def main() -> None:
    if STORE.exists():
        STORE.unlink()
    _save(json.loads(json.dumps(_SEED)))
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
