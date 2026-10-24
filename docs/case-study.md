# Case study: building a grounded, multi-tenant RAG agent on free tiers

## The problem

Teams want to ask questions across scattered documents (policies, runbooks, product data)
and get answers they can trust. A plain "LLM plus vector search" chatbot fails that in four
ways:
1. It invents facts and citations.
2. It answers confidently when the documents don't cover the question.
3. It obeys instructions planted inside documents.
4. It leaks data across customers unless tenancy is designed in from the start.

The constraint was to do this on free API tiers (Groq, NVIDIA API Catalog) and free hosting.

## What was built

Atlas AI has the following parts:
- **Ingestion:** nine source types, including SSRF-safe URL fetching.
- **Retrieval:** hybrid search in a single PostgreSQL database (pgvector HNSW plus full-text
  search, fused with Reciprocal Rank Fusion).
- **Agent:** an explicit LangGraph state machine that decides how to handle each question,
  validates its own citations, and abstains when evidence is missing.
- **Product surface:** a React workspace with streaming chat and evidence inspection, plus an
  evaluation framework and a run inspector that make the system's behaviour measurable.

## Key decisions and trade-offs

1. **One datastore.** PostgreSQL holds relational data, vectors, lexical search, and agent
   checkpoints. Deleting a document is a single transactional cascade, and the tenant filter
   lives in the same SQL statement that ranks results, so isolation does not depend on
   post-filtering. The cost: lexical ranking is `ts_rank_cd`, not BM25, and that is stated
   wherever it matters.
2. **Probe before choosing.** Before writing adapters I called the providers' real endpoints.
   Three things in the "catalog" were not usable:
   - The planned embedding model returned 404 for this account.
   - Every listed NVIDIA reranker was retired (410) or not provisioned (404).
   - Groq allows 8,000 tokens per minute, and gpt-oss spends completion tokens on hidden
     reasoning.

   The defaults came from what actually answered: `nemotron-3.5-embed-1b` at 1,024 dimensions
   (verified Matryoshka truncation, so it stays HNSW-indexable), gpt-oss-120b with
   `reasoning_effort=low`, and a local CPU cross-encoder as the reranker fallback.
3. **Rules before LLM calls.** Under an 8k TPM budget, a routing call for "hello" or "2+2" is
   waste. Deterministic rules handle unambiguous messages. An LLM router (Pydantic-validated
   JSON) runs only for follow-ups, where it resolves pronouns against checkpointed history.
   Budgets for retrieval attempts and LLM calls are hard invariants, enforced inside the nodes.
4. **Abstention is a two-stage decision.** Measured top-similarity distributions for answerable
   and unanswerable questions overlap (answerable p10 0.287, unanswerable max 0.409), so no
   threshold separates them. The retrieval gate is therefore a lenient pre-filter. The real
   decision is the model's `INSUFFICIENT_EVIDENCE` sentinel plus citation validation (ADR 0006).
5. **Citations are validated, not trusted.** Every marker must map to a passage retrieved for
   this answer. Links that aren't a source's URL are stripped, and each sentence is labelled
   supported or general. Messages store citation references, not passage text, so deleting a
   document removes its text everywhere.
6. **Prompt injection is handled in layers.** The layers are delimited sources with
   neutralized tags, a router that never sees passages, output validation, a suspicious-passage
   flag, and benchmark cases with a poisoned document.

## Experiments

The dataset is version-controlled (60 cases, 9 categories, 11 documents, phrase-based relevance
labels verified against chunks before every run). All four retrieval configurations used the
same cases, labels, and k = 5.

| Mode | Recall@5 | MRR | nDCG@5 | p50 latency |
|---|---|---|---|---|
| dense | 1.000 | 0.949 | 0.961 | 537 ms |
| lexical | 0.918 | 0.777 | 0.801 | 25 ms |
| hybrid (RRF) | 1.000 | 0.905 | 0.923 | 599 ms |
| hybrid + local rerank | 1.000 | 0.871 | 0.902 | 2,068 ms |

End-to-end with Groq gpt-oss-120b on hybrid retrieval, using deterministic reference checks
(no LLM judge):

| Metric | Result |
|---|---|
| Correctness | 0.967 |
| Citation precision | 0.990 |
| Abstention accuracy | 0.967 (all unanswerable questions declined) |
| Injected content in answers | 0.000 |
| End-to-end latency | p50 1.46 s, p95 4.0 s |

## What the measurements changed

- **Lexical retrieval alone fails on paraphrases**, which the embedding model fixes; that is
  the expected result.
- **On this small corpus dense retrieval ranks slightly better than hybrid.** Hybrid stays the
  default as a robustness choice for exact identifiers. That is a judgement, not a measured
  benefit here, and the docs say so.
- **The small reranker hurt** (MRR −0.034) and cost about 1.5 s on CPU, so it ships off by
  default rather than as a box ticked.
- **Error analysis found two real bugs and one metric bug:**
  - The router treated existential "there" as a back-reference and asked a pointless
    clarification question.
  - A citation-format mismatch: gpt-oss writes `【S1】`, which made the validator reject correct
    answers. Mocks could not have revealed this; a live smoke test did.
  - The metric bug: a non-breaking hyphen in "post‑mortem" scored a correct answer as wrong.
  The fixes raised correctness from 0.950 to 0.967. The remaining misses are documented: one
  ambiguity weakness, and one case of run-to-run LLM variance.

## Verification

- **Tests:** 234 backend tests (unit, plus integration tests against real PostgreSQL) and 49
  frontend tests, plus a Playwright journey in a real browser.
- **Static checks:** strict mypy, Ruff, and ESLint all clean.
- **Live runs:** with real Groq and NVIDIA calls through the Docker container, including SSE
  streaming.
- **Independent review:** a separate review pass found 15 issues, all fixed with regression
  tests. The most serious were:
  - a JSON key-amplification memory bomb;
  - request transactions committing after the response had been sent;
  - phishing links in protocol-relative or upper-case form surviving citation validation;
  - the LLM budget being exceeded on follow-up turns.

## Limitations

- The corpus is small, so recall saturates.
- Thresholds were calibrated on the evaluation set (no held-out split).
- Each configuration was run once.
- Answer metrics are string-based checks, not entailment.
- Authentication lacks password reset, MFA, and invitations.
- Rate limits and locks are in-process (single instance).
- There is no OCR.
- The public deployment is prepared but not yet published (it needs the owner's accounts).
