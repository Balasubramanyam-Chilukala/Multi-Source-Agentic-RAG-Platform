"""HTTP client shared by provider adapters: bounded retries, backoff with jitter, and
rate-limit awareness.

Retries happen only for transient failures (429, 5xx, timeouts, connection errors). A 429 whose
reset time exceeds ``max_backoff`` fails immediately with ``ProviderRateLimitError`` instead of
blocking the request, so the user gets a clear "retry shortly" message.
"""

import asyncio
import json
import random
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from app.core.logging import get_logger
from app.providers.base import (
    ProviderAuthError,
    ProviderError,
    ProviderModelNotFoundError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logger = get_logger("atlas.providers")

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(value: str | None) -> float | None:
    """Parse ``retry-after`` (seconds) or Groq-style resets such as ``1m26.4s`` / ``250ms``."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    parts = _DURATION_PART.findall(value)
    if not parts:
        return None
    return sum(float(number) * _UNIT_SECONDS[unit] for number, unit in parts)


def retry_after_seconds(response: httpx.Response) -> float | None:
    explicit = parse_duration(response.headers.get("retry-after"))
    if explicit is not None:
        return explicit
    # Groq reports separate request and token windows; wait for whichever is exhausted.
    candidates = []
    for window in ("requests", "tokens"):
        remaining = response.headers.get(f"x-ratelimit-remaining-{window}")
        reset = parse_duration(response.headers.get(f"x-ratelimit-reset-{window}"))
        if reset is not None and remaining is not None and remaining.strip() in {"0", "0.0"}:
            candidates.append(reset)
    return max(candidates) if candidates else None


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        error = body.get("error", body.get("detail", body.get("title", "")))
        if isinstance(error, dict):
            error = error.get("message", "")
        return str(error)[:200]
    return str(body)[:200]


def map_error_response(provider: str, response: httpx.Response) -> ProviderError:
    status = response.status_code
    detail = _error_detail(response)
    message = f"{provider} returned HTTP {status}: {detail}"
    lowered = detail.lower()
    if status == 429:
        if "quota" in lowered or "credit" in lowered:
            return ProviderQuotaExceededError(message, provider=provider, status_code=status)
        return ProviderRateLimitError(
            message, provider=provider, retry_after=retry_after_seconds(response)
        )
    if status == 402:
        return ProviderQuotaExceededError(message, provider=provider, status_code=status)
    if status in (401, 403):
        return ProviderAuthError(
            f"{provider} rejected the credentials (HTTP {status})",
            provider=provider,
            status_code=status,
        )
    if status in (404, 410) or (
        status == 400
        and "model" in lowered
        and ("not exist" in lowered or "decommission" in lowered)
    ):
        return ProviderModelNotFoundError(message, provider=provider, status_code=status)
    if status >= 500:
        return ProviderUnavailableError(message, provider=provider, status_code=status)
    return ProviderError(message, provider=provider, status_code=status)


class ProviderHTTPClient:
    def __init__(
        self,
        provider: str,
        *,
        api_key: str,
        timeout: float,
        max_retries: int,
        max_backoff: float,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.provider = provider
        self.max_retries = max_retries
        self.max_backoff = max_backoff
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _backoff(self, attempt: int) -> float:
        base: float = min(self.max_backoff, 0.5 * (2**attempt))
        return base / 2 + random.uniform(0, base / 2)  # noqa: S311  (jitter, not crypto)

    async def _wait_before_retry(self, error: ProviderError, attempt: int) -> None:
        """Sleep before the next attempt, or re-raise when retrying is not worthwhile."""
        if not error.retryable or attempt >= self.max_retries:
            raise error
        delay = self._backoff(attempt)
        if isinstance(error, ProviderRateLimitError) and error.retry_after is not None:
            if error.retry_after > self.max_backoff:
                raise error
            delay = max(delay, error.retry_after)
        logger.warning(
            "provider_retry",
            provider=self.provider,
            category=error.category,
            attempt=attempt + 1,
            delay_s=round(delay, 2),
        )
        await self._sleep(delay)

    async def post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            started = time.perf_counter()
            try:
                response = await self._client.post(url, json=payload)
            except httpx.TimeoutException:
                error: ProviderError = ProviderTimeoutError(
                    f"{self.provider} request timed out", provider=self.provider
                )
            except httpx.TransportError as exc:
                error = ProviderUnavailableError(
                    f"{self.provider} connection failed ({type(exc).__name__})",
                    provider=self.provider,
                )
            else:
                logger.debug(
                    "provider_response",
                    provider=self.provider,
                    status_code=response.status_code,
                    latency_ms=round((time.perf_counter() - started) * 1000, 1),
                )
                if response.is_success:
                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise ProviderResponseError(
                            f"{self.provider} returned invalid JSON", provider=self.provider
                        ) from exc
                    if not isinstance(body, dict):
                        raise ProviderResponseError(
                            f"{self.provider} returned a non-object JSON body",
                            provider=self.provider,
                        )
                    return body
                error = map_error_response(self.provider, response)
            await self._wait_before_retry(error, attempt)
            attempt += 1

    async def stream_sse(self, url: str, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Yield parsed ``data:`` events. Retries only until the stream has started, so a
        partially delivered answer is never duplicated."""
        attempt = 0
        started = False
        while True:
            try:
                async with self._client.stream("POST", url, json=payload) as response:
                    if not response.is_success:
                        await response.aread()
                        error: ProviderError = map_error_response(self.provider, response)
                    else:
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                return
                            try:
                                event = json.loads(data)
                            except ValueError as exc:
                                raise ProviderResponseError(
                                    f"{self.provider} sent a malformed stream event",
                                    provider=self.provider,
                                ) from exc
                            if isinstance(event, dict):
                                if "error" in event:
                                    raise ProviderError(
                                        f"{self.provider} stream error: "
                                        f"{str(event['error'])[:200]}",
                                        provider=self.provider,
                                    )
                                started = True
                                yield event
                        return
            except httpx.TimeoutException:
                error = ProviderTimeoutError(
                    f"{self.provider} stream timed out", provider=self.provider
                )
            except httpx.TransportError as exc:
                error = ProviderUnavailableError(
                    f"{self.provider} connection failed ({type(exc).__name__})",
                    provider=self.provider,
                )
            if started:
                raise error
            await self._wait_before_retry(error, attempt)
            attempt += 1
