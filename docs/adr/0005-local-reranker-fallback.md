# ADR 0005 — Local CPU cross-encoder as the working reranker

- Status: Accepted (2026-10-24)

## Context
The spec asks for optional reranking, preferably via NVIDIA. Probing the account showed every
listed NVIDIA rerank endpoint either retired (HTTP 410, end of life 2026-05-18 / 2026-08-25) or
not provisioned (404). No GPU is available, and the deployment target has 0.1 CPU / 512 MB.

## Decision
- Keep the NVIDIA rerank adapter (request/response shape tested with mocks) so it can be enabled
  when an endpoint is provisioned.
- Add `RERANKER_PROVIDER=local`: fastembed's ONNX `Xenova/ms-marco-MiniLM-L-6-v2` cross-encoder,
  installed via the optional `rerank-local` extra, run in a worker thread.
- Default `RERANKER_PROVIDER=none` in deployment.

## Consequences
- Measured on atlas-handbook-v1: MRR 0.871 vs 0.905 for hybrid without reranking, +~1.6 s p50
  latency on a laptop CPU. The small general-purpose reranker does not beat a strong embedding
  model on this domain, so it stays off by default; the comparison remains reproducible.
- The extra pulls Pillow (pinned < 13 by fastembed) with open advisories; documented in
  security.md and excluded from the default image.
