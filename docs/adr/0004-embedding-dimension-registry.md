# ADR 0004 — Embedding model registry and dimension safety

- Status: Accepted (2026-10-24); schema built in Phase 1

## Context
Different embedding models produce different vector dimensions. Mixing them in one index corrupts
retrieval, and switching models must not require dropping data.

## Decision
- `embedding_models` registers `(provider, model, version, dimension)`.
- `chunks.embedding` is a dimensionless `vector` column with `embedding_model_id` and
  `embedding_dim`. A CHECK constraint requires `vector_dims(embedding) = embedding_dim`, and that
  embedding, model, and dimension are all set or all null.
- ANN indexes are created per model as partial expression indexes, e.g.
  `CREATE INDEX ix_chunks_embedding_hnsw_<model> ON chunks USING hnsw ((embedding::vector(1024)) vector_cosine_ops) WHERE embedding_model_id = '<id>'`,
  and are excluded from Alembic autogenerate.
- `collections.active_embedding_model_id` picks which vectors a collection's queries use.

## Re-embedding procedure
1. Register the new model row.
2. Backfill: write new chunk rows (or a parallel embedding table, if needed later) for the new model.
3. Build that model's index.
4. Switch the collection's active model.
5. Delete the old vectors.

Queries always filter on the active model ID, so a mismatched dimension can never reach the
distance operator.

## Consequences
- The database itself rejects a wrong-dimension write (verified by
  `tests/integration/test_schema.py`).
- pgvector's HNSW limit for `vector` is 2,000 dimensions. Larger models need `halfvec` casting
  or a reduced output dimension; registration rejects dimensions above 2,000.
- Verified against the live API: `nvidia/nemotron-3.5-embed-1b` (2,048 dims natively) accepts
  `dimensions: 1024`, so the default configuration requests 1,024 dimensions and indexes them
  with HNSW. The requested dimension is part of the model version (`d1024`).
