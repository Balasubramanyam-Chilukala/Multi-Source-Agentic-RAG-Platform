"""Chat orchestration: conversation records, the agent run, persistence, and streaming.

The request's own DB session is used only to validate input and record the user message; it is
committed before the agent starts. The agent run uses short-lived sessions of its own, so a
streaming response (or a client disconnect) never holds a request transaction open, and the
answer is persisted even if the client goes away mid-stream.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.runtime import AgentRuntime
from app.agent.state import AgentContext, AgentState
from app.auth.security import Principal
from app.core.config import Settings
from app.core.errors import BadRequestError, NotFoundError
from app.core.logging import get_logger
from app.core.text import content_terms
from app.db.models.conversation import AgentRun, Conversation, Message, MessageRole, RunOutcome
from app.db.models.knowledge import Chunk, Collection, Document, DocumentStatus
from app.providers.base import ChatProvider
from app.retrieval.service import Retriever
from app.retrieval.types import EvidenceChunk, RetrievalConfig
from app.schemas.chat import ChatResponse, CitationOut, MessageOut, RunSummary

logger = get_logger("atlas.chat")

SUMMARY_CHUNK_LIMIT = 8
EVIDENCE_TEXT_LIMIT = 600


@dataclass
class PreparedChat:
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    collection_ids: list[uuid.UUID]
    question: str


@dataclass
class ChatDependencies:
    sessionmaker: async_sessionmaker[AsyncSession]
    agent: AgentRuntime
    chat: ChatProvider
    retriever: Retriever
    retrieval_config: RetrievalConfig
    settings: Settings


def _title_from(question: str) -> str:
    title = " ".join(question.split())
    return title if len(title) <= 80 else title[:77].rsplit(" ", 1)[0] + "…"


async def prepare_chat(
    session: AsyncSession,
    principal: Principal,
    *,
    message: str,
    conversation_id: uuid.UUID | None,
    collection_ids: list[uuid.UUID] | None,
) -> PreparedChat:
    """Validate scope, create/load the conversation, and store the user's message."""
    query = select(Collection.id).where(Collection.tenant_id == principal.tenant_id)
    if collection_ids:
        query = query.where(Collection.id.in_(collection_ids))
    else:
        # Managed collections (the evaluation corpus) are only searched when asked for explicitly.
        query = query.where(Collection.settings["managed"].astext.is_(None))
    allowed = list(await session.scalars(query))
    if collection_ids and len(allowed) != len(set(collection_ids)):
        raise BadRequestError("One or more collections were not found")

    conversation: Conversation | None
    if conversation_id is None:
        conversation = Conversation(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            collection_id=collection_ids[0]
            if collection_ids and len(collection_ids) == 1
            else None,
            title=_title_from(message),
        )
        session.add(conversation)
        await session.flush()
    else:
        conversation = await session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == principal.tenant_id,
                Conversation.user_id == principal.user_id,  # conversations are private per user
            )
        )
        if conversation is None:
            raise NotFoundError("Conversation not found")

    user_message = Message(
        tenant_id=principal.tenant_id,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=message,
    )
    session.add(user_message)
    conversation.last_message_at = datetime.now(UTC)
    await session.flush()
    return PreparedChat(conversation.id, user_message.id, allowed, message)


def _evidence_reference(item: dict[str, Any]) -> dict[str, Any]:
    """What is stored about a retrieved passage: identifiers, location, scores — no text."""
    keys = (
        "chunk_id",
        "document_id",
        "document_title",
        "page_number",
        "section",
        "source_uri",
        "rank",
        "dense_score",
        "lexical_score",
        "fused_score",
        "rerank_score",
        "suspicious",
    )
    return {k: item.get(k) for k in keys}


def _citation_reference(citation: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in citation.items() if k != "snippet"}


