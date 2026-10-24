"""NVIDIA API Catalog adapters: embeddings (OpenAI-compatible) and reranking (NeMo Retriever).

Model availability is per account. On the account this project was built with,
``nvidia/nemotron-3.5-embed-1b`` answered with Matryoshka output dimensions, while the listed
rerank endpoints were retired (HTTP 410) or not provisioned (404); see
docs/architecture.md §7. The rerank adapter is therefore tested against mocked responses only.
"""

from typing import Any

from app.providers.base import InputType, ProviderResponseError
from app.providers.http import ProviderHTTPClient


class NvidiaEmbeddings:
    name = "nvidia"

    def __init__(
        self,
        *,
        model: str,
        dimension: int,
        base_url: str,
        http: ProviderHTTPClient,
        batch_size: int = 32,
    ) -> None:
        self.model = model
        self.dimension = dimension
        # Changing the requested dimension changes the vector space, so it is part of the version.
        self.version = f"d{dimension}"
        self._url = base_url.rstrip("/") + "/embeddings"
        self._http = http
        self._batch_size = batch_size

    async def embed(self, texts: list[str], *, input_type: InputType) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            body = await self._http.post_json(
                self._url,
                {
                    "model": self.model,
                    "input": batch,
                    "input_type": input_type,
                    "encoding_format": "float",
                    "truncate": "END",
                    "dimensions": self.dimension,
                },
            )
            vectors.extend(self._parse(body, expected=len(batch)))
        return vectors

    def _parse(self, body: dict[str, Any], *, expected: int) -> list[list[float]]:
        data = body.get("data")
        if not isinstance(data, list) or len(data) != expected:
            raise ProviderResponseError(
                "embedding response has wrong item count", provider=self.name
            )
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        vectors = []
        for item in ordered:
            vector = item.get("embedding")
            if not isinstance(vector, list) or len(vector) != self.dimension:
                raise ProviderResponseError(
                    "embedding has dimension "
                    f"{len(vector) if isinstance(vector, list) else 'n/a'}, "
                    f"expected {self.dimension}",
                    provider=self.name,
                )
            vectors.append([float(x) for x in vector])
        return vectors


class NvidiaReranker:
    name = "nvidia"

    def __init__(self, *, model: str, url_template: str, http: ProviderHTTPClient) -> None:
        self.model = model
        # Hosted rerankers use a per-model path, e.g. nvidia/llama-3_2-nv-rerankqa-1b-v2.
        self._url = url_template.format(model_path=model.replace(".", "_"))
        self._http = http

    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        body = await self._http.post_json(
            self._url,
            {
                "model": self.model,
                "query": {"text": query},
                "passages": [{"text": p} for p in passages],
                "truncate": "END",
            },
        )
        rankings = body.get("rankings")
        if not isinstance(rankings, list):
            raise ProviderResponseError("rerank response has no rankings", provider=self.name)
        scores: list[float | None] = [None] * len(passages)
        for item in rankings:
            index, logit = item.get("index"), item.get("logit")
            if not isinstance(index, int) or not 0 <= index < len(passages):
                raise ProviderResponseError("rerank index out of range", provider=self.name)
            if not isinstance(logit, int | float):
                raise ProviderResponseError("rerank score missing", provider=self.name)
            scores[index] = float(logit)
        if any(score is None for score in scores):
            raise ProviderResponseError("rerank response omitted passages", provider=self.name)
        return [score for score in scores if score is not None]
