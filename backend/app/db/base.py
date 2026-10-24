"""Declarative base, constraint naming convention, and shared column mixins."""

import uuid
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, ForeignKey, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Deterministic constraint names keep Alembic autogenerate diffs and downgrades stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    # Fetch server-generated values (created_at/updated_at) via RETURNING on INSERT *and* UPDATE,
    # so objects stay fully loaded after a flush; async sessions cannot lazy-load them later.
    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenantScopedMixin:
    """Every tenant-owned row carries ``tenant_id`` so queries can always filter by tenant."""

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
        )
