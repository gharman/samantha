"""OpenAI Responses API provider and deterministic fake."""

from __future__ import annotations
import json
from typing import Any


class FakeLLM:
    def __init__(
        self, scripts: dict[str, dict[str, Any]] | list[dict[str, Any]] | None = None
    ) -> None:
        self.scripts, self.calls = scripts or {}, 0

    def complete(
        self, messages: list[dict[str, str]], signature: str = "default"
    ) -> dict[str, Any]:
        self.calls += 1
        if isinstance(self.scripts, list):
            return self.scripts[min(self.calls - 1, len(self.scripts) - 1)]
        return self.scripts.get(
            signature,
            self.scripts.get("default", {"assessment": "healthy", "actions": []}),
        )


class OpenAILLM:
    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")

    def complete(
        self, messages: list[dict[str, str]], signature: str = "default"
    ) -> dict[str, Any]:
        import httpx

        response = httpx.post(
            f"{self.base_url}/v1/responses",
            timeout=30,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "input": messages,
                "text": {"format": {"type": "json_object"}},
            },
        )
        response.raise_for_status()
        payload = response.json()
        text = payload.get("output_text")
        if not text:
            text = payload["output"][0]["content"][0]["text"]
        return json.loads(text)


AnthropicLLM = OpenAILLM
