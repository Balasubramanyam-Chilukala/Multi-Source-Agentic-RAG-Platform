"""Liveness and readiness probes.

``/health/live`` only proves the process is serving requests. ``/health/ready`` verifies the
dependencies a request actually needs: database connectivity, the pgvector extension, that the
schema is at the latest migration, and that the agent's checkpoint tables exist. It never
calls an LLM provider, so probes cost nothing against free-tier quotas.
"""

import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.logging import get_logger
from app.db.session import Database, get_database

router = APIRouter(prefix="/health", tags=["health"])
logger = get_logger("atlas.health")

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
READINESS_TIMEOUT_SECONDS = 5.0


class CheckResult(BaseModel):
    status: Literal["ok", "fail"]
    detail: str | None = None


class LivenessResponse(BaseModel):
    status: Literal["alive"] = "alive"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckResult]


@lru_cache
def migration_head() -> str | None:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_INI.parent / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


async def _check_pgvector(conn: AsyncConnection) -> CheckResult:
    result = await conn.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    )
    version = result.scalar_one_or_none()
    if version is None:
        return CheckResult(status="fail", detail="pgvector extension is not installed")
    return CheckResult(status="ok", detail=f"pgvector {version}")


async def _check_migrations(conn: AsyncConnection) -> CheckResult:
    head = migration_head()
    has_table = await conn.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))
    if not has_table.scalar_one():
        return CheckResult(status="fail", detail=f"no migrations applied; expected {head}")
    current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    if current != head:
        return CheckResult(status="fail", detail=f"schema at {current}, expected {head}")
    return CheckResult(status="ok", detail=str(current))


async def _check_checkpointer(conn: AsyncConnection) -> CheckResult:
    exists = await conn.execute(text("SELECT to_regclass('public.checkpoints') IS NOT NULL"))
    if not exists.scalar_one():
        return CheckResult(
            status="fail", detail="checkpoint tables missing; run `python -m app.cli migrate`"
        )
    return CheckResult(status="ok")


async def _run_checks(database: Database) -> dict[str, CheckResult]:
    async with database.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        checks = {"database": CheckResult(status="ok")}
        checks["pgvector"] = await _check_pgvector(conn)
        checks["migrations"] = await _check_migrations(conn)
        checks["checkpointer"] = await _check_checkpointer(conn)
        return checks


@router.get("/live", response_model=LivenessResponse)
async def live() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
async def ready(database: Annotated[Database, Depends(get_database)]) -> JSONResponse:
    try:
        async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
            checks = await _run_checks(database)
    except Exception as exc:
        # Only the exception type is reported: driver messages can include hostnames or DSNs.
        logger.warning("readiness_database_unavailable", error_type=type(exc).__name__)
        checks = {"database": CheckResult(status="fail", detail=type(exc).__name__)}

    is_ready = all(check.status == "ok" for check in checks.values())
    body = ReadinessResponse(status="ready" if is_ready else "not_ready", checks=checks)
    return JSONResponse(status_code=200 if is_ready else 503, content=body.model_dump())
