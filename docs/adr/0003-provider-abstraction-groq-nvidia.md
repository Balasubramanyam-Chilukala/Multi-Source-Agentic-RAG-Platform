# ADR 0003 — Provider abstraction for Groq and NVIDIA

- Status: Accepted (2026-10-24); adapters implemented in Phases 2–3

## Context
Only free-tier Groq and NVIDIA API access is available. Model availability varies by account,
and free tiers have rate limits and quotas.

## Decision
- Define three small interfaces: `ChatProvider`, `EmbeddingProvider`, `RerankProvider`.
- Implement adapters with `httpx` against the OpenAI-compatible endpoints (Groq
  `https://api.groq.com/openai/v1`, NVIDIA `https://integrate.api.nvidia.com/v1`), plus a `mock`
  adapter for tests and labelled demo mode.
- Selection is by configuration (`LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `RERANKER_PROVIDER`).
  Startup validation requires a key for every selected non-mock provider.
- Adapters own bounded retries (exponential backoff + jitter, honouring `retry-after`), timeouts,
  and the mapping of 429/5xx/timeout/malformed responses to typed errors.
- Fallback to another provider happens only if it is explicitly configured. There is never a silent
  switch to a paid service.

## Consequences
- Tests mock HTTP at the transport level. CI never calls a real provider.
- Defaults are chosen from the account's actual model catalog (see architecture.md §7), and each
  endpoint is verified with a real call before it becomes a default.
