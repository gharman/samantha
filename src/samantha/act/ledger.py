"""Protected paths and the operator's declared knobs."""

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
        """Record a knob declared with ``samantha.expose`` and its protection."""
        protected = any(p.search(knob) for p in self.protect)
        row = {"state": "protected" if protected else "declared", "bounds": bounds}
        self.data[knob] = row
        save_json(self.path, self.data)
        return row
