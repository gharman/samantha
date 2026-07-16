"""In-process script runner."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

from ._boot import arm


def run(script: str, args: list[str], **config: object) -> int:
    path = str(Path(script).resolve())
    arm(path, force=True, **config)
    old_argv = sys.argv
    sys.argv = [path, *args]
    try:
        runpy.run_path(path, run_name="__main__")
        return 0
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int | type(None)) else 1
    finally:
        sys.argv = old_argv

