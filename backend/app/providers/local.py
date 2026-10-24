"""Local CPU cross-encoder reranker (fastembed + ONNX Runtime; no GPU, no PyTorch).

Install with ``uv sync --extra rerank-local``. The model (~80 MB for MiniLM-L6) is downloaded
on first use and cached; first-call latency includes loading it. Inference runs in a worker
thread so it does not block the event loop.
"""

import asyncio
from typing import Any


class LocalCrossEncoderReranker:
    name = "local"

    def __init__(self, *, model: str) -> None:
        self.model = model
        self._encoder: Any = None
        self._lock = asyncio.Lock()

    async def _load(self) -> Any:
        async with self._lock:
            if self._encoder is None:
                try:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder  # noqa: PLC0415
                except ImportError as exc:  # pragma: no cover - depends on optional extra
                    raise RuntimeError(
                        "RERANKER_PROVIDER=local requires `uv sync --extra rerank-local`"
                    ) from exc
                self._encoder = await asyncio.to_thread(TextCrossEncoder, self.model)
            return self._encoder

    async def rerank(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        encoder = await self._load()
        scores = await asyncio.to_thread(lambda: list(encoder.rerank(query, passages)))
        return [float(s) for s in scores]
