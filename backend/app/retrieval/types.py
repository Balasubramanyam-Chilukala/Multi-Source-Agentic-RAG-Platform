import uuid
from dataclasses import dataclass, field
from typing import Literal

RetrievalModeName = Literal["dense", "lexical", "hybrid", "hybrid_rerank"]


@dataclass(frozen=True)
class RetrievalConfig:
    mode: RetrievalModeName = "hybrid"
    top_k: int = 6
    candidate_k: int = 24
    rrf_k: int = 60
    # Weight of the lexical list in hybrid fusion (dense is 1.0); see retrieval/fusion.py.
    lexical_weight: float = 1.0
    min_dense_similarity: float = 0.35
    min_lexical_coverage: float = 0.5
    min_rerank_score: float = -4.0

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "top_k": self.top_k,
            "candidate_k": self.candidate_k,
            "rrf_k": self.rrf_k,
            "lexical_weight": self.lexical_weight,
            "min_dense_similarity": self.min_dense_similarity,
            "min_lexical_coverage": self.min_lexical_coverage,
            "min_rerank_score": self.min_rerank_score,
        }


@dataclass(frozen=True)
class RankedId:
    chunk_id: uuid.UUID
    score: float


@dataclass
class EvidenceChunk:
    """A retrieved passage with every signal that contributed to its rank."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    collection_id: uuid.UUID
    document_title: str
    source_uri: str | None
    text: str
    ordinal: int
    page_number: int | None
    section: str | None
    dense_score: float | None = None
    dense_rank: int | None = None
    lexical_score: float | None = None
    lexical_rank: int | None = None
    fused_score: float | None = None
    rerank_score: float | None = None
    rank: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "collection_id": str(self.collection_id),
            "document_title": self.document_title,
            "source_uri": self.source_uri,
            "text": self.text,
            "ordinal": self.ordinal,
            "page_number": self.page_number,
            "section": self.section,
            "dense_score": self.dense_score,
            "dense_rank": self.dense_rank,
            "lexical_score": self.lexical_score,
            "lexical_rank": self.lexical_rank,
            "fused_score": self.fused_score,
            "rerank_score": self.rerank_score,
            "rank": self.rank,
        }


@dataclass
class Sufficiency:
    sufficient: bool
    reason: str
    top_dense_similarity: float | None = None
    top_lexical_coverage: float | None = None
    top_rerank_score: float | None = None


@dataclass
class RetrievalResult:
    query: str
    config: RetrievalConfig
    chunks: list[EvidenceChunk]
    sufficiency: Sufficiency
    timings_ms: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    embedding_model: str | None = None
    reranker: str | None = None
