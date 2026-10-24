"""Evaluation runs and their per-case results."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.knowledge import JobStatus
from app.db.types import str_enum


class EvaluationMode(StrEnum):
    DENSE = "dense"
    LEXICAL = "lexical"
    HYBRID = "hybrid"
    HYBRID_RERANK = "hybrid_rerank"


class EvaluationRun(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "evaluation_runs"

    dataset_name: Mapped[str] = mapped_column(String(200))
    dataset_version: Mapped[str] = mapped_column(String(50))
    retrieval_mode: Mapped[EvaluationMode] = mapped_column(
        str_enum(EvaluationMode, "evaluation_mode")
    )
    # Full experiment configuration (models, top-k, thresholds) for reproducibility.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "evaluation_status"), default=JobStatus.QUEUED
    )
    # Aggregates, e.g. recall@k, MRR, nDCG, latency percentiles; model-judged scores are labelled.
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationCaseResult(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "evaluation_case_results"
    __table_args__ = (UniqueConstraint("evaluation_run_id", "case_id"),)

    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[str] = mapped_column(String(100))
    retrieved_chunk_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    retrieval_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    answer_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    answer: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    error_message: Mapped[str | None] = mapped_column(Text)
