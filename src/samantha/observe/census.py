"""Guarded configuration-shaped object inventory."""
from __future__ import annotations

import argparse
import dataclasses
import re
import types
from typing import Any

CONFIG_NAME = re.compile(r"(?i)^(cfg|conf|config|params|opts|options|hparams|settings)$")


def guarded(value: Any, limit: int = 500) -> dict[str, Any]:
    result: dict[str, Any] = {"type": type(value).__name__}
    try:
        result["length"] = len(value)
    except Exception:
        pass
    try:
        if isinstance(value, dict):
            result["fields"] = {str(k)[:80]: _scalar(v) for k, v in list(value.items())[:30]}
        else:
            result["fields"] = {str(k)[:80]: _scalar(v) for k, v in list(vars(value).items())[:30]}
    except Exception:
        result["fields"] = "<unavailable>"
    return result


def _scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value if not isinstance(value, str) else value[:200]
    return f"<{type(value).__name__}>"


def census(namespace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    shapes = (dict, argparse.Namespace, types.SimpleNamespace)
    return {name: guarded(value) for name, value in list(namespace.items()) if CONFIG_NAME.match(name) and (isinstance(value, shapes) or dataclasses.is_dataclass(value))}

