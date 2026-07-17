"""Human-readable, home-directory resident state."""

from __future__ import annotations

import json
import os
import re
import threading
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
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            return row


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
