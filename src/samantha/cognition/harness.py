"""Budgeted wake protocol with defensive structured action handling."""

from __future__ import annotations
import hashlib
import json
import logging
import time
import uuid
from collections import deque
from typing import Any
from .charter import CHARTER


class Cognition:
    def __init__(self, resident: Any, llm: Any) -> None:
        self.resident, self.llm = resident, llm
        self.wakes: deque[float] = deque()
        self.tokens = 0
        self.open_flags: dict[str, dict[str, Any]] = {}
        self.warned = False

    def wake(self, event: Any) -> dict[str, Any] | None:
        now = time.time()
        while self.wakes and self.wakes[0] < now - 3600:
            self.wakes.popleft()
        cfg = self.resident.config
        if (
            len(self.wakes) >= cfg.max_wakes_per_hour
            or self.tokens >= cfg.token_budget_per_day
            or len(self.open_flags) >= cfg.flag_budget
        ):
            if not self.warned:
                logging.getLogger("samantha").warning("cognition budget exhausted")
                self.warned = True
            return None
        prompt = json.dumps(
            {"event": event.sentinel, "evidence": event.evidence, "mode": cfg.mode}
        )
        try:
            response = self.llm.complete(
                [{"role": "user", "content": CHARTER + "\n" + prompt}], event.sentinel
            )
        except Exception as exc:
            logging.getLogger("samantha").warning("cognition disabled: %s", exc)
            return None
        self.wakes.append(now)
        used = len(prompt.split())
        self.tokens += used
        digest = hashlib.sha256(
            json.dumps(event.evidence, default=str).encode()
        ).hexdigest()[:16]
        self.resident.journal.write(
            "wake",
            sentinel=event.sentinel,
            evidence_bundle_digest=digest,
            tokens_used=used,
        )
        self._actions(response.get("actions", []), event.evidence)
        return response

    def _actions(self, actions: list[dict[str, Any]], evidence: Any) -> None:
        for action in actions:
            kind = action.get("type", "none")
            body = {k: v for k, v in action.items() if k != "type"}
            if kind == "flag":
                body.setdefault("id", uuid.uuid4().hex[:8])
                body.setdefault("status", "open")
                body.setdefault("evidence", evidence)
                self.open_flags[body["id"]] = body
                self.resident.journal.write("flag", **body)
            elif kind == "question":
                body.setdefault("id", uuid.uuid4().hex[:8])
                body.setdefault("status", "pending")
                self.resident.journal.write("question", **body)
            elif kind in {"proposal", "intervene"}:
                if kind == "intervene" and self.resident.config.mode != "act":
                    body = {
                        "kind": "config",
                        "body": body,
                        "rationale": "intervention rejected outside act mode",
                    }
                self.resident.journal.write("proposal", **body)
            elif kind == "update_model":
                (self.resident.state / "model.md").write_text(str(body.get("text", "")))

    def chat(self, text: str) -> str:
        """Answer a local operator without blocking host code."""
        context = {
            "operator_question": text,
            "mode": self.resident.config.mode,
            "model": (self.resident.state / "model.md").read_text(errors="replace"),
        }
        try:
            response = self.llm.complete(
                [
                    {
                        "role": "user",
                        "content": CHARTER
                        + "\nThe following is a direct operator question. Answer it "
                        + "concisely using your resident evidence.\n"
                        + json.dumps(context),
                    }
                ],
                "chat",
            )
        except Exception as exc:
            logging.getLogger("samantha").warning("chat cognition failed: %s", exc)
            return "I couldn't reach the configured model."
        return str(
            response.get("answer")
            or response.get("assessment")
            or "I don't have enough evidence yet."
        )
