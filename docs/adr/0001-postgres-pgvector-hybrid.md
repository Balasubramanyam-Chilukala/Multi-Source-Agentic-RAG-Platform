# ADR 0001 — PostgreSQL + pgvector for relational, vector, and lexical data

- Status: Accepted (2026-10-24). The default retrieval mode was later changed to dense by
  [ADR 0007](0007-dense-default-retrieval.md); the storage and search design below is unchanged.

## Context
Atlas AI needs relational records (tenants, documents, conversations), dense vectors,
lexical search, and agent checkpoints. Deleting a document must remove all of its derived data.

## Decision
Use one PostgreSQL 17 database with the pgvector extension:
- Dense retrieval: `pgvector` cosine distance with HNSW indexes (per embedding model, see ADR 0004).
- Lexical retrieval: a generated `tsvector` column with a GIN index, ranked with `ts_rank_cd`.
- Fusion: Reciprocal Rank Fusion in application code, so each retriever stays testable on its own.

## Consequences
- Deletes are transactional and cascade through foreign keys. Vectors cannot be orphaned in a
  separate store.
- Tenant/collection filters are plain `WHERE` clauses, applied in the same query as the ANN search.
- The lexical ranker is PostgreSQL full-text search (`ts_rank_cd`), **not BM25**, and is described
  that way in docs and the UI. A true BM25 extension can be evaluated later if measurements justify it.
- Scale ceiling: fine for portfolio/demo-scale corpora. A dedicated vector store becomes worth it only
  at sizes this project is not targeting.
