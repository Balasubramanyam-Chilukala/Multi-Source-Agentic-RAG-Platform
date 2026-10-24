"""Alembic environment.

The database URL comes from (in order) an explicit ``sqlalchemy.url`` set programmatically, the
``DATABASE_URL`` environment variable, or the application settings. Only the database URL is
needed, so migrations run in CI and deploy jobs without any LLM provider secrets.
"""

import asyncio
import os
import sys
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.db.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.config import get_settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Per-embedding-model ANN indexes are created at runtime when a model is registered.
DYNAMIC_INDEX_PREFIX = "ix_chunks_embedding_hnsw_"
# Tables owned and migrated by LangGraph's PostgreSQL checkpointer, not by Alembic.
EXTERNAL_TABLE_PREFIX = "checkpoint"


def _database_url() -> str:
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        return explicit
    from_env = os.environ.get("DATABASE_URL")
    if from_env:
        return from_env
    return get_settings().database_url.get_secret_value()


def _include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    if name is None:
        return True
    if type_ == "index" and name.startswith(DYNAMIC_INDEX_PREFIX):
        return False
    table_name = obj.table.name if type_ == "index" else name
    # Reflected-only objects of LangGraph's tables are not ours to drop.
    return not (reflected and compare_to is None and table_name.startswith(EXTERNAL_TABLE_PREFIX))


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": _database_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    # psycopg's async mode cannot use the Proactor loop that Windows selects by default.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(run_async_migrations(), loop_factory=loop_factory)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
