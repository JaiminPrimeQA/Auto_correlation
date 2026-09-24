"""Minimal local mock API for JMeter smoke testing.

Issues a fresh random token on POST /auth/login and only returns 200 from
GET /me when the Authorization header carries that exact token. This lets the
smoke test prove that JMeter extracted the token and propagated it downstream:
a 200 means correlation worked; a 401 means the stale baked value was sent.

No real credentials or external hosts are involved.
"""

from __future__ import annotations

import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_STATE: dict[str, str] = {"token": ""}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0) or 0)
        self.rfile.read(length)
        if self.path.startswith("/auth/login"):
            token = "tok_" + uuid.uuid4().hex
            _STATE["token"] = token
            self._send(200, {"token": token, "expiresIn": 3600})
        else:
            self._send(404, {"error": "not found"})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/me"):
            auth = self.headers.get("Authorization", "")
            if auth == f"Bearer {_STATE['token']}" and _STATE["token"]:
                self._send(200, {"id": 1, "name": "Alice", "correlated": True})
            else:
                self._send(401, {"error": "unauthorized"})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *args) -> None:  # silence default logging
        pass


def main(port: int = 8080) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock api listening on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    import sys

    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8080)
