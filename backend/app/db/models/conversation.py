"""Long-term conversation persistence, agent run telemetry, and user feedback.

Short-term graph state lives in LangGraph's PostgreSQL checkpointer (Phase 3); these tables are
the application's durable record of what was asked, answered, and how.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import str_enum


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class RunOutcome(StrEnum):
    ANSWERED = "answered"
    ABSTAINED = "abstained"
    CLARIFICATION = "clarification"
    ERROR = "error"


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_tenant_id_user_id", "tenant_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("collections.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(300))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "agent_runs"

    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(200))
    retrieval_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    # Ordered list of executed graph nodes with per-node latency and status.
    node_trace: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    retrieved_chunk_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    retrieval_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    generation_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    latency_ms: Mapped[float | None] = mapped_column(Float)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[RunOutcome | None] = mapped_column(str_enum(RunOutcome, "run_outcome"))
    error_category: Mapped[str | None] = mapped_column(String(100))
    # Retrieval attempts (query, sufficiency, timings), reranking results, citation checks.
    # Holds identifiers and scores only, never passage text.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class Message(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[MessageRole] = mapped_column(str_enum(MessageRole, "message_role"))
    content: Mapped[str] = mapped_column(Text)
    # References only (chunk/document ids, titles, locations); snippets are loaded live so that
    # deleting a document also removes its text from conversation views.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    # Outcome, claim support labels, provider/model, and retrieved evidence references.
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}"
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )


class Feedback(UUIDPrimaryKeyMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id"),
        CheckConstraint("rating IN (-1, 1)", name="rating_values"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(Text)
