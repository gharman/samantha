"""Audited generic runtime operations and intervention groups."""

from __future__ import annotations

import asyncio
import inspect
import logging
import queue
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .introspect import LiveNode, RuntimeGraph, _preview


@dataclass
class OperationResult:
    op: str
    path: str
    ok: bool
    detail: str
    verified: bool = False
    value: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "path": self.path,
            "ok": self.ok,
            "detail": self.detail,
            "verified": self.verified,
            "value": _json_safe(self.value),
        }


@dataclass
class InterventionGroup:
    id: str
    intent: str
    lifetime: str
    results: list[OperationResult] = field(default_factory=list)
    undo: list[tuple[LiveNode, Any]] = field(default_factory=list)
    status: str = "active"


class RuntimeOperator:
    """Execute only fixed operations against already-live Python objects."""

    def __init__(self, resident: Any) -> None:
        self.resident = resident
        self.graph = RuntimeGraph(resident.root)
        self.groups: dict[str, InterventionGroup] = {}
        self._locks: dict[str, threading.Lock] = {}

    def inventory(
        self, query: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        self.graph.refresh()
        return self.graph.snapshot(query, limit)

    def execute(
        self,
        intent: str,
        operations: list[dict[str, Any]],
        lifetime: str = "contextual",
    ) -> InterventionGroup:
        self.graph.refresh()
        group = InterventionGroup(uuid.uuid4().hex[:10], intent, lifetime)
        self.groups[group.id] = group
        for operation in operations:
            result = self._execute_one(group, operation)
            group.results.append(result)
        if not any(result.ok for result in group.results):
            group.status = "failed"
        self.resident.journal.write(
            "intervention",
            id=group.id,
            knob="runtime-group",
            old=None,
            new=[result.as_dict() for result in group.results],
            prediction={"intent": intent, "lifetime": lifetime},
            status=group.status,
        )
        return group

    def revert(self, group_id: str, reason: str = "operator request") -> bool:
        group = self.groups.get(group_id)
        if not group or group.status == "reverted":
            return False
        ok = True
        for node, old in reversed(group.undo):
            try:
                node.write(old)
                ok = ok and (node.read() is old or node.read() == old)
            except Exception:
                ok = False
        group.status = "reverted" if ok else "revert_failed"
        self.resident.journal.write(
            "revert", intervention_id=group.id, reason=reason, verified=ok
        )
        return ok

    def _execute_one(
        self, group: InterventionGroup, operation: dict[str, Any]
    ) -> OperationResult:
        op = str(operation.get("op", ""))
        path = str(operation.get("path", ""))
        if op in {"search", "inspect"}:
            return self._read_operation(op, path, operation)
        if op == "emit":
            message = str(operation.get("value") or "")
            logging.getLogger("samantha").info("operator receipt: %s", message)
            return OperationResult(
                op, "samantha.logger", True, "receipt emitted", True, message
            )
        if op == "rewrite_output":
            try:
                return self._rewrite_output(group, operation)
            except Exception as exc:
                return OperationResult(
                    op, "stdio", False, f"{type(exc).__name__}: {exc}"
                )
        try:
            node = self.graph.get(path)
        except KeyError as exc:
            return OperationResult(op, path, False, str(exc))
        if self._protected(path):
            return OperationResult(op, path, False, "path is protected")
        lock = self._locks.setdefault(path, threading.Lock())
        if not lock.acquire(blocking=False):
            return OperationResult(
                op, path, False, "path has a conflicting intervention"
            )
        try:
            return self._mutate(group, node, operation)
        except Exception as exc:
            return OperationResult(op, path, False, f"{type(exc).__name__}: {exc}")
        finally:
            lock.release()

    def _read_operation(
        self, op: str, path: str, operation: dict[str, Any]
    ) -> OperationResult:
        if op == "search":
            query = str(operation.get("query") or "")
            found = self.graph.snapshot(query, limit=100)
            return OperationResult(
                op, path, True, f"found {len(found)} paths", True, found
            )
        try:
            value = self.graph.describe(path)
            return OperationResult(op, path, True, "inspected live path", True, value)
        except KeyError as exc:
            return OperationResult(op, path, False, str(exc))

    def _mutate(
        self, group: InterventionGroup, node: LiveNode, operation: dict[str, Any]
    ) -> OperationResult:
        op, path = str(operation["op"]), node.path
        value = operation.get("value")
        if op == "set":
            old = node.read()
            node.write(value)
            verified = node.read() == value
            if verified:
                group.undo.append((node, old))
            return OperationResult(
                op,
                path,
                verified,
                "assignment verified" if verified else "assignment did not persist",
                verified,
                node.read(),
            )
        if op in {"append", "enqueue"}:
            target = node.read()
            before = _length(target)
            if op == "enqueue" and hasattr(target, "put"):
                target.put(value)
            elif hasattr(target, "append"):
                target.append(value)
            else:
                raise TypeError("target supports neither put nor append")
            after = _length(target)
            verified = before is None or after == before + 1
            return OperationResult(op, path, True, "value inserted", verified, value)
        if op == "call":
            target = node.read()
            if not callable(target):
                raise TypeError("target is not callable")
            if not self._project_callable(target):
                raise PermissionError("only project-defined callables may be invoked")
            args = operation.get("args") or []
            kwargs = operation.get("kwargs") or {}
            result = target(*args, **kwargs)
            if inspect.isawaitable(result):
                result = asyncio.run(result)
            return OperationResult(
                op, path, True, "existing callable returned", True, result
            )
        if op == "intercept":
            return self._intercept(group, node, operation)
        raise ValueError(f"unsupported operation: {op}")

    def _intercept(
        self, group: InterventionGroup, node: LiveNode, operation: dict[str, Any]
    ) -> OperationResult:
        original = node.read()
        if not callable(original) or not node.mutable:
            raise TypeError("interception requires an assignable callable")
        if not self._project_callable(original):
            raise PermissionError("only project-defined callables may be intercepted")
        overrides = operation.get("kwargs") or {}
        metadata = operation.get("value") or {}
        lifetime = operation.get("lifetime") or group.lifetime
        signature = inspect.signature(original)
        restored = False

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            nonlocal restored
            bound = signature.bind_partial(*args, **kwargs)
            condition = metadata.get("when", {}) if isinstance(metadata, dict) else {}
            matches = _matches(bound.arguments, condition)
            if matches:
                _patch_arguments(bound.arguments, metadata)
                for name, value in overrides.items():
                    if name in signature.parameters:
                        bound.arguments[name] = value
                    else:
                        var_keyword = next(
                            (
                                name
                                for name, parameter in signature.parameters.items()
                                if parameter.kind is inspect.Parameter.VAR_KEYWORD
                            ),
                            None,
                        )
                        if not var_keyword:
                            raise TypeError(f"callable has no argument named {name}")
                        extras = dict(bound.arguments.get(var_keyword, {}))
                        extras[name] = value
                        bound.arguments[var_keyword] = extras
            if matches and lifetime == "one_shot" and not restored:
                node.write(original)
                restored = True
            result = original(*bound.args, **bound.kwargs)
            if matches and isinstance(metadata, dict):
                result = _transform_result(result, metadata)
            receipt = metadata.get("receipt") if isinstance(metadata, dict) else None
            if matches and receipt:
                logging.getLogger("samantha").info(
                    "operator receipt: %s",
                    str(receipt).replace("{result}", str(result)),
                )
            return result

        wrapper.__name__ = getattr(original, "__name__", "samantha_interceptor")
        wrapper.__wrapped__ = original
        node.write(wrapper)
        if lifetime != "one_shot":
            group.undo.append((node, original))
        verified = node.read() is wrapper
        return OperationResult(
            "intercept",
            node.path,
            verified,
            f"installed {lifetime} argument interceptor",
            verified,
            {"overrides": overrides, "lifetime": lifetime},
        )

    def _rewrite_output(
        self, group: InterventionGroup, operation: dict[str, Any]
    ) -> OperationResult:
        value = operation.get("value") or {}
        if not isinstance(value, dict) or not value.get("match"):
            raise ValueError("output rewrite requires match and replace")
        observer = getattr(self.resident, "observer", None)
        if not observer or not hasattr(observer, "add_output_rewrite"):
            raise RuntimeError("output observation is unavailable")
        lifetime = str(operation.get("lifetime") or group.lifetime)
        rule = observer.add_output_rewrite(
            str(value["match"]),
            str(value.get("replace", "")),
            value.get("source"),
            lifetime,
        )
        return OperationResult(
            "rewrite_output",
            "stdio",
            True,
            f"installed {lifetime} output rewrite",
            True,
            rule,
        )

    def _protected(self, path: str) -> bool:
        return any(pattern.search(path) for pattern in self.resident.ledger.protect)

    def _project_callable(self, target: Any) -> bool:
        module = sys.modules.get(getattr(target, "__module__", ""))
        module_file = getattr(module, "__file__", None)
        if module_file:
            try:
                if self.resident.root.resolve() in Path(module_file).resolve().parents:
                    return True
            except OSError:
                pass
        try:
            source = inspect.getsourcefile(target) or inspect.getfile(target)
            root = self.resident.root.resolve()
            return root in Path(source).resolve().parents
        except (TypeError, OSError):
            return False


def _length(value: Any) -> int | None:
    try:
        return len(value)
    except (TypeError, AttributeError):
        if isinstance(value, queue.Queue):
            return value.qsize()
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in list(value.items())[:100]}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value[:100]]
    return _preview(value)


