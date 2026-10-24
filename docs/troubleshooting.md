# Troubleshooting

Every response carries an `X-Request-ID` header, and error bodies include `request_id`. Search
the backend's JSON logs for it: every log line of that request carries the same `request_id`.

| Symptom | Likely cause | What to do |
|---|---|---|
| `/health/ready` → `migrations: fail` | Schema behind code | `uv run python -m app.cli migrate` (Docker: `docker compose run --rm migrate`) |
| `/health/ready` → `checkpointer: fail` | LangGraph tables missing | Same `migrate` command; it also runs `checkpointer.setup()` |
| `/health/ready` → `database: fail (OperationalError)` | DB unreachable, wrong `DATABASE_URL`, or Neon compute waking from suspend | Check the URL uses `postgresql+psycopg://`; retry after a few seconds on Neon free tier |
| `InterfaceError … ProactorEventLoop` on Windows | psycopg async cannot use Windows' default loop | Run uvicorn with `--loop asyncio:SelectorEventLoop` (the CLI and tests handle this already) |
| Startup error `GROQ_API_KEY is required…` | Selected provider has no key | Set the key, or use `LLM_PROVIDER=mock` for offline work |
| Chat answers "The configured model is not available for this account" (`model_unavailable`) | Model id not provisioned (NVIDIA returns 404/410 per account) | List models: `GET https://integrate.api.nvidia.com/v1/models`; then verify with one real call; set `LLM_MODEL` / `EMBEDDING_MODEL` |
| Chat answers "free-tier rate limit was reached" (`rate_limited`) | Groq TPM/RPD or NVIDIA limits | Every member of `LLM_FALLBACK_CHAIN` was exhausted. The run's `details.provider_failovers` lists each skipped provider and why. Wait for the reset; reduce `AGENT_MAX_LLM_CALLS`; add another provider (one account each) to the chain |
| Server refuses to start: "OpenRouter model … is not a free model" | An OpenRouter entry without `:free` | Use a `:free` model id; paid OpenRouter models are blocked on purpose |
| Answers come from a fallback model | The primary was rate-limited, down, or returned an empty answer | Expected; see `provider_failovers` in the run details. Probe a model with one real call before adding it: listed models are not always callable (e.g. `gemini-2.5-flash` is closed to new users) |
| Document `failed`: "Embedding failed: …rate limit" | Embedding API throttled during ingestion | Click **Retry** (or `POST /documents/{id}/retry`); the job is idempotent |
| Document `unsupported`: "needs OCR" | Scanned PDF, no text layer | OCR is not enabled; supply a text PDF |
| Document stuck `pending` after a restart | Background task was interrupted | On startup it is marked `failed` with "interrupted by a server restart"; retry it |
| Search returns nothing in `dense` mode, warning "No documents have been embedded with the configured model" | Embedding model/dimension changed after ingestion | `POST /collections/{id}/reembed` (admin) |
| `ValueError: Embedding dimension … cannot be indexed` | Model output > 2,000 dims | Set `EMBEDDING_DIMENSION` ≤ 2000 (Matryoshka models) |
| `ck_chunks_embedding_consistency` violation | A vector of the wrong length was written | Indicates a provider/registry mismatch; the DB is protecting the index. Check `EMBEDDING_DIMENSION` matches what the provider returns |
| Irrelevant passages retrieved | Chunks too large/small, or wrong mode | Inspect the run (Runs → attempt scores); try `POST /search` with each mode; adjust `chunk_size` in Settings and re-upload |
| Agent abstains on answerable questions | Threshold too strict for the embedding model, or model ignored citations | Check the run's `sufficiency` and `validate_answer` trace entries; recalibrate `MIN_DENSE_SIMILARITY` (see retrieval-and-evaluation.md) |
| Answer abstained after streaming text | Citation validation rejected the draft (no valid `[S#]`) | Expected behaviour; see `validate_answer` → `invalid_markers` in the run trace |
| Slow answers | Free-tier queueing, cold start, reranker on CPU | Run trace shows per-node latency; disable reranking on small instances |
| Render: first request takes ~1 min | Free web service spun down after 15 min idle | Expected on the free tier |
| Render: container restarts / OOM | 512 MB limit | Keep `RERANKER_PROVIDER=none`, `DB_POOL_SIZE=3` |
| `alembic check` reports drift | Model changed without a migration | `uv run alembic revision --autogenerate -m "…"`, review, commit |
| Integration tests skipped | No PostgreSQL | `docker compose up -d db` or set `TEST_DATABASE_URL` |
