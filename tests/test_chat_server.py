import json
import shutil
import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from samantha.chat.server import ChatServer
from samantha.state import Journal


class SlowCognition:
    def __init__(self) -> None:
        self.release = threading.Event()

    def chat(self, text: str) -> str:
        if text == "boom":
            raise RuntimeError("provider exploded")
        if text == "slow":
            self.release.wait(5)
        return f"echo {text}"


@pytest.fixture
def server(tmp_path: Path) -> Iterator[ChatServer]:
    # AF_UNIX paths are limited to ~104 bytes; pytest's tmp_path is often longer.
    runtime = Path(tempfile.mkdtemp(prefix="sam-", dir="/tmp"))
    resident = SimpleNamespace(
        config=SimpleNamespace(mode="advise", model="test-model"),
        key="project",
        root=tmp_path,
        state=tmp_path,
        journal=Journal(tmp_path, "run"),
        cognition=SlowCognition(),
    )
    chat = ChatServer(resident, runtime / "samantha")
    chat.start()
    yield chat
    chat.stop()
    shutil.rmtree(runtime, ignore_errors=True)


def ask(chat: ChatServer, text: str, hangup: bool = False) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        client.connect(str(chat.path))
        incoming = client.makefile("rb")
        incoming.readline()
        client.sendall((json.dumps({"type": "command", "text": text}) + "\n").encode())
        if hangup:
            return {}
        return json.loads(incoming.readline())


def test_client_hangup_mid_reply_does_not_end_chat(server: ChatServer) -> None:
    ask(server, "slow", hangup=True)
    time.sleep(0.1)
    server.resident.cognition.release.set()
    assert ask(server, "still there?")["reply"] == "echo still there?"


def test_failed_request_returns_error_and_chat_continues(server: ChatServer) -> None:
    failed = ask(server, "boom")
    assert failed["ok"] is False
    assert "provider exploded" in str(failed["error"])
    assert ask(server, "again")["reply"] == "echo again"
