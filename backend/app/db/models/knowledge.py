"""Collections, documents, ingestion jobs, chunks, and the embedding model registry."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import str_enum


class SourceType(StrEnum):
    UPLOAD = "upload"
    URL = "url"
    TEXT = "text"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    # Parsed successfully but no extractable text (e.g. a scanned PDF that needs OCR).
    UNSUPPORTED = "unsupported"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EmbeddingModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Registry of embedding models; every stored vector references the model that produced it.

    Vectors of different dimensions never share an index: chunk rows record their dimension, a
    CHECK constraint ties it to the actual vector, and ANN indexes are created per model.
    """

    __tablename__ = "embedding_models"
    __table_args__ = (
        UniqueConstraint("provider", "model", "version"),
        CheckConstraint("dimension > 0 AND dimension <= 16000", name="dimension_range"),
    )

    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(50), default="1", server_default="1")
    dimension: Mapped[int] = mapped_column(Integer)


class Collection(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    active_embedding_model_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("embedding_models.id", ondelete="RESTRICT")
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class Document(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("collection_id", "content_hash"),
        Index("ix_documents_tenant_id_status", "tenant_id", "status"),
    )

    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[SourceType] = mapped_column(str_enum(SourceType, "source_type"))
    source_uri: Mapped[str | None] = mapped_column(String(2048))
    mime_type: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    # SHA-256 hex digest of the raw content, used for duplicate detection within a collection.
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[DocumentStatus] = mapped_column(
        str_enum(DocumentStatus, "document_status"), default=DocumentStatus.PENDING
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class IngestionJob(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "ingestion_jobs"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "job_status"), default=JobStatus.QUEUED
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Chunk(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal"),
        CheckConstraint(
            "(embedding IS NULL AND embedding_model_id IS NULL AND embedding_dim IS NULL)"
            " OR (embedding IS NOT NULL AND embedding_model_id IS NOT NULL"
            " AND vector_dims(embedding) = embedding_dim)",
            name="embedding_consistency",
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_chunks_tenant_id_collection_id", "tenant_id", "collection_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # Denormalized from the document so retrieval can filter by collection without a join.
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(500))
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}"
    )
    # PostgreSQL full-text search vector (ts_rank-based lexical retrieval, not BM25).
    # Section headings are weighted above body text ('A' vs 'B') in ts_rank_cd.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(section, '')), 'A')"
            " || setweight(to_tsvector('english', text), 'B')",
            persisted=True,
        ),
    )
    # Dimensionless column; per-model HNSW indexes cast to vector(N) with a partial predicate.
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    embedding_model_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("embedding_models.id", ondelete="RESTRICT"), index=True
    )
    embedding_dim: Mapped[int | None] = mapped_column(Integer)


class DocumentSource(Base):
    """Raw uploaded bytes, kept only until ingestion succeeds so failed jobs can be retried.

    Stored in PostgreSQL because free-tier hosts have ephemeral filesystems. Cleared after a
    successful ingestion (data minimization); URL documents are re-fetched on retry instead.
    """

    __tablename__ = "document_sources"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    filename: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[bytes] = mapped_column(BYTEA)
