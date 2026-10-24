from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import PrincipalDep, SessionDep, SettingsDep, ip_rate_limit
from app.auth.security import Principal, create_access_token
from app.core.errors import AuthenticationError
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)
    workspace_name: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class MeResponse(BaseModel):
    user_id: str
    tenant_id: str
    email: str
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int
    user: MeResponse


def _token(principal: Principal, settings: SettingsDep) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(
            principal, secret=settings.signing_key, ttl_minutes=settings.access_token_ttl_minutes
        ),
        expires_in=settings.access_token_ttl_minutes * 60,
        user=_me(principal),
    )


def _me(principal: Principal) -> MeResponse:
    return MeResponse(
        user_id=str(principal.user_id),
        tenant_id=str(principal.tenant_id),
        email=principal.email,
        role=principal.role.value,
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(ip_rate_limit("auth"))],
)
async def register(
    body: RegisterRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    principal = await auth_service.register(
        session,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        workspace_name=body.workspace_name,
        allow_registration=settings.allow_registration,
        request_id=request.state.request_id,
    )
    return _token(principal, settings)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(ip_rate_limit("auth"))])
async def login(
    body: LoginRequest, request: Request, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    principal = await auth_service.authenticate(
        session, email=body.email, password=body.password, request_id=request.state.request_id
    )
    if principal is None:
        await session.commit()  # keep the failed-login audit record
        raise AuthenticationError("Invalid email or password")
    return _token(principal, settings)


@router.get("/me", response_model=MeResponse)
async def me(principal: PrincipalDep) -> MeResponse:
    return _me(principal)
