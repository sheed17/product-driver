import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BODY = b"<html><body><h1>adjudicator clean page</h1><p>owner: unassigned</p></body></html>"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)


srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H)
srv.daemon_threads = True
srv.serve_forever()
