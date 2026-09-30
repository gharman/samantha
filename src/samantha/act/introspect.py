"""Live project object graph discovery without application registration."""

from __future__ import annotations

import inspect
import json
import gc
import re
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
        if self.access == "exposure":
            return self.parent.get()
        if self.access == "attr":
            return getattr(self.parent, self.key)
        if self.access == "item":
            return self.parent[self.key]
        return self.value

    def write(self, value: Any) -> None:
        if not self.mutable:
            raise TypeError(f"{self.path} is not assignable")
        if self.access == "exposure":
            bounds = self.parent.bounds
            if bounds and not bounds[0] <= value <= bounds[1]:
                raise ValueError(f"{value!r} is outside declared bounds {bounds}")
            self.parent.set(value)
        elif self.access == "attr":
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
        # (id(class), attribute) -> instance-level paths the program reads in place
        # of that class attribute: instance __dict__ entries and bound methods.
        self._instance_paths: dict[tuple[int, str], list[str]] = {}

    def refresh(self) -> "RuntimeGraph":
        self.nodes.clear()
        self._seen.clear()
        self._aliases.clear()
        self._instance_paths.clear()
        # Active function frames contain the objects that can affect what the program is
        # doing now; index them first so bounded planner views retain them. Modules come
        # before loose instances so shared objects are expanded under durable paths.
        self._exposures()
        self._frames()
        self._modules()
        self._objects()
        self._index_instance_paths()
        return self

    def _exposures(self) -> None:
        """Knobs the host declared with ``samantha.expose``: bounded, preferred."""
        from samantha import _exposures

        for name, exposure in list(_exposures.items()):
            node = LiveNode(f"exposed:{name}", None, exposure, name, "exposure", True)
            try:
                node.value = node.read()
            except Exception:
                continue
            self._add(node, 0)

    def snapshot(
        self, query: str | None = None, limit: int = 500, only_matches: bool = False
    ) -> list[dict[str, Any]]:
        """Describe live nodes, most relevant to ``query`` first.

        Requiring every query word to match made a natural-language request match
        nothing, so nodes are ranked by how many distinct query words they contain.
        """
        items = [self.describe(node) for node in self.nodes.values()]
        words = _query_words(query)
        if not words:
            return [] if only_matches else items[:limit]
        scores = {item["path"]: _relevance(item, words) for item in items}
        # A class name often matches the query better than the instance attribute
        # path does, but the program reads the instance. Rank each instance path at
        # least as high as the class attribute it stands in for, and ahead of it.
        for item in items:
            for path in self.instance_paths(item["path"]):
                if path in scores:
                    scores[path] = max(scores[path], scores[item["path"]])
        scored = [
            (scores[item["path"]], item.get("scope") == "class", index)
            for index, item in enumerate(items)
        ]
        if only_matches:
            scored = [row for row in scored if row[0] > 0]
        scored.sort(key=lambda row: (-row[0], row[1], row[2]))
        return [items[index] for *_, index in scored[:limit]]

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
        if _class_attribute(current):
            result["scope"] = "class"
            if shadowed := self.instance_paths(current):
                result["shadowed_by"] = shadowed[:3]
        return result

    def instance_paths(self, node: LiveNode | str) -> list[str]:
        """Instance-level paths live objects read instead of a class attribute."""
        current = self.nodes.get(node) if isinstance(node, str) else node
        if current is None or not _class_attribute(current):
            return []
        return self._instance_paths.get((id(current.parent), current.key), [])

    def shadowing_instances(self, node: LiveNode) -> list[Any]:
        """Live instances whose own ``__dict__`` hides this class attribute.

        Scans the heap rather than the graph so instances past the node budget
        still count.
        """
        if not _class_attribute(node):
            return []
        cls, name = node.parent, node.key
        return [
            value
            for value in gc.get_objects()
            if issubclass(type(value), cls) and name in _safe_vars(value)
        ]

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

    def _index_instance_paths(self) -> None:
        for path, node in self.nodes.items():
            if node.access != "attr" or not isinstance(node.key, str):
                continue
            if isinstance(node.parent, (type, types.ModuleType)):
                continue
            for cls in type(node.parent).__mro__:
                if cls is not object and node.key in cls.__dict__:
                    self._instance_paths.setdefault((id(cls), node.key), []).append(
                        path
                    )

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
            # Only classes the project defines: an imported library class (Path,
            # OrderedDict, Tensor) would fill the node budget with its instances and
            # crowd out the project's own objects.
            project_types.update(
                value
                for value in _safe_vars(module).values()
                if isinstance(value, type) and self._project_class(value)
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


def _class_attribute(node: LiveNode) -> bool:
    return node.access == "attr" and isinstance(node.parent, type)


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


_STOPWORDS = {
    "a", "an", "and", "are", "be", "can", "change", "could", "do", "for", "how",
    "in", "is", "it", "make", "me", "my", "of", "on", "or", "please", "set", "so",
    "that", "the", "this", "to", "what", "when", "with", "you",
}  # fmt: skip


def _query_words(query: str | None) -> set[str]:
    words = re.findall(r"[a-z0-9_]+", (query or "").lower())
    return {word for word in words if len(word) > 1 and word not in _STOPWORDS}


def _relevance(item: dict[str, Any], words: set[str]) -> int:
    path = item["path"].lower()
    detail = f"{item['type']} {item['preview']}".lower()
    return sum(2 if word in path else 1 if word in detail else 0 for word in words)


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
