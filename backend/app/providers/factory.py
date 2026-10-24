"""Builds provider adapters from settings and owns their HTTP clients' lifecycle."""

from dataclasses import dataclass, field

import httpx

from app.core.config import OPENROUTER_FREE_SUFFIX, Settings
from app.providers.base import ChatProvider, EmbeddingProvider, RerankProvider
from app.providers.fallback import ChainChat
from app.providers.http import ProviderHTTPClient
from app.providers.local import LocalCrossEncoderReranker
from app.providers.mock import HashingEmbeddings, MockChat, MockReranker
from app.providers.nvidia import NvidiaEmbeddings, NvidiaReranker
from app.providers.openai_compat import OpenAICompatibleChat


class ProviderConfigurationError(ValueError):
    pass


def supports_reasoning_effort(provider: str, model: str) -> bool:
    """Models verified to accept ``reasoning_effort`` (others may reject the parameter)."""
    return (provider == "groq" and "gpt-oss" in model) or provider == "gemini"


@dataclass
class ProviderRegistry:
    settings: Settings
    transport: httpx.AsyncBaseTransport | None = None
    _http: dict[tuple[str, str], ProviderHTTPClient] = field(default_factory=dict)
    _chat: dict[tuple[str, str], ChatProvider] = field(default_factory=dict)
    _embeddings: EmbeddingProvider | None = None
    _reranker: RerankProvider | None = None
    _reranker_built: bool = False

    def _client(self, provider: str, purpose: str = "chat") -> ProviderHTTPClient:
        """One HTTP client per (provider, purpose). Chat clients in a fallback chain fail over
        quickly; embedding/rerank clients keep the patient retry policy (no alternative)."""
        if (provider, purpose) not in self._http:
            key = getattr(self.settings, f"{provider}_api_key", None)
            if key is None:
                raise ProviderConfigurationError(f"{provider.upper()}_API_KEY is not configured")
            in_chain = purpose == "chat" and bool(self.settings.llm_fallback_chain)
            self._http[(provider, purpose)] = ProviderHTTPClient(
                provider,
                api_key=key.get_secret_value(),
                timeout=self.settings.provider_timeout_seconds,
                max_retries=(
                    self.settings.chain_max_retries
                    if in_chain
                    else self.settings.provider_max_retries
                ),
                max_backoff=(
                    self.settings.chain_max_backoff_seconds
                    if in_chain
                    else self.settings.provider_max_backoff_seconds
                ),
                transport=self.transport,
            )
        return self._http[(provider, purpose)]

    def _build_chat(self, provider: str, model: str) -> ChatProvider:
        if provider == "mock":
            return MockChat()
        base_url = {
            "groq": self.settings.groq_base_url,
            "nvidia": self.settings.nvidia_base_url,
            "gemini": self.settings.gemini_base_url,
            "openrouter": self.settings.openrouter_base_url,
        }
        if provider not in base_url:
            raise ProviderConfigurationError(f"unknown chat provider {provider!r}")
        if provider == "openrouter" and not model.endswith(OPENROUTER_FREE_SUFFIX):
            # Defence in depth: settings validation already rejects these at startup.
            raise ProviderConfigurationError(f"OpenRouter model {model!r} is not a free model")
        return OpenAICompatibleChat(
            name=provider,
            model=model,
            base_url=base_url[provider],
            http=self._client(provider),
            temperature=self.settings.llm_temperature,
            max_tokens=self.settings.llm_max_tokens,
            reasoning_effort=(
                self.settings.llm_reasoning_effort
                if supports_reasoning_effort(provider, model)
                else None
            ),
        )

    def chat(self, provider: str | None = None, model: str | None = None) -> ChatProvider:
        """The chat provider for a request: the selected model, followed by the configured
        fallback chain (``LLM_FALLBACK_CHAIN``) when there is one."""
        provider = provider or self.settings.llm_provider
        model = model or (self.settings.llm_model if provider == self.settings.llm_provider else "")
        if not model:
            raise ProviderConfigurationError(f"a model must be specified for provider {provider!r}")
        key = (provider, model)
        if key not in self._chat:
            members = [(provider, model)]
            members += [m for m in self.settings.chat_chain() if m not in members]
            if len(members) == 1:
                self._chat[key] = self._build_chat(provider, model)
            else:
                self._chat[key] = ChainChat([self._build_chat(p, m) for p, m in members])
        return self._chat[key]

    def embeddings(self) -> EmbeddingProvider:
        if self._embeddings is None:
            if self.settings.embedding_provider == "mock":
                self._embeddings = HashingEmbeddings()
            else:
                self._embeddings = NvidiaEmbeddings(
                    model=self.settings.embedding_model,
                    dimension=self.settings.embedding_dimension,
                    base_url=self.settings.nvidia_base_url,
                    http=self._client("nvidia", "embeddings"),
                    batch_size=self.settings.embedding_batch_size,
                )
        return self._embeddings

    def reranker(self) -> RerankProvider | None:
        if not self._reranker_built:
            self._reranker_built = True
            provider, model = self.settings.reranker_provider, self.settings.reranker_model or ""
            if provider == "mock":
                self._reranker = MockReranker()
            elif provider == "local":
                self._reranker = LocalCrossEncoderReranker(model=model)
            elif provider == "nvidia":
                self._reranker = NvidiaReranker(
                    model=model,
                    url_template=self.settings.nvidia_rerank_url,
                    http=self._client("nvidia", "rerank"),
                )
        return self._reranker

    async def aclose(self) -> None:
        for client in self._http.values():
            await client.aclose()
        self._http.clear()
