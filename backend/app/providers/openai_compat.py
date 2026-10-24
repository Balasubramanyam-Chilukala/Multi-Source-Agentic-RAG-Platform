"""Chat adapter for OpenAI-compatible endpoints (Groq and NVIDIA API Catalog both qualify)."""

import time
from collections.abc import AsyncIterator
from typing import Any

from app.providers.base import (
    ChatMessage,
    ChatResult,
    ChatStreamChunk,
    ProviderResponseError,
    Usage,
)
from app.providers.http import ProviderHTTPClient


def _usage(body: dict[str, Any] | None) -> Usage:
    if not isinstance(body, dict):
        return Usage()
    prompt = body.get("prompt_tokens")
    completion = body.get("completion_tokens")
    return Usage(
        prompt_tokens=prompt if isinstance(prompt, int) else None,
        completion_tokens=completion if isinstance(completion, int) else None,
    )


class OpenAICompatibleChat:
    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        http: ProviderHTTPClient,
        temperature: float,
        max_tokens: int,
        reasoning_effort: str | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._http = http
        self._temperature = temperature
        self._max_tokens = max_tokens
        # Only passed for models that accept it (decided by the factory); "low" keeps hidden
        # reasoning from eating the free-tier token budget meant for the answer.
        self._reasoning_effort = reasoning_effort
        self._extra_headers: dict[str, str] = {}

    def _payload(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self._temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self._max_tokens,
        }
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort
        return payload

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResult:
        payload = self._payload(messages, max_tokens=max_tokens, temperature=temperature)
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        body = await self._http.post_json(self._url, payload)
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError(
                f"{self.name} response has no message content", provider=self.name
            ) from exc
        if not isinstance(content, str) or not content.strip():
            # e.g. a reasoning model that spent its whole token budget thinking.
            raise ProviderResponseError(f"{self.name} returned empty content", provider=self.name)
        return ChatResult(
            content=content,
            provider=self.name,
            model=str(body.get("model") or self.model),
            usage=_usage(body.get("usage")),
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        payload = self._payload(messages, max_tokens=max_tokens, temperature=temperature)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        started = time.perf_counter()
        parts: list[str] = []
        usage = Usage()
        model = self.model
        async for event in self._http.stream_sse(self._url, payload):
            model = str(event.get("model") or model)
            if event.get("usage"):
                usage = _usage(event["usage"])
            # Groq also reports usage under x_groq.usage on the final chunk.
            elif isinstance(event.get("x_groq"), dict) and event["x_groq"].get("usage"):
                usage = _usage(event["x_groq"]["usage"])
            for choice in event.get("choices") or []:
                delta = (choice.get("delta") or {}).get("content")
                # Reasoning deltas ("reasoning" field) are deliberately not forwarded.
                if isinstance(delta, str) and delta:
                    parts.append(delta)
                    yield ChatStreamChunk(delta=delta)
        if not "".join(parts).strip():
            raise ProviderResponseError(f"{self.name} streamed no content", provider=self.name)
        yield ChatStreamChunk(
            result=ChatResult(
                content="".join(parts),
                provider=self.name,
                model=model,
                usage=usage,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        )
