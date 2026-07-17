"""OpenAI Responses API provider and deterministic fake."""

from __future__ import annotations
import json
import re
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

    def chat(self, messages: list[dict[str, str]]) -> str:
        response = self.complete(messages, "chat")
        return str(response.get("answer") or response.get("assessment") or response)


class OpenAILLM:
    def __init__(
        self, api_key: str, model: str, base_url: str, timeout: float = 120.0
    ) -> None:
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.timeout = timeout

    def complete(
        self, messages: list[dict[str, str]], signature: str = "default"
    ) -> dict[str, Any]:
        import httpx

        body: dict[str, Any] = {
            "model": self.model,
            "input": messages,
            "instructions": "Return a single valid JSON object and no markdown.",
        }
        if signature == "cold_start":
            body["text"] = {"format": _cold_start_format()}
        response = httpx.post(
            f"{self.base_url}/v1/responses",
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
        )
        if response.is_error:
            detail = _error_detail(response)
            raise RuntimeError(
                f"OpenAI API returned HTTP {response.status_code}: {detail}"
            )
        payload = response.json()
        text = _output_text(payload)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.S)
            if match:
                return json.loads(match.group())
            return {"assessment": text, "answer": text, "actions": []}

    def chat(self, messages: list[dict[str, str]]) -> str:
        """Return unconstrained conversational text rather than structured actions."""
        import httpx

        response = httpx.post(
            f"{self.base_url}/v1/responses",
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "input": messages,
                "instructions": (
                    "You are Samantha, the resident intelligence in this program. "
                    "Respond naturally and directly to the operator. You can have an "
                    "ordinary conversation as well as discuss the supplied project context."
                ),
            },
        )
        if response.is_error:
            raise RuntimeError(
                f"OpenAI API returned HTTP {response.status_code}: "
                f"{_error_detail(response)}"
            )
        return _output_text(response.json())

    def check(self) -> None:
        """Validate credentials, connectivity, and model access without token usage."""
        import httpx

        response = httpx.get(
            f"{self.base_url}/v1/models/{self.model}",
            timeout=min(self.timeout, 30),
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        if response.is_error:
            raise RuntimeError(
                f"OpenAI API returned HTTP {response.status_code}: "
                f"{_error_detail(response)}"
            )


def _error_detail(response: Any) -> str:
    try:
        payload = response.json()
        return str(payload.get("error", {}).get("message") or "request rejected")[:500]
    except (ValueError, AttributeError):
        return response.text[:500] or "request rejected"


def _cold_start_format() -> dict[str, Any]:
    fields = (
        "architecture",
        "goals",
        "watching",
        "knob_candidates",
        "protected",
        "ambiguities",
    )
    properties: dict[str, Any] = {"summary": {"type": "string"}}
    properties.update(
        {name: {"type": "array", "items": {"type": "string"}} for name in fields}
    )
    return {
        "type": "json_schema",
        "name": "samantha_cold_start_analysis",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": ["summary", *fields],
            "additionalProperties": False,
        },
    }


def _output_text(payload: dict[str, Any]) -> str:
    """Find text across Responses output items, skipping reasoning/tool records."""
    if isinstance(payload.get("output_text"), str) and payload["output_text"]:
        return payload["output_text"]
    refusals = []
    for item in payload.get("output", []):
        for content in item.get("content", []) or []:
            text = content.get("text")
            if isinstance(text, str) and text:
                return text
            refusal = content.get("refusal")
            if refusal:
                refusals.append(str(refusal))
    if refusals:
        raise RuntimeError(f"OpenAI refused the response: {'; '.join(refusals)}")
    status = payload.get("status", "unknown")
    details = payload.get("incomplete_details") or {}
    suffix = f": {details}" if details else ""
    raise RuntimeError(f"OpenAI response contained no text (status={status}){suffix}")


AnthropicLLM = OpenAILLM
