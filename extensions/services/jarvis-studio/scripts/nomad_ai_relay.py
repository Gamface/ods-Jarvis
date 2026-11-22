#!/usr/bin/env python3
"""Let Project NOMAD's AI chat use the same cloud model as Hermes.

NOMAD's AI client can point at any OpenAI-compatible server, but it always
sends the dummy key "nomad" and can't send a real one. This relay listens on
NOMAD's private Docker network only, adds the OpenRouter key from Hermes's
.env, and pins the model to the one Hermes uses (model.default in Hermes's
config.yaml, re-read on each request). In NOMAD it is set as the remote AI
URL http://172.18.0.1:8765 (the gateway of the
project-nomad_default network; host.docker.internal points at docker0 instead).

Endpoints: GET /v1/models (just Hermes's model), POST /v1/chat/completions
(streaming passed through). Everything else returns 404. Standard library only.
"""

import http.client
import json
import os
import re
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERMES = Path(os.environ.get("HERMES_DATA", "~/ods/data/hermes")).expanduser()
BIND = os.environ.get("NOMAD_RELAY_BIND", "172.18.0.1")
PORT = int(os.environ.get("NOMAD_RELAY_PORT", "8765"))
UPSTREAM = "openrouter.ai"
CTX = ssl.create_default_context()


def _key() -> str:
    for line in (HERMES / ".env").read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("OPENROUTER_API_KEY is not set in Hermes's .env")


def _model() -> str:
    m = re.search(r'^\s{2}default:\s*"?([^"\s#]+)', (HERMES / "config.yaml").read_text(), re.M)
    model = m.group(1) if m else "openrouter/free"
    # Hermes on the local model ("ods/current") isn't reachable through OpenRouter.
    return model if "/" in model and not model.startswith("ods/") else "openrouter/free"


class Relay(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _json(self, code: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            m = _model()
            return self._json(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "openrouter"}]})
        if self.path.rstrip("/") in ("", "/health"):
            return self._json(200, {"ok": True, "model": _model()})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        # Always consume the body, or it corrupts the next request on a kept-alive connection.
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            return self._json(404, {"error": "only /v1/chat/completions is relayed"})
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._json(400, {"error": "invalid JSON"})
        body["model"] = _model()
        data = json.dumps(body).encode()
        conn = http.client.HTTPSConnection(UPSTREAM, context=CTX, timeout=300)
        try:
            conn.request("POST", "/api/v1/chat/completions", data, {
                "Authorization": f"Bearer {_key()}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:8080",
                "X-Title": "Project NOMAD (via Jarvis)",
            })
            up = conn.getresponse()
            self.send_response(up.status)
            self.send_header("Content-Type", up.getheader("Content-Type", "application/json"))
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while chunk := up.read1(8192):
                self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except (OSError, http.client.HTTPException) as e:
            if not self.wfile.closed:
                try:
                    self._json(502, {"error": f"upstream error: {type(e).__name__}"})
                except OSError:
                    pass
        finally:
            conn.close()


if __name__ == "__main__":
    _key()  # fail fast if the key is missing (never printed)
    print(f"NOMAD AI relay on http://{BIND}:{PORT} -> {UPSTREAM} model {_model()}", flush=True)
    ThreadingHTTPServer((BIND, PORT), Relay).serve_forever()
