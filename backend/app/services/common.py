"""Helpers shared by services: tenant-scoped lookups and audit logging."""

import uuid
from typing import Any, Protocol, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped

from app.auth.security import Principal
from app.core.errors import NotFoundError
from app.db.base import Base
from app.db.models.audit import AuditLog


class TenantOwned(Protocol):
    id: Mapped[uuid.UUID]
    tenant_id: Mapped[uuid.UUID]


async def get_owned[T: Base](
    session: AsyncSession, model: type[T], object_id: uuid.UUID, principal: Principal, label: str
) -> T:
    """Fetch a row by id *within the caller's tenant*.

    Rows from other tenants are reported as "not found" rather than "forbidden", so the API
    never confirms that another tenant's object exists.
    """
    row: T | None = await session.scalar(
        cast(Any, select(model)).where(
            model.id == object_id,  # type: ignore[attr-defined]
            model.tenant_id == principal.tenant_id,  # type: ignore[attr-defined]
        )
    )
    if row is None:
        raise NotFoundError(f"{label} not found")
    return row


def audit(
    session: AsyncSession,
    *,
    action: str,
    principal: Principal | None,
    tenant_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | str | None = None,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit record to the current transaction (committed with the action itself)."""
    session.add(
        AuditLog(
            tenant_id=principal.tenant_id if principal else tenant_id,
            actor_user_id=principal.user_id if principal else None,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            request_id=request_id,
            details=details or {},
        )
    )
