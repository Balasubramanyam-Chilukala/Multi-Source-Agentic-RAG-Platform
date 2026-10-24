"""Embedding model registration and per-model ANN indexes (see ADR 0004)."""

import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import MAX_INDEXED_DIMENSION
from app.db.models.knowledge import EmbeddingModel
from app.providers.base import EmbeddingProvider

INDEX_PREFIX = "ix_chunks_embedding_hnsw_"


def index_name(model_id: uuid.UUID) -> str:
    return f"{INDEX_PREFIX}{model_id.hex[:16]}"


async def ensure_embedding_model(
    session: AsyncSession, provider: EmbeddingProvider
) -> EmbeddingModel:
    """Get-or-create the registry row for ``provider`` and make sure its HNSW index exists."""
    if not 0 < provider.dimension <= MAX_INDEXED_DIMENSION:
        raise ValueError(
            f"Embedding dimension {provider.dimension} cannot be indexed "
            f"(max {MAX_INDEXED_DIMENSION})"
        )
    await session.execute(
        insert(EmbeddingModel)
        .values(
            id=uuid.uuid4(),
            provider=provider.name,
            model=provider.model,
            version=provider.version,
            dimension=provider.dimension,
        )
        .on_conflict_do_nothing(index_elements=["provider", "model", "version"])
    )
    model = await session.scalar(
        select(EmbeddingModel).where(
            EmbeddingModel.provider == provider.name,
            EmbeddingModel.model == provider.model,
            EmbeddingModel.version == provider.version,
        )
    )
    if model is None:  # pragma: no cover - the upsert above guarantees a row
        raise RuntimeError("embedding model registration failed")
    if model.dimension != provider.dimension:
        raise ValueError("Registered embedding model has a different dimension; bump its version")
    # Identifiers below are a UUID and a validated int, so inlining them is safe; the partial
    # predicate must be a literal for the planner to match it with query predicates.
    await session.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {index_name(model.id)} ON chunks "
            f"USING hnsw ((embedding::vector({int(model.dimension)})) vector_cosine_ops) "
            f"WHERE embedding_model_id = '{model.id}'"
        )
    )
    return model
