"""Reciprocal Rank Fusion (Cormack, Clarke & Büttcher, 2009).

score(d) = Σ_r 1 / (k + rank_r(d)), with 1-based ranks. RRF uses ranks only, so it needs no
calibration between cosine similarities and ts_rank_cd scores, which live on different scales.
"""

import uuid
from collections.abc import Sequence

from app.retrieval.types import RankedId


def reciprocal_rank_fusion(rankings: Sequence[Sequence[RankedId]], k: int = 60) -> list[RankedId]:
    if k <= 0:
        raise ValueError("k must be positive")
    scores: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        seen: set[uuid.UUID] = set()
        for position, item in enumerate(ranking, start=1):
            if item.chunk_id in seen:  # count each document once per ranking
                continue
            seen.add(item.chunk_id)
            scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + 1.0 / (k + position)
    # Ties broken by id for determinism.
    return [
        RankedId(cid, s) for cid, s in sorted(scores.items(), key=lambda kv: (-kv[1], str(kv[0])))
    ]
