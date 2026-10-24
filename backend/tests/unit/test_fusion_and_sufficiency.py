import uuid

import pytest

from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.service import assess_sufficiency
from app.retrieval.types import EvidenceChunk, RankedId, RetrievalConfig

A, B, C, D = (uuid.UUID(int=i) for i in range(1, 5))


def ranked(*ids: uuid.UUID) -> list[RankedId]:
    return [RankedId(i, 1.0) for i in ids]


def test_rrf_scores_follow_the_formula() -> None:
    fused = reciprocal_rank_fusion([ranked(A, B), ranked(B, C)], k=60)
    scores = {r.chunk_id: r.score for r in fused}
    assert scores[B] == pytest.approx(1 / 62 + 1 / 61)
    assert scores[A] == pytest.approx(1 / 61)
    assert scores[C] == pytest.approx(1 / 62)
    assert [r.chunk_id for r in fused] == [B, A, C]


def test_rrf_rewards_agreement_over_a_single_top_rank() -> None:
    # D is first in one list only; B is second in both lists.
    fused = reciprocal_rank_fusion([ranked(D, B, A), ranked(C, B, A)], k=60)
    assert fused[0].chunk_id == B


def test_rrf_handles_empty_and_single_lists() -> None:
    assert reciprocal_rank_fusion([[], []]) == []
    assert [r.chunk_id for r in reciprocal_rank_fusion([ranked(A, B), []])] == [A, B]


def test_rrf_ignores_duplicates_within_a_ranking() -> None:
    fused = reciprocal_rank_fusion([ranked(A, A, B)], k=60)
    assert {r.chunk_id: r.score for r in fused}[B] == pytest.approx(1 / 63)


def test_rrf_is_deterministic_on_ties() -> None:
    first = reciprocal_rank_fusion([ranked(A), ranked(B)])
    second = reciprocal_rank_fusion([ranked(B), ranked(A)])
    assert [r.chunk_id for r in first] == [r.chunk_id for r in second]


def test_rrf_rejects_non_positive_k() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([ranked(A)], k=0)


def chunk(text: str, dense: float | None = None, rerank: float | None = None) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        collection_id=uuid.uuid4(),
        document_title="Doc",
        source_uri=None,
        text=text,
        ordinal=0,
        page_number=None,
        section=None,
        dense_score=dense,
        rerank_score=rerank,
    )


CONFIG = RetrievalConfig(min_dense_similarity=0.5, min_lexical_coverage=0.5, min_rerank_score=0.0)


def test_no_results_is_insufficient() -> None:
    result = assess_sufficiency("anything", [], CONFIG)
    assert (result.sufficient, result.reason) == (False, "no_results")


def test_high_dense_similarity_is_sufficient() -> None:
    result = assess_sufficiency("vacation days", [chunk("unrelated words", dense=0.8)], CONFIG)
    assert (result.sufficient, result.reason) == (True, "dense_similarity")


def test_term_coverage_is_sufficient_without_dense() -> None:
    result = assess_sufficiency(
        "vacation days policy", [chunk("Vacation days accrue monthly per policy.")], CONFIG
    )
    assert (result.sufficient, result.reason) == (True, "term_coverage")


def test_off_topic_passages_are_insufficient() -> None:
    result = assess_sufficiency(
        "parental leave duration", [chunk("Cafeteria hours are 8 to 5.", dense=0.2)], CONFIG
    )
    assert result.sufficient is False
    assert result.top_dense_similarity == 0.2


def test_reranker_signal_takes_precedence() -> None:
    passages = [chunk("Vacation days accrue monthly.", dense=0.9, rerank=-3.0)]
    result = assess_sufficiency("vacation days", passages, CONFIG)
    assert (result.sufficient, result.reason) == (False, "rerank_below_threshold")
