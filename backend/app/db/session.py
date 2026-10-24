"""Async engine and session management.

``Database`` owns the engine for the application's lifetime (created in the FastAPI lifespan) so
tests can construct an app against any database without module-level globals.
"""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


class Database:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        engine = create_async_engine(
            settings.database_url.get_secret_value(),
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_pre_ping=True,
            # Keep bound parameters (document text, emails) out of exception messages and logs.
            hide_parameters=True,
            connect_args={"connect_timeout": settings.db_connect_timeout_seconds},
        )
        return cls(engine)

    async def dispose(self) -> None:
        await self.engine.dispose()


def get_database(request: Request) -> Database:
    database: Database = request.app.state.db
    return database


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Request-scoped session; commits on success and rolls back on any exception."""
    async with get_database(request).sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
