"""Reciprocal Rank Fusion (Cormack, Clarke & Büttcher, 2009), optionally weighted.

score(d) = Σ_r w_r / (k + rank_r(d)), with 1-based ranks and w_r = 1 by default. RRF uses ranks
only, so it needs no calibration between cosine similarities and ts_rank_cd scores, which live
on different scales. A weight below 1 lets a weaker ranking contribute without overriding a
stronger one; the hybrid retriever uses it to down-weight the lexical list (tuned on dev splits).
"""

import uuid
from collections.abc import Sequence

from app.retrieval.types import RankedId


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[RankedId]],
    k: int = 60,
    weights: Sequence[float] | None = None,
) -> list[RankedId]:
    if k <= 0:
        raise ValueError("k must be positive")
    if weights is None:
        weights = [1.0] * len(rankings)
    if len(weights) != len(rankings):
        raise ValueError("one weight per ranking is required")
    if any(w < 0 for w in weights):
        raise ValueError("weights must be non-negative")
    scores: dict[uuid.UUID, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        seen: set[uuid.UUID] = set()
        for position, item in enumerate(ranking, start=1):
            if item.chunk_id in seen:  # count each document once per ranking
                continue
            seen.add(item.chunk_id)
            scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + weight / (k + position)
    # Ties broken by id for determinism.
    return [
        RankedId(cid, s) for cid, s in sorted(scores.items(), key=lambda kv: (-kv[1], str(kv[0])))
    ]
