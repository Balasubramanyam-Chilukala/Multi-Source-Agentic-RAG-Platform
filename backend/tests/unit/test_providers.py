"""Provider adapters against mocked HTTP: request shapes, parsing, retries, error mapping."""

import json
from collections.abc import Callable

import httpx
import pytest

from app.providers.base import (
    ChatMessage,
    ProviderAuthError,
    ProviderModelNotFoundError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.providers.http import ProviderHTTPClient, parse_duration, retry_after_seconds
from app.providers.mock import HashingEmbeddings, MockChat
from app.providers.nvidia import NvidiaEmbeddings, NvidiaReranker
from app.providers.openai_compat import OpenAICompatibleChat

Handler = Callable[[httpx.Request], httpx.Response]
KEY = "gsk_" + "k" * 40


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client(
    handler: Handler, *, retries: int = 3, max_backoff: float = 5.0
) -> tuple[ProviderHTTPClient, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    http = ProviderHTTPClient(
        "groq",
        api_key=KEY,
        timeout=5,
        max_retries=retries,
        max_backoff=max_backoff,
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
    )
    return http, sleeps


def chat_response(content: str = "hello") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "openai/gpt-oss-120b",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        },
    )


def groq_chat(http: ProviderHTTPClient) -> OpenAICompatibleChat:
    return OpenAICompatibleChat(
        name="groq",
        model="openai/gpt-oss-120b",
        base_url="https://api.groq.com/openai/v1",
        http=http,
        temperature=0.1,
        max_tokens=256,
        reasoning_effort="low",
    )


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("2", 2.0), ("1m26.4s", 86.4), ("250ms", 0.25), ("2.272s", 2.272), ("", None), ("soon", None)],
)
def test_parse_duration(value: str, seconds: float | None) -> None:
    assert parse_duration(value) == (pytest.approx(seconds) if seconds is not None else None)


def test_retry_after_uses_exhausted_groq_window() -> None:
    response = httpx.Response(
        429,
        headers={
            "x-ratelimit-remaining-requests": "10",
            "x-ratelimit-reset-requests": "50s",
            "x-ratelimit-remaining-tokens": "0",
            "x-ratelimit-reset-tokens": "3.5s",
        },
    )
    assert retry_after_seconds(response) == pytest.approx(3.5)


async def test_chat_request_shape_and_parsing() -> None:
    recorder = Recorder(chat_response("Paris"))
    http, _ = client(recorder)
    result = await groq_chat(http).complete(
        [ChatMessage("user", "Capital of France?")], json_mode=True
    )

    request = recorder.requests[0]
    body = json.loads(request.content)
    assert request.url == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert body["response_format"] == {"type": "json_object"}
    assert body["reasoning_effort"] == "low"
    assert body["max_tokens"] == 256
    assert (
        result.content,
        result.provider,
        result.usage.prompt_tokens,
        result.usage.completion_tokens,
    ) == ("Paris", "groq", 12, 3)


async def test_429_is_retried_after_reported_reset() -> None:
    recorder = Recorder(
        httpx.Response(429, headers={"retry-after": "2"}, json={"error": {"message": "slow down"}}),
        chat_response("ok"),
    )
    http, sleeps = client(recorder)
    result = await groq_chat(http).complete([ChatMessage("user", "hi")])
    assert result.content == "ok"
    assert len(recorder.requests) == 2
    assert sleeps[0] >= 2.0


async def test_429_with_long_reset_fails_fast_with_retry_after() -> None:
    recorder = Recorder(
        httpx.Response(429, headers={"retry-after": "120"}, json={"error": {"message": "limit"}})
    )
    http, sleeps = client(recorder, max_backoff=20)
    with pytest.raises(ProviderRateLimitError) as exc_info:
        await groq_chat(http).complete([ChatMessage("user", "hi")])
    assert exc_info.value.retry_after == 120
    assert sleeps == []
    assert len(recorder.requests) == 1


async def test_5xx_retries_are_bounded_with_growing_backoff() -> None:
    recorder = Recorder(*[httpx.Response(503, text="busy") for _ in range(4)])
    http, sleeps = client(recorder, retries=3)
    with pytest.raises(ProviderUnavailableError):
        await groq_chat(http).complete([ChatMessage("user", "hi")])
    assert len(recorder.requests) == 4  # 1 attempt + 3 retries
    assert len(sleeps) == 3
    assert sleeps[2] > sleeps[0]


