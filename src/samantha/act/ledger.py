"""Persistent per-knob trust ladder."""

from __future__ import annotations
import re
from pathlib import Path
from typing import Any
from samantha.state import load_json, save_json

# Keywords must be whole path segments: "retrieval" is not "eval", but dict keys
# like "eval-split" or "seed offset" still are.
DEFAULT_PROTECT = (
    r'(?i)(?:^|[-_./:"\s])(eval|seed|tests?|ckpt|checkpoint)(?:$|[-_./:"\s])',
)


class Ledger:
    def __init__(self, path: Path, protect: list[str] | None = None) -> None:
        self.path, self.data = path, load_json(path, {})
        self.protect = [re.compile(p) for p in [*DEFAULT_PROTECT, *(protect or [])]]

    def register(
        self, knob: str, bounds: tuple[float, float] | None = None
    ) -> dict[str, Any]:
        state = (
            "protected" if any(p.search(knob) for p in self.protect) else "candidate"
        )
        row = self.data.setdefault(knob, {"state": state, "score": 0, "bounds": bounds})
        self.save()
        return row

    def transition(self, knob: str, state: str, cause: str = "") -> tuple[str, str]:
        old = self.data[knob]["state"]
        if old in {"protected", "dead"}:
            return old, old
        self.data[knob]["state"] = state
        self.save()
        return old, state

    def outcome(self, knob: str, kept: bool) -> str:
        row = self.data[knob]
        row["score"] += 1 if kept else -2
        if row["score"] < 0:
            row["state"] = "suspended"
        elif row["state"] == "probed" and row["score"] >= 2:
            row["state"] = "trusted"
        self.save()
        return row["state"]

    def authorized(self, knob: str, value: Any) -> bool:
        row = self.data.get(knob, {})
        bounds = row.get("bounds")
        return row.get("state") == "trusted" and (
            not bounds or bounds[0] <= value <= bounds[1]
        )

    def save(self) -> None:
        save_json(self.path, self.data)
