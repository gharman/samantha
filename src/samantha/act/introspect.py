"""Live project object graph discovery without application registration."""

from __future__ import annotations

import inspect
import json
import gc
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
        self._seen: dict[int, str] = {}
        self._aliases: dict[str, str] = {}

    def refresh(self) -> "RuntimeGraph":
        self.nodes.clear()
        self._seen.clear()
        self._aliases.clear()
        # Active function frames contain the objects that can affect what the program is
        # doing now; index them first so bounded planner views retain them. Modules come
        # before loose instances so shared objects are expanded under durable paths.
        self._frames()
        self._modules()
        self._objects()
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
        if alias := self._aliases.get(current.path):
            result["same_object_as"] = alias
        return result

    def get(self, path: str) -> LiveNode:
        if path in self.nodes:
            return self.nodes[path]
        # A shared object's children are indexed once, under the first path that reached
        # it; resolve paths written through any other name for that object.
        for prefix in sorted(self._aliases, key=len, reverse=True):
            if path.startswith(prefix + "/"):
                resolved = self._aliases[prefix] + path[len(prefix) :]
                if resolved in self.nodes:
                    return self.nodes[resolved]
        raise KeyError(f"live path not found: {path}")

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
            stack = []
            while frame:
                stack.append(frame)
                frame = frame.f_back
            # Number frames from the outermost so an active frame keeps its path while
            # the thread calls deeper; innermost numbering shifted on every call.
            for depth, frame in enumerate(reversed(stack)):
                if frame.f_locals is frame.f_globals:
                    continue  # module-level code: indexed under its module path
                if self._project_file(frame.f_code.co_filename):
                    prefix = f"frame:{thread_id}:{frame.f_code.co_name}:{depth}"
                    for name, value in list(frame.f_locals.items()):
                        # CPython fast locals are not safely assignable, but their child
                        # objects remain fully introspectable and mutable.
                        self._add(LiveNode(f"{prefix}::{name}", value), 0)

    def _objects(self) -> None:
        """Find project instances even when their coroutine frames are suspended."""
        project_types: set[type[Any]] = set()
        for module in list(sys.modules.values()):
            if not isinstance(module, types.ModuleType) or not self._project_module(
                module
            ):
                continue
            project_types.update(
                value
                for value in _safe_vars(module).values()
                if isinstance(value, type)
            )
        if not project_types:
            return
        added = 0
        for value in gc.get_objects():
            if added >= 1_000 or len(self.nodes) >= self.max_nodes:
                return
            try:
                kind = type(value)
                if kind not in project_types:
                    continue
                path = f"object:{kind.__module__}.{kind.__qualname__}:{id(value)}"
                before = len(self.nodes)
                self._add(LiveNode(path, value), 0)
                if len(self.nodes) > before:
                    added += 1
            except Exception:
                continue

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
            self._aliases[node.path] = self._seen[identity]
            return
        self._seen[identity] = node.path
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
            namespace = _safe_vars(value)
            if isinstance(value, type) and self._project_class(value):
                # vars(cls) is a mappingproxy; the class attribute is the assignable
                # target that changes behavior for every instance.
                namespace = dict(vars(value))
            for attr, child in list(namespace.items())[:100]:
                if attr.startswith("__"):
                    continue
                path = f"{node.path}/{attr}"
                self._add(LiveNode(path, child, value, attr, "attr", True), depth + 1)
            if not isinstance(value, type) and self._project_class(type(value)):
                self._methods(node, value, namespace, depth)

    def _methods(
        self, node: LiveNode, value: Any, namespace: dict[str, Any], depth: int
    ) -> None:
        """Expose public methods of project instances as callable, bound paths."""
        for name in _public_methods(type(value), self._project_class):
            if name in namespace:
                continue
            # Read through getattr so a call binds self; assign via the class path.
            method = LiveNode(f"{node.path}/{name}", None, value, name, "attr", False)
            try:
                method.value = method.read()
            except Exception:
                continue
            self._add(method, depth + 1)

    def _project_class(self, cls: type) -> bool:
        module = sys.modules.get(getattr(cls, "__module__", ""))
        return isinstance(module, types.ModuleType) and self._project_module(module)

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


def _public_methods(cls: type, is_project: Any) -> list[str]:
    names: dict[str, None] = {}
    for base in cls.__mro__:
        if not is_project(base):
            continue
        for name, attr in vars(base).items():
            if name.startswith("_"):
                continue
            if inspect.isfunction(attr) or isinstance(
                attr, (classmethod, staticmethod)
            ):
                names.setdefault(name)
    return list(names)[:50]


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