def _matches(arguments: dict[str, Any], condition: Any) -> bool:
    if not isinstance(condition, dict) or not condition:
        return True
    return all(arguments.get(name) == expected for name, expected in condition.items())


def _patch_arguments(arguments: dict[str, Any], metadata: Any) -> None:
    if not isinstance(metadata, dict):
        return
    updates = metadata.get("argument_update", {})
    if isinstance(updates, dict):
        for argument, patch in updates.items():
            current = arguments.get(argument)
            if isinstance(current, dict) and isinstance(patch, dict):
                arguments[argument] = {**current, **patch}
    appends = metadata.get("argument_append", {})
    if isinstance(appends, dict):
        for argument, fields in appends.items():
            current = arguments.get(argument)
            if not isinstance(current, dict) or not isinstance(fields, dict):
                continue
            changed = dict(current)
            for field, value in fields.items():
                existing = changed.get(field)
                if isinstance(existing, list):
                    changed[field] = [*existing, value]
            arguments[argument] = changed


def _transform_result(result: Any, metadata: dict[str, Any]) -> Any:
    replacements = metadata.get("result_replace", {})
    if isinstance(result, str) and isinstance(replacements, dict):
        for old, new in replacements.items():
            result = result.replace(str(old), str(new))
    updates = metadata.get("result_update", {})
    if isinstance(result, dict) and isinstance(updates, dict):
        result = {**result, **updates}
    return result
