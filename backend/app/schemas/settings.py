"""Workspace (tenant) settings: user-adjustable behaviour, never secrets.

Stored in ``tenants.settings`` as a partial override of the server defaults. ``effective``
merges both so every consumer sees a complete, validated configuration.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import MOCK_CHAT_MODEL, RetrievalMode, Settings


class WorkspaceSettingsUpdate(BaseModel):
    """Fields a workspace admin may override. ``None`` means "use the server default"."""

    model_config = ConfigDict(extra="forbid")

    llm_provider: Literal["groq", "nvidia", "gemini", "openrouter", "mock"] | None = None
    llm_model: str | None = Field(default=None, max_length=200)
    retrieval_mode: RetrievalMode | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)
    chunk_size: int | None = Field(default=None, ge=200, le=4000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=1000)
    min_dense_similarity: float | None = Field(default=None, ge=-1, le=1)
    evaluation_dataset: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _overlap(self) -> Self:
        size, overlap = self.chunk_size, self.chunk_overlap
        if size is not None and overlap is not None and overlap * 2 > size:
            raise ValueError("chunk_overlap must be at most half of chunk_size")
        return self


class WorkspaceSettings(BaseModel):
    llm_provider: str
    llm_model: str
    retrieval_mode: RetrievalMode
    top_k: int
    chunk_size: int
    chunk_overlap: int
    min_dense_similarity: float
    evaluation_dataset: str

    @classmethod
    def effective(cls, server: Settings, overrides: dict[str, object]) -> "WorkspaceSettings":
        valid = WorkspaceSettingsUpdate.model_validate(
            {k: v for k, v in overrides.items() if k in WorkspaceSettingsUpdate.model_fields}
        )
        provider = valid.llm_provider or server.llm_provider
        # A model override only makes sense for the provider it was chosen with.
        model = valid.llm_model or (server.llm_model if provider == server.llm_provider else "")
        if provider == "mock":
            model = MOCK_CHAT_MODEL
        elif not model:
            model = server.llm_model
        size = valid.chunk_size or server.chunk_size
        overlap = valid.chunk_overlap if valid.chunk_overlap is not None else server.chunk_overlap
        return cls(
            llm_provider=provider,
            llm_model=model,
            retrieval_mode=valid.retrieval_mode or server.retrieval_mode,
            top_k=valid.top_k or server.retrieval_top_k,
            chunk_size=size,
            chunk_overlap=min(overlap, size // 2),
            min_dense_similarity=(
                valid.min_dense_similarity
                if valid.min_dense_similarity is not None
                else server.min_dense_similarity
            ),
            evaluation_dataset=valid.evaluation_dataset or "atlas-handbook-v1",
        )


class ProviderStatus(BaseModel):
    name: str
    configured: bool
    capabilities: list[str]


class SystemInfo(BaseModel):
    """Read-only server configuration shown in the Settings screen. Contains no secrets."""

    environment: str
    demo_mode: bool
    default_llm_provider: str
    default_llm_model: str
    # Ordered "provider:model" fallbacks tried when the selected model is unavailable.
    fallback_chain: list[str]
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    reranker_provider: str
    reranker_model: str | None
    providers: list[ProviderStatus]
    max_upload_bytes: int
    agent_max_retrieval_attempts: int
    agent_max_llm_calls: int
