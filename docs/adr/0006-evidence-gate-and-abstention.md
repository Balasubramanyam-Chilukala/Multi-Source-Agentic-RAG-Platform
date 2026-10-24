# ADR 0006 — Retrieval gate as a pre-filter; model abstention as the primary decision

- Status: Accepted (2026-10-24)

## Context
The agent must abstain when evidence is insufficient. A similarity threshold is cheap but
measured top-1 similarities for answerable and unanswerable questions overlap
(answerable p10 0.287, unanswerable max 0.409 on atlas-handbook-v1).

## Decision
Two stages:
1. A lenient retrieval gate (`MIN_DENSE_SIMILARITY=0.32` or query-term coverage ≥ 0.5) that only
   filters clearly off-topic questions and triggers one query refinement.
2. The model must reply `INSUFFICIENT_EVIDENCE` when sources lack the answer, and citation
   validation rejects answers without valid citations (one regeneration, then abstention).

## Consequences
- Abstention accuracy 0.967 and correctness 0.950 with gpt-oss-120b on the benchmark; all 8
  unanswerable questions were declined.
- Abstention quality depends on the LLM following instructions; a weaker model would need a
  stricter gate. The threshold is model-specific and was calibrated on the evaluation set (no
  held-out split) — a documented limitation.
