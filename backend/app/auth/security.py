"""Password hashing (Argon2id via pwdlib) and JWT access tokens (HS256)."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.db.models.tenancy import MembershipRole

_hasher = PasswordHash.recommended()
# Verified against when the email is unknown, so login timing doesn't reveal which emails exist.
_DUMMY_HASH = _hasher.hash("atlas-dummy-password-for-timing")

ALGORITHM = "HS256"
ISSUER = "atlas-ai"

ROLE_RANK = {
    MembershipRole.VIEWER: 0,
    MembershipRole.MEMBER: 1,
    MembershipRole.ADMIN: 2,
    MembershipRole.OWNER: 3,
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        _hasher.verify(password, _DUMMY_HASH)
        return False
    return _hasher.verify(password, password_hash)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. Every data-access path is scoped by ``tenant_id``."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: MembershipRole
    email: str

    def has_role(self, minimum: MembershipRole) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]


class InvalidTokenError(Exception):
    pass


def create_access_token(principal: Principal, *, secret: str, ttl_minutes: int) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "sub": str(principal.user_id),
        "tid": str(principal.tenant_id),
        "role": principal.role.value,
        "email": principal.email,
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, *, secret: str) -> Principal:
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "tid", "role"]},
        )
        return Principal(
            user_id=uuid.UUID(claims["sub"]),
            tenant_id=uuid.UUID(claims["tid"]),
            role=MembershipRole(claims["role"]),
            email=str(claims.get("email", "")),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidTokenError(type(exc).__name__) from exc
