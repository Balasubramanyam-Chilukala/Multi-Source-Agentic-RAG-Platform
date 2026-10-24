"""Ordered chat-provider chain (``LLM_FALLBACK_CHAIN``).

Each member is a separate provider account (or a separately rate-limited model on the same
account). The chain moves to the next member only on availability-type failures: rate limit,
quota, outage, timeout, unavailable model, or an empty/malformed answer. It never moves on for
authentication or bad-request errors (those indicate misconfiguration and should surface), and
never once a streamed answer has started reaching the user. Every failover is recorded on the
result so run telemetry shows which provider actually answered and why others were skipped.
"""

from collections.abc import AsyncIterator, Sequence

from app.core.logging import get_logger
from app.providers.base import (
    ChatMessage,
    ChatProvider,
    ChatResult,
    ChatStreamChunk,
    ProviderError,
    ProviderModelNotFoundError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logger = get_logger("atlas.providers.chain")

FAILOVER_ERRORS = (
    ProviderRateLimitError,
    ProviderQuotaExceededError,
    ProviderUnavailableError,
    ProviderTimeoutError,
    ProviderModelNotFoundError,
    ProviderResponseError,
)


def _skip(
    member: ChatProvider, exc: ProviderError, next_member: ChatProvider | None
) -> dict[str, str]:
    logger.warning(
        "provider_failover",
        provider=member.name,
        model=member.model,
        category=exc.category,
        next_provider=next_member.name if next_member else None,
    )
    return {"provider": member.name, "model": member.model, "error_category": exc.category}


class ChainChat:
    def __init__(self, members: Sequence[ChatProvider]) -> None:
        if not members:
            raise ValueError("a provider chain needs at least one member")
        self.members = list(members)
        # Report the primary; ChatResult.provider/model carry whoever actually answered.
        self.name = self.members[0].name
        self.model = self.members[0].model

    def _next(self, index: int) -> ChatProvider | None:
        return self.members[index + 1] if index + 1 < len(self.members) else None

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResult:
        skipped: list[dict[str, str]] = []
        for index, member in enumerate(self.members):
            try:
                result = await member.complete(
                    messages, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature
                )
            except FAILOVER_ERRORS as exc:
                skipped.append(_skip(member, exc, self._next(index)))
                if self._next(index) is None:
                    raise
                continue
            result.failovers = skipped
            return result
        raise AssertionError("unreachable")  # pragma: no cover

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        skipped: list[dict[str, str]] = []
        for index, member in enumerate(self.members):
            delivered = False
            try:
                async for chunk in member.stream(
                    messages, max_tokens=max_tokens, temperature=temperature
                ):
                    if chunk.result is not None:
                        chunk.result.failovers = skipped
                    delivered = delivered or bool(chunk.delta)
                    yield chunk
                return
            except FAILOVER_ERRORS as exc:
                if delivered:
                    raise  # never splice two providers' answers together
                skipped.append(_skip(member, exc, self._next(index)))
                if self._next(index) is None:
                    raise
