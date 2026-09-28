"""samantha public API."""

from __future__ import annotations

import os
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ._boot import _truthy, arm, resident
from .state import redact

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
    """True only when this interpreter is running samantha's own CLI."""
    # During `python -m pkg`, packages import while argv[0] is still "-m".
    args = iter(getattr(sys, "orig_argv", [])[1:])
    for arg in args:
        if arg in {"-X", "-W"}:
            next(args, None)
        elif arg.startswith("-m"):
            module = arg[2:] or next(args, "")
            return module.split(".")[0] == "samantha"
        elif arg == "-c" or not arg.startswith("-"):
            break
    if not sys.argv:
        return False
    script = Path(sys.argv[0])
    if script.name in {"samantha", "samantha.exe"}:
        return True
    try:
        return Path(__file__).resolve().parent in script.resolve().parents
    except OSError:
        return False


def _multiprocessing_child() -> bool:
    """True in a multiprocessing helper re-importing the parent's __main__."""
    # Spawn children and the forkserver start as `python -c "from multiprocessing..."`
    # and re-import __main__ before parent_process() is set.
    orig = getattr(sys, "orig_argv", [])
    for flag, code in zip(orig, orig[1:]):
        if flag == "-c" and code.startswith("from multiprocessing."):
            return True
    mp = sys.modules.get("multiprocessing")
    if mp is None:
        return False
    return mp.parent_process() is not None or mp.current_process().name != "MainProcess"


def _should_arm() -> bool:
    if os.getenv("SAMANTHA") == "off" or _invoked_as_cli():
        return False
    return not _multiprocessing_child() or _truthy(os.getenv("SAMANTHA_ARM_CHILDREN"))


def _report_disabled(error: Exception) -> None:
    """Say once why the resident is missing, without touching the host."""
    if _truthy(os.getenv("SAMANTHA_QUIET")):
        return
    try:
        message = redact(f"{type(error).__name__}: {error}")
        print(f"[samantha] disabled: {message}", file=sys.__stderr__, flush=True)
    except Exception:
        pass


def _application_entry() -> str | None:
    """Prefer the importing application frame over a generated console shim."""
    package = Path(__file__).resolve().parent
    cwd = Path.cwd().resolve()
    for frame in inspect.stack()[1:]:
        candidate = Path(frame.filename).resolve()
        parts = set(candidate.parts)
        if not candidate.is_file() or package in candidate.parents:
            continue
        if {".venv", "venv", "site-packages"} & parts:
            continue
        if candidate == cwd or cwd in candidate.parents:
            return str(candidate)
    if sys.argv and Path(sys.argv[0]).is_file():
        return sys.argv[0]
    return None


if _should_arm():
    try:
        arm(_application_entry())
    except Exception as error:  # Importing samantha must never break the host.
        _report_disabled(error)
