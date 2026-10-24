# Security

This describes what is implemented and tested, and what is not. Having authentication does not
make the application production-secure. The remaining gaps are listed at the end.

## Authentication and authorization

- **Accounts:** email + password, hashed with Argon2id (pwdlib defaults); minimum 10 characters.
  Login timing is equalized for unknown emails. Registration creates a new workspace (tenant)
  owned by the registrant and can be disabled with `ALLOW_REGISTRATION=false`.
- **Tokens:** HS256 JWT access tokens (`iss`, `sub`, `tid`, `role`, `exp`, `jti`), 12 h TTL,
  signed with `JWT_SECRET` (required and ≥ 32 characters in production; development generates a
  random per-process secret).
- **Membership is re-checked on every request:** a deactivated user or a removed membership is
  locked out immediately, even with an unexpired token. A correctly signed token for a tenant the
  user is not a member of is rejected. Both cases are tested.
- **Roles:**

  | Role | Can do |
  |---|---|
  | viewer | read and chat |
  | member | the above, plus upload, delete documents, create collections |
  | admin | the above, plus delete collections, workspace settings, evaluations, re-embedding, viewing every run in the workspace |
  | owner | the above, plus delete the workspace |

- **Tenant isolation:**
  - Every tenant-owned table has `tenant_id`.
  - Every service query filters on the caller's tenant, including retrieval SQL, so isolation is
    enforced in the same statement that ranks the results.
  - Objects from other tenants return **404, not 403**, so the API never confirms they exist.
  - Conversations and runs are private to their author; admins can inspect runs for debugging.
  - Isolation, role, and deactivation tests are in `backend/tests/integration/test_isolation.py`.
- **Token storage (frontend):** the bearer token is kept in memory and `localStorage`. This is
  simple and works across the Vercel and Render domains, but a successful XSS could read it. The
  mitigation is to render no untrusted HTML (below); httpOnly cookies would be the next step.

## Input handling

- **Size limits:**
  - An ASGI body-size limit rejects oversized requests before they are buffered or spooled to
    disk (`MAX_UPLOAD_BYTES` + 1 MB). Uploads are read in 1 MB pieces and stop at the limit.
  - Questions are limited to 2,000 characters; pasted text to 2 MB.
- **File type** is decided from extension or MIME type and verified against magic bytes, so a
  `.pdf` must start with `%PDF` and a `.docx` must be a ZIP. Binary data in text files is
  rejected.
- **Parser safeguards:**
  - PDF: encrypted files rejected, page cap.
  - DOCX: zip-bomb check (compression ratio of at most 100).
  - CSV and JSON: row and item caps, a recursion guard, keys and headers truncated to 100
    characters, and extracted text capped at 4× the input size (minimum 1 MB). That stops
    repeated-key amplification, where a small file used to expand to many MB of text.
  - DOCX: the uncompressed cap is 50 MB.
  - HTML is parsed to text; scripts and styles are removed and nothing is ever rendered.
- **SQL injection:** all queries are parameterized through SQLAlchemy. The only interpolated
  values in SQL are an embedding model's UUID and integer dimension from the registry, needed to
  match partial expression indexes; they never come from user input.
- **Pydantic validation** on every request body. Unknown fields are rejected in settings updates.
  Validation errors never echo the submitted input.

## URL ingestion (SSRF)

Implemented in `app/ingestion/url_fetch.py` and tested in `tests/unit/test_url_fetch.py`:
- **What a URL may be:** only `http` and `https` on ports 80 and 443; no credentials in URLs; no
  `localhost` or `.local` / `.internal` hosts; an optional host allowlist (`URL_ALLOWED_HOSTS`).
- **Address checks:** every resolved address must be globally routable. That rejects loopback,
  RFC 1918, link-local (including the cloud metadata address `169.254.169.254`), CGNAT,
  multicast, reserved, and IPv4-mapped IPv6 forms of these.
- **DNS-rebinding defence:** the connection is **pinned to the validated IP**. TLS SNI and
  certificate verification still use the hostname.
- **Redirects** are followed manually, at most 3, and re-validated on every hop.
- **Response limits:** content-type allowlist, size cap while streaming, and a timeout.

## Model providers and cost safety

- **Fallback chain:** `LLM_FALLBACK_CHAIN` uses one account per provider. It switches only on
  availability failures; authentication and bad-request errors surface instead of being masked.
  It never switches mid-stream.
- **No paid models via OpenRouter:** an OpenRouter model id without `:free` makes the server
  refuse to start, the factory refuses to build it, and the settings API rejects it. The
  OpenRouter key used here has no spend limit, so this check is the guard.
