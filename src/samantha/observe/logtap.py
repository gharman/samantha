"""Transparent logging and stdio taps."""

from __future__ import annotations

import io
import logging
import sys
import time
import threading
import uuid
from collections.abc import Callable
from typing import Any


class _Handler(logging.Handler):
    def __init__(self, emit_line: Callable[[str, str], None]) -> None:
        super().__init__()
        self.emit_line = emit_line

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("samantha"):
            return
        try:
            self.emit_line("log", self.format(record))
        except Exception:
            self.handleError(record)


class LogTap:
    def __init__(self, emit_line: Callable[[str, str], None]) -> None:
        self.handler = _Handler(emit_line)

    def start(self) -> None:
        logging.getLogger().addHandler(self.handler)

    def stop(self) -> None:
        logging.getLogger().removeHandler(self.handler)


class Tee(io.TextIOBase):
    def __init__(
        self,
        wrapped: Any,
        source: str,
        emit_line: Callable[[str, str], None],
        rewrites: list[dict[str, Any]] | None = None,
        rewrite_lock: threading.Lock | None = None,
    ) -> None:
        self.wrapped, self.source, self.emit_line = wrapped, source, emit_line
        self.rewrites = rewrites if rewrites is not None else []
        self.rewrite_lock = rewrite_lock or threading.Lock()
        self._buffer = ""
        self._last_frame = 0.0

    def write(self, data: str) -> int:
        rendered = self._rewrite(data)
        self.wrapped.write(rendered)
        self._buffer += rendered
        pieces = self._buffer.split("\n")
        self._buffer = pieces.pop()
        for line in pieces:
            self.emit_line(self.source, line)
        if "\r" in self._buffer and time.monotonic() - self._last_frame >= 1:
            self.emit_line(self.source, self._buffer.rsplit("\r", 1)[-1])
            self._last_frame = time.monotonic()
        return len(data)

    def _rewrite(self, data: str) -> str:
        with self.rewrite_lock:
            expired = []
            for rule in self.rewrites:
                if rule["source"] not in {None, self.source}:
                    continue
                if rule["match"] in data:
                    data = data.replace(rule["match"], rule["replace"])
                    rule["applications"] += 1
                    if rule["lifetime"] == "one_shot":
                        expired.append(rule)
            for rule in expired:
                self.rewrites.remove(rule)
        return data

    def flush(self) -> None:
        self.wrapped.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.wrapped, name)


class StreamTap:
    def __init__(self, emit_line: Callable[[str, str], None]) -> None:
        self.emit_line = emit_line
        self.originals: tuple[Any, Any] | None = None
        self.rewrites: list[dict[str, Any]] = []
        self._rewrite_lock = threading.Lock()

    def start(self) -> None:
        self.originals = sys.stdout, sys.stderr
        sys.stdout = Tee(
            sys.stdout, "stdout", self.emit_line, self.rewrites, self._rewrite_lock
        )
        sys.stderr = Tee(
            sys.stderr, "stderr", self.emit_line, self.rewrites, self._rewrite_lock
        )

    def stop(self) -> None:
        if self.originals:
            sys.stdout, sys.stderr = self.originals
            self.originals = None

    def add_rewrite(
        self, match: str, replace: str, source: str | None, lifetime: str
    ) -> dict[str, Any]:
        if source not in {None, "stdout", "stderr"}:
            raise ValueError("output source must be stdout, stderr, or null")
        if lifetime not in {"one_shot", "process", "contextual", "temporary"}:
            raise ValueError("unsupported output rewrite lifetime")
        rule = {
            "id": uuid.uuid4().hex[:10],
            "match": match,
            "replace": replace,
            "source": source,
            "lifetime": lifetime,
            "applications": 0,
        }
        with self._rewrite_lock:
            self.rewrites.append(rule)
        return dict(rule)
