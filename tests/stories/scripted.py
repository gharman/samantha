"""A scripted stand-in for the OpenAI Responses API, for the offline stories.

Only the tests use it. It answers on 127.0.0.1 with fixed text that starts with
LABEL, so a screen from a story cannot pass for a real model's words.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

LABEL = "(scripted model)"


def message(text: str) -> dict[str, Any]:
    """One Responses API payload with this output text."""
    return {"id": "resp_scripted", "object": "response", "status": "completed",
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": text}]}]}


def format_name(body: dict[str, Any]) -> str | None:
    return ((body.get("text") or {}).get("format") or {}).get("name")


def serve(respond: Callable[[dict[str, Any]], dict[str, Any]]) -> ThreadingHTTPServer:
    """Start the scripted model on a free local port, on a daemon thread.
    respond(body) returns the Responses API payload for one request body."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/v1/models/"):
                self._send(200, {"id": self.path.rsplit("/", 1)[-1], "object": "model"})
            else:
                self._send(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/responses":
                self._send(404, {"error": {"message": "not found"}})
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            try:
                self._send(200, respond(body))
            except Exception as exc:
                self._send(500, {"error": {"message": f"scripted model: {exc!r}"}})

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, name="scripted-model", daemon=True).start()
    return server


def scripted_env(server: ThreadingHTTPServer) -> dict[str, str]:
    """The settings that point Samantha at the scripted model."""
    return {
        "OPENAI_API_KEY": "none",  # not a key: the scripted model does not check it
        "SAMANTHA_MODEL": "scripted-model",
        "SAMANTHA_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
    }
