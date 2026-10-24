"""Shared FastAPI dependencies: settings, DB session, providers, authentication, rate limits."""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import InvalidTokenError, Principal, decode_access_token
from app.core.config import Settings
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.rate_limit import SlidingWindowLimiter
from app.db.models.tenancy import Membership, MembershipRole, User
from app.db.session import get_session
from app.providers.factory import ProviderRegistry

_bearer = HTTPBearer(auto_error=False, description="Access token from /api/v1/auth/login")


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_providers(request: Request) -> ProviderRegistry:
    providers: ProviderRegistry = request.app.state.providers
    return providers


def get_limiter(request: Request) -> SlidingWindowLimiter:
    limiter: SlidingWindowLimiter = request.app.state.limiter
    return limiter


# scope="function": the session commits (or rolls back) when the endpoint returns, *before* the
# response is sent. With the default request scope the commit would happen after the client already
# got its 2xx, so a failed commit would be reported as success and read-after-write could race.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
ProvidersDep = Annotated[ProviderRegistry, Depends(get_providers)]
LimiterDep = Annotated[SlidingWindowLimiter, Depends(get_limiter)]


async def get_principal(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Authentication required")
    try:
        claims = decode_access_token(credentials.credentials, secret=settings.signing_key)
    except InvalidTokenError:
        raise AuthenticationError("Invalid or expired access token") from None

    # Re-check membership on every request so deactivation and role changes apply immediately.
    row = (
        await session.execute(
            select(Membership.role, User.email)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.user_id == claims.user_id,
                Membership.tenant_id == claims.tenant_id,
                User.is_active.is_(True),
            )
        )
    ).one_or_none()
    if row is None:
        raise AuthenticationError("Account is no longer active in this workspace")
    principal = Principal(claims.user_id, claims.tenant_id, row.role, row.email)
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require_role(minimum: MembershipRole) -> Callable[..., Coroutine[Any, Any, Principal]]:
    async def dependency(principal: PrincipalDep) -> Principal:
        if not principal.has_role(minimum):
            raise PermissionDeniedError(f"This action requires the {minimum.value} role or higher")
        return principal

    return dependency


MemberDep = Annotated[Principal, Depends(require_role(MembershipRole.MEMBER))]
AdminDep = Annotated[Principal, Depends(require_role(MembershipRole.ADMIN))]


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _window(per: str) -> float:
    return 60.0 if per == "minute" else 3600.0


def user_rate_limit(
    bucket: str, *, per: str = "minute"
) -> Callable[..., Coroutine[Any, Any, None]]:
    """Per-user limit. Depends on the principal, so it also enforces authentication."""
    limit_field = f"rate_limit_{bucket}_per_{per}"

    async def dependency(
        principal: PrincipalDep, settings: SettingsDep, limiter: LimiterDep
    ) -> None:
        limiter.check(
            f"{bucket}:user:{principal.user_id}",
            limit=getattr(settings, limit_field),
            window_seconds=_window(per),
        )

    return dependency


def ip_rate_limit(bucket: str, *, per: str = "minute") -> Callable[..., None]:
    """Per-client-IP limit for unauthenticated routes (login, registration)."""
    limit_field = f"rate_limit_{bucket}_per_{per}"

    def dependency(request: Request, settings: SettingsDep, limiter: LimiterDep) -> None:
        limiter.check(
            f"{bucket}:ip:{client_ip(request)}",
            limit=getattr(settings, limit_field),
            window_seconds=_window(per),
        )

    return dependency
