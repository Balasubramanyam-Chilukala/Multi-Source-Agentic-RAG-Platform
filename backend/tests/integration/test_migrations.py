"""Migrations against a real PostgreSQL + pgvector database (``docker compose up -d db``)."""

import asyncio
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from alembic import command
from fastapi import FastAPI
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.health import migration_head
from app.core.config import Settings
from app.db.base import Base
from app.db.session import Database
from app.main import create_app
from tests.conftest import alembic_config

pytestmark = pytest.mark.integration


@pytest.fixture
async def database(test_database_url: str) -> AsyncIterator[Database]:
    database = Database(create_async_engine(test_database_url))
    yield database
    await database.dispose()


async def _setup_checkpointer(database_url: str) -> None:
    from app.agent.runtime import create_postgres_runtime  # noqa: PLC0415
    from app.core.config import Settings  # noqa: PLC0415

    settings = Settings(
        _env_file=None,
        environment="test",
        llm_provider="mock",
        embedding_provider="mock",
        database_url=database_url,
    )
    runtime = await create_postgres_runtime(settings, setup=True)
    await runtime.close()


async def _table_names(database: Database) -> set[str]:
    async with database.engine.connect() as conn:
        return set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))


async def _ready(app: FastAPI, database: Database) -> httpx.Response:
    app.state.db = database
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/health/ready")


async def test_upgrade_downgrade_roundtrip_and_readiness(
    test_database_url: str, database: Database, make_settings: Callable[..., Settings]
) -> None:
    config = alembic_config(test_database_url)
    # Alembic's env.py calls asyncio.run(), so it must run outside the test's event loop.
    await asyncio.to_thread(command.downgrade, config, "base")
    app = create_app(make_settings(database_url=test_database_url))

    # Empty database: not ready because no migrations have been applied.
    response = await _ready(app, database)
    assert response.status_code == 503
    assert response.json()["checks"]["migrations"]["status"] == "fail"

    await asyncio.to_thread(command.upgrade, config, "head")
    await _setup_checkpointer(test_database_url)
    tables = await _table_names(database)
    assert set(Base.metadata.tables) <= tables

    response = await _ready(app, database)
    assert response.status_code == 200, response.text
    checks = response.json()["checks"]
    statuses = {name: check["status"] for name, check in checks.items()}
    assert statuses == {
        "database": "ok",
        "pgvector": "ok",
        "migrations": "ok",
        "checkpointer": "ok",
    }

    await asyncio.to_thread(command.downgrade, config, "base")
    assert (await _table_names(database)) & set(Base.metadata.tables) == set()

    await asyncio.to_thread(command.upgrade, config, "head")
    async with database.engine.connect() as conn:
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    assert version == migration_head()


def test_models_match_migrations(test_database_url: str) -> None:
    """Fails if a model changed without a corresponding migration (autogenerate drift)."""
    config = alembic_config(test_database_url)
    command.upgrade(config, "head")
    command.check(config)
