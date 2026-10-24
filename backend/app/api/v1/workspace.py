"""Workspace-level data deletion (the "delete everything" retention control)."""

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from app.agent.runtime import get_agent
from app.api.deps import SessionDep, require_role
from app.auth.security import Principal
from app.core.errors import BadRequestError
from app.core.logging import get_logger
from app.db.models.conversation import Conversation
from app.db.models.tenancy import MembershipRole, Tenant
from app.services.common import audit

router = APIRouter(prefix="/workspace", tags=["workspace"])
logger = get_logger("atlas.api.workspace")
OwnerDep = Depends(require_role(MembershipRole.OWNER))


class DeleteWorkspaceRequest(BaseModel):
    confirm_name: str


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    body: DeleteWorkspaceRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = OwnerDep,
) -> None:
    """Permanently delete the workspace: collections, documents, chunks and vectors,
    conversations, runs, evaluations, feedback, memberships, and conversation memory.
    The audit trail keeps an anonymized record (tenant reference set to NULL)."""
    tenant = await session.get(Tenant, principal.tenant_id)
    if tenant is None or body.confirm_name.strip() != tenant.name:
        raise BadRequestError("confirm_name must match the workspace name exactly")
    thread_ids = [
        str(cid)
        for cid in await session.scalars(
            select(Conversation.id).where(Conversation.tenant_id == tenant.id)
        )
    ]
    audit(
        session,
        action="workspace.delete",
        principal=None,
        request_id=request.state.request_id,
        details={"tenant_id": str(tenant.id), "conversations": len(thread_ids)},
    )
    await session.delete(tenant)  # every tenant-owned table cascades from tenants.id
    await session.commit()
    try:
        agent = await get_agent(request.app)
        for thread_id in thread_ids:
            await agent.delete_thread(thread_id)
    except Exception as exc:
        logger.warning("workspace_checkpoint_cleanup_failed", error_type=type(exc).__name__)
