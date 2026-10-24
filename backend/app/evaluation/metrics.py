"""Deterministic retrieval and answer metrics.

Retrieval metrics use binary relevance labels over chunk ids:
- Recall@k: fraction of relevant chunks found in the top k.
- Precision@k: fraction of the top k (or fewer, if fewer were returned) that is relevant.
- Hit rate@k: 1 if any relevant chunk is in the top k.
- MRR: reciprocal rank of the first relevant chunk (0 if none in the ranking).
- nDCG@k: DCG with gain 1 for relevant chunks, normalized by the ideal ordering.

Answer metrics are reference-based string checks, not model judgements:
- completeness: fraction of required facts present (each fact has accepted surface forms).
- correctness: 1 if every required fact is present and no forbidden fact appears.
- citation precision: fraction of cited chunks that are labelled relevant.
- faithfulness (proxy): fraction of answer sentences that carry a valid citation. It does
  **not** verify entailment between the sentence and the cited passage.
- abstention accuracy: abstained/clarified on unanswerable cases, answered on answerable ones.
"""

import math
import re
from collections.abc import Sequence

_SPACES = re.compile(r"\s+")
_MARKDOWN = re.compile(r"[*_`]")
# Models emit typographic hyphens (e.g. U+2011 NON-BREAKING HYPHEN); compare them as "-".
_HYPHENS = re.compile("[‐-―−]")


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float | None:
    if not relevant:
        return None
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def precision_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float | None:
    top = retrieved[:k]
    if not relevant:
        return None
    if not top:
        return 0.0
    return sum(1 for r in top if r in relevant) / len(top)


def hit_rate_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float | None:
    if not relevant:
        return None
    return 1.0 if set(retrieved[:k]) & relevant else 0.0


def reciprocal_rank(retrieved: Sequence[str], relevant: set[str]) -> float | None:
    if not relevant:
        return None
    for position, chunk_id in enumerate(retrieved, start=1):
        if chunk_id in relevant:
            return 1.0 / position
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float | None:
    if not relevant:
        return None
    dcg = sum(1.0 / math.log2(i + 1) for i, r in enumerate(retrieved[:k], start=1) if r in relevant)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0


def retrieval_metrics(
    retrieved: Sequence[str], relevant: set[str], k: int
) -> dict[str, float | None]:
    return {
        "recall@k": recall_at_k(retrieved, relevant, k),
        "precision@k": precision_at_k(retrieved, relevant, k),
        "hit_rate@k": hit_rate_at_k(retrieved, relevant, k),
        "mrr": reciprocal_rank(retrieved, relevant),
        "ndcg@k": ndcg_at_k(retrieved, relevant, k),
    }


def normalize_answer(text: str) -> str:
    text = _MARKDOWN.sub("", text.replace(" ", " ").replace(" ", " "))
    text = _HYPHENS.sub("-", text)
    return _SPACES.sub(" ", text).strip().lower()


def fact_present(answer: str, alternatives: Sequence[str]) -> bool:
    normalized = normalize_answer(answer)
    for alternative in alternatives:
        needle = normalize_answer(alternative)
        # Whole-token match for short numeric facts, so "5" does not match "15".
        pattern = (
            rf"(?<![\w.,]){re.escape(needle)}(?![\w])"
            if needle[:1].isalnum()
            else re.escape(needle)
        )
        if re.search(pattern, normalized):
            return True
    return False


def answer_metrics(
    *,
    answer: str,
    outcome: str,
    answerable: bool,
    expected_outcome: str | None,
    required_facts: Sequence[Sequence[str]],
    forbidden_facts: Sequence[str],
    cited_chunk_ids: Sequence[str],
    relevant: set[str],
    claims: Sequence[dict[str, object]],
) -> dict[str, float | None]:
    # For unanswerable (and ambiguous) cases both abstaining and asking for clarification are
    # acceptable; ``expected_outcome`` is kept in the dataset for documentation.
    abstained = outcome in ("abstained", "clarification")
    abstention_correct = (0.0 if abstained else 1.0) if answerable else (1.0 if abstained else 0.0)

    forbidden_hit = any(fact_present(answer, [f]) for f in forbidden_facts)
    result: dict[str, float | None] = {
        "abstention_accuracy": abstention_correct,
        "forbidden_content": 1.0 if forbidden_hit else 0.0,
        "completeness": None,
        "correctness": None,
        "citation_precision": None,
        "faithfulness": None,
    }
    if not answerable:
        # Answering an unanswerable question is incorrect whatever it says.
        result["correctness"] = 1.0 if abstained and not forbidden_hit else 0.0
        return result
    if abstained:
        result["completeness"] = 0.0
        result["correctness"] = 0.0
        return result
    present = [fact_present(answer, alternatives) for alternatives in required_facts]
    result["completeness"] = sum(present) / len(present) if present else 1.0
    result["correctness"] = 1.0 if all(present) and not forbidden_hit else 0.0
    if cited_chunk_ids:
        result["citation_precision"] = (
            sum(1 for c in cited_chunk_ids if c in relevant) / len(cited_chunk_ids)
            if relevant
            else None
        )
    if claims:
        result["faithfulness"] = sum(1 for c in claims if c.get("supported")) / len(claims)
    return result


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile; ``None`` for empty input."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def mean(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None