- **Free-tier data use:** some free endpoints (OpenRouter free models, Gemini's free tier) may
  log or train on prompts, and prompts contain retrieved document text. Keep those providers out
  of the chain for real company data.

## Prompt injection

Retrieved text is untrusted. Several defences are layered; none is sufficient alone:
1. **Structure.** Sources are delimited `<source>` blocks. Tag-like sequences inside them are
   neutralized, so they cannot close the block or open `<system>`. The system prompt marks
   sources as data and puts security rules above everything.
2. **Isolation of decisions.** The router and tools never see retrieved passages. The LLM
   router does see recent conversation history, which includes earlier assistant answers
   (derived from documents, up to 500 characters each), so document influence on routing is
   reduced, not eliminated. The tools themselves are harmless: arithmetic, metadata listing
   within the caller's tenant, and summaries that go through citation validation.
3. **Output validation.** Citations must reference retrieved passages. Links not belonging to a
   retrieved source are stripped: Markdown link targets of any scheme (including `javascript:`),
   absolute URLs in any letter case, bare `www.` hosts, and protocol-relative `//host` links.
   Injected phishing links therefore don't survive. Unsupported answers
   are replaced by an abstention.
4. **Flagging.** A heuristic detector marks instruction-like passages (`suspicious=true`). The
   prompt annotates them and the UI warns. It is a signal, not a boundary.
5. **Measurement.** The benchmark contains a poisoned vendor notice. No answer contained the
   injected value: forbidden content was 0.000 over 60 cases with Groq `gpt-oss-120b`, and over
   93 held-out test-split generations (3 × 31) with `gpt-oss-20b`. That is evidence
   on this dataset, not a guarantee against all attacks.

## Output rendering

- **Model output and retrieved text** are rendered as Markdown with raw HTML disabled; nothing
  uses `dangerouslySetInnerHTML`.
- **External source links** use `rel="noopener noreferrer"`.
- **API responses** carry `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, and `Cross-Origin-Resource-Policy: same-site`.

## Secrets

- **Where they live:** provider keys, the JWT secret, and the database URL come only from
  environment variables or deployment secrets. They are typed `SecretStr`, so they are masked in
  `repr` and dumps, and are never sent to the frontend. The settings UI shows only whether a
  provider is configured.
- **SQL parameters:** the engine runs with `hide_parameters=True`, so SQL errors in logs never
  include bound values such as document text or emails.
- **Logs:** a structlog processor redacts sensitive keys (`*api_key*`, `authorization`,
  `password`, `token`, `database_url`, …) and credential-shaped values (`gsk_…`, `nvapi-…`,
  `sk-…`, Bearer tokens, bare JWTs, `user:password@` in URLs) from every log line, including third-party loggers.
  This is tested.
- **Repository and image:** `.env` files are gitignored and excluded from Docker images
  (verified). CI runs gitleaks.

## Abuse controls

| What | Limit | Key |
|---|---|---|
| Chat and search | 20 per minute | per user |
| Ingestion | 30 per minute | per user |
| Login and registration | 10 per minute | per client IP |
| Evaluation runs | 6 per hour | per user |

- **Responses:** 429 with `Retry-After`. Provider rate limits are surfaced to users as clear
  "retry shortly" messages.
- **Single-instance only:** the limiter is in-process, so each replica enforces its own window.
  That is adequate for one free-tier instance; a shared store would be needed for horizontal
  scaling.
- **Spoofable IP limit:** behind Render's proxy, `FORWARDED_ALLOW_IPS="*"` trusts
  `X-Forwarded-For`, so a client can spoof its IP for the per-IP auth limit.

## Concurrency

- **Request transactions** commit when the endpoint returns, before the response is sent
  (`Depends(get_session, scope="function")`), so a failed commit can never be reported as a
  2xx.
- **Conversations:** one run at a time; a concurrent turn gets 409.
- **Ingestion:** retries lock the document row; background jobs only process a document if
  they are still its queued job, so a stale or duplicate job cannot overwrite a ready document.
- **URL fetches** release the DB connection while waiting on the network.
- **Shutdown** gives in-flight streaming runs 10 s to finish and persist.

## Audit logging

`audit_logs` records registration, logins, failed logins (with the email domain only), creation
and deletion of collections and documents, retries, re-embedding, settings changes, evaluation
runs, conversation deletion, and workspace deletion, each with its request ID. Failed-login
records survive the rejected request; the route commits them explicitly.

## Data retention and deletion

| Data | Retention / deletion |
|---|---|
| Raw uploaded bytes (`document_sources`) | Kept only until ingestion succeeds (needed for retries), then deleted. |
| Documents | `DELETE /documents/{id}` removes chunks, vectors, full-text data, jobs, and source bytes (FK cascade, tested). |
| Collections | Deletion cascades to all its documents. |
| Conversation text | Messages store citation **references**, not passage text; snippets are loaded live, so deleting a document removes its text from history views. Run telemetry stores ids and scores, not text. |
| Conversations | `DELETE /conversations/{id}` removes messages, feedback, and the LangGraph checkpoint thread. Runs are kept, unlinked, for telemetry. |
| Workspace | `DELETE /workspace` (owner, with name confirmation) removes everything tenant-scoped, including conversation memory. The audit trail keeps an anonymized record. |
| Logs | stdout JSON logs contain request metadata, never document text or prompts; retention is whatever the host keeps. |
| LangSmith (optional, off by default) | Receives prompts, which include document text. Enable it only if that is acceptable. |

## Dependency scanning

- **Python:** `pip-audit` over the locked runtime dependencies fails CI on any known
  vulnerability. Current result: none. The optional `rerank-local` extra pulls `pillow 12.3.0`
  (via fastembed, which pins `<13`) with 37 advisories fixed only in 13.0. The extra uses
  fastembed's text cross-encoder, which never decodes images; it is not installed in the default
  image, and CI audits it as informational.
- **Frontend:** `npm audit --omit=dev --audit-level=high` in CI.

## Known limitations

- No email verification, password reset, MFA, account lockout, or invitations; workspace
  members can only be added at the database level.
- Registration returns 409 for an existing email, which allows account enumeration. Login is
  timing-equalized, but registration is not.
- The conversation lock and rate limiter are in-process (single instance).
- Bearer tokens in `localStorage` (see above); no refresh or revocation list beyond the
  per-request membership check.
- The in-process rate limiter is not shared across replicas, and the per-IP limit is spoofable
  behind a trusting proxy.
- Prompt-injection defences are layered heuristics measured on one dataset, not a proof.
- No antivirus scanning of uploads; no OCR.
- Ingestion runs in-process: a crash mid-ingestion marks the job failed at next startup (it is
  retryable), but there is no durable queue.
