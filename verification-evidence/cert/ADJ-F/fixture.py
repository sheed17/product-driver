"""ADJ-F disposable loopback fixture. Local only. No external hosts.

Paths:
  /            clean page, text "adjudicator clean page"
  /hang        never responds (holds the connection open) -> goto times out
  /boom        page containing the string "Traceback (most recent call last):"
  /quiet       page containing only "nothing to see"
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGES = {
    "/": "<html><body><h1>adjudicator clean page</h1><p>owner: unassigned</p></body></html>",
    "/boom": (
        "<html><body><pre>Traceback (most recent call last):\n"
        "  File \"app.py\", line 1, in handler\n"
        "RuntimeError: exception detail unavailable</pre></body></html>"
    ),
    "/quiet": "<html><body><p>nothing to see</p></body></html>",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/hang":
            # Send nothing at all; the client waits until its own timeout.
            time.sleep(600)
            return
        body = PAGES.get(path)
        if body is None:
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>not found</body></html>")
            return
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def start() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
