"""Iterative model planning over live runtime introspection tools."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from samantha.cognition.harness import operator_intent
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
        observed: set[str] = set()
        round_number = 0
        max_rounds = int(getattr(self.resident.config, "act_max_rounds", 0))
        while max_rounds <= 0 or round_number < max_rounds:
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
                result.ok and self._is_write(operation)
                for operation, result in zip(operations, group.results, strict=False)
            )
            if mutated:
                receipt = self._receipt(group, "")
                answer = self._final_answer(
                    operator_text,
                    group,
                    str(plan.get("reply") or ""),
                )
                return f"{receipt}\n\n{answer}" if answer else receipt
            # Reads that return new information keep planning; repeating reads whose
            # results the planner already holds is a loop, not progress.
            evidence = json.dumps(
                [
                    [op.get("op"), op.get("path"), op.get("query"), result.as_dict()]
                    for op, result in zip(operations, group.results, strict=False)
                ],
                sort_keys=True,
                default=str,
            )
            if evidence in observed:
                return str(
                    plan.get("reply")
                    or "I kept re-reading the same live state without finding an "
                    "action to take, so nothing was changed."
                )
            observed.add(evidence)
            round_number += 1
        return (
            f"I reached the configured runtime inspection limit ({max_rounds}) without "
            "applying a verified action."
        )

    def _context(
        self, operator_text: str, transcript: list[dict[str, Any]], round_number: int
    ) -> dict[str, Any]:
        # Ranked by relevance to the request and to the names that the operator's
        # '# daemon:' directives mention: a short request ("help") matched nothing,
        # and frame locals filled the 300. The planner can search for the rest.
        structure = self._source_structure()
        named = _directive_names(structure.get("directives", []))
        inventory = self.runtime.inventory(
            query=f"{operator_text} {named}".strip(), limit=300
        )
        return {
            "mode": "act",
            "operator_request": operator_text,
            "operator_intent": operator_intent(self.resident),
            "authority": (
                "The current operator message authorizes relevant runtime actions. "
                "Use Python's full live introspection surface: reachable application objects, "
                "methods, adapters, database/file/service APIs, and state are eligible even "
                "without handlers or declarations. Irreversible existing-callable invocation "
                "is allowed only when directly necessary for this request. Do not use exec, "
                "eval, subprocess/process launching, source writes, imports, or invented paths. "
                "Inspect first when the correct target is uncertain."
            ),
            "operation_semantics": {
                "search": "query live paths; read-only",
                "inspect": "inspect one exact live path; read-only",
                "set": (
                    "assign JSON value to exact attribute/item path. A path with scope "
                    "'class' is a class attribute; when it lists shadowed_by, live "
                    "instances read those paths instead, so set or call them"
                ),
                "append": "append JSON value to an existing collection",
                "enqueue": "put or append JSON value into an existing queue",
                "call": (
                    "invoke any allowed callable reachable from live project state with JSON "
                    "args/kwargs; {'$ref':'exact live path'} passes an existing live object"
                ),
                "intercept": (
                    "wrap an assignable existing callable. kwargs_json overrides named "
                    "arguments. value_json may contain when (argument equality map), "
                    "when_contains (argument substring map), "
                    "argument_update (argument-name to mapping merge), argument_append "
                    "(argument-name to field/value append map), argument_replace "
                    "(argument-name to string replacement map), result_replace (string "
                    "replacement map), result_update (mapping merge), "
                    "activate_output_rewrite (a future output rewrite specification), and "
                    "receipt. one_shot restores after the first matching invocation; "
                    "process persists"
                ),
                "rewrite_output": (
                    "fallback for text that has no reachable emitting callable: rewrite "
                    "matching future stdout/stderr text. Registration does not prove the host "
                    "writes through the tapped stream"
                ),
                "emit": "write an operator-visible receipt through Samantha's host logger",
                "lifetimes": (
                    "one_shot restores after the first matching call; temporary "
                    "reverts after duration_s seconds (default 120); until_failure "
                    "reverts automatically on NaN/inf metrics, a new ERROR or "
                    "Traceback line, or an exception through the interceptor; "
                    "process and contextual last until reverted"
                ),
                "revert": (
                    "undo an active intervention: path is its id from "
                    "active_interventions, or 'last' for the most recent"
                ),
            },
            "project_theory": self._project_theory(),
            "source_structure": structure,
            "declared_knobs": self.runtime.declared_knobs(),
            "live_paths": inventory,
            "active_interventions": [
                group.summary() for group in self.runtime.revertible()
            ],
            "prior_tool_results": transcript,
            "recent_chat": self._recent_chat(),
            "open_findings": self._open_findings(),
            "instructions": (
                "Prefer a declared_knobs path (declared by the host, bounds-checked) "
                "when one fits the request. Choose converse for ordinary conversation. "
                "Choose inspect when more runtime "
                "information is required. Choose act only for operations that advance the "
                "request. Mark every operation effect read or write. A read-only call does "
                "not finish a tool turn: always continue planning from its result. Return "
                "no operations on the following round when that evidence is sufficient for "
                "an informational answer. For "
                "future behavior, intercept the future consumer/call and patch its arguments "
                "instead of changing an already-consumed value. Encode values/args/kwargs as "
                "JSON strings. Select contextual lifetime from intent. Never state an "
                "unverified result in reply. Do not ask for an application-specific handler "
                "when reachable objects or the application's own APIs can perform the task. "
                "For coupled future changes, activate presentation rewrites from the same "
                "future-call interceptor instead of installing independent next-event rules. "
                "Prefer intercepting a live progress/render/print callback and transforming "
                "its message argument; use raw output rewriting only when no emitting callable "
                "is reachable, and use source null unless the stream is verified. "
                "If the request asks for help and open_findings or operator_intent show an "
                "unmet target, act on it with in-range changes that you predict meet the "
                "target."
            ),
        }

    def _final_answer(
        self, operator_text: str, group: InterventionGroup, planned_reply: str
    ) -> str:
        converse = getattr(self.llm, "chat", None)
        if not converse:
            return planned_reply
        context = {
            "operator_request": operator_text,
            "planned_reply_before_tools": planned_reply,
            "verified_tool_results": [result.as_dict() for result in group.results],
            "recent_process_activity_after_tools": self._recent_activity(),
            "recent_chat": self._recent_chat(),
            "project_theory": self._project_theory(),
            "verification_rule": (
                "Distinguish installed future behavior from an observed outcome. Resumed or "
                "already-queued work is unaffected unless the operation explicitly changes it."
            ),
        }
        messages = [
            {
                "role": "user",
                "content": (
                    "Answer the operator's request now, using the verified tool results and "
                    "new process output below. Do not promise a future retrieval that already "
                    "ran. If a callable printed its result and returned null, use the captured "
                    "process output. State clearly what the result establishes and what remains "
                    "unknown. Never claim an unverified action.\n" + json.dumps(context)
                ),
            }
        ]
        try:
            return str(converse(messages))
        except Exception as exc:
            logging.getLogger("samantha").warning(
                "post-action synthesis failed: %s", exc
            )
            return planned_reply or f"Post-action explanation failed: {exc}"

    def _open_findings(self) -> list[dict[str, Any]]:
        """Open flags from sentinel wakes, and the latest questions and proposals."""
        cognition = getattr(self.resident, "cognition", None)
        flags = list(getattr(cognition, "open_flags", {}).values())[-10:]
        rows = [
            row
            for row in self.resident.journal.recent(200)
            if row.get("type") in {"question", "proposal"}
        ]
        return [self._clip(row) for row in [*flags, *rows[-5:]]]

    @staticmethod
    def _clip(row: dict[str, Any], limit: int = 2_000) -> dict[str, Any]:
        """Cap long string fields (flag evidence can be large) to bound the context."""
        return {
            key: value[:limit] + "..."
            if isinstance(value, str) and len(value) > limit
            else value
            for key, value in row.items()
        }

    def _recent_activity(self) -> list[dict[str, Any]]:
        observer = getattr(self.resident, "observer", None)
        if observer and hasattr(observer, "recent_snapshot"):
            return observer.recent_snapshot()
        return []

    def _recent_chat(self, limit: int = 30) -> list[dict[str, Any]]:
        return self.resident.journal.recent(limit, "chat")

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
            errors = []
            for field, target, default in (
                ("value_json", "value", None),
                ("args_json", "args", []),
                ("kwargs_json", "kwargs", {}),
            ):
                try:
                    item[target] = _decode(item.get(field), default)
                except ValueError as exc:
                    # Never substitute a default for a malformed value: assigning
                    # None would look like a verified change.
                    errors.append(f"invalid {field} {item.get(field)!r}: {exc}")
            if errors:
                item["decode_error"] = "; ".join(errors)
            decoded.append(item)
        return decoded

    def _receipt(self, group: InterventionGroup, reply: str) -> str:
        lines = [f"Applied runtime intervention {group.id} ({group.lifetime}):"]
        for result in group.results:
            marker = "✓" if result.ok and result.verified else "•" if result.ok else "✗"
            value = "" if result.value is None else f" → {result.as_dict()['value']}"
            lines.append(f"{marker} {result.op} {result.path}: {result.detail}{value}")
        if reply:
            lines.extend(["", reply])
        return "\n".join(lines)

    @staticmethod
    def _is_write(operation: dict[str, Any]) -> bool:
        op = str(operation.get("op", ""))
        if op == "call":
            return operation.get("effect") == "write"
        return op in {
            "set",
            "append",
            "enqueue",
            "intercept",
            "rewrite_output",
            "emit",
            "revert",
        }

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
    if not isinstance(value, str):
        raise ValueError(f"expected a JSON string, got {type(value).__name__}")
    return json.loads(value)


def _directive_names(directives: list[Any]) -> str:
    """CONSTANT and Class.attr names that '# daemon:' directives mention."""
    text = " ".join(map(str, directives))
    names = re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b|\b[A-Z]\w*\.\w+\b", text)
    return " ".join(dict.fromkeys(names))
