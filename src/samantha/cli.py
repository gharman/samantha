"""Attach a terminal to a Samantha resident in a running Python process."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path


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
            found.append((int(path.stem), path))
        except ValueError:
            continue
    return sorted(found)


def _select(pid: int | None) -> Path | None:
    if pid is not None:
        candidate = runtime_dir() / f"{pid}.sock"
        return candidate if candidate.exists() else None
    found = residents()
    if len(found) == 1:
        return found[0][1]
    return None


def connect(path: Path) -> int:
    with socket.socket(socket.AF_UNIX) as client:
        client.connect(str(path))
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
                client.sendall(message.encode())
                response = _message(incoming.readline())
                print(response.get("reply") or response.get("error") or "No response.")
                print()
                if kind == "bye":
                    break
        except (EOFError, KeyboardInterrupt):
            pass
    return 0


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
