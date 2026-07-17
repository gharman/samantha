"""Line-oriented numeric series extraction."""

from __future__ import annotations

import re
import statistics
from collections import defaultdict, deque
from typing import Any

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|[-+]?inf|nan"
PAIR = re.compile(
    rf"(?P<name>[A-Za-z][\w./-]*(?:\s+[A-Za-z][\w.-]*)?)\s*[:=]\s*(?P<value>{NUMBER})\s*(?P<pct>%?)",
    re.I,
)
SPACE = re.compile(
    rf"(?P<name>[A-Za-z][\w./-]+)\s+(?P<value>{NUMBER})(?P<pct>%)?", re.I
)
CEILING = re.compile(
    rf"(?:ceiling|target|expected|budget|limit)\s*[:=]?\s*(?P<value>{NUMBER})", re.I
)


def _name(value: str) -> str:
    return re.sub(r"[\s/]+", ".", value.strip()).lower()


class MetricStore:
    def __init__(self, size: int = 4096) -> None:
        self.series: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=size))
        self.references: dict[str, float] = {}
        self.patterns: dict[str, re.Pattern[str]] = {}

    def define(self, name: str, regex: str) -> None:
        self.patterns[name] = re.compile(regex)

    def ingest(self, line: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        matches = list(PAIR.finditer(line))
        if not matches:
            matches = list(SPACE.finditer(line))
        ceiling = CEILING.search(line)
        for match in matches:
            name, raw = _name(match.group("name")), match.group("value")
            if name in {"ceiling", "target", "expected", "budget", "limit"}:
                continue
            value = float(raw)
            if match.groupdict().get("pct"):
                value /= 100
            self.series[name].append(value)
            item: dict[str, Any] = {"series": name, "value": value}
            if ceiling:
                reference = float(ceiling.group("value"))
                self.references[name] = reference
                item["reference"] = reference
            found.append(item)
        for name, pattern in self.patterns.items():
            if match := pattern.search(line):
                value = float(match.group(1))
                self.series[name].append(value)
                found.append({"series": name, "value": value})
        return found

    def stats(self, name: str, window: int = 100) -> dict[str, float]:
        values = list(self.series.get(name, ()))[-window:]
        if not values:
            return {}
        slope = (values[-1] - values[0]) / max(1, len(values) - 1)
        return {
            "n": len(values),
            "min": min(values),
            "max": max(values),
            "mean": statistics.fmean(values),
            "slope": slope,
        }
