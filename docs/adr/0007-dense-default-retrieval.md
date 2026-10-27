# ADR 0007 — Dense retrieval by default; hybrid stays optional

- Status: Accepted (2026-10-27). Supersedes the default-mode choice in ADR 0001; the hybrid
  machinery itself (pgvector + PostgreSQL FTS + RRF) is unchanged.

## Context
Hybrid retrieval (dense + PostgreSQL full-text search, fused with equal-weight RRF) was the
default, on the argument that lexical matching protects exact identifiers. Neither benchmark
tests that argument, and the held-out results contradicted the choice:

| Test split | dense | hybrid (plain RRF) |
|---|---|---|
| SciFact, 300 claims, nDCG@10 | 0.745 | 0.633 (ΔMRR −0.124, CI excludes 0) |
| Handbook, 25 questions, MRR | 0.940 | 0.833 (ΔMRR −0.107, CI excludes 0) |

The likely cause is the lexical leg. `ts_rank_cd` has no IDF term and scores 0.401 nDCG@10 on
SciFact, against about 0.67 for BM25 in the literature. Equal-weight fusion lets that weaker list
push good dense hits down.

## Options considered
1. Switch the default to dense.
2. Keep hybrid, but down-weight the lexical list with a weight tuned on the dev splits.

Option 2 was tried first, because it could keep any benefit lexical matching has.
`evaluate --calibrate-fusion` ran hybrid on both dev splits (handbook 29 questions, SciFact 809
claims) for lexical weights 0–1, maximising mean nDCG@k
([results](../../backend/eval/results/fusion-calibration/20261027T144528Z.json)):

| Lexical weight | 0 (dense order) | 0.1 | 0.3 | 0.5 | 1.0 |
|---|---|---|---|---|---|
| Mean dev nDCG | **0.871** | 0.869 | 0.849 | 0.839 | 0.814 |

Every non-zero weight lowered SciFact. On the handbook, a weight of 0.1 to 0.7 gained one
question. The runner-up, weight 0.1, was then checked once on the test splits:
- **SciFact:** nDCG@10 0.725 against 0.745 for dense. Dense is still significantly better
  (ΔMRR +0.027, CI [+0.009, +0.046]).
- **Handbook:** identical to dense.

## Decision
- **Default:** `RETRIEVAL_MODE=dense`. Weight 0 reproduces the dense order, so running the
  lexical query on every request would add latency and nothing else.
- **Kept options:** `hybrid` and `hybrid_rerank` remain available per workspace.
- **Weight setting:** `RRF_LEXICAL_WEIGHT` (default 1.0, plain RRF) remains for anyone who opts
  into hybrid, and can be re-tuned with `--calibrate-fusion`.

## Consequences
- **Quality:** retrieval quality improves on both test sets with no extra cost, and p50 latency
  drops by the cost of the lexical query and fusion (measured: about 4 ms on the handbook, about
  40 ms on SciFact).
- **Untested risk:** exact-identifier lookups (codes, SKUs, error strings) are not covered by
  either benchmark. A workspace that relies on them should use hybrid.
- **Revisit when:** BM25 or a learned sparse retriever replaces `ts_rank_cd`, or a benchmark with
  identifier-heavy queries exists. Either could change the outcome, and should be re-measured
  the same way.
