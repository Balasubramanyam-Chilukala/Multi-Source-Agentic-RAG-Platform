"""Registration and login. Registration creates a new workspace (tenant) owned by the user."""

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import Principal, hash_password, verify_password
from app.core.errors import AuthenticationError, ConflictError, PermissionDeniedError
from app.db.models.tenancy import Membership, MembershipRole, Tenant, User
from app.services.common import audit


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "workspace"
    return f"{slug}-{uuid.uuid4().hex[:6]}"


async def register(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str,
    workspace_name: str,
    allow_registration: bool,
    request_id: str | None,
) -> Principal:
    if not allow_registration:
        raise PermissionDeniedError("Self-service registration is disabled")
    normalized = email.strip().lower()
    exists = await session.scalar(select(User.id).where(func.lower(User.email) == normalized))
    if exists is not None:
        raise ConflictError("An account with this email already exists")

    tenant = Tenant(name=workspace_name.strip(), slug=_slugify(workspace_name))
    user = User(
        email=normalized, display_name=display_name.strip(), password_hash=hash_password(password)
    )
    session.add_all([tenant, user])
    await session.flush()
    session.add(Membership(tenant_id=tenant.id, user_id=user.id, role=MembershipRole.OWNER))
    principal = Principal(user.id, tenant.id, MembershipRole.OWNER, normalized)
    audit(session, action="auth.register", principal=principal, request_id=request_id)
    await session.flush()
    return principal


async def authenticate(
    session: AsyncSession, *, email: str, password: str, request_id: str | None
) -> Principal | None:
    """Return the principal, or ``None`` after recording a failed attempt in the audit log.

    The caller must commit before rejecting, so the failure record is kept.
    """
    normalized = email.strip().lower()
    user = await session.scalar(select(User).where(func.lower(User.email) == normalized))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        if user is None:
            verify_password(password, None)  # equalize timing for unknown emails
        audit(
            session,
            action="auth.login_failed",
            principal=None,
            details={"email_domain": normalized.rsplit("@", 1)[-1]},
            request_id=request_id,
        )
        return None

    # Users currently belong to one workspace; pick the oldest membership deterministically.
    membership = await session.scalar(
        select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at)
    )
    if membership is None:
        raise AuthenticationError("This account is not a member of any workspace")
    principal = Principal(user.id, membership.tenant_id, membership.role, user.email)
    audit(session, action="auth.login", principal=principal, request_id=request_id)
    return principal
