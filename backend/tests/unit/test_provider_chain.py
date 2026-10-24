"""Provider chain (LLM_FALLBACK_CHAIN): ordering, failover rules, free-only OpenRouter, request
shapes for Gemini/OpenRouter, and telemetry. HTTP is mocked; nothing calls a real provider."""

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.providers.base import ChatMessage, ChatResult, ProviderAuthError, ProviderRateLimitError
from app.providers.factory import ProviderRegistry

GROQ_KEY, GEMINI_KEY, OR_KEY, NV_KEY = (
    "gsk_" + "g" * 40,
    "AQ.gem-" + "m" * 30,
    "sk-or-v1-" + "o" * 40,
    "nvapi-" + "n" * 40,
)
CHAIN = [
    "groq:openai/gpt-oss-20b",
    "gemini:gemini-3.6-flash",
    "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia:nvidia/nemotron-3-ultra-550b-a55b",
]


def settings(make_settings: Callable[..., Settings], **overrides: object) -> Settings:
    values: dict[str, object] = {
        "llm_provider": "groq",
        "llm_model": "openai/gpt-oss-120b",
        "groq_api_key": GROQ_KEY,
        "gemini_api_key": GEMINI_KEY,
        "openrouter_api_key": OR_KEY,
        "nvidia_api_key": NV_KEY,
        "llm_fallback_chain": CHAIN,
        "chain_max_retries": 0,
    }
    values.update(overrides)
    return make_settings(**values)


def ok(content: str, model: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        },
    )


class Router:
    """Answers per (host, model) from a script; records every request."""

    def __init__(self, script: dict[str, httpx.Response]) -> None:
        self.script = script
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        model = json.loads(request.content)["model"]
        return self.script.get(model, httpx.Response(500, text="unexpected"))

    def models(self) -> list[str]:
        return [json.loads(r.content)["model"] for r in self.requests]


async def ask(registry: ProviderRegistry) -> ChatResult:
    try:
        return await registry.chat().complete([ChatMessage("user", "hi")])
    finally:
        await registry.aclose()


async def test_chain_tries_members_in_order_until_one_answers(
    make_settings: Callable[..., Settings],
) -> None:
    router = Router(
        {
            "openai/gpt-oss-120b": httpx.Response(429, json={"error": {"message": "tokens"}}),
            "openai/gpt-oss-20b": httpx.Response(503, text="busy"),
            "gemini-3.6-flash": ok("Answer [S1].", "gemini-3.6-flash"),
        }
    )
    result = await ask(
        ProviderRegistry(settings(make_settings), transport=httpx.MockTransport(router))
    )
    assert router.models() == ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "gemini-3.6-flash"]
    assert (result.provider, result.model, result.content) == (
        "gemini",
        "gemini-3.6-flash",
        "Answer [S1].",
    )
    assert [f["error_category"] for f in result.failovers] == [
        "rate_limited",
        "provider_unavailable",
    ]


async def test_empty_answer_and_missing_model_fail_over(
    make_settings: Callable[..., Settings],
) -> None:
    router = Router(
        {
            "openai/gpt-oss-120b": ok("", "openai/gpt-oss-120b"),  # reasoning ate the budget
            "openai/gpt-oss-20b": httpx.Response(
                404, json={"detail": "Function not found for account"}
            ),
            "gemini-3.6-flash": httpx.Response(
                200, json={"choices": [{"message": {"content": None}}]}
            ),
            "nvidia/nemotron-3-ultra-550b-a55b:free": ok(
                "From OpenRouter [S1].", "nvidia/nemotron-3-ultra-550b-a55b:free"
            ),
        }
    )
    result = await ask(
        ProviderRegistry(settings(make_settings), transport=httpx.MockTransport(router))
    )
    assert result.provider == "openrouter"
    assert [f["error_category"] for f in result.failovers] == [
        "malformed_response",
        "model_unavailable",
        "malformed_response",
    ]


async def test_auth_errors_do_not_fail_over(make_settings: Callable[..., Settings]) -> None:
    router = Router(
        {"openai/gpt-oss-120b": httpx.Response(401, json={"error": {"message": "bad key"}})}
    )
    with pytest.raises(ProviderAuthError):
        await ask(ProviderRegistry(settings(make_settings), transport=httpx.MockTransport(router)))
    assert router.models() == ["openai/gpt-oss-120b"]


