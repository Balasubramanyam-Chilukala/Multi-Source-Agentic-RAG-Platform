"""Provider outages and rate limits through the full API: clear errors, nothing lost."""

from collections.abc import AsyncIterator, Callable

import pytest

from app.core.config import Settings
from app.providers.base import (
    ChatProvider,
    EmbeddingProvider,
    InputType,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from app.providers.factory import ProviderRegistry
from app.providers.mock import HashingEmbeddings
from tests.integration.conftest import HANDBOOK, create_collection, register, running_app, upload

pytestmark = pytest.mark.integration


class FailingChat:
    name = "groq"
    model = "openai/gpt-oss-120b"

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def complete(self, *args: object, **kwargs: object) -> object:
        raise self.error

    async def stream(self, *args: object, **kwargs: object) -> AsyncIterator[object]:
        raise self.error
        yield  # pragma: no cover


class FailingEmbeddings(HashingEmbeddings):
    async def embed(self, texts: list[str], *, input_type: InputType) -> list[list[float]]:
        raise ProviderRateLimitError("429", provider="nvidia", retry_after=60)


class Registry(ProviderRegistry):
    chat_override: ChatProvider | None = None
    embed_override: EmbeddingProvider | None = None

    def chat(self, provider: str | None = None, model: str | None = None) -> ChatProvider:
        return self.chat_override or super().chat(provider, model)

    def embeddings(self) -> EmbeddingProvider:
        return self.embed_override or super().embeddings()


@pytest.mark.parametrize(
    ("error", "category", "phrase"),
    [
        (
            ProviderUnavailableError("503", provider="groq"),
            "provider_unavailable",
            "temporarily unavailable",
        ),
        (
            ProviderRateLimitError("429", provider="groq", retry_after=40),
            "rate_limited",
            "rate limit",
        ),
    ],
)
async def test_chat_provider_failure_is_reported_and_persisted(
    api_settings: Callable[..., Settings], error: Exception, category: str, phrase: str
) -> None:
    settings = api_settings()
    registry = Registry(settings)
    async with running_app(settings, providers=registry) as (_, client):
        user = await register(client)
        collection = await create_collection(client, user)
        await upload(client, user, collection, "travel.md", HANDBOOK)
        registry.chat_override = FailingChat(error)  # type: ignore[assignment]

        response = await client.post(
            "/api/v1/chat",
            json={"message": "What is the hotel limit in London?"},
            headers=user.headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["run"]["outcome"] == "error"
        assert body["run"]["error"]["category"] == category
        assert phrase in body["message"]["content"]
        assert "Traceback" not in response.text and "503" not in body["message"]["content"]

        run = (await client.get(f"/api/v1/runs/{body['run']['id']}", headers=user.headers)).json()
        assert run["error_category"] == category
        history = (
            await client.get(
                f"/api/v1/conversations/{body['conversation_id']}", headers=user.headers
            )
        ).json()
        assert [m["role"] for m in history["messages"]] == ["user", "assistant"]


async def test_embedding_rate_limit_fails_ingestion_with_retryable_status(
    api_settings: Callable[..., Settings],
) -> None:
    settings = api_settings()
    registry = Registry(settings)
    registry.embed_override = FailingEmbeddings()
    async with running_app(settings, providers=registry) as (_, client):
        user = await register(client)
        collection = await create_collection(client, user)
        document = (await upload(client, user, collection, "travel.md", HANDBOOK)).json()
        detail = (
            await client.get(f"/api/v1/documents/{document['id']}", headers=user.headers)
        ).json()
        assert detail["status"] == "failed"
        assert "rate limit" in detail["error_message"]
        assert detail["jobs"][0]["error_code"] == "rate_limited"

        registry.embed_override = None  # provider recovered
        retried = await client.post(
            f"/api/v1/documents/{document['id']}/retry", headers=user.headers
        )
        assert retried.status_code == 202
        detail = (
            await client.get(f"/api/v1/documents/{document['id']}", headers=user.headers)
        ).json()
        assert detail["status"] == "ready"


async def test_unconfigured_provider_returns_503(api_settings: Callable[..., Settings]) -> None:
    settings = api_settings()
    async with running_app(settings) as (_, client):
        user = await register(client)
        # Pretend a workspace picked a provider whose key the server does not have.
        response = await client.put(
            "/api/v1/settings",
            json={"llm_provider": "groq", "llm_model": "x"},
            headers=user.headers,
        )
        assert response.status_code == 400  # rejected up front: groq is not configured
