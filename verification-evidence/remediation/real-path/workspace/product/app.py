import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

STATE = os.environ["APPROVAL_STATE"]
HERE = os.path.dirname(os.path.abspath(__file__))
MEMORY = {"payments": []}


def fixed():
    # Read per request rather than captured at import: the "builder" fixes the
    # product by writing this marker, and the running service must pick that up
    # the same way a real code change would after a restart.
    return os.path.exists(os.path.join(HERE, "FIXED"))


def load():
    # The buggy build answers from memory and never consults what it wrote, so
    # a restarted process disagrees with its own ledger.
    if fixed() and os.path.exists(STATE):
        with open(STATE) as fh:
            return json.load(fh)
    return MEMORY


def save(state):
    MEMORY.update(state)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, STATE)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._reply(200, {"ok": True})
        state = load()
        return self._reply(200, {"payments": state["payments"]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._reply(400, {"error": "malformed request"})
        invoice = body.get("invoice")
        if not invoice:
            return self._reply(400, {"error": "invoice is required"})

        state = load()
        payments = list(state["payments"])
        if fixed() and invoice in payments:
            # Already paid. The truthful answer is the existing outcome, not a
            # second effect.
            return self._reply(200, {"status": "already approved", "invoice": invoice})
        payments.append(invoice)
        save({"payments": payments})
        return self._reply(200, {"status": "approved", "invoice": invoice})


HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
