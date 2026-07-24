import io
import json
from pathlib import Path

from samantha import cli


class BrokenSocket:
    def __enter__(self) -> "BrokenSocket":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def connect(self, path: str) -> None:
        pass

    def makefile(self, mode: str) -> io.BytesIO:
        hello = {
            "type": "hello",
            "pid": 123,
            "root": "/tmp/example",
            "project": "abc",
            "model": "test-model",
            "mode": "act",
        }
        return io.BytesIO((json.dumps(hello) + "\n").encode())

    def sendall(self, payload: bytes) -> None:
        raise BrokenPipeError


def test_broken_pipe_transitions_to_detached_chat(
    monkeypatch: object, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli.socket, "socket", lambda *args: BrokenSocket())
    monkeypatch.setattr("builtins.input", lambda prompt: "are you there?")
    monkeypatch.setattr(cli, "_configure_history", lambda: None)

    def detached(
        hello: dict[str, object],
        pending: str | None = None,
        *,
        delivered: bool = False,
    ) -> int:
        captured.update(hello=hello, pending=pending, delivered=delivered)
        return 0

    monkeypatch.setattr(cli, "_continue_detached", detached)
    assert cli.connect(tmp_path / "resident.sock") == 0
    assert captured["pending"] == "are you there?"
    assert captured["delivered"] is False
    assert captured["hello"]["pid"] == 123


def test_detached_history_reads_recent_persisted_chat(tmp_path: Path) -> None:
    journal = tmp_path / "journal-run.jsonl"
    journal.write_text(
        json.dumps({"type": "chat", "direction": "in", "text": "hello"})
        + "\n"
        + json.dumps({"type": "observation", "text": "ignored"})
        + "\n"
        + json.dumps({"type": "chat", "direction": "out", "text": "hi"})
        + "\n"
    )
    assert cli._detached_history(tmp_path) == [
        {"direction": "in", "text": "hello"},
        {"direction": "out", "text": "hi"},
    ]
