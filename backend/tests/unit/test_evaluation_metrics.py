"""Evaluation metrics against hand-computed values, and dataset validation."""

import math

import pytest

from app.evaluation.dataset import CATEGORIES, load_dataset
from app.evaluation.metrics import (
    answer_metrics,
    fact_present,
    hit_rate_at_k,
    ndcg_at_k,
    percentile,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

RETRIEVED = ["a", "x", "b", "y", "z"]
RELEVANT = {"a", "b", "c"}


def test_recall_precision_hit_at_k() -> None:
    assert recall_at_k(RETRIEVED, RELEVANT, 3) == pytest.approx(2 / 3)
    assert precision_at_k(RETRIEVED, RELEVANT, 3) == pytest.approx(2 / 3)
    assert precision_at_k(RETRIEVED, RELEVANT, 5) == pytest.approx(2 / 5)
    assert hit_rate_at_k(RETRIEVED, RELEVANT, 1) == 1.0
    assert hit_rate_at_k(["x", "y"], RELEVANT, 2) == 0.0


def test_precision_uses_returned_count_when_fewer_than_k() -> None:
    assert precision_at_k(["a"], RELEVANT, 5) == 1.0
    assert precision_at_k([], RELEVANT, 5) == 0.0


def test_reciprocal_rank() -> None:
    assert reciprocal_rank(["x", "y", "b"], RELEVANT) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x", "y"], RELEVANT) == 0.0


def test_ndcg_matches_hand_computation() -> None:
    dcg = 1 / math.log2(2) + 1 / math.log2(4)  # relevant at ranks 1 and 3
    ideal = 1 / math.log2(2) + 1 / math.log2(3) + 1 / math.log2(4)  # 3 relevant within k=5
    assert ndcg_at_k(RETRIEVED, RELEVANT, 5) == pytest.approx(dcg / ideal)
    assert ndcg_at_k(["a", "b", "c"], RELEVANT, 3) == pytest.approx(1.0)


def test_unlabelled_cases_are_excluded_not_zeroed() -> None:
    for fn in (recall_at_k, precision_at_k, hit_rate_at_k, ndcg_at_k):
        assert fn(RETRIEVED, set(), 5) is None
    assert reciprocal_rank(RETRIEVED, set()) is None


@pytest.mark.parametrize(
    ("answer", "alternatives", "expected"),
    [
        ("The cap is **250 EUR** per night.", ["250"], True),
        ("The cap is 1,250 EUR.", ["250"], False),
        ("Within 15 minutes.", ["5"], False),
        ("Within 5 minutes.", ["5", "five"], True),
        ("Rate: 0.30 EUR per km", ["0.30"], True),
        ("Contact Jonas Weber.", ["jonas weber"], True),
        ("Open 08:00–18:00", ["08:00"], True),
    ],
)
def test_fact_matching_is_token_aware(answer: str, alternatives: list[str], expected: bool) -> None:
    assert fact_present(answer, alternatives) is expected


def _answer(**overrides: object) -> dict[str, float | None]:
    values: dict[str, object] = {
        "answer": "Hotels are capped at 250 EUR [S1].",
        "outcome": "answered",
        "answerable": True,
        "expected_outcome": None,
        "required_facts": [["250"]],
        "forbidden_facts": ["9999"],
        "cited_chunk_ids": ["c1"],
        "relevant": {"c1"},
        "claims": [{"supported": True}],
    }
    values.update(overrides)
    return answer_metrics(**values)  # type: ignore[arg-type]


def test_correct_supported_answer() -> None:
    m = _answer()
    assert (
        m["correctness"],
        m["completeness"],
        m["citation_precision"],
        m["faithfulness"],
        m["abstention_accuracy"],
        m["forbidden_content"],
    ) == (1.0, 1.0, 1.0, 1.0, 1.0, 0.0)


def test_forbidden_content_makes_answer_incorrect() -> None:
    m = _answer(answer="The limit is 9999 EUR [S1]. Also 250 EUR [S1].")
    assert m["correctness"] == 0.0 and m["forbidden_content"] == 1.0


def test_abstaining_on_answerable_question_is_penalized() -> None:
    m = _answer(answer="I could not find sufficient evidence...", outcome="abstained")
    assert m["abstention_accuracy"] == 0.0 and m["correctness"] == 0.0


def test_abstaining_on_unanswerable_question_is_correct() -> None:
    m = _answer(answerable=False, outcome="abstained", required_facts=[], relevant=set())
    assert m["abstention_accuracy"] == 1.0 and m["correctness"] == 1.0
    answered = _answer(answerable=False, outcome="answered", required_facts=[], relevant=set())
    assert answered["abstention_accuracy"] == 0.0 and answered["correctness"] == 0.0


def test_citation_precision_counts_irrelevant_citations() -> None:
    assert _answer(cited_chunk_ids=["c1", "c9"])["citation_precision"] == 0.5


def test_percentile_nearest_rank() -> None:
    values = list(range(1, 101))
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile([], 50) is None


def test_bundled_dataset_is_valid_and_covers_every_category() -> None:
    dataset = load_dataset("atlas-handbook-v1")
    assert 50 <= len(dataset.cases) <= 100
    assert {c.category for c in dataset.cases} == set(CATEGORIES)
    assert sum(1 for c in dataset.cases if not c.answerable) >= 8
    assert len(dataset.documents) >= 10


def test_typographic_hyphens_and_spaces_are_normalized() -> None:
    assert fact_present("A blameless post‑mortem within five working days", ["post-mortem"])
    assert fact_present("250 EUR", ["250 EUR"])
