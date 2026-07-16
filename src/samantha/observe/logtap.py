"""Transparent logging and stdio taps."""
from __future__ import annotations

import io
import logging
import sys
import threading
import time
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
    def __init__(self, wrapped: Any, source: str, emit_line: Callable[[str, str], None]) -> None:
        self.wrapped, self.source, self.emit_line = wrapped, source, emit_line
        self._buffer = ""
        self._last_frame = 0.0

    def write(self, data: str) -> int:
        result = self.wrapped.write(data)
        self._buffer += data
        pieces = self._buffer.split("\n")
        self._buffer = pieces.pop()
        for line in pieces:
            self.emit_line(self.source, line)
        if "\r" in self._buffer and time.monotonic() - self._last_frame >= 1:
            self.emit_line(self.source, self._buffer.rsplit("\r", 1)[-1])
            self._last_frame = time.monotonic()
        return result

    def flush(self) -> None:
        self.wrapped.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.wrapped, name)


class StreamTap:
    def __init__(self, emit_line: Callable[[str, str], None]) -> None:
        self.emit_line = emit_line
        self.originals: tuple[Any, Any] | None = None

    def start(self) -> None:
        self.originals = sys.stdout, sys.stderr
        sys.stdout = Tee(sys.stdout, "stdout", self.emit_line)
        sys.stderr = Tee(sys.stderr, "stderr", self.emit_line)

    def stop(self) -> None:
        if self.originals:
            sys.stdout, sys.stderr = self.originals
            self.originals = None

