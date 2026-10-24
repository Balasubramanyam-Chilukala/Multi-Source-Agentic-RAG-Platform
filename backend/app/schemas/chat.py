import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.db.models.conversation import MessageRole, RunOutcome
from app.schemas.common import ORMModel


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    conversation_id: uuid.UUID | None = None
    collection_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)


class CitationOut(BaseModel):
    marker: str
    chunk_id: str
    document_id: str
    document_title: str
    page_number: int | None = None
    section: str | None = None
    source_uri: str | None = None
    snippet: str | None = None
    source_deleted: bool = False


class MessageOut(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    citations: list[CitationOut] = []
    meta: dict[str, Any] = Field(default_factory=dict, serialization_alias="metadata")
    run_id: uuid.UUID | None = None
    created_at: datetime
    feedback: int | None = None


class RunSummary(BaseModel):
    id: uuid.UUID | None
    outcome: RunOutcome | None
    provider: str | None
    model: str | None
    latency_ms: float | None
    retrieval_attempts: int
    llm_calls: int
    prompt_tokens: int | None
    completion_tokens: int | None
    error: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    message: MessageOut
    run: RunSummary
    evidence: list[dict[str, Any]]


class ConversationOut(ORMModel):
    id: uuid.UUID
    title: str
    collection_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None
    message_count: int = 0


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class FeedbackCreate(BaseModel):
    message_id: uuid.UUID
    rating: Literal[-1, 1] = Field(description="1 for thumbs up, -1 for thumbs down")
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackOut(ORMModel):
    id: uuid.UUID
    message_id: uuid.UUID
    rating: int
    comment: str | None
    created_at: datetime


class RunOut(ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID | None
    request_id: str | None
    provider: str | None
    model: str | None
    outcome: RunOutcome | None
    error_category: str | None
    latency_ms: float | None
    retrieval_attempts: int
    generation_calls: int
    prompt_tokens: int | None
    completion_tokens: int | None
    created_at: datetime


class RunDetail(RunOut):
    retrieval_config: dict[str, Any]
    node_trace: list[dict[str, Any]]
    retrieved_chunk_ids: list[str]
    details: dict[str, Any]
