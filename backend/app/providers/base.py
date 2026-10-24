"""Provider interfaces and the typed errors every adapter maps its failures onto.

The rest of the application only depends on these protocols, so switching Groq ↔ NVIDIA ↔ mock
is a configuration change. Error classes carry a stable ``category`` that is recorded in run
telemetry and turned into user-facing messages without exposing provider internals.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]
InputType = Literal["query", "passage"]


class ProviderError(Exception):
    category = "provider_error"
    retryable = False
    user_message = "The AI provider returned an error."

    def __init__(self, message: str, *, provider: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class ProviderRateLimitError(ProviderError):
    category = "rate_limited"
    retryable = True
    user_message = "The AI provider's free-tier rate limit was reached. Please retry shortly."

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int | None = 429,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, provider=provider, status_code=status_code)
        self.retry_after = retry_after


class ProviderUnavailableError(ProviderError):
    category = "provider_unavailable"
    retryable = True
    user_message = "The AI provider is temporarily unavailable."


class ProviderTimeoutError(ProviderError):
    category = "provider_timeout"
    retryable = True
    user_message = "The AI provider did not respond in time."


class ProviderAuthError(ProviderError):
    category = "provider_auth"
    user_message = "The AI provider rejected the server's credentials."


class ProviderModelNotFoundError(ProviderError):
    category = "model_unavailable"
    user_message = "The configured model is not available for this account."


class ProviderResponseError(ProviderError):
    category = "malformed_response"
    user_message = "The AI provider returned an unexpected response."


class ProviderQuotaExceededError(ProviderError):
    category = "quota_exceeded"
    user_message = "The AI provider's free-tier quota is exhausted."


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    def add(self, other: "Usage") -> None:
        if other.prompt_tokens is not None:
            self.prompt_tokens = (self.prompt_tokens or 0) + other.prompt_tokens
        if other.completion_tokens is not None:
            self.completion_tokens = (self.completion_tokens or 0) + other.completion_tokens


@dataclass
class ChatResult:
    content: str
    provider: str
    model: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    # Providers skipped by a fallback chain before this one answered.
    failovers: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ChatStreamChunk:
    """Either a text delta, or (on the final chunk) the aggregate result."""

    delta: str = ""
    result: ChatResult | None = None


class ChatProvider(Protocol):
    name: str
    model: str

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResult: ...

    def stream(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[ChatStreamChunk]: ...


class EmbeddingProvider(Protocol):
    name: str
    model: str
    version: str
    dimension: int

    async def embed(self, texts: list[str], *, input_type: InputType) -> list[list[float]]: ...


class RerankProvider(Protocol):
    name: str
    model: str

    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        """Return one relevance score per passage, aligned with the input order."""
        ...
