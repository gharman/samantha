"""Learned metric envelopes."""

from __future__ import annotations
from pathlib import Path
from typing import Any
from .state import load_json, save_json


class Baselines:
    def __init__(self, path: Path) -> None:
        self.path, self.data = path, load_json(path, {})

    def learn(self, metrics: Any) -> None:
        for name in metrics.series:
            stats = metrics.stats(name)
            if stats:
                self.data[name] = {
                    "mean": stats["mean"],
                    "min": stats["min"],
                    "max": stats["max"],
                    "direction": "down" if "loss" in name or "error" in name else "up",
                }
        save_json(self.path, self.data)
