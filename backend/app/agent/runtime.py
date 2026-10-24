"""Agent lifecycle: the compiled graph plus its PostgreSQL checkpointer.

The checkpointer uses its own small psycopg pool (LangGraph requires ``autocommit=True`` and
``dict_row``), separate from the SQLAlchemy engine.

Checkpoint tables are created by ``python -m app.cli migrate`` and, for convenience, at startup.
``setup()`` must never run inside a request: it issues ``CREATE INDEX CONCURRENTLY``, which
waits for every older open transaction — including the request's own session — to finish.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import make_url

from app.agent.graph import build_graph
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger("atlas.agent.runtime")


@dataclass
class AgentRuntime:
    graph: CompiledStateGraph[Any, Any, Any, Any]
    checkpointer: BaseCheckpointSaver[Any]
    pool: AsyncConnectionPool[Any] | None = None

    async def delete_thread(self, thread_id: str) -> None:
        await self.checkpointer.adelete_thread(thread_id)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()


def checkpoint_conninfo(settings: Settings) -> str:
    url = make_url(settings.database_url.get_secret_value()).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


async def create_postgres_runtime(settings: Settings, *, setup: bool) -> AgentRuntime:
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        checkpoint_conninfo(settings),
        min_size=1,
        max_size=4,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open(wait=True, timeout=settings.db_pool_timeout_seconds)
    checkpointer = AsyncPostgresSaver(pool)
    if setup:
        await checkpointer.setup()  # idempotent; creates/migrates LangGraph's own tables
    graph = build_graph(checkpointer, max_retrieval_attempts=settings.agent_max_retrieval_attempts)
    logger.info("agent_ready", checkpointer="postgres", setup=setup)
    return AgentRuntime(graph=graph, checkpointer=checkpointer, pool=pool)


_lock = asyncio.Lock()


async def start_agent(app: FastAPI) -> None:
    """Called from the lifespan, before any request: safe to run checkpoint migrations."""
    try:
        app.state.agent = await create_postgres_runtime(app.state.settings, setup=True)
    except Exception as exc:  # DB down at boot: chat will connect lazily once it is back
        logger.warning("agent_startup_deferred", error_type=type(exc).__name__)


async def get_agent(app: FastAPI) -> AgentRuntime:
    if app.state.agent is None:
        async with _lock:
            if app.state.agent is None:
                app.state.agent = await create_postgres_runtime(app.state.settings, setup=False)
    runtime: AgentRuntime = app.state.agent
    return runtime


async def shutdown_agent(app: FastAPI) -> None:
    runtime: AgentRuntime | None = getattr(app.state, "agent", None)
    if runtime is not None:
        await runtime.close()
        app.state.agent = None
