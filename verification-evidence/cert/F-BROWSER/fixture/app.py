"""Disposable exception-detail fixture for the F-BROWSER certification.

Loopback only, stdlib only, no credentials, no external host. Started and torn
down by the driver as an ordinary declared service.

The seeded UI defect shape here is deliberately NOT the recorded ``stale_read``
one. It is ``phantom_owner``: the screen FABRICATES backend data it was never
given — it renders an owner and a next action that the durable store does not
hold. Clean, the same screen says "unassigned" / "none recorded".

Env:
    PORT    loopback port to bind
    STORE   path to the durable JSON store (recreated at startup)
    DEFECT  comma-separated flags, default "none":
              phantom_owner   the screen invents an owner + next action
              traceback       the screen renders a Python traceback
              console_error   the page throws a JS error on load
              swallowed_500   the page fetches a 500 and hides the failure
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8099"))
STORE = Path(os.environ.get("STORE", "store.json"))
DEFECTS = {d.strip() for d in os.environ.get("DEFECT", "none").split(",") if d.strip()}

INITIAL = {
    "EX-1": {
        "id": "EX-1",
        "status": "open",
        "owner": "",
        "next_action": "",
    }
}


def load() -> dict:
    try:
        return json.loads(STORE.read_text())
    except Exception:
        return json.loads(json.dumps(INITIAL))


def save(data: dict) -> None:
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(STORE)


PAGE = """<!doctype html>
<title>Exception EX-1</title>
<body>
<h1>Exception EX-1</h1>
<p>status: {status}</p>
<p>owner: {owner}</p>
<p>next action: {next_action}</p>
<button id="resolve">Resolve</button>
<button id="assign">Assign to me</button>
{extra}
<script>
document.getElementById('resolve').addEventListener('click', async () => {{
  await fetch('/resolve', {{method: 'POST'}});
  location.reload();
}});
document.getElementById('assign').addEventListener('click', async () => {{
  await fetch('/assign', {{method: 'POST'}});
  location.reload();
}});
{script}
</script>
</body>
"""


def render_detail() -> str:
    record = load()["EX-1"]
    owner = record["owner"]
    next_action = record["next_action"]
    if "phantom_owner" in DEFECTS:
        # The UI lie: data the durable store never held, rendered as fact.
        owner = owner or "Dana Whitfield"
        next_action = next_action or "call the carrier"
    extra = ""
    script = ""
    if "traceback" in DEFECTS:
        extra = (
            "<pre>Traceback (most recent call last):\n"
            '  File "app.py", line 1, in render\n'
            "RuntimeError: exception detail unavailable</pre>"
        )
    if "console_error" in DEFECTS:
        script = "throw new Error('render pipeline blew up');"
    if "swallowed_500" in DEFECTS:
        script += "\nfetch('/boom').catch(() => {});"
    return PAGE.format(
        status=record["status"],
        owner=owner or "unassigned",
        next_action=next_action or "none recorded",
        extra=extra,
        script=script,
    )


def offsite_page(target: str) -> str:
    return (
        "<!doctype html><title>offsite</title><body>"
        "<h1>Exception EX-1</h1>"
        f'<p><a id="offsite" href="{target}">carrier portal</a></p>'
        f'<img id="pixel" src="{target}/pixel.png">'
        "</body>"
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):  # quiet
        pass

    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8") -> None:
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send(200, "ok", "text/plain")
        elif path in ("/", "/detail"):
            self._send(200, render_detail())
        elif path == "/hang":
            time.sleep(120)
        elif path == "/boom":
            self._send(500, "boom", "text/plain")
        elif path == "/offsite":
            target = (parse_qs(parsed.query).get("target") or [""])[0]
            self._send(200, offsite_page(target))
        elif path == "/quiet":
            self._send(200, "<!doctype html><title>quiet</title><body><p>nothing here</p></body>")
        else:
            self._send(404, "<!doctype html><body><p>not found</p></body>")

    def do_POST(self) -> None:
        data = load()
        if self.path == "/resolve":
            data["EX-1"]["status"] = "resolved"
            save(data)
            self._send(200, "ok", "text/plain")
        elif self.path == "/assign":
            data["EX-1"]["owner"] = "operator@local"
            data["EX-1"]["next_action"] = "call the carrier"
            save(data)
            self._send(200, "ok", "text/plain")
        else:
            self._send(404, "no", "text/plain")


def main() -> None:
    save(json.loads(json.dumps(INITIAL)))  # a fresh store per service start
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
