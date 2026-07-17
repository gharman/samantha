"""Nonblocking-to-host Unix socket chat server."""

from __future__ import annotations
import json
import logging
import os
import socket
import threading
from pathlib import Path
from typing import Any
from .protocol import decode, encode


class ChatServer:
    def __init__(self, resident: Any, runtime: Path) -> None:
        self.resident, self.runtime = resident, runtime
        self.path = runtime / f"{os.getpid()}.sock"
        self.sock: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.running = False

    def start(self) -> None:
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.runtime.stat().st_mode & 0o077:
            raise PermissionError("unsafe runtime directory permissions")
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.sock.listen()
        (self.runtime / f"{os.getpid()}.pid").write_text(
            json.dumps({"pid": os.getpid(), "project": self.resident.key})
        )
        self.running = True
        self.thread = threading.Thread(
            target=self._serve, name="samantha-sock", daemon=True
        )
        self.thread.start()

    def _serve(self) -> None:
        try:
            assert self.sock
            while self.running:
                client, _ = self.sock.accept()
                with client:
                    self._client(client)
        except Exception as exc:
            if self.running:
                logging.getLogger("samantha").warning("chat disabled: %s", exc)

    def _client(self, client: socket.socket) -> None:
        client.sendall(
            encode(
                "hello",
                mode=self.resident.config.mode,
                project=self.resident.key,
                pid=os.getpid(),
                root=str(self.resident.root),
                model=self.resident.config.model,
            )
        )
        for line in client.makefile("rb"):
            try:
                message = decode(line)
            except (ValueError, json.JSONDecodeError):
                continue
            self.resident.journal.write(
                "chat", direction="in", text=json.dumps(message)
            )
            response = self._handle(message)
            client.sendall(encode("status", **response))
            self.resident.journal.write(
                "chat", direction="out", text=json.dumps(response)
            )
            if message["type"] == "bye":
                break

    def _handle(self, message: dict[str, Any]) -> dict[str, Any]:
        text = str(message.get("text", ""))
        if text.startswith("goal "):
            goal = text[5:]
            self.resident.config.goal = goal
            if message.get("persist"):
                with (self.resident.state / "goals.md").open("a") as stream:
                    stream.write(goal + "\n")
            return {
                "ok": True,
                "mode": self.resident.config.mode,
                "reply": "Goal updated.",
            }
        if text and (cognition := getattr(self.resident, "cognition", None)):
            return {
                "ok": True,
                "mode": self.resident.config.mode,
                "reply": cognition.chat(text),
            }
        return {
            "ok": True,
            "mode": self.resident.config.mode,
            "reply": "Cognition is dormant; configure OPENAI_API_KEY in .env.",
        }

    def stop(self) -> None:
        self.running = False
        if self.sock:
            self.sock.close()
        self.path.unlink(missing_ok=True)
        (self.runtime / f"{os.getpid()}.pid").unlink(missing_ok=True)
