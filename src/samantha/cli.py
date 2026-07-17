"""Command line interface."""
from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path

from .identity import discover_project
from .runner import run
from .state import state_root


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="samantha")
    sub = root.add_subparsers(dest="command", required=True)
    launch = sub.add_parser("run")
    launch.add_argument("--mode", choices=("off", "observe", "advise", "act"))
    launch.add_argument("--goal")
    launch.add_argument("--state-dir")
    launch.add_argument("script")
    launch.add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("ps")
    status = sub.add_parser("status")
    status.add_argument("--state-dir")
    chat = sub.add_parser("chat")
    chat.add_argument("--pid", type=int)
    return root


def runtime_dir() -> Path:
    path = Path(os.getenv("XDG_RUNTIME_DIR", f"/tmp/samantha-{os.getuid()}")) / "samantha"
    return path


def main(argv: list[str] | None = None) -> int:
    ns = parser().parse_args(argv)
    if ns.command == "run":
        args = ns.args[1:] if ns.args[:1] == ["--"] else ns.args
        return run(ns.script, args, mode=ns.mode, goal=ns.goal, state_dir=ns.state_dir)
    if ns.command == "ps":
        for pidfile in sorted(runtime_dir().glob("*.pid")):
            print(pidfile.read_text().strip())
        return 0
    if ns.command == "chat":
        sockets = [runtime_dir() / f"{ns.pid}.sock"] if ns.pid else list(runtime_dir().glob("*.sock"))
        if not sockets:
            return 1
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(sockets[0])); incoming = client.makefile("rb")
            print(incoming.readline().decode().strip())
            try:
                while line := input("samantha> "):
                    kind = "bye" if line == "bye" else "command"
                    client.sendall((json.dumps({"type": kind, "text": line}) + "\n").encode())
                    print(incoming.readline().decode().strip())
                    if line == "bye": break
            except EOFError: pass
        return 0
    root, key = discover_project()
    state = state_root(key, ns.state_dir)
    print(json.dumps({"project": str(root), "key": key, "state": str(state)}))
    return 0
