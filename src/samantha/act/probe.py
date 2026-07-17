"""Exact-restore reversibility probes."""
from __future__ import annotations
from typing import Any
def probe(getter: Any, setter: Any, bounds: tuple[float, float] | None = None) -> bool:
    old = getter()
    if not isinstance(old, (int, float)) or isinstance(old, bool): return False
    epsilon = max(abs(old) * .01, .001); trial = old + epsilon
    if bounds: trial = min(bounds[1], max(bounds[0], trial))
    try: setter(trial); return getter() == trial
    except Exception: return False
    finally:
        try: setter(old)
        except Exception: pass

