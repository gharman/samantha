"""Iterative model planning over live runtime introspection tools."""

from __future__ import annotations

import json
import logging
from typing import Any

from samantha.state import load_json

from .runtime import InterventionGroup, RuntimeOperator


class ActController:
    def __init__(self, resident: Any, llm: Any) -> None:
        self.resident = resident
        self.llm = llm
        self.runtime = RuntimeOperator(resident)

    def handle(self, operator_text: str) -> str:
        planner = getattr(self.llm, "plan_action", None)
        if not planner:
            return self._plain_fallback(operator_text)
        transcript: list[dict[str, Any]] = []
        for round_number in range(4):
            context = self._context(operator_text, transcript, round_number)
            try:
                plan = planner([{"role": "user", "content": json.dumps(context)}])
            except Exception as exc:
                logging.getLogger("samantha").warning("act planning failed: %s", exc)
                return self._plain_fallback(operator_text)
            operations = self._decode_operations(plan.get("operations", []))
            if not operations:
                return str(plan.get("reply") or self._plain_fallback(operator_text))
            group = self.runtime.execute(
                operator_text,
                operations,
                str(plan.get("lifetime") or "contextual"),
            )
            transcript.append(
                {
                    "plan": plan,
                    "group_id": group.id,
                    "status": group.status,
                    "results": [result.as_dict() for result in group.results],
                }
            )
            mutated = any(
                result.op not in {"search", "inspect"} and result.ok
                for result in group.results
            )
            if mutated:
                return self._receipt(group, str(plan.get("reply") or ""))
        return "I inspected the runtime but could not form a verified action plan."

    def _context(
        self, operator_text: str, transcript: list[dict[str, Any]], round_number: int
    ) -> dict[str, Any]:
        query = operator_text if round_number == 0 else None
        inventory = self.runtime.inventory(query=query, limit=600)
        if not inventory:
            inventory = self.runtime.inventory(limit=600)
        return {
            "mode": "act",
            "operator_request": operator_text,
            "authority": (
                "The current operator message authorizes relevant runtime actions. "
                "Irreversible existing-callable invocation is allowed only when directly "
                "necessary for this request. Do not use exec, eval, source writes, imports, "
                "or invented paths. Inspect first when the correct target is uncertain."
            ),
            "operation_semantics": {
                "search": "query live paths; read-only",
                "inspect": "inspect one exact live path; read-only",
                "set": "assign JSON value to exact attribute/item path",
                "append": "append JSON value to an existing collection",
                "enqueue": "put or append JSON value into an existing queue",
                "call": "invoke an existing project callable with JSON args/kwargs",
                "intercept": (
                    "wrap an assignable existing callable and override named arguments; "
                    "one_shot restores on first invocation, process persists"
                ),
                "emit": "write an operator-visible receipt through Samantha's host logger",
            },
            "project_theory": self._project_theory(),
            "source_structure": self._source_structure(),
            "live_paths": inventory,
            "prior_tool_results": transcript,
            "instructions": (
                "Choose converse for ordinary conversation. Choose inspect when more runtime "
                "information is required. Choose act only for operations that advance the "
                "request. Encode values/args/kwargs as JSON strings. Select contextual "
                "lifetime from intent. Never state an unverified result in reply."
            ),
        }

    def _project_theory(self) -> str:
        try:
            return (self.resident.state / "model.md").read_text(errors="replace")[
                :30_000
            ]
        except OSError:
            return ""

    def _source_structure(self) -> dict[str, Any]:
        inventory = load_json(self.resident.state / "source-inventory.json", {})
        return {
            "entry": inventory.get("entry"),
            "directives": inventory.get("directives", []),
            "files": {
                path: {
                    "docstring": details.get("docstring"),
                    "imports": details.get("imports", []),
                    "outline": details.get("outline", []),
                    "help_strings": details.get("help_strings", []),
                }
                for path, details in inventory.get("files", {}).items()
            },
        }

    def _decode_operations(
        self, operations: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        decoded = []
        for operation in operations:
            item = dict(operation)
            item["path"] = item.get("path") or ""
            item["value"] = _decode(item.get("value_json"), None)
            item["args"] = _decode(item.get("args_json"), [])
            item["kwargs"] = _decode(item.get("kwargs_json"), {})
            decoded.append(item)
        return decoded

    def _receipt(self, group: InterventionGroup, reply: str) -> str:
        lines = [f"Applied runtime intervention {group.id} ({group.lifetime}):"]
        for result in group.results:
            marker = "✓" if result.ok and result.verified else "•" if result.ok else "✗"
            lines.append(f"{marker} {result.op} {result.path}: {result.detail}")
        if reply:
            lines.extend(["", reply])
        return "\n".join(lines)

    def _plain_fallback(self, operator_text: str) -> str:
        messages = [
            {
                "role": "user",
                "content": (
                    "The operator is speaking to Samantha in act mode, but runtime planning "
                    "is unavailable. Converse normally and be honest that no action ran.\n"
                    + operator_text
                ),
            }
        ]
        converse = getattr(self.llm, "chat", None)
        return (
            str(converse(messages)) if converse else "Runtime planning is unavailable."
        )


def _decode(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default
