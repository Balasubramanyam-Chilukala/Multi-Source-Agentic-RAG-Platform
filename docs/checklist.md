# Implementation checklist

`[x]` done and verified · `[~]` partial (see note) · `[ ]` not done

## Phase 0–1 — Planning and backend foundation
- [x] Architecture, lifecycles, schema, ADRs 0001–0006, free-tier assessment (`docs/`)
- [x] Settings validated at startup; secrets as `SecretStr`; blank keys treated as unset
- [x] FastAPI factory, lifespan-managed engine, providers, pipeline, agent
- [x] `/health/live`, `/health/ready` (DB, pgvector, migration head, checkpointer; no LLM calls)
- [x] JSON logs with request IDs and central secret redaction; security headers
- [x] Error envelope; no stack traces or input echo in responses
- [x] Alembic migrations 0001–0003; `alembic check` in tests and CI

## Phase 2 — Ingestion and retrieval
- [x] PDF, DOCX, TXT, Markdown, HTML, CSV, JSON, pasted text, URL
- [x] SSRF-resistant URL fetch (IP pinning, redirect re-validation, private-range rejection)
- [x] Size/type validation, magic-byte checks, zip-bomb and page caps, malformed-file errors
- [x] Content-hash duplicate detection (409 with existing id; race-safe)
- [x] Section-aware chunking with overlap; title/page/section metadata
- [x] NVIDIA embeddings (verified model and 1,024-dim output), mock embeddings
- [x] Dense (HNSW per model), lexical (PostgreSQL FTS, not BM25), RRF hybrid, rerank
- [x] Embedding model registry, dimension CHECK, re-embed endpoint
- [x] Ingestion status, retries (idempotent), restart recovery, deletion cascade
- [~] OCR — not implemented; scanned PDFs are flagged `unsupported`

## Phase 3 — Agentic RAG
- [x] LangGraph `StateGraph` with typed state, conditional routing, bounded retries and LLM budget
- [x] Rule + LLM routing, query refinement, evidence sufficiency, abstention
- [x] Citation validation (normalization, unknown markers, foreign URLs, claim labels)
- [x] Tools: calculator (AST), document metadata, summarize document, search
- [x] Groq + NVIDIA chat adapters (OpenAI-compatible), retries/backoff/rate-limit headers, fallback (opt-in)
- [x] PostgreSQL checkpointer (short-term memory) + conversation tables (long-term)
- [x] SSE streaming with disconnect-safe persistence
- [x] Verified live with real Groq + NVIDIA calls

## Phase 4 — Frontend
- [x] React 19 + TS strict + Vite + Tailwind + TanStack Query; typed API client and SSE parser
- [x] Dashboard, knowledge sources (upload/paste/URL, polling, retry, delete, chunk viewer)
- [x] Chat: streaming, Markdown, citation chips, evidence panel, claims highlight, feedback, stop/retry, 429 countdown
- [x] Evaluation dashboard (mode comparison chart, history, run detail) and run inspector (waterfall, attempts)
- [x] Settings (workspace vs. server configuration), role gating, light/dark, 375 px responsive
- [x] 49 component tests; Playwright journey passed against a live (mock-provider) backend
- [x] nginx Docker image (non-root, CSP, SPA fallback) and Vercel config

## Phase 5 — Evaluation and observability
- [x] Versioned dataset: 60 cases, 9 categories, 11 documents, verified relevance labels
- [x] Recall/Precision/Hit@k, MRR, nDCG; deterministic answer metrics; latency percentiles; tokens
- [x] Four-mode comparison with identical cases, labels, and k; JSON + Markdown reports; DB storage
- [x] Real measured results (NVIDIA embeddings, local reranker, Groq answers)
- [x] Run telemetry (node trace, attempts, scores, tokens, outcome, error category)
- [x] Optional OpenTelemetry and LangSmith; neither required

## Phase 6 — Security hardening
- [x] JWT auth, Argon2, per-request membership check, roles
- [x] Tenant isolation and per-user conversation privacy (tests incl. forged tokens, deactivation)
- [x] Rate limits (429 + Retry-After), body-size limit, audit log
- [x] Prompt-injection defences and benchmark cases
- [x] Workspace/document/conversation deletion; data-minimizing storage of message citations
- [x] `pip-audit` clean on runtime deps (optional extra issue documented)
- [~] No email verification / password reset / MFA / invitations (documented)

## Phase 7 — CI/CD and deployment
- [x] GitHub Actions: lint, types, tests with pgvector service, migrations, benchmark smoke, audits, Docker builds, gitleaks
- [x] Backend image (multi-stage, non-root, ~110 MiB idle) and Compose
- [x] Render blueprint, entrypoint migration strategy, deployment guide
- [ ] Public deployment — requires the owner's Neon/Render/Vercel/GitHub accounts (steps in `docs/deployment.md`)
- [ ] CI run on GitHub — the repository has no remote yet

## Phase 8 — Portfolio
- [x] README with measured results, case study, troubleshooting
- [x] Independent code review: 15 findings fixed with regression tests
- [ ] Screenshots — need a browser session against the running app
