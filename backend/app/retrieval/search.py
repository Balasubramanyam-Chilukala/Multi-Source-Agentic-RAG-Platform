"""Dense and lexical candidate retrieval. Both are always constrained to the caller's tenant
and the requested collections, inside the same SQL statement as the ranking."""

import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.types import RankedId

_COLLECTIONS = bindparam("collection_ids", type_=ARRAY(UUID(as_uuid=True)))


async def dense_search(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    collection_ids: list[uuid.UUID],
    query_vector: list[float],
    embedding_model_id: uuid.UUID,
    dimension: int,
    k: int,
) -> list[RankedId]:
    """Cosine-similarity search over chunks embedded with ``embedding_model_id``.

    The cast ``embedding::vector(N)`` and the literal model predicate match the per-model
    partial HNSW index. Both inlined values come from the registry (UUID and int), never from
    user input. ``hnsw.iterative_scan`` keeps filtered ANN searches from returning too few rows.
    """
    dim = int(dimension)
    vector_literal = "[" + ",".join(f"{float(x):.7g}" for x in query_vector) + "]"
    await session.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))
    statement = text(
        f"""
        SELECT id, 1 - (embedding::vector({dim}) <=> CAST(:query AS vector({dim}))) AS similarity
        FROM chunks
        WHERE embedding_model_id = '{embedding_model_id}'
          AND tenant_id = :tenant_id
          AND collection_id = ANY(:collection_ids)
        ORDER BY embedding::vector({dim}) <=> CAST(:query AS vector({dim}))
        LIMIT :k
        """  # noqa: S608 - only registry-sourced UUID/int are interpolated
    ).bindparams(_COLLECTIONS)
    rows = await session.execute(
        statement,
        {"query": vector_literal, "tenant_id": tenant_id, "collection_ids": collection_ids, "k": k},
    )
    return [RankedId(row.id, float(row.similarity)) for row in rows]


# Build an OR-query from the question's own lexemes: to_tsvector applies the same stemming and
# stopword removal as the indexed column, and OR semantics suit natural-language questions
# (websearch_to_tsquery would require *every* term to match). Ranking: ts_rank_cd with
# normalization 1 (divides by 1 + log(document length)). This is PostgreSQL full-text search,
# not BM25.
_LEXICAL_SQL = text(
    """
    WITH q AS (
        SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query
        FROM unnest(to_tsvector('english', :question))
    )
    SELECT c.id, ts_rank_cd(c.tsv, q.query, 1) AS rank
    FROM chunks c, q
    WHERE q.query IS NOT NULL
      AND c.tenant_id = :tenant_id
      AND c.collection_id = ANY(:collection_ids)
      AND c.tsv @@ q.query
    ORDER BY rank DESC, c.id
    LIMIT :k
    """
).bindparams(_COLLECTIONS)


async def lexical_search(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    collection_ids: list[uuid.UUID],
    question: str,
    k: int,
) -> list[RankedId]:
    rows = await session.execute(
        _LEXICAL_SQL,
        {"question": question, "tenant_id": tenant_id, "collection_ids": collection_ids, "k": k},
    )
    return [RankedId(row.id, float(row.rank)) for row in rows]