async def test_timeouts_are_retried_then_reported() -> None:
    recorder = Recorder(httpx.ReadTimeout("t"), httpx.ReadTimeout("t"))
    http, _ = client(recorder, retries=1)
    with pytest.raises(ProviderTimeoutError):
        await groq_chat(http).complete([ChatMessage("user", "hi")])
    assert len(recorder.requests) == 2


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(401, json={"error": {"message": "bad key"}}), ProviderAuthError),
        (
            httpx.Response(404, json={"detail": "Function not found for account"}),
            ProviderModelNotFoundError,
        ),
        (httpx.Response(410, json={"detail": "end of life"}), ProviderModelNotFoundError),
        (
            httpx.Response(400, json={"error": {"message": "The model `x` does not exist"}}),
            ProviderModelNotFoundError,
        ),
        (httpx.Response(402, json={"detail": "credits"}), ProviderQuotaExceededError),
        (httpx.Response(200, text="<html>not json</html>"), ProviderResponseError),
        (httpx.Response(200, json={"choices": []}), ProviderResponseError),
    ],
)
async def test_non_retryable_errors_are_mapped(
    response: httpx.Response, error: type[Exception]
) -> None:
    recorder = Recorder(response)
    http, sleeps = client(recorder)
    with pytest.raises(error):
        await groq_chat(http).complete([ChatMessage("user", "hi")])
    assert len(recorder.requests) == 1
    assert sleeps == []


async def test_error_messages_never_contain_the_api_key() -> None:
    http, _ = client(Recorder(httpx.Response(401, json={"error": {"message": "invalid"}})))
    with pytest.raises(ProviderAuthError) as exc_info:
        await groq_chat(http).complete([ChatMessage("user", "hi")])
    assert KEY not in str(exc_info.value)


def sse(*events: dict[str, object]) -> httpx.Response:
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


async def test_streaming_skips_reasoning_and_reports_usage() -> None:
    recorder = Recorder(
        sse(
            {"model": "m", "choices": [{"delta": {"reasoning": "thinking..."}}]},
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
        )
    )
    http, _ = client(recorder)
    chunks = [c async for c in groq_chat(http).stream([ChatMessage("user", "hi")])]
    assert [c.delta for c in chunks if c.delta] == ["Hel", "lo"]
    final = chunks[-1].result
    assert final is not None and final.content == "Hello" and final.usage.completion_tokens == 2


async def test_stream_is_retried_only_before_it_starts() -> None:
    recorder = Recorder(
        httpx.Response(503, text="busy"), sse({"choices": [{"delta": {"content": "ok"}}]})
    )
    http, _ = client(recorder)
    chunks = [c async for c in groq_chat(http).stream([ChatMessage("user", "hi")])]
    assert chunks[-1].result is not None and chunks[-1].result.content == "ok"


async def test_nvidia_embeddings_batches_and_validates_dimension() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["input_type"] == "passage" and body["dimensions"] == 4
        data = [{"index": i, "embedding": [0.1, 0.2, 0.3, 0.4]} for i in range(len(body["input"]))]
        return httpx.Response(200, json={"data": list(reversed(data))})

    http, _ = client(handler)
    embedder = NvidiaEmbeddings(
        model="nvidia/nemotron-3.5-embed-1b",
        dimension=4,
        base_url="https://x/v1",
        http=http,
        batch_size=2,
    )
    vectors = await embedder.embed(["a", "b", "c"], input_type="passage")
    assert len(vectors) == 3 and all(len(v) == 4 for v in vectors)


async def test_nvidia_embeddings_reject_wrong_dimension() -> None:
    http, _ = client(
        lambda r: httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})
    )
    embedder = NvidiaEmbeddings(model="m", dimension=4, base_url="https://x/v1", http=http)
    with pytest.raises(ProviderResponseError, match="dimension"):
        await embedder.embed(["a"], input_type="query")


async def test_nvidia_reranker_maps_rankings_to_input_order() -> None:
    recorder = Recorder(
        httpx.Response(
            200, json={"rankings": [{"index": 1, "logit": 4.5}, {"index": 0, "logit": -2.0}]}
        )
    )
    http, _ = client(recorder)
    reranker = NvidiaReranker(
        model="nvidia/llama-3.2-nv-rerankqa-1b-v2",
        url_template="https://ai.api.nvidia.com/v1/retrieval/{model_path}/reranking",
        http=http,
    )
    assert await reranker.rerank("q", ["p0", "p1"]) == [-2.0, 4.5]
    assert (
        recorder.requests[0].url.path
        == "/v1/retrieval/nvidia/llama-3_2-nv-rerankqa-1b-v2/reranking"
    )


async def test_hashing_embeddings_are_deterministic_and_normalized() -> None:
    embedder = HashingEmbeddings(dimension=64)
    a1, a2, related, unrelated = await embedder.embed(
        ["vacation days", "vacation days", "vacation day policy", "server outage"],
        input_type="query",
    )

    def dot(x: list[float], y: list[float]) -> float:
        return sum(i * j for i, j in zip(x, y, strict=True))

    assert a1 == a2
    assert dot(a1, a1) == pytest.approx(1.0)
    assert dot(a1, related) > dot(a1, unrelated)


async def test_mock_chat_abstains_without_relevant_sources() -> None:
    chat = MockChat()
    system = ChatMessage("system", "TASK: generate")
    user = ChatMessage(
        "user",
        "Question: What is the parental leave policy?\n"
        '<source id="S1" title="t">The cafeteria opens at 8.</source>',
    )
    assert (await chat.complete([system, user])).content == "INSUFFICIENT_EVIDENCE"
