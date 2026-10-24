# Atlas AI — frontend

React + TypeScript (strict) single-page app for the Atlas AI knowledge platform: workspace dashboard,
knowledge sources, streaming chat with citations and evidence, run inspection, evaluation, and settings.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server on http://localhost:5173 |
| `npm run build` | Type-check (`tsc -b`) and build to `dist/` |
| `npm run preview` | Serve the production build |
| `npm run lint` | ESLint (flat config: typescript-eslint, react-hooks, jsx-a11y) |
| `npm run typecheck` | `tsc -b` (no emit) |
| `npm test` | Vitest + React Testing Library (jsdom) |
| `npm run e2e` | Playwright smoke test against a **running** backend (see below) |

## Configuration

One public, build-time variable (copy `.env.example` to `.env.local`):

```
VITE_API_BASE_URL=http://localhost:8000
```

It is compiled into the bundle, so never put secrets in `VITE_*` variables. The backend must list the
frontend origin in `CORS_ORIGINS`.

## E2E smoke test

Start a backend with mock providers on port 8001 (no API quota used), then run Playwright. It starts
the Vite dev server itself, pointed at `E2E_API_URL` (default `http://localhost:8001`):

```powershell
# in backend/
$env:LLM_PROVIDER="mock"; $env:EMBEDDING_PROVIDER="mock"; $env:RERANKER_PROVIDER="mock"; $env:RERANKER_MODEL="term-coverage"
$env:CORS_ORIGINS='["http://localhost:5173"]'
uv run uvicorn --factory app.main:create_app --port 8001 --loop asyncio:SelectorEventLoop
# in frontend/
npx playwright install chromium   # once
npm run e2e
```

The journey covers register → collection → Markdown upload → ingestion ready → streamed chat →
citation → evidence → thumbs up → run trace → settings → evaluation run. `e2e/visual.spec.ts`
(375 px, dark mode, overflow check) runs only when `E2E_SCREENSHOTS=<dir>` is set.

## Deployment

- **Docker**: `docker build --build-arg VITE_API_BASE_URL=https://api.example.com -t atlas-frontend .`
  Multi-stage build; serves `dist/` from `nginx-unprivileged` as a non-root user on port 8080, with SPA
  fallback, long-cache fingerprinted assets, and a CSP (`script-src 'self'`).
- **Vercel**: `vercel.json` sets the SPA rewrite and security headers. Set `VITE_API_BASE_URL` in the
  project's environment variables.

## Client architecture

```
src/
  api/          client.ts (fetch wrapper, bearer token, error envelope → ApiError, 401 handling)
                endpoints.ts (one typed function per endpoint) · types.ts (request/response types)
                sse.ts (Server-Sent Events over POST: incremental parser + streamChat)
  hooks/        TanStack Query hooks per resource, query keys, polling rules (polling.ts)
  lib/          auth context + roles, theme, remarkCitations (markers → chips), formatters
  components/   ui/ (shadcn-style Radix primitives), badges, error/empty/loading states, layout
  features/     auth · dashboard · knowledge · chat · runs · evaluations · settings
```

- **Server state** lives in TanStack Query. Ingestion and evaluations poll only while work is in
  flight (`pending`/`processing`, `queued`/`running`), then stop.
- **Errors** use the backend envelope `{error: {code, message, request_id, details}}`; error states show
  the message and a copyable request ID, and 429s count down `Retry-After`.
- **Auth**: the token is kept in memory and mirrored to `localStorage` so reloads keep the session.
  Trade-off: any script on the origin could read it, so XSS has to be prevented: no raw HTML is
  rendered (react-markdown without `rehype-raw`, `skipHtml`), remote images from model output are shown
  as links, external links use `rel="noopener noreferrer"` and http(s) only, and the CSP blocks inline
  scripts. Any 401 clears the session and redirects to `/login?next=…`. UI actions are hidden or
  disabled by role (viewer < member < admin < owner); the backend still enforces every check.
- **Chat streaming**: `fetch` + `ReadableStream`, parsed incrementally (split chunks, CRLF, `:`
  keep-alives). A pure reducer (`features/chat/streamReducer.ts`) handles `meta` / `step` / `token` /
  `token_reset` / `final` / `error`. Streamed tokens are provisional; `final` replaces the draft with
  the validated answer. If you press stop or the connection drops, the server still finishes and saves
  the answer, so the page polls the conversation until it appears.
- **Citations**: a remark plugin turns `[S1]` in text nodes (never in code) into `<cite>` nodes rendered
  as chips. A chip opens the evidence panel. History answers carry evidence references without text,
  which is fetched on demand from `GET /chunks/{id}`.
