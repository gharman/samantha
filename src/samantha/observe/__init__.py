"""Observation plane coordinator."""

from __future__ import annotations

import logging
import queue
from typing import Any

from .logtap import LogTap, StreamTap
from .metrics import MetricStore


class Observer:
    def __init__(self, journal: Any) -> None:
        self.journal = journal
        self.metrics = MetricStore()
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
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
            self.events.put_nowait({"source": source, "text": text, "metrics": found})
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
