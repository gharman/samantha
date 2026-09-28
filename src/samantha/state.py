"""Human-readable, home-directory resident state."""

from __future__ import annotations

import json
import os
import re
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_REDACTIONS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|passw(or)?d)\s*[:=]\s*\S+"),
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"Bearer\s+\S+", re.I),
)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        for pattern in _REDACTIONS:
            value = pattern.sub(
                lambda m: (m.group(1) + "=[REDACTED]") if m.lastindex else "[REDACTED]",
                value,
            )
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def state_root(project_key: str, override: str | Path | None = None) -> Path:
    base = Path(
        override or os.getenv("SAMANTHA_STATE_DIR") or Path.home() / ".samantha"
    )
    path = base / project_key
    path.mkdir(parents=True, exist_ok=True)
    if not override and not os.getenv("SAMANTHA_STATE_DIR"):
        try:
            base.chmod(0o700)
            path.chmod(0o700)
        except OSError:
            pass
    return path


class Journal:
    def __init__(self, root: Path, run_id: str | None = None) -> None:
        self.run_id = (
            run_id or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}"
        )
        self.path = root / f"journal-{self.run_id}.jsonl"
        self._seq = 0
        self._lock = threading.Lock()
        self._stream: Any = None
        # Readers need recent context on every chat round; serve it from memory rather
        # than re-reading a journal that grows for the life of the process. Chat keeps
        # its own buffer so bursts of metric rows cannot evict the conversation.
        self._recent: deque[dict[str, Any]] = deque(maxlen=200)
        self._chat: deque[dict[str, Any]] = deque(maxlen=100)

    def write(self, event_type: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            row = redact(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "run_id": self.run_id,
                    "seq": self._seq,
                    "type": event_type,
                    **fields,
                }
            )
            if self._stream is None:
                # Reopening per row cost ~20x a held handle on every metric line.
                self._stream = self.path.open("a", encoding="utf-8")
            self._stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            self._stream.flush()
            self._recent.append(row)
            if event_type == "chat":
                self._chat.append(row)
            return row

    def recent(
        self, limit: int = 50, event_type: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._chat if event_type == "chat" else self._recent)
        if event_type not in {None, "chat"}:
            rows = [row for row in rows if row.get("type") == event_type]
        return rows[-limit:]

    def close(self) -> None:
        with self._lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(redact(value), indent=2, sort_keys=True, default=str) + "\n"
    )
    tmp.replace(path)
