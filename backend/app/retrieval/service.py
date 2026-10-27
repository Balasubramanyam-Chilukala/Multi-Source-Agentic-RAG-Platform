"""Hybrid retrieval orchestration and evidence sufficiency.

Modes (identical candidate pools so comparisons are fair):
- ``dense``: vector similarity only
- ``lexical``: PostgreSQL full-text search only
- ``hybrid``: both, fused with RRF
- ``hybrid_rerank``: hybrid candidates re-scored by the configured reranker

Sufficiency is a heuristic gate, **not** a correctness guarantee: it asks whether the best
passage is close enough to the question to be worth answering from. See
docs/retrieval-and-evaluation.md for the rules and their limitations.
"""

import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import term_coverage
from app.db.models.knowledge import Chunk, Document, EmbeddingModel
from app.providers.base import EmbeddingProvider, RerankProvider
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.search import dense_search, lexical_search
from app.retrieval.types import (
    EvidenceChunk,
    RankedId,
    RetrievalConfig,
    RetrievalResult,
    Sufficiency,
)


def assess_sufficiency(
    question: str, chunks: list[EvidenceChunk], config: RetrievalConfig
) -> Sufficiency:
    if not chunks:
        return Sufficiency(False, "no_results")
    dense = max((c.dense_score for c in chunks if c.dense_score is not None), default=None)
    coverage = max(term_coverage(question, f"{c.section or ''} {c.text}") for c in chunks)
    rerank = max((c.rerank_score for c in chunks if c.rerank_score is not None), default=None)
    result = Sufficiency(False, "below_thresholds", dense, round(coverage, 3), rerank)
    if rerank is not None:
        # When a cross-encoder scored the passages, it is the most reliable signal available.
        result.sufficient = rerank >= config.min_rerank_score
        result.reason = "rerank_score" if result.sufficient else "rerank_below_threshold"
        return result
    if dense is not None and dense >= config.min_dense_similarity:
        result.sufficient, result.reason = True, "dense_similarity"
    elif coverage >= config.min_lexical_coverage:
        result.sufficient, result.reason = True, "term_coverage"
    return result


class Retriever:
    def __init__(self, embedder: EmbeddingProvider, reranker: RerankProvider | None) -> None:
        self.embedder = embedder
        self.reranker = reranker

    async def _model(self, session: AsyncSession) -> EmbeddingModel | None:
        return await session.scalar(
            select(EmbeddingModel).where(
                EmbeddingModel.provider == self.embedder.name,
                EmbeddingModel.model == self.embedder.model,
                EmbeddingModel.version == self.embedder.version,
            )
        )

    async def retrieve(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        collection_ids: list[uuid.UUID],
        query: str,
        config: RetrievalConfig,
        question: str | None = None,
        query_vector: list[float] | None = None,
    ) -> RetrievalResult:
        """``query`` is what is searched; ``question`` (default: query) is what sufficiency and
        reranking are judged against, so a refined query cannot hide an off-topic question."""
        question = question or query
        timings: dict[str, float] = {}
        warnings: list[str] = []
        dense: list[RankedId] = []
        lexical: list[RankedId] = []
        model = None

        if config.mode in ("dense", "hybrid", "hybrid_rerank"):
            model = await self._model(session)
            if model is None:
                warnings.append("No documents have been embedded with the configured model yet")
            else:
                started = time.perf_counter()
                if query_vector is not None:  # precomputed in a batch (benchmarks)
                    vector = query_vector
                else:
                    [vector] = await self.embedder.embed([query], input_type="query")
                timings["embed_query"] = _ms(started)
                started = time.perf_counter()
                dense = await dense_search(
                    session,
                    tenant_id=tenant_id,
                    collection_ids=collection_ids,
                    query_vector=vector,
                    embedding_model_id=model.id,
                    dimension=model.dimension,
                    k=config.candidate_k,
                )
                timings["dense"] = _ms(started)
        if config.mode in ("lexical", "hybrid", "hybrid_rerank"):
            started = time.perf_counter()
            lexical = await lexical_search(
                session,
                tenant_id=tenant_id,
                collection_ids=collection_ids,
                question=query,
                k=config.candidate_k,
            )
            timings["lexical"] = _ms(started)

        if config.mode == "dense":
            ranked = dense
        elif config.mode == "lexical":
            ranked = lexical
        else:
            started = time.perf_counter()
            ranked = reciprocal_rank_fusion(
                [dense, lexical], k=config.rrf_k, weights=[1.0, config.lexical_weight]
            )
            timings["fusion"] = _ms(started)

        rerank_pool = (
            ranked[: config.candidate_k]
            if config.mode == "hybrid_rerank"
            else ranked[: config.top_k]
        )
        chunks = await self._hydrate(session, tenant_id, rerank_pool, dense, lexical, config.mode)

        reranker_name = None
        if config.mode == "hybrid_rerank":
            if self.reranker is None:
                warnings.append(
                    "Reranking requested but no reranker is configured; using hybrid order"
                )
            else:
                started = time.perf_counter()
                scores = await self.reranker.rerank(question, [c.text for c in chunks])
                timings["rerank"] = _ms(started)
                for chunk, score in zip(chunks, scores, strict=True):
                    chunk.rerank_score = score
                chunks.sort(key=lambda c: (-(c.rerank_score or 0.0), str(c.chunk_id)))
                reranker_name = f"{self.reranker.name}/{self.reranker.model}"
        chunks = chunks[: config.top_k]
        for position, chunk in enumerate(chunks, start=1):
            chunk.rank = position

        return RetrievalResult(
            query=query,
            config=config,
            chunks=chunks,
            sufficiency=assess_sufficiency(question, chunks, config),
            timings_ms=timings,
            warnings=warnings,
            embedding_model=f"{model.provider}/{model.model}@{model.version}" if model else None,
            reranker=reranker_name,
        )

    async def _hydrate(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        ranked: list[RankedId],
        dense: list[RankedId],
        lexical: list[RankedId],
        mode: str,
    ) -> list[EvidenceChunk]:
        if not ranked:
            return []
        rows = await session.execute(
            select(Chunk, Document.title, Document.source_uri)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.id.in_([r.chunk_id for r in ranked]), Chunk.tenant_id == tenant_id)
        )
        by_id = {row[0].id: row for row in rows}
        dense_pos = {r.chunk_id: (i, r.score) for i, r in enumerate(dense, start=1)}
        lexical_pos = {r.chunk_id: (i, r.score) for i, r in enumerate(lexical, start=1)}
        chunks = []
        for item in ranked:
            row = by_id.get(item.chunk_id)
            if row is None:  # deleted between search and hydration
                continue
            chunk, title, source_uri = row
            evidence = EvidenceChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                collection_id=chunk.collection_id,
                document_title=title,
                source_uri=source_uri,
                text=chunk.text,
                ordinal=chunk.ordinal,
                page_number=chunk.page_number,
                section=chunk.section,
                fused_score=item.score if mode in ("hybrid", "hybrid_rerank") else None,
            )
            if item.chunk_id in dense_pos:
                evidence.dense_rank, evidence.dense_score = dense_pos[item.chunk_id]
            if item.chunk_id in lexical_pos:
                evidence.lexical_rank, evidence.lexical_score = lexical_pos[item.chunk_id]
            chunks.append(evidence)
        return chunks


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