class ChatRunner:
    def __init__(
        self, deps: ChatDependencies, principal: Principal, request_id: str | None
    ) -> None:
        self.deps = deps
        self.principal = principal
        self.request_id = request_id

    def _context(self, prepared: PreparedChat, started: float) -> AgentContext:
        deps, tenant_id = self.deps, self.principal.tenant_id
        collection_ids = prepared.collection_ids

        @asynccontextmanager
        async def retrieve_session() -> AsyncIterator[AsyncSession]:
            async with deps.sessionmaker() as session:
                yield session

        async def list_documents() -> list[dict[str, Any]]:
            async with deps.sessionmaker() as session:
                rows = await session.execute(
                    select(Document.title, Document.status, Document.chunk_count)
                    .where(
                        Document.tenant_id == tenant_id, Document.collection_id.in_(collection_ids)
                    )
                    .order_by(Document.title)
                )
                return [
                    {"title": r.title, "status": r.status.value, "chunk_count": r.chunk_count}
                    for r in rows
                ]

        async def find_document_chunks(title: str) -> tuple[str | None, list[EvidenceChunk]]:
            wanted = set(content_terms(title))
            async with deps.sessionmaker() as session:
                documents = list(
                    await session.scalars(
                        select(Document).where(
                            Document.tenant_id == tenant_id,
                            Document.collection_id.in_(collection_ids),
                            Document.status == DocumentStatus.READY,
                        )
                    )
                )
                if not documents or not wanted:
                    return None, []
                scored = [
                    (len(wanted & set(content_terms(d.title))) / len(wanted), d) for d in documents
                ]
                score, best = max(scored, key=lambda item: (item[0], item[1].title))
                if score < 0.5:
                    return None, []
                chunks = await session.scalars(
                    select(Chunk)
                    .where(Chunk.document_id == best.id)
                    .order_by(Chunk.ordinal)
                    .limit(SUMMARY_CHUNK_LIMIT)
                )
                return best.title, [
                    EvidenceChunk(
                        chunk_id=c.id,
                        document_id=best.id,
                        collection_id=best.collection_id,
                        document_title=best.title,
                        source_uri=best.source_uri,
                        text=c.text,
                        ordinal=c.ordinal,
                        page_number=c.page_number,
                        section=c.section,
                        rank=i,
                    )
                    for i, c in enumerate(chunks, start=1)
                ]

        async def persist(state: AgentState) -> dict[str, str]:
            return await self._persist(prepared, state, started)

        return AgentContext(
            tenant_id=tenant_id,
            chat=deps.chat,
            retriever=deps.retriever,
            retrieval_config=deps.retrieval_config,
            max_retrieval_attempts=deps.settings.agent_max_retrieval_attempts,
            max_llm_calls=deps.settings.agent_max_llm_calls,
            history_turns=deps.settings.agent_history_turns,
            max_question_chars=deps.settings.max_question_chars,
            retrieve_session=retrieve_session,
            list_documents=list_documents,
            find_document_chunks=find_document_chunks,
            persist=persist,
        )

    async def _persist(
        self, prepared: PreparedChat, state: AgentState, started: float
    ) -> dict[str, str]:
        outcome = state.get("outcome") or "error"
        error = state.get("error")
        evidence = state.get("evidence", [])
        async with self.deps.sessionmaker() as session, session.begin():
            run = AgentRun(
                tenant_id=self.principal.tenant_id,
                conversation_id=prepared.conversation_id,
                user_id=self.principal.user_id,
                request_id=self.request_id,
                provider=state.get("provider"),
                model=state.get("model"),
                retrieval_config=self.deps.retrieval_config.as_dict(),
                node_trace=state.get("trace", []),
                retrieved_chunk_ids=[str(e["chunk_id"]) for e in evidence],
                retrieval_attempts=state.get("retrieval_attempts", 0),
                generation_calls=state.get("llm_calls", 0),
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                prompt_tokens=state.get("prompt_tokens") or None,
                completion_tokens=state.get("completion_tokens") or None,
                outcome=RunOutcome(outcome),
                error_category=(error or {}).get("category"),
                details={
                    "retrieval_attempts": state.get("retrieval_log", []),
                    "final_sufficiency": state.get("sufficiency", {}),
                    "suspicious_chunk_ids": state.get("suspicious_chunk_ids", []),
                    "action": state.get("action"),
                    "route_source": state.get("route_source"),
                    "provider_failovers": state.get("provider_failovers", []),
                    "claims_total": len(state.get("claims", [])),
                    "claims_supported": sum(
                        1 for c in state.get("claims", []) if c.get("supported")
                    ),
                    "error": error,
                },
            )
            session.add(run)
            await session.flush()
            message = Message(
                tenant_id=self.principal.tenant_id,
                conversation_id=prepared.conversation_id,
                role=MessageRole.ASSISTANT,
                content=state.get("answer", ""),
                citations=[_citation_reference(c) for c in state.get("citations", [])],
                meta={
                    "outcome": outcome,
                    "claims": state.get("claims", []),
                    "provider": state.get("provider"),
                    "model": state.get("model"),
                    "evidence": [_evidence_reference(e) for e in evidence],
                    "error": error,
                },
                run_id=run.id,
            )
            session.add(message)
            conversation = await session.get(Conversation, prepared.conversation_id)
            if conversation is not None:
                conversation.last_message_at = datetime.now(UTC)
            await session.flush()
            return {"run_id": str(run.id), "message_id": str(message.id)}

    async def run(
        self, prepared: PreparedChat, on_event: Callable[[dict[str, Any]], None] | None = None
    ) -> ChatResponse:
        started = time.perf_counter()
        context = self._context(prepared, started)
        config: RunnableConfig = {
            "configurable": {"thread_id": str(prepared.conversation_id)},
            "recursion_limit": 50,
        }
        inputs: dict[str, Any] = {
            "question": prepared.question,
            "collection_ids": [str(c) for c in prepared.collection_ids],
        }
        final: dict[str, Any] = {}
        try:
            async with asyncio.timeout(self.deps.settings.agent_timeout_seconds):
                async for mode, payload in self.deps.agent.graph.astream(
                    inputs, config, context=context, stream_mode=["custom", "values"]
                ):
                    # With a list of stream modes LangGraph yields (mode, payload) tuples.
                    if not isinstance(payload, dict):
                        continue
                    if mode == "values":
                        final = payload
                    elif on_event is not None:
                        on_event(payload)
        except TimeoutError:
            logger.warning("agent_timeout", conversation_id=str(prepared.conversation_id))
            final = await self._persist_failure(
                prepared,
                final,
                started,
                "timeout",
                "The answer took too long to generate. Please try again.",
            )
        except Exception as exc:
            logger.exception("agent_run_failed", error_type=type(exc).__name__)
            final = await self._persist_failure(
                prepared,
                final,
                started,
                "internal_error",
                "Something went wrong while answering. Please try again.",
            )
        if not final.get("run_id"):
            # The graph's persist step failed (e.g. a transient DB error): try once more, and let
            # a second failure surface as a 500 rather than return an answer that wasn't saved.
            final = await self._persist_failure(
                prepared,
                final,
                started,
                "persistence_error",
                "The answer could not be saved. Please try again.",
            )
        return self._response(prepared, final, started)

    async def _persist_failure(
        self,
        prepared: PreparedChat,
        partial: dict[str, Any],
        started: float,
        category: str,
        message: str,
    ) -> dict[str, Any]:
        state: dict[str, Any] = {
            **partial,
            "outcome": "error",
            "answer": message,
            "error": {"category": category, "message": message},
            "citations": [],
            "claims": [],
        }
        if partial.get("run_id"):  # already persisted by the graph before the failure
            return state
        ids = await self._persist(prepared, state, started)  # type: ignore[arg-type]
        return {**state, "run_id": ids["run_id"], "assistant_message_id": ids["message_id"]}

    def _response(
        self, prepared: PreparedChat, final: dict[str, Any], started: float
    ) -> ChatResponse:
        evidence = [
            {**e, "text": str(e.get("text", ""))[:EVIDENCE_TEXT_LIMIT]}
            for e in final.get("evidence", [])
        ]
        outcome = final.get("outcome") or "error"
        message_id = final.get("assistant_message_id")
        return ChatResponse(
            conversation_id=prepared.conversation_id,
            user_message_id=prepared.user_message_id,
            message=MessageOut(
                id=uuid.UUID(message_id) if message_id else uuid.uuid4(),
                role=MessageRole.ASSISTANT,
                content=final.get("answer", ""),
                citations=[CitationOut(**c) for c in final.get("citations", [])],
                meta={
                    "outcome": outcome,
                    "claims": final.get("claims", []),
                    "provider": final.get("provider"),
                    "model": final.get("model"),
                    "error": final.get("error"),
                },
                run_id=uuid.UUID(final["run_id"]) if final.get("run_id") else None,
                created_at=datetime.now(UTC),
            ),
            run=RunSummary(
                id=uuid.UUID(final["run_id"]) if final.get("run_id") else None,
                outcome=RunOutcome(outcome),
                provider=final.get("provider"),
                model=final.get("model"),
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                retrieval_attempts=final.get("retrieval_attempts", 0),
                llm_calls=final.get("llm_calls", 0),
                prompt_tokens=final.get("prompt_tokens") or None,
                completion_tokens=final.get("completion_tokens") or None,
                error=final.get("error"),
            ),
            evidence=evidence,
        )


async def count_messages(session: AsyncSession, conversation_id: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        or 0
    )
