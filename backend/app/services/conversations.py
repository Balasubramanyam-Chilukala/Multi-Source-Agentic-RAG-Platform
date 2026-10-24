"""Conversation history, feedback, and run inspection. All queries are tenant-scoped, and
conversations are additionally private to the user who created them."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import Principal
from app.core.errors import BadRequestError, NotFoundError
from app.db.models.conversation import AgentRun, Conversation, Feedback, Message, MessageRole
from app.db.models.knowledge import Chunk
from app.db.models.tenancy import MembershipRole
from app.schemas.chat import (
    CitationOut,
    ConversationDetail,
    ConversationOut,
    FeedbackCreate,
    FeedbackOut,
    MessageOut,
    RunDetail,
    RunOut,
)
from app.schemas.common import Page
from app.services.common import audit

SNIPPET_CHARS = 320


async def _own_conversation(
    session: AsyncSession, principal: Principal, conversation_id: uuid.UUID
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == principal.tenant_id,
            Conversation.user_id == principal.user_id,
        )
    )
    if conversation is None:
        raise NotFoundError("Conversation not found")
    return conversation


async def list_conversations(
    session: AsyncSession, principal: Principal, limit: int, offset: int
) -> Page[ConversationOut]:
    scope = (
        Conversation.tenant_id == principal.tenant_id,
        Conversation.user_id == principal.user_id,
    )
    total = await session.scalar(select(func.count()).select_from(Conversation).where(*scope))
    counts = (
        select(Message.conversation_id, func.count(Message.id).label("n"))
        .where(Message.tenant_id == principal.tenant_id)
        .group_by(Message.conversation_id)
        .subquery()
    )
    rows = await session.execute(
        select(Conversation, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.conversation_id == Conversation.id)
        .where(*scope)
        .order_by(func.coalesce(Conversation.last_message_at, Conversation.created_at).desc())
        .limit(limit)
        .offset(offset)
    )
    items = []
    for conversation, count in rows:
        out = ConversationOut.model_validate(conversation)
        out.message_count = count
        items.append(out)
    return Page(items=items, total=total or 0, limit=limit, offset=offset)


async def _hydrate_citations(
    session: AsyncSession, principal: Principal, messages: list[Message]
) -> dict[str, str]:
    ids = {uuid.UUID(c["chunk_id"]) for m in messages for c in m.citations if c.get("chunk_id")}
    if not ids:
        return {}
    rows = await session.execute(
        select(Chunk.id, Chunk.text).where(
            Chunk.id.in_(ids), Chunk.tenant_id == principal.tenant_id
        )
    )
    return {str(cid): " ".join(text.split())[:SNIPPET_CHARS] for cid, text in rows}


def message_out(
    message: Message, snippets: dict[str, str], feedback: dict[uuid.UUID, int]
) -> MessageOut:
    citations = []
    for citation in message.citations:
        snippet = snippets.get(str(citation.get("chunk_id")))
        citations.append(
            CitationOut(**{**citation, "snippet": snippet, "source_deleted": snippet is None})
        )
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        citations=citations,
        meta=message.meta,
        run_id=message.run_id,
        created_at=message.created_at,
        feedback=feedback.get(message.id),
    )


async def get_conversation(
    session: AsyncSession, principal: Principal, conversation_id: uuid.UUID
) -> ConversationDetail:
    conversation = await _own_conversation(session, principal, conversation_id)
    messages = list(
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at, Message.id)
        )
    )
    snippets = await _hydrate_citations(session, principal, messages)
    feedback_rows = await session.execute(
        select(Feedback.message_id, Feedback.rating).where(
            Feedback.user_id == principal.user_id, Feedback.message_id.in_([m.id for m in messages])
        )
    )
    feedback = {mid: rating for mid, rating in feedback_rows}
    base = ConversationOut.model_validate(conversation)
    return ConversationDetail(
        **base.model_dump(exclude={"message_count"}),
        message_count=len(messages),
        messages=[message_out(m, snippets, feedback) for m in messages],
    )


async def rename_conversation(
    session: AsyncSession, principal: Principal, conversation_id: uuid.UUID, title: str
) -> ConversationOut:
    conversation = await _own_conversation(session, principal, conversation_id)
    conversation.title = title.strip()[:300]
    await session.flush()
    return ConversationOut.model_validate(conversation)


async def delete_conversation(
    session: AsyncSession, principal: Principal, conversation_id: uuid.UUID, request_id: str | None
) -> None:
    conversation = await _own_conversation(session, principal, conversation_id)
    await session.delete(
        conversation
    )  # messages and feedback cascade; runs keep telemetry, unlinked
    audit(
        session,
        action="conversation.delete",
        principal=principal,
        resource_type="conversation",
        resource_id=conversation_id,
        request_id=request_id,
    )


async def submit_feedback(
    session: AsyncSession, principal: Principal, data: FeedbackCreate
) -> FeedbackOut:
    message = await session.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.id == data.message_id,
            Message.tenant_id == principal.tenant_id,
            Conversation.user_id == principal.user_id,
        )
    )
    if message is None:
        raise NotFoundError("Message not found")
    if message.role != MessageRole.ASSISTANT:
        raise BadRequestError("Feedback can only be given on assistant answers")
    statement = (
        insert(Feedback)
        .values(
            id=uuid.uuid4(),
            tenant_id=principal.tenant_id,
            message_id=message.id,
            user_id=principal.user_id,
            rating=data.rating,
            comment=data.comment,
        )
        .on_conflict_do_update(
            index_elements=["message_id", "user_id"],
            set_={"rating": data.rating, "comment": data.comment, "updated_at": func.now()},
        )
        .returning(Feedback)
    )
    feedback = (await session.scalars(statement)).one()
    return FeedbackOut.model_validate(feedback)


def _run_scope(principal: Principal) -> list[Any]:
    scope: list[Any] = [AgentRun.tenant_id == principal.tenant_id]
    if not principal.has_role(MembershipRole.ADMIN):
        scope.append(AgentRun.user_id == principal.user_id)  # members see only their own runs
    return scope


async def list_runs(
    session: AsyncSession, principal: Principal, *, outcome: str | None, limit: int, offset: int
) -> Page[RunOut]:
    scope = _run_scope(principal)
    if outcome:
        scope.append(AgentRun.outcome == outcome)
    total = await session.scalar(select(func.count()).select_from(AgentRun).where(*scope))
    rows = await session.scalars(
        select(AgentRun)
        .where(*scope)
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[RunOut.model_validate(r) for r in rows], total=total or 0, limit=limit, offset=offset
    )


async def get_run(session: AsyncSession, principal: Principal, run_id: uuid.UUID) -> RunDetail:
    run = await session.scalar(
        select(AgentRun).where(AgentRun.id == run_id, *_run_scope(principal))
    )
    if run is None:
        raise NotFoundError("Run not found")
    return RunDetail.model_validate(run)
