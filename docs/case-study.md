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

The first benchmark was a single run over a 60-question, author-written handbook set, and its
threshold was tuned on the same questions. A review called it too easy and too self-referential,
so the evaluation was rebuilt around held-out data:

- **Splits.** The handbook was split 29 dev / 31 test, stratified by category. A public corpus,
  BEIR SciFact (5,183 abstracts), was added: its official train qrels are dev and its 300 test
  claims are held out. Tuning happens on dev only.
- **Repetitions and significance.** Three repetitions per configuration, with a paired
  bootstrap CI for every mode-vs-hybrid comparison.
- **Model-judged faithfulness.** A judge from a different model family scores whether each
  cited sentence is supported by its passages. It is reported next to the structural proxy, and
  checked first against 32 hand-labelled pairs: 0.906 exact and 0.969 binary agreement, with no
  false "supported" verdicts.

| Result (test splits) | Value |
|---|---|
| SciFact nDCG@10: dense / hybrid / hybrid + rerank / lexical | 0.745 / 0.633 / 0.704 / 0.401 |
| Handbook MRR: dense / hybrid | 0.940 / 0.833 (Δ +0.107, CI [+0.027, +0.200]) |
| Answer correctness (gpt-oss-120b, dense, 3 runs) | 0.914 ± 0.019 (gpt-oss-20b, hybrid: 0.925 ± 0.019) |
| Citation precision | 1.000 |
| Judged faithfulness | 1.000 (93 claims; 20b: 0.986) |
| Abstention accuracy | 0.914 ± 0.019 |
| Injected or superseded content | 0 of 93 answers |

## What the measurements changed

- **The default retrieval mode was the wrong one, and it was fixed by measurement.** Dense beat
  hybrid significantly on both test sets. On SciFact, PostgreSQL's `ts_rank_cd` (no IDF) reaches
  0.401 nDCG@10 against about 0.67 for BM25 in the literature, and equal-weight RRF let it drag
  good dense hits down. Rather than switching by assumption, the lexical list got a fusion
  weight tuned on the dev splits. Weight 0 won (0.871 mean dev nDCG, against 0.814 for plain
  RRF). The runner-up, 0.1, closed most of the test gap (SciFact 0.633 → 0.725) but still lost to
  dense (Δ MRR +0.027, CI [+0.009, +0.046]). Dense became the default
  ([ADR 0007](adr/0007-dense-default-retrieval.md)), and hybrid stays optional.
- **The reranker is corpus-dependent.** It helps significantly on SciFact (+0.072 MRR), makes no
  significant difference on the handbook, and costs 1.4–3.7 s on CPU. It stays optional.
- **The held-out split exposed a threshold that only looked perfect.** Tuned on all 60
  questions, the gate passed every answerable one. Tuned on dev only, it blocks one answerable
  test paraphrase. That miss is reported, not re-tuned away.
- **Building the measurement found more bugs than the measurements did.** The six problems
  listed in [retrieval-and-evaluation.md](retrieval-and-evaluation.md#measurement-problems-found-during-this-work)
  would each have published a wrong number:
  - chunk-level scoring of document qrels
  - truncated judge JSON
  - unwrapped single verdicts
  - judge passages without document titles
  - judge outages counted as answer errors
  - free-tier token limits scored as wrong answers
- **Earlier error analysis found two real agent bugs and one metric bug:**
  - The router treated existential "there" as a back-reference.
  - gpt-oss writes `【S1】` citations, which the validator rejected. Mocks could not have revealed
    this; a live smoke test did.
  - A non-breaking hyphen made a correct answer score as wrong.

## Verification

- **Tests:** 261 backend tests (unit, plus integration tests against real PostgreSQL) and 49
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

- The domain test split is small (31 questions), and it, the judge calibration set, and their
  labels were written by the project author. SciFact covers retrieval only.
- Judged faithfulness is model-judged, calibrated on 32 pairs; it is not ground truth.
- Identifier-heavy queries, where lexical matching should help, are not benchmarked, so the
  dense default is untested for that use.
- Authentication lacks password reset, MFA, and invitations.
- Rate limits and locks are in-process (single instance).
- There is no OCR.
- The public deployment runs on free tiers (Render + Neon): the backend sleeps after 15 idle
  minutes and takes about a minute to wake.
