# Deployment (free tier)

**Status: not deployed yet.** Everything below is prepared and the container is verified locally
(healthy, about 110 MiB RAM at idle). Publishing requires your GitHub, Neon, Render, and Vercel
accounts, which only you can authorize. No live URL exists until you complete these steps.

## Architecture and why

| Part | Host | Free-tier facts (checked 2026-10-24) |
|---|---|---|
| PostgreSQL + pgvector | **Neon** Free | 1 GB storage per project (writes blocked above it), 100 CU-hours/month per project, pgvector supported, compute scales to zero after 5 min idle, no card required, permanent (not a trial) |
| Backend (Docker) | **Render** Free web service | 512 MB RAM / 0.1 CPU; spins down after 15 min without traffic, first request then takes about 1 min; 750 instance-hours/month; ephemeral filesystem |
| Frontend (static SPA) | **Vercel** Hobby | Free, personal and non-commercial use only; 100 GB/month fast data transfer |

Render's own free PostgreSQL is **not** used: it expires 30 days after creation and is then
deleted. Persistent data therefore lives only in Neon. Nothing user-related is written to the
Render filesystem: uploads are stored in Postgres until processed.

Sources: [Render free](https://render.com/docs/free), [Render instance types](https://render.com/docs/compute-plans),
[Neon pricing](https://neon.com/pricing), [Vercel Hobby](https://vercel.com/docs/plans/hobby).
Re-check these pages before relying on the limits; they change.

## Step 1 — Rotate the API keys

The Groq, NVIDIA, Gemini, and OpenRouter keys used during development were pasted into a
chat. Create new keys in the [Groq console](https://console.groq.com/keys), on
[build.nvidia.com](https://build.nvidia.com), in [Google AI Studio](https://aistudio.google.com/apikey)
and on [OpenRouter](https://openrouter.ai/settings/keys), delete the old ones, and use only the
new ones below. Update `backend/.env` locally too.

## Step 2 — Push to GitHub

```bash
cd "C:\Multi-Source Agentic RAG Platform"
git add -A && git commit -m "Atlas AI"
git branch -M main
git remote add origin https://github.com/<you>/atlas-ai.git
git push -u origin main
```

`backend/.env` is gitignored. Run `git status` and confirm no `.env` file is staged before
pushing. CI (`.github/workflows/ci.yml`) runs on the push without any secrets.

## Step 3 — Neon database

1. At [neon.com](https://neon.com), create a project (Postgres 17, a region near your Render
   region).
2. Copy the **direct** connection string, not the `-pooler` one. PgBouncer in transaction mode
   breaks psycopg's automatic prepared statements and LangGraph's `CREATE INDEX CONCURRENTLY`.
3. Turn it into a SQLAlchemy URL by changing the scheme:
   `postgresql+psycopg://USER:PASSWORD@ep-xxx.REGION.aws.neon.tech/neondb?sslmode=require`
4. Nothing else is needed. The first backend start runs `CREATE EXTENSION vector` and all
   migrations.

## Step 4 — Render backend

1. In the Render dashboard choose **New → Blueprint**, select the repo, and Render reads
   `render.yaml` (Docker, free plan, health check `/health/live`, `JWT_SECRET` auto-generated).
2. Fill in the `sync: false` variables:
   - `DATABASE_URL`: the Neon URL from step 3
   - `GROQ_API_KEY`, `NVIDIA_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`: the new keys
   - `CORS_ORIGINS`: `["https://<your-app>.vercel.app"]` (fill in after step 5; JSON list syntax)
   - `DEMO_SEED_PASSWORD`: optional. If set, each boot (idempotently) creates the
     `demo@atlas.example` account with the sample handbook, so visitors can sign in. Use a
     throwaway password of at least 10 characters.
3. Deploy. On boot the entrypoint runs `python -m app.cli migrate` (because
   `RUN_MIGRATIONS_ON_START=true`), optionally seeds the demo, then starts uvicorn on `$PORT`.
4. Verify:
   ```bash
   curl https://<service>.onrender.com/health/live
   curl https://<service>.onrender.com/health/ready   # database, pgvector, migrations, checkpointer = ok
   ```

### Migration strategy

- **Forward-only:** migrations are reviewed in pull requests and must not drop user data;
  `0002` rebuilds a *generated* column, which is recomputed from stored text.
- **Applied at boot** via an explicit flag, because no separate release step is assumed on the
  free tier. If a migration fails, the container exits before serving traffic and Render keeps
  the previous deploy live.
- **Destructive changes** (dropping or rewriting columns that hold data) must not ship this way.
  Run them manually against a Neon branch first, then promote. Neon branching is available on the
  free tier.
- **To run migrations manually instead:** set `RUN_MIGRATIONS_ON_START=false` and run
  `DATABASE_URL=... uv run python -m app.cli migrate` from your machine.

## Step 5 — Vercel frontend

1. In Vercel choose **New Project**, import the repo, and set the **Root Directory** to
   `frontend`. The framework preset is Vite; `frontend/vercel.json` adds the SPA rewrite.
2. Add the environment variable `VITE_API_BASE_URL=https://<service>.onrender.com`.
3. Deploy, then put the Vercel URL into Render's `CORS_ORIGINS` and redeploy the backend.

## Step 6 — Public smoke test

```bash
API=https://<service>.onrender.com
curl -s $API/health/ready
TOKEN=$(curl -s -X POST $API/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"demo@atlas.example","password":"<DEMO_SEED_PASSWORD>"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -X POST $API/api/v1/chat -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"What is the hotel limit in London?"}' | python -m json.tool | head -30
```

Then, in the UI:
1. Ask a question and click a citation.
2. Restart the Render service, reload, and confirm the conversation is still there. This checks
   persistence across restarts.
3. Put the verified URL in the README.

## Operating notes

- **Cold starts:**
  - Render: the first request after 15 min idle waits about a minute (the browser shows Render's
    loading page).
  - Neon: compute also resumes on first query, adding a few seconds; `/health/ready` may briefly
    report `database: fail` until it does.
- **Quotas:**
  - Groq: free-tier limits are per model (8,000 tokens/minute observed for gpt-oss-120b on this
    account). Users get a clear "rate limit reached, retry shortly" answer and the run is
    recorded with `error_category=rate_limited`.
  - NVIDIA: limits apply to embeddings at ingestion and per query.
- **Memory:** keep `RERANKER_PROVIDER=none` on the 512 MB instance. The local cross-encoder
  needs more RAM and CPU than the free tier offers.
- **Single instance:** ingestion recovery and the rate limiter assume one backend instance,
  which is what the free tier runs.
- **Provider chain:** `LLM_FALLBACK_CHAIN` (set in `render.yaml`) tries Groq gpt-oss-120b,
  Groq gpt-oss-20b, Gemini 3.6 Flash, two OpenRouter free models, then NVIDIA. It uses one account
  per provider; combining several accounts of the same provider to get around its limits is
  against Groq's usage policy and is not supported.
- **No paid fallbacks:** OpenRouter models must end in `:free` (enforced at startup), and the
  other providers are used on their free tiers.
- **Privacy:** some free models' hosts (OpenRouter free endpoints, Gemini's free tier) may log or
  train on prompts, which include retrieved document text. That is fine for the fictional demo
  corpus; remove those entries from the chain before using real company documents.