async def test_exhausted_chain_raises_the_last_error(
    make_settings: Callable[..., Settings],
) -> None:
    limited = httpx.Response(429, json={"error": {"message": "slow down"}})
    router = Router(
        {
            m: limited
            for m in [
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "gemini-3.6-flash",
                "nvidia/nemotron-3-ultra-550b-a55b:free",
                "nvidia/nemotron-3-ultra-550b-a55b",
            ]
        }
    )
    with pytest.raises(ProviderRateLimitError):
        await ask(ProviderRegistry(settings(make_settings), transport=httpx.MockTransport(router)))
    assert len(router.requests) == 5  # each member tried once (chain_max_retries=0)


async def test_request_shapes_per_provider(make_settings: Callable[..., Settings]) -> None:
    router = Router(
        {
            "openai/gpt-oss-120b": httpx.Response(503, text="x"),
            "openai/gpt-oss-20b": httpx.Response(503, text="x"),
            "gemini-3.6-flash": httpx.Response(503, text="x"),
            "nvidia/nemotron-3-ultra-550b-a55b:free": httpx.Response(503, text="x"),
            "nvidia/nemotron-3-ultra-550b-a55b": ok("ok", "nvidia/nemotron-3-ultra-550b-a55b"),
        }
    )
    await ask(ProviderRegistry(settings(make_settings), transport=httpx.MockTransport(router)))
    by_model = {json.loads(r.content)["model"]: r for r in router.requests}
    gemini = by_model["gemini-3.6-flash"]
    assert (
        str(gemini.url)
        == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    )
    assert gemini.headers["authorization"] == f"Bearer {GEMINI_KEY}"
    assert json.loads(gemini.content)["reasoning_effort"] == "low"
    openrouter = by_model["nvidia/nemotron-3-ultra-550b-a55b:free"]
    assert str(openrouter.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert openrouter.headers["authorization"] == f"Bearer {OR_KEY}"
    assert "reasoning_effort" not in json.loads(openrouter.content)
    assert "reasoning_effort" not in json.loads(
        by_model["nvidia/nemotron-3-ultra-550b-a55b"].content
    )
    assert json.loads(by_model["openai/gpt-oss-120b"].content)["reasoning_effort"] == "low"


async def test_stream_fails_over_before_first_token_only(
    make_settings: Callable[..., Settings],
) -> None:
    def sse(text: str) -> httpx.Response:
        body = (
            f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}\n\ndata: [DONE]\n\n"
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body.encode()
        )

    router = Router(
        {
            "openai/gpt-oss-120b": httpx.Response(429, json={"error": {"message": "x"}}),
            "openai/gpt-oss-20b": sse("Streamed [S1]."),
        }
    )
    registry = ProviderRegistry(settings(make_settings), transport=httpx.MockTransport(router))
    chunks = [c async for c in registry.chat().stream([ChatMessage("user", "hi")])]
    await registry.aclose()
    final = chunks[-1].result
    assert final is not None and final.provider == "groq" and final.model == "openai/gpt-oss-20b"
    assert [f["model"] for f in final.failovers] == ["openai/gpt-oss-120b"]


def test_paid_openrouter_models_are_rejected_at_startup(
    make_settings: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError, match="not a free model"):
        settings(make_settings, llm_fallback_chain=["openrouter:anthropic/some-paid-model"])
    with pytest.raises(ValidationError, match="not a free model"):
        settings(make_settings, llm_provider="openrouter", llm_model="openai/gpt-paid")


def test_chain_requires_keys_and_valid_entries(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="GEMINI_API_KEY is required"):
        settings(make_settings, gemini_api_key=None)
    with pytest.raises(ValidationError, match="provider:model"):
        settings(make_settings, llm_fallback_chain=["just-a-model"])
    with pytest.raises(ValidationError, match="unknown provider"):
        settings(make_settings, llm_fallback_chain=["anthropic:claude"])


def test_google_api_key_is_accepted_as_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", GEMINI_KEY)
    loaded = Settings(
        _env_file=None, environment="test", llm_provider="mock", embedding_provider="mock"
    )
    assert (
        loaded.gemini_api_key is not None and loaded.gemini_api_key.get_secret_value() == GEMINI_KEY
    )
    assert GEMINI_KEY not in repr(loaded)


def test_no_chain_means_a_single_provider(make_settings: Callable[..., Settings]) -> None:
    registry = ProviderRegistry(settings(make_settings, llm_fallback_chain=[]))
    assert type(registry.chat()).__name__ == "OpenAICompatibleChat"
