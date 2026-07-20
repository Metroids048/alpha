"""DeepSeek implementation of the structured LLM protocol."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"


class DeepSeekLLMError(RuntimeError):
    """A sanitized DeepSeek request or response failure."""


class DeepSeekStructuredLLM:
    """Call DeepSeek's chat completions endpoint and return a JSON object."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        resolved_key = (
            api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY", "")
        )
        if not resolved_key.strip():
            raise ValueError("DEEPSEEK_API_KEY is required")
        if client is not None and transport is not None:
            raise ValueError("provide either client or transport, not both")

        self._api_key = resolved_key.strip()
        self.base_url = (
            base_url or os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_DEEPSEEK_BASE_URL
        ).rstrip("/")
        self.model_id = model or os.getenv("DEEPSEEK_MODEL") or DEFAULT_DEEPSEEK_MODEL
        self._client = client or httpx.Client(timeout=timeout, transport=transport)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> DeepSeekStructuredLLM:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
    ) -> dict[str, Any]:
        schema_text = json.dumps(json_schema, ensure_ascii=False, sort_keys=True)
        payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"{user_prompt}\n\nRequired JSON schema (follow it exactly):\n"
                        f"{schema_text}"
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.TimeoutException:
            raise DeepSeekLLMError("DeepSeek request timed out") from None
        except httpx.RequestError:
            raise DeepSeekLLMError(
                "DeepSeek request failed before receiving a response"
            ) from None

        if response.status_code < 200 or response.status_code >= 300:
            raise DeepSeekLLMError(f"DeepSeek returned HTTP {response.status_code}")

        try:
            envelope = response.json()
        except (json.JSONDecodeError, ValueError):
            raise DeepSeekLLMError(
                "DeepSeek returned an invalid response envelope"
            ) from None

        try:
            content = envelope["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise DeepSeekLLMError(
                "DeepSeek returned an empty structured response"
            ) from None
        if not isinstance(content, str) or not content.strip():
            raise DeepSeekLLMError("DeepSeek returned empty structured content")

        try:
            structured = json.loads(content)
        except json.JSONDecodeError:
            raise DeepSeekLLMError(
                "DeepSeek structured content was not valid JSON object text"
            ) from None
        if not isinstance(structured, dict):
            raise DeepSeekLLMError("DeepSeek structured content must be a JSON object")
        return structured
