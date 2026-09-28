"""A scripted stand-in for the model provider so the testbed runs without a key.

Act-mode plans can be scripted with ``TESTBED_PLANS=/path/plans.json`` holding a
list of plan objects (the ``plan_action`` schema); each planning round consumes
the next one, and an exhausted script answers conversationally with no operations.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any


class ScriptedLLM:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        path = os.getenv("TESTBED_PLANS")
        self.plans: list[dict[str, Any]] = json.loads(open(path).read()) if path else []

    def complete(
        self, messages: list[dict[str, str]], signature: str = "default"
    ) -> dict[str, Any]:
        time.sleep(self.delay)
        return {"assessment": "fake", "actions": []}

    def chat(self, messages: list[dict[str, str]]) -> str:
        time.sleep(self.delay)
        return f"(fake model) received {len(messages[-1]['content'])} chars"

    def plan_action(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        time.sleep(self.delay)
        if self.plans:
            return self.plans.pop(0)
        return {
            "intent": "converse",
            "reply": "(fake model) no scripted plan remains",
            "done": True,
            "lifetime": "contextual",
            "operations": [],
        }


def install(delay: float) -> None:
    from samantha._boot import resident
    from samantha.cognition.harness import Cognition

    current = resident()
    if current is None:
        return
    llm = ScriptedLLM(delay)
    current.cognition = Cognition(current, llm)
    current.cognition.provider_ready = True
    if current.config.mode == "act":
        from samantha.act.controller import ActController

        current.actuator = ActController(current, llm)
