"""Deterministic local verification target for the scale audit.

Loopback only. SQLite-backed so persisted-state checks are real.

Injected defects are controlled by env vars so the same binary can produce a
green suite and a suite with specific failing cases:

  TARGET_BAD_IDS      ids whose GET /item/<n> returns the wrong owner
  TARGET_DUP_IDS      ids where POST /approve applies twice (idempotency bug)
  TARGET_500_IDS      ids whose GET /item/<n> returns HTTP 500
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get("TARGET_DB", "/tmp/r4-target.sqlite")
PORT = int(os.environ.get("TARGET_PORT", "8731"))


def _ids(name: str) -> set[int]:
    raw = os.environ.get(name, "").strip()
    return {int(x) for x in raw.split(",") if x.strip()}


BAD = _ids("TARGET_BAD_IDS")
DUP = _ids("TARGET_DUP_IDS")
ERR = _ids("TARGET_500_IDS")

_lock = threading.Lock()


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init() -> None:
    with conn() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS approvals "
            "(seq INTEGER PRIMARY KEY AUTOINCREMENT, item INTEGER, key TEXT)"
        )
        c.execute("DELETE FROM approvals")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):  # quiet
        pass

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, html: str) -> None:
        body = html.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path
        if path == "/health":
            return self._send(200, {"status": "ready"})
        if path.startswith("/item/"):
            n = int(path.rsplit("/", 1)[-1])
            if n in ERR:
                return self._send(500, {"error": "internal", "item": n})
            owner = "tenant-a" if n not in BAD else "tenant-b"
            return self._send(
                200, {"item": n, "owner": owner, "label": f"item-{n}", "state": "open"}
            )
        if path.startswith("/approvals/"):
            n = int(path.rsplit("/", 1)[-1])
            with conn() as c:
                rows = c.execute(
                    "SELECT COUNT(*) FROM approvals WHERE item=?", (n,)
                ).fetchone()
            return self._send(200, {"item": n, "approval_count": rows[0]})
        if path.startswith("/ui/"):
            n = int(path.rsplit("/", 1)[-1])
            owner = "tenant-a" if n not in BAD else "tenant-b"
            return self._send_html(
                200,
                f"<html><body><h1 id='label'>item-{n}</h1>"
                f"<p id='owner'>{owner}</p>"
                f"<button id='approve'>Approve</button></body></html>",
            )
        return self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        if self.path != "/approve":
            return self._send(404, {"error": "not found"})
        item = int(payload.get("item", 0))
        key = str(payload.get("key", ""))
        with _lock, conn() as c:
            existing = c.execute(
                "SELECT COUNT(*) FROM approvals WHERE item=? AND key=?", (item, key)
            ).fetchone()[0]
            # Correct behaviour: an idempotency key applies at most once.
            # Injected defect: for DUP ids the key is ignored.
            if existing == 0 or item in DUP:
                c.execute("INSERT INTO approvals(item, key) VALUES (?,?)", (item, key))
            total = c.execute(
                "SELECT COUNT(*) FROM approvals WHERE item=?", (item,)
            ).fetchone()[0]
        return self._send(200, {"item": item, "approval_count": total, "ok": True})


if __name__ == "__main__":
    init()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"target listening on 127.0.0.1:{PORT} db={DB}", flush=True)
    print(f"injected: BAD={sorted(BAD)} DUP={sorted(DUP)} ERR={sorted(ERR)}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
