"""Agent state (checkpointed per conversation) and run context (not checkpointed).

Short-term memory is ``turns``: the last few question/answer pairs of the thread, kept by the
LangGraph PostgreSQL checkpointer. Everything else in ``AgentState`` is per-run and is reset by
the first node of every run.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict

from app.providers.base import ChatProvider
from app.retrieval.service import Retriever
from app.retrieval.types import RetrievalConfig

Action = Literal[
    "retrieve", "direct", "clarify", "calculate", "list_documents", "summarize_document"
]
Outcome = Literal["answered", "abstained", "clarification", "error"]

MAX_TURNS_KEPT = 20


class Turn(TypedDict):
    role: Literal["user", "assistant"]
    content: str


def keep_recent_turns(existing: list[Turn] | None, new: list[Turn] | None) -> list[Turn]:
    """Reducer: append new turns and keep a bounded window."""
    return [*(existing or []), *(new or [])][-MAX_TURNS_KEPT:]


class AgentState(TypedDict, total=False):
    # Inputs for the current run
    question: str
    collection_ids: list[str]
    # Short-term memory (persists across runs in the same thread)
    turns: Annotated[list[Turn], keep_recent_turns]
    # Planning
    action: Action
    search_query: str
    clarification_question: str
    expression: str
    document_title: str
    route_source: str  # "rules", "llm", "llm_fallback", or "degraded"
    next_step: str  # set by validate_answer: "generate" (retry), "abstain", or "finalize"
    # Retrieval
    retrieval_attempts: int
    evidence: list[dict[str, Any]]
    sufficiency: dict[str, Any]
    retrieval_log: list[dict[str, Any]]
    suspicious_chunk_ids: list[str]
    # Generation
    llm_calls: int
    draft: str
    citation_retry: bool
    answer: str
    citations: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    outcome: Outcome | None
    error: dict[str, Any] | None
    # Telemetry
    trace: list[dict[str, Any]]
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    provider_failovers: list[dict[str, str]]
    run_id: str
    assistant_message_id: str


@dataclass
class AgentContext:
    """Per-run dependencies, injected via LangGraph's runtime context."""

    tenant_id: uuid.UUID
    chat: ChatProvider
    retriever: Retriever
    retrieval_config: RetrievalConfig
    max_retrieval_attempts: int
    max_llm_calls: int
    history_turns: int
    max_question_chars: int
    # Tenant-bound tool and persistence callbacks supplied by the chat service.
    retrieve_session: Callable[[], Any]
    list_documents: Callable[[], Awaitable[list[dict[str, Any]]]]
    find_document_chunks: Callable[[str], Awaitable[tuple[str | None, list[Any]]]]
    persist: Callable[[AgentState], Awaitable[dict[str, str]]]
