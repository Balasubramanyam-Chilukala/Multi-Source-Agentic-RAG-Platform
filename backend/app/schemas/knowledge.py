import uuid
from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from app.db.models.knowledge import DocumentStatus, JobStatus, SourceType
from app.schemas.common import ORMModel


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class CollectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class CollectionOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    document_count: int = 0
    ready_document_count: int = 0
    chunk_count: int = 0
    active_embedding_model: str | None = None


class IngestionJobOut(ORMModel):
    id: uuid.UUID
    status: JobStatus
    attempt: int
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class DocumentOut(ORMModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    title: str
    source_type: SourceType
    source_uri: str | None
    mime_type: str | None
    size_bytes: int | None
    content_hash: str
    status: DocumentStatus
    error_message: str | None
    page_count: int | None
    chunk_count: int
    meta: dict[str, Any] = Field(serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentOut):
    jobs: list[IngestionJobOut] = Field(default_factory=list)


class TextDocumentCreate(BaseModel):
    collection_id: uuid.UUID
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=2_000_000)
    format: str = Field(default="text", pattern="^(text|markdown)$")


class UrlDocumentCreate(BaseModel):
    collection_id: uuid.UUID
    url: AnyHttpUrl
    title: str | None = Field(default=None, max_length=500)


class ChunkOut(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    text: str
    page_number: int | None
    section: str | None
    token_count: int | None
