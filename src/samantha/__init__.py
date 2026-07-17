"""samantha public API."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ._boot import arm, resident

__version__ = "1.0.0"
_exposures: dict[str, "Exposure"] = {}


@dataclass
class Exposure:
    name: str
    get: Callable[[], Any]
    set: Callable[[Any], None]
    bounds: tuple[float, float] | None = None
    kind: str = "value"


def expose(
    name: str,
    *,
    obj: Any = None,
    attr: str | None = None,
    setter: Callable[[Any], None] | None = None,
    kind: str = "value",
    logged: bool = True,
    bounds: tuple[float, float] | None = None,
) -> None:
    if setter is not None:

        def get() -> None:
            return None

        set_value = setter
    elif obj is not None and attr:

        def get() -> Any:
            return getattr(obj, attr)

        def set_value(value: Any) -> None:
            setattr(obj, attr, value)
    else:
        raise ValueError("provide setter or obj and attr")
    _exposures[name] = Exposure(name, get, set_value, bounds, kind)
    if current := resident():
        current.ledger.register(name, bounds)


def goal(text: str) -> None:
    if current := resident():
        current.config.goal = text
        current.journal.write("chat", direction="api", text=text)


def event(text: str) -> None:
    if current := resident():
        current.journal.write("observation", source="api", payload=text)


def silence(minutes: int = 30) -> None:
    if current := resident():
        current.quiet_until = __import__("time").time() + minutes * 60


def _invoked_as_cli() -> bool:
    if not sys.argv:
        return False
    return Path(sys.argv[0]).name in {"samantha", "samantha.exe", "__main__.py"}


if os.getenv("SAMANTHA") != "off" and not _invoked_as_cli():
    try:
        entry = sys.argv[0] if sys.argv and Path(sys.argv[0]).is_file() else None
        arm(entry)
    except Exception:  # Importing samantha must never break the host.
        pass
