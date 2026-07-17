"""Fingerprint-bound runtime claims."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from .identity import fingerprint
from .state import load_json, save_json
class ClaimStore:
    def __init__(self, path: Path) -> None: self.path, self.claims = path, load_json(path, {})
    def record(self, key: str, claim: str, source: Path, function: str | None = None) -> None:
        self.claims[key] = {"claim": claim, "source": str(source), "function": function, "fingerprint": fingerprint(source, function), "status": "fact"}; save_json(self.path, self.claims)
    def validate(self) -> list[str]:
        demoted = []
        for key, value in self.claims.items():
            try: current = fingerprint(value["source"], value.get("function"))
            except OSError: current = None
            if current != value.get("fingerprint") and value.get("status") == "fact": value["status"] = "hypothesis"; demoted.append(key)
        save_json(self.path, self.claims); return demoted

