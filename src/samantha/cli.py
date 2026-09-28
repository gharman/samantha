"""Attach a terminal to a Samantha resident in a running Python process."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import socket
import time
from pathlib import Path
from typing import Any


def runtime_dir() -> Path:
    base = Path(os.getenv("XDG_RUNTIME_DIR", f"/tmp/samantha-{os.getuid()}"))
    return base / "samantha"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="samantha")
    result.add_argument("--pid", type=int, help="connect to this Python process")
    result.add_argument(
        "--list", action="store_true", help="list running Samantha residents"
    )
    return result


def residents() -> list[tuple[int, Path]]:
    found = []
    for path in runtime_dir().glob("*.sock"):
        try:
            pid = int(path.stem)
        except ValueError:
            continue
        if _alive(pid):
            found.append((pid, path))
        else:
            _remove_stale(path)
    return sorted(found)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _remove_stale(path: Path) -> None:
    """Remove files left by a resident that exited without orderly shutdown."""
    for stale in (path, path.with_suffix(".pid")):
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass


def _select(pid: int | None) -> Path | None:
    if pid is not None:
        candidate = runtime_dir() / f"{pid}.sock"
        return candidate if candidate.exists() else None
    found = residents()
    if len(found) == 1:
        return found[0][1]
    return None


def connect(path: Path) -> int:
    _configure_history()
    with socket.socket(socket.AF_UNIX) as client:
        try:
            client.connect(str(path))
        except (ConnectionRefusedError, FileNotFoundError):
            # A reused PID can look alive while nothing listens on the socket.
            _remove_stale(path)
            print(f"Samantha in process {path.stem} is no longer running.")
            return 1
        incoming = client.makefile("rb")
        hello = _message(incoming.readline())
        print(f"Connected to Samantha in process {hello.get('pid', path.stem)}")
        print(
            f"Project: {hello.get('root', 'unknown')} ({hello.get('project', 'unknown')})"
        )
        print(
            f"Mode: {hello.get('mode', 'unknown')} · Model: {hello.get('model', 'unknown')}"
        )
        print()
        try:
            while line := input("samantha> "):
                kind = "bye" if line.strip() in {"bye", "quit", "exit"} else "command"
                message = json.dumps({"type": kind, "text": line}) + "\n"
                try:
                    client.sendall(message.encode())
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return _continue_detached(hello, line, delivered=False)
                print("Samantha is working…", flush=True)
                payload = incoming.readline()
                if not payload:
                    return _continue_detached(hello, line, delivered=True)
                response = _message(payload)
                print(response.get("reply") or response.get("error") or "No response.")
                print()
                if kind == "bye":
                    break
        except (EOFError, KeyboardInterrupt):
            pass
    return 0


def _continue_detached(
    hello: dict[str, object],
    pending: str | None = None,
    *,
    delivered: bool = False,
) -> int:
    """Continue a memory-backed conversation after the resident process exits."""
    pid = hello.get("pid", "unknown")
    print(f"\nThe host process {pid} is no longer active.")
    print(
        "Continuing in detached chat mode; live inspection and actions are unavailable.\n"
    )
    delivery_note = (
        "The last message was sent, but the process ended before returning a response; its "
        "effects are unknown and must not be claimed."
        if delivered
        else "The last message was not delivered to the resident process."
    )
    root = Path(str(hello.get("root") or Path.cwd())).resolve()
    project = str(hello.get("project") or "unknown")
    try:
        from samantha._boot import Config
        from samantha.cognition.llm import OpenAILLM
        from samantha.state import Journal, state_root

        config = Config.resolve(_dotenv_start=root)
        if not config.api_key:
            print(
                "Detached chat is unavailable because no model credentials were found."
            )
            return 0
        model = str(hello.get("model") or config.model)
        llm = OpenAILLM(config.api_key, model, config.base_url, config.llm_timeout)
        state = state_root(project, config.state_dir)
        journal = Journal(state, f"detached-{int(time.time())}-{os.getpid()}")
        theory = _read_text(state / "model.md", 30_000)
        history = _detached_history(state)
    except Exception as exc:
        print(f"Detached chat could not start: {exc}")
        return 0

    line = pending
    while True:
        try:
            line = line if line is not None else input("samantha (detached)> ")
        except (EOFError, KeyboardInterrupt):
            return 0
        if not line or line.strip() in {"bye", "quit", "exit"}:
            return 0
        messages = [
            {
                "role": "user",
                "content": (
                    "The observed Python process has ended. Continue Samantha's conversation "
                    "using persisted project memory, but do not claim live inspection, runtime "
                    f"state, or actions. {delivery_note} If this message requested an action, "
                    "discuss what could be done after a new resident starts.\n\n"
                    f"Project root: {root}\nPersisted theory:\n{theory}\n\n"
                    f"Recent conversation:\n{json.dumps(history)}\n\n"
                    f"Operator: {line}"
                ),
            }
        ]
        print("Samantha is working…", flush=True)
        try:
            reply = str(llm.chat(messages))
        except Exception as exc:
            print(f"Detached model request failed: {exc}\n")
        else:
            print(f"{reply}\n")
            journal.write("chat", direction="in", text=line, detached=True)
            journal.write("chat", direction="out", text=reply, detached=True)
            history.extend(
                [
                    {"direction": "in", "text": line},
                    {"direction": "out", "text": reply},
                ]
            )
            history = history[-40:]
        line = None


def _detached_history(state: Path, limit: int = 40) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(state.glob("journal-*.jsonl"))[-5:]:
        try:
            for raw in path.read_text(errors="replace").splitlines():
                row = json.loads(raw)
                if row.get("type") == "chat":
                    rows.append(
                        {
                            "direction": row.get("direction"),
                            "text": row.get("text"),
                        }
                    )
        except (OSError, json.JSONDecodeError):
            continue
    return rows[-limit:]


def _read_text(path: Path, limit: int) -> str:
    try:
        return path.read_text(errors="replace")[:limit]
    except OSError:
        return "No persisted project theory is available."


def _configure_history() -> None:
    try:
        import readline
    except ImportError:
        return
    directory = Path.home() / ".samantha"
    history = directory / "cli-history"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
        readline.read_history_file(history)
    except FileNotFoundError:
        pass
    except OSError:
        return
    readline.set_history_length(1_000)

    def save() -> None:
        try:
            readline.write_history_file(history)
            history.chmod(0o600)
        except OSError:
            pass

    atexit.register(save)


def _message(line: bytes) -> dict[str, object]:
    try:
        return json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"error": "The resident sent an unreadable response."}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    found = residents()
    if args.list:
        for pid, _ in found:
            print(pid)
        return 0
    path = _select(args.pid)
    if path is None:
        if found:
            parser().error("multiple residents are running; choose one with --pid")
        parser().error("no running Samantha resident found")
    return connect(path)
