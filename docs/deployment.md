# Deployment (Render + Neon, free tier)

**Status:** the code is on GitHub and both builds were reproduced locally exactly as the Render
blueprint runs them. The backend image passed its health checks and used about 110 MiB RAM at
idle; the frontend build ran from a clean `npm ci`. The deployment itself needs your Render and
Neon accounts. Add the live URL to the README only after the smoke test in Step 6 passes.

## Architecture and why

| Part | Host | Free-tier facts (checked 2026-10-24) |
|---|---|---|
| Backend (Docker) | **Render** free web service | 512 MB RAM and 0.1 CPU. Spins down after 15 min with no traffic; the next request then takes about 1 min. 750 instance-hours per month. Ephemeral filesystem. |
| Frontend (static SPA) | **Render** static site | Free. Served from Render's CDN. |
| PostgreSQL + pgvector | **Neon** Free | 1 GB storage per project (writes are blocked above it). 100 CU-hours per month. pgvector supported. Scales to zero after 5 min idle. No card required, and it is permanent, not a trial. |

**Why Neon instead of Render's database:** Render's free PostgreSQL expires 30 days after
creation and is then deleted, so the data lives in Neon. Nothing user-related is written to the
Render filesystem; uploads are stored in Postgres until they are processed.

Sources: [Render free](https://render.com/docs/free), [Render instance types](https://render.com/docs/compute-plans),
[Render Blueprint spec](https://render.com/docs/blueprint-spec), [Neon pricing](https://neon.com/pricing).
These limits change, so re-check the pages before relying on them.

## Step 1: Rotate the API keys

The Groq, NVIDIA, Gemini, and OpenRouter keys used during development were pasted into a chat.
1. Create new keys in the [Groq console](https://console.groq.com/keys), on
   [build.nvidia.com](https://build.nvidia.com), in [Google AI Studio](https://aistudio.google.com/apikey)
   and on [OpenRouter](https://openrouter.ai/settings/keys).
2. Delete the old keys.
3. Use only the new ones in the steps below, and put them in `backend/.env` locally as well.
4. On OpenRouter, consider setting a credit limit of 0 on the key. The app already only allows
   `:free` models; the limit adds a second guard.

## Step 2: Create the Neon database

1. Sign in at [neon.com](https://neon.com) and create a project: Postgres 17, region **US East
   (Ohio)** or the region closest to the Render region you pick.
2. Open **Connect** and choose the **direct** connection, with "Connection pooling" switched
   **off**. The pooled `-pooler` host breaks psycopg's prepared statements and LangGraph's
   `CREATE INDEX CONCURRENTLY`.
3. Copy the URL and change only the scheme, from `postgresql://` to `postgresql+psycopg://`:
   ```
   postgresql+psycopg://neondb_owner:PASSWORD@ep-xxxx.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
   If Neon appends `&channel_binding=require`, keep it.

Nothing else is needed. The backend's first start creates the `vector` extension and runs every
migration.

## Step 3: Deploy the blueprint on Render

1. Sign in at [render.com](https://render.com) with GitHub, and allow access to the
   `Multi-Source-Agentic-RAG-Platform` repository.
2. Choose **New → Blueprint**, select the repo and branch `main`. Render reads `render.yaml` and
   shows two services, `atlas-ai-backend` (Docker, free) and `atlas-ai-frontend` (static site).
3. Render asks for the variables marked `sync: false`. Fill them in:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the Neon URL from Step 2 |
   | `GROQ_API_KEY`, `NVIDIA_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | the new keys |
   | `DEMO_SEED_PASSWORD` | a throwaway password of at least 10 characters for the public demo account `demo@atlas.example` |
   | `CORS_ORIGINS` | `["https://atlas-ai-frontend.onrender.com"]` for now (you correct it in Step 4) |
   | `VITE_API_BASE_URL` (frontend) | `https://atlas-ai-backend.onrender.com` for now (you correct it in Step 4) |

   `JWT_SECRET` is generated automatically, and everything else comes from `render.yaml`.
4. Click **Apply**. On its first boot the backend:
   - runs migrations;
   - seeds the demo workspace, ingesting the 11 handbook documents through NVIDIA embeddings;
   - starts the API.

   The first deploy takes several minutes, mostly the Docker build. Follow it under
   **atlas-ai-backend → Logs**; look for `migrations_complete`, then `demo_seeded`, then
   `Application startup complete`.

## Step 4: Fix the two URLs

Render gives each service a URL like `https://<name>.onrender.com`. If a name is taken, it adds a
suffix, for example `atlas-ai-backend-x7k2.onrender.com`.

1. Copy the real backend URL from the **atlas-ai-backend** page.
2. Open **atlas-ai-frontend → Environment**, set `VITE_API_BASE_URL` to that URL (no trailing
   slash), save, then click **Manual Deploy → Deploy latest commit**. The value is compiled into
   the bundle, so the site must be rebuilt.
3. Copy the real frontend URL from the **atlas-ai-frontend** page.
4. Open **atlas-ai-backend → Environment**, set `CORS_ORIGINS` to `["https://<your-frontend>.onrender.com"]`
   (JSON list, exact origin, no trailing slash), and save. The backend redeploys on its own.
5. Under **atlas-ai-frontend → Redirects/Rewrites**, confirm there is a rewrite `/*` →
   `/index.html`. If the blueprint didn't create it, add it there. Without it, refreshing a
   page such as `/chat/...` returns 404.

## Step 5: Check the backend

```bash
curl https://<backend>.onrender.com/health/live    # {"status":"alive"}
curl https://<backend>.onrender.com/health/ready   # database, pgvector, migrations, checkpointer all "ok"
```

The first request after the service has been idle can take about a minute; that is the free
tier waking up.

## Step 6: Smoke test the public app

1. Open `https://<frontend>.onrender.com` and sign in as `demo@atlas.example` with your
   `DEMO_SEED_PASSWORD`.
2. In **Chat**, ask "What is the hotel limit in London?". You should get 250 EUR with a citation;
   click it and the evidence panel opens.
3. Ask "What is the parental leave policy for pets?". It should decline.
4. Open the run from the answer and check that the node timeline shows the provider that
   answered.
5. **Persistence:** on Render, click **atlas-ai-backend → Manual Deploy → Restart service**.
   Reload the app; the conversation must still be there.
6. Upload a small Markdown file in **Knowledge sources** and watch it reach Ready.
7. Put the verified URL into the README under "Live demo".

## Optional: stop strangers from using up your free quota

Visitors can register their own workspaces. That's good for showing multi-tenancy, but each one
draws on your free LLM quota. To allow only the demo account, set `ALLOW_REGISTRATION=false` on
the backend and share the demo login instead.

## Migration strategy

- **Forward-only and reviewed:** migrations are reviewed in pull requests and must not drop user
  data.
- **Applied at boot:** they run because `RUN_MIGRATIONS_ON_START=true`. If a migration fails,
  the container exits before serving traffic and Render keeps the previous deploy live.
- **Destructive changes:** test them on a Neon branch first; branching is available on the free
  plan.
- **Running them manually instead:** set `RUN_MIGRATIONS_ON_START=false`, then run
  `DATABASE_URL=... uv run python -m app.cli migrate` from your machine.

## Operating notes

- **Cold starts:**
  - Render's free backend sleeps after 15 minutes idle, and the first request then takes about a
    minute.
  - Neon's compute also resumes on first query, adding a few seconds; `/health/ready` may briefly
    report `database: fail`.
- **Quotas:** answers go through `LLM_FALLBACK_CHAIN` (Groq gpt-oss-120b → Groq gpt-oss-20b →
  Gemini 3.6 Flash → two OpenRouter `:free` models → NVIDIA), one account per provider. If every
  member is exhausted, users get a clear "rate limit reached, retry shortly" message, and the run
  records each skipped provider in `provider_failovers`.
- **Privacy:** some free endpoints (OpenRouter free models, Gemini's free tier) may log or train
  on prompts, and prompts include retrieved document text. That is fine for the fictional demo
  corpus. Remove those entries before using real company documents.
- **Memory:** keep `RERANKER_PROVIDER=none` on the 512 MB instance.
- **Single instance:** the rate limiter, the per-conversation lock, and job recovery are
  in-process. Before running more than one backend instance, move them to Postgres or Redis.
- **No paid services:** OpenRouter models must end in `:free` (checked at startup); every other
  provider runs on its free tier.
