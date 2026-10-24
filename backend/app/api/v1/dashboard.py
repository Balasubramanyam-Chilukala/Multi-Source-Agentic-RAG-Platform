"""Workspace overview for the dashboard screen."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import PrincipalDep, SessionDep
from app.db.models.audit import AuditLog
from app.db.models.conversation import AgentRun, Conversation
from app.db.models.knowledge import Collection, Document
from app.db.models.tenancy import MembershipRole

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Audit actions that are useful as an activity feed (login events are left out).
ACTIVITY_ACTIONS = (
    "collection.create",
    "collection.delete",
    "document.create",
    "document.delete",
    "document.retry",
    "settings.update",
    "evaluation.run",
    "conversation.delete",
)


class DashboardOut(BaseModel):
    collections: int
    documents: int
    documents_by_status: dict[str, int]
    chunks: int
    conversations: int
    runs_last_7_days: dict[str, int]
    recent_conversations: list[dict[str, Any]]
    recent_documents: list[dict[str, Any]]
    recent_activity: list[dict[str, Any]]


@router.get("", response_model=DashboardOut)
async def dashboard(session: SessionDep, principal: PrincipalDep) -> DashboardOut:
    tenant = principal.tenant_id
    collections = await session.scalar(
        select(func.count()).select_from(Collection).where(Collection.tenant_id == tenant)
    )
    status_rows = await session.execute(
        select(Document.status, func.count(), func.coalesce(func.sum(Document.chunk_count), 0))
        .where(Document.tenant_id == tenant)
        .group_by(Document.status)
    )
    by_status: dict[str, int] = {}
    chunks = 0
    for status, count, chunk_sum in status_rows:
        by_status[status.value] = count
        chunks += int(chunk_sum)
    conversations = await session.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(Conversation.tenant_id == tenant, Conversation.user_id == principal.user_id)
    )
    since = datetime.now(UTC) - timedelta(days=7)
    run_scope = [AgentRun.tenant_id == tenant, AgentRun.created_at >= since]
    if not principal.has_role(MembershipRole.ADMIN):
        run_scope.append(AgentRun.user_id == principal.user_id)
    runs = {
        (outcome.value if outcome else "unknown"): count
        for outcome, count in await session.execute(
            select(AgentRun.outcome, func.count()).where(*run_scope).group_by(AgentRun.outcome)
        )
    }
    recent_conversations = [
        {"id": str(c.id), "title": c.title, "last_message_at": c.last_message_at or c.created_at}
        for c in await session.scalars(
            select(Conversation)
            .where(Conversation.tenant_id == tenant, Conversation.user_id == principal.user_id)
            .order_by(func.coalesce(Conversation.last_message_at, Conversation.created_at).desc())
            .limit(5)
        )
    ]
    recent_documents = [
        {
            "id": str(d.id),
            "title": d.title,
            "status": d.status.value,
            "collection_id": str(d.collection_id),
            "chunk_count": d.chunk_count,
            "updated_at": d.updated_at,
        }
        for d in await session.scalars(
            select(Document)
            .where(Document.tenant_id == tenant)
            .order_by(Document.updated_at.desc())
            .limit(6)
        )
    ]
    recent_activity = [
        {
            "action": a.action,
            "resource_type": a.resource_type,
            "resource_id": a.resource_id,
            "created_at": a.created_at,
            "details": {
                k: v for k, v in a.details.items() if k in ("title", "name", "source_type")
            },
        }
        for a in await session.scalars(
            select(AuditLog)
            .where(AuditLog.tenant_id == tenant, AuditLog.action.in_(ACTIVITY_ACTIONS))
            .order_by(AuditLog.created_at.desc())
            .limit(10)
        )
    ]
    return DashboardOut(
        collections=collections or 0,
        documents=sum(by_status.values()),
        documents_by_status=by_status,
        chunks=chunks,
        conversations=conversations or 0,
        runs_last_7_days=runs,
        recent_conversations=recent_conversations,
        recent_documents=recent_documents,
        recent_activity=recent_activity,
    )
