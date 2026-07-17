"""Conservative static hot-read candidate analysis."""

from __future__ import annotations

import dis
from types import FunctionType
from typing import Any


def candidates(function: FunctionType) -> set[str]:
    result: set[str] = set()
    chain: list[str] = []
    for instruction in dis.get_instructions(function):
        if instruction.opname in {"LOAD_GLOBAL", "LOAD_NAME", "LOAD_ATTR"}:
            chain.append(str(instruction.argval))
        elif instruction.opname == "BINARY_SUBSCR" and chain:
            result.add(".".join(chain))
            chain.clear()
        elif instruction.opname.startswith(("STORE_", "RETURN", "CALL")):
            chain.clear()
    return result


class CountingDict(dict):
    def __init__(self, source: dict[Any, Any], key: Any) -> None:
        super().__init__(source)
        self.watched_key, self.reads = key, 0

    def __getitem__(self, key: Any) -> Any:
        if key == self.watched_key:
            self.reads += 1
        return super().__getitem__(key)
