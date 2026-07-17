"""Read-only tool facade used by cognition."""

from __future__ import annotations
from typing import Any


class Tools:
    def __init__(self, resident: Any) -> None:
        self.resident = resident

    def read_source(self, path: str, start: int = 1, end: int = 200) -> str:
        target = (self.resident.root / path).resolve()
        if self.resident.root not in target.parents:
            return ""
        return "".join(
            target.read_text(errors="replace").splitlines(True)[start - 1 : end]
        )

    def query_metrics(self, series: str, window: int = 100) -> dict[str, float]:
        return self.resident.observer.metrics.stats(series, window)

    def define_metric(self, name: str, regex: str) -> None:
        self.resident.observer.metrics.define(name, regex)
        self.resident.journal.write(
            "observation",
            source="metric-definition",
            payload={"name": name, "regex": regex},
        )
