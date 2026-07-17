"""Deterministic watchers over bounded telemetry."""

from __future__ import annotations
import math
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class WakeEvent:
    sentinel: str
    evidence: dict[str, Any]


class Sentinels:
    def __init__(self, metrics: Any) -> None:
        self.metrics, self.seen_templates, self.fired = metrics, set(), set()

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
            window = list(values)[-25:]
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
        fresh = [
            e
            for e in events
            if e.sentinel
            + str(e.evidence.get("series", e.evidence.get("template", "")))
            not in self.fired
        ]
        for e in fresh:
            self.fired.add(
                e.sentinel
                + str(e.evidence.get("series", e.evidence.get("template", "")))
            )
        return fresh
