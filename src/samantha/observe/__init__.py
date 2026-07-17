"""Observation plane coordinator."""

from __future__ import annotations

import logging
import queue
from collections import deque
from typing import Any

from .logtap import LogTap, StreamTap
from .metrics import MetricStore


class Observer:
    def __init__(self, journal: Any) -> None:
        self.journal = journal
        self.metrics = MetricStore()
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.recent: deque[dict[str, Any]] = deque(maxlen=200)
        self.logtap = LogTap(self.observe)
        self.streamtap = StreamTap(self.observe)
        self.disabled = False
        self.on_observation: Any = None

    def start(self) -> None:
        try:
            self.logtap.start()
            self.streamtap.start()
        except Exception as exc:  # non-interference boundary
            logging.getLogger("samantha").warning("observer disabled: %s", exc)
            self.disabled = True

    def observe(self, source: str, text: str) -> None:
        if self.disabled:
            return
        try:
            found = self.metrics.ingest(text)
            event = {"source": source, "text": text[:2_000], "metrics": found}
            self.events.put_nowait(event)
            self.recent.append(event)
            if found:
                self.journal.write(
                    "observation",
                    source=source,
                    payload={"text": text, "metrics": found},
                )
            if self.on_observation:
                self.on_observation(source, text)
        except Exception as exc:
            logging.getLogger("samantha").warning("observation disabled: %s", exc)
            self.disabled = True

    def stop(self) -> None:
        self.logtap.stop()
        self.streamtap.stop()

    def recent_snapshot(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self.recent)[-limit:]

    def add_output_rewrite(
        self,
        match: str,
        replace: str,
        source: str | None = None,
        lifetime: str = "process",
    ) -> dict[str, Any]:
        return self.streamtap.add_rewrite(match, replace, source, lifetime)
