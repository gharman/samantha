"""Deterministic watchers over bounded telemetry."""

from __future__ import annotations
import math
import re
import time
from itertools import islice
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WakeEvent:
    sentinel: str
    evidence: dict[str, Any]
    at: float = field(default_factory=time.time)


class Sentinels:
    def __init__(self, metrics: Any, rearm: dict[str, float] | None = None) -> None:
        self.metrics, self.seen_templates = metrics, set()
        # Sentinel and series -> when it last fired. Each fires once, unless rearm
        # names its sentinel: then it fires again that many seconds later.
        self.fired: dict[str, float] = {}
        self.rearm = rearm or {}

    def inspect_line(self, text: str) -> list[WakeEvent]:
        events: list[WakeEvent] = []
        if re.search(r"\b(?:ERROR|WARNING)\b", text, re.I):
            template = re.sub(r"(?:0x)?[0-9a-f]{4,}|\d+", "#", text.lower())
            if template not in self.seen_templates:
                self.seen_templates.add(template)
                events.append(
                    WakeEvent("log-novelty", {"line": text, "template": template})
                )
        return self._once(events)

    def inspect_metrics(self) -> list[WakeEvent]:
        events: list[WakeEvent] = []
        for name, values in self.metrics.series.items():
            # Series hold up to 4,096 points; copy only the tail on every line.
            window = list(islice(reversed(values), 25))[::-1]
            if any(not math.isfinite(value) for value in window):
                events.append(
                    WakeEvent("nan-inf", {"series": name, "values": window[-3:]})
                )
            if len(window) >= 20 and max(window) - min(window) < 1e-6:
                events.append(WakeEvent("stall", {"series": name, "window": window}))
            ref = self.metrics.references.get(name)
            if (
                ref is not None
                and len(window) >= 20
                and ref - window[-1] > 0.05
                and abs(window[-1] - window[0]) < 0.005
            ):
                events.append(
                    WakeEvent(
                        "gap", {"series": name, "value": window[-1], "reference": ref}
                    )
                )
        return self._once(events)

    def _once(self, events: list[WakeEvent]) -> list[WakeEvent]:
        fresh = []
        for e in events:
            key = e.sentinel + str(
                e.evidence.get("series", e.evidence.get("template", ""))
            )
            last = self.fired.get(key)
            after = self.rearm.get(e.sentinel)
            if last is None or (after is not None and e.at - last >= after):
                self.fired[key] = e.at
                fresh.append(e)
        return fresh
