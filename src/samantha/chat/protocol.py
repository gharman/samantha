"""NDJSON chat messages."""

from __future__ import annotations
import json
from typing import Any

VALID = {"hello", "status", "ask", "answer", "flag", "ack", "intent", "command", "bye"}


def encode(kind: str, **fields: Any) -> bytes:
    if kind not in VALID:
        raise ValueError(kind)
    return (json.dumps({"type": kind, **fields}, default=str) + "\n").encode()


def decode(line: bytes) -> dict[str, Any]:
    message = json.loads(line)
    if message.get("type") not in VALID:
        raise ValueError("unknown message type")
    return message
