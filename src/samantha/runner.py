"""In-process script runner."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

from ._boot import arm


def run(script: str, args: list[str], **config: object) -> int:
    path = str(Path(script).resolve())
    resident = arm(path, force=True, **config)
    old_argv = sys.argv
    sys.argv = [path, *args]
    try:
        runpy.run_path(path, run_name="__main__")
        return 0
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int | type(None)) else 1
    finally:
        sys.argv = old_argv
        if resident and hasattr(resident, "observer"):
            from .sentinels import Sentinels

            events = Sentinels(resident.observer.metrics).inspect_metrics()
            for event in events:
                resident.journal.write(
                    "observation",
                    source="sentinel",
                    payload={"sentinel": event.sentinel, "evidence": event.evidence},
                )
            resident.baselines.learn(resident.observer.metrics)
            resident.stop()
