"""Transparent logging and stdio taps."""

from __future__ import annotations

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
        if record.name.startswith("samantha") or _reaches_tapped_stream(record):
            return
        try:
            self.emit_line("log", self.format(record))
        except Exception:
            self.handleError(record)


def _reaches_tapped_stream(record: logging.LogRecord) -> bool:
    """True when a host handler will write this record through a tapped stream.

    The tapped stream observes that output already; observing the record here too
    doubled every logged metric point.
    """
    logger: logging.Logger | None = logging.getLogger(record.name)
    found = False
    while logger:
        for handler in logger.handlers:
            found = True
            if (
                isinstance(handler, logging.StreamHandler)
                and isinstance(getattr(handler, "stream", None), Tee)
                and record.levelno >= handler.level
            ):
                return True
        logger = logger.parent if logger.propagate else None
    last_resort = logging.lastResort
    # With no handlers at all, logging's last resort writes to the current stderr.
    return (
        not found
        and last_resort is not None
        and record.levelno >= last_resort.level
        and isinstance(sys.stderr, Tee)
    )


class LogTap:
    """Observe every handled log record without registering a root handler.

    A root handler made ``logging.basicConfig()`` a silent no-op for hosts that
    configure logging after ``import samantha``, so their console logs vanished.
    """

    def __init__(self, emit_line: Callable[[str, str], None]) -> None:
        self.handler = _Handler(emit_line)
        self._original: Any = None

    def start(self) -> None:
        if self._original is not None:
            return
        original = logging.Logger.callHandlers
        handler = self.handler

        def call_handlers(logger: logging.Logger, record: logging.LogRecord) -> None:
            original(logger, record)
            handler.handle(record)

        self._original = original
        logging.Logger.callHandlers = call_handlers  # type: ignore[method-assign]

    def stop(self) -> None:
        if self._original is not None:
            logging.Logger.callHandlers = self._original  # type: ignore[method-assign]
            self._original = None


class Tee:
    """Observe writes while presenting the wrapped stream's full interface.

    Deliberately not an ``io.TextIOBase`` subclass: its ``isatty``, ``fileno``,
    ``encoding``, and ``writable`` defaults would shadow ``__getattr__`` and make
    terminal-aware libraries (rich, tqdm, click) treat the console as a pipe.
    """

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

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

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

    def remove_rewrite(self, rule_id: str) -> bool:
        with self._rewrite_lock:
            before = len(self.rewrites)
            self.rewrites[:] = [rule for rule in self.rewrites if rule["id"] != rule_id]
            return len(self.rewrites) < before
