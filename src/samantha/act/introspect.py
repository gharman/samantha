"""Live project object graph discovery without application registration."""

from __future__ import annotations

import inspect
import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class LiveNode:
    path: str
    value: Any
    parent: Any = None
    key: Any = None
    access: str = "root"
    mutable: bool = False

    def read(self) -> Any:
        if self.access == "attr":
            return getattr(self.parent, self.key)
        if self.access == "item":
            return self.parent[self.key]
        return self.value

    def write(self, value: Any) -> None:
        if not self.mutable:
            raise TypeError(f"{self.path} is not assignable")
        if self.access == "attr":
            setattr(self.parent, self.key, value)
        elif self.access == "item":
            self.parent[self.key] = value
        else:
            raise TypeError(f"{self.path} is not assignable")


class RuntimeGraph:
    def __init__(self, root: Path, max_depth: int = 4, max_nodes: int = 4_000) -> None:
        self.root = root.resolve()
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.nodes: dict[str, LiveNode] = {}
        self._seen: set[int] = set()

    def refresh(self) -> "RuntimeGraph":
        self.nodes.clear()
        self._seen.clear()
        self._modules()
        self._frames()
        return self

    def snapshot(
        self, query: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        words = (query or "").lower().split()
        result = []
        for node in self.nodes.values():
            item = self.describe(node)
            haystack = f"{item['path']} {item['type']} {item['preview']}".lower()
            if words and not all(word in haystack for word in words):
                continue
            result.append(item)
            if len(result) >= limit:
                break
        return result

    def describe(self, node: LiveNode | str) -> dict[str, Any]:
        current = self.nodes[node] if isinstance(node, str) else node
        try:
            value = current.read()
        except Exception as exc:
            return {"path": current.path, "type": "unavailable", "preview": str(exc)}
        result = {
            "path": current.path,
            "type": type(value).__name__,
            "preview": _preview(value),
            "mutable": current.mutable,
            "callable": callable(value),
        }
        if callable(value):
            try:
                result["signature"] = str(inspect.signature(value))
            except (TypeError, ValueError):
                result["signature"] = "unknown"
        return result

    def get(self, path: str) -> LiveNode:
        try:
            return self.nodes[path]
        except KeyError as exc:
            raise KeyError(f"live path not found: {path}") from exc

    def _modules(self) -> None:
        for name, module in list(sys.modules.items()):
            if not isinstance(module, types.ModuleType) or not self._project_module(
                module
            ):
                continue
            for attr, value in _safe_vars(module).items():
                if attr.startswith("__"):
                    continue
                path = f"module:{name}::{attr}"
                node = LiveNode(path, value, module, attr, "attr", True)
                self._add(node, 0)

    def _frames(self) -> None:
        for thread_id, frame in sys._current_frames().items():
            depth = 0
            while frame and depth < 30:
                if self._project_file(frame.f_code.co_filename):
                    prefix = f"frame:{thread_id}:{frame.f_code.co_name}:{depth}"
                    for name, value in list(frame.f_locals.items()):
                        # CPython fast locals are not safely assignable, but their child
                        # objects remain fully introspectable and mutable.
                        self._add(LiveNode(f"{prefix}::{name}", value), 0)
                frame = frame.f_back
                depth += 1

    def _add(self, node: LiveNode, depth: int) -> None:
        if len(self.nodes) >= self.max_nodes or node.path in self.nodes:
            return
        self.nodes[node.path] = node
        value = node.value
        if (
            depth >= self.max_depth
            or _scalar(value)
            or isinstance(value, types.ModuleType)
        ):
            return
        identity = id(value)
        if identity in self._seen:
            return
        self._seen.add(identity)
        if isinstance(value, dict):
            for key, child in list(value.items())[:100]:
                if not isinstance(key, (str, int, float, bool)):
                    continue
                path = f"{node.path}/{json.dumps(key, ensure_ascii=False)}"
                self._add(LiveNode(path, child, value, key, "item", True), depth + 1)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value[:100]):
                path = f"{node.path}/[{index}]"
                mutable = isinstance(value, list)
                self._add(
                    LiveNode(path, child, value, index, "item", mutable), depth + 1
                )
        else:
            for attr, child in list(_safe_vars(value).items())[:100]:
                if attr.startswith("__"):
                    continue
                path = f"{node.path}/{attr}"
                self._add(LiveNode(path, child, value, attr, "attr", True), depth + 1)

    def _project_module(self, module: types.ModuleType) -> bool:
        return self._project_file(getattr(module, "__file__", ""))

    def _project_file(self, filename: str) -> bool:
        try:
            path = Path(filename).resolve()
        except (OSError, TypeError):
            return False
        return (
            path.is_file() and self.root in path.parents and ".venv" not in path.parts
        )


def _safe_vars(value: Any) -> dict[str, Any]:
    try:
        namespace = vars(value)
        return namespace if isinstance(namespace, dict) else {}
    except Exception:
        return {}


def _scalar(value: Any) -> bool:
    return isinstance(value, (str, bytes, int, float, bool, type(None)))


def _preview(value: Any) -> str:
    if _scalar(value):
        return repr(value)[:500]
    if isinstance(value, dict):
        return f"dict(len={len(value)}, keys={list(value)[:12]!r})"
    if isinstance(value, (list, tuple, set, frozenset)):
        return f"{type(value).__name__}(len={len(value)})"
    if isinstance(value, types.ModuleType):
        return f"module({value.__name__})"
    if callable(value):
        return getattr(value, "__qualname__", type(value).__name__)
    return f"<{type(value).__module__}.{type(value).__name__}>"
