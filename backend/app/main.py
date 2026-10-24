"""FastAPI application factory."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.runtime import shutdown_agent, start_agent
from app.api import health
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import REQUEST_ID_HEADER, BodySizeLimitMiddleware, RequestContextMiddleware
from app.core.observability import configure_observability
from app.core.rate_limit import SlidingWindowLimiter
from app.db.session import Database
from app.ingestion.pipeline import IngestionPipeline
from app.providers.factory import ProviderRegistry

logger = get_logger("atlas.app")

# Multipart framing and form fields add a little on top of the file itself.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024


async def _drain_background_runs(app: FastAPI, grace_seconds: float = 10.0) -> None:
    """Let in-flight streaming runs finish (and persist) before resources are closed."""
    runs = set(app.state.background_runs)
    if not runs:
        return
    _, pending = await asyncio.wait(runs, timeout=grace_seconds)
    for task in pending:
        task.cancel()
    if pending:
        logger.warning("shutdown_cancelled_runs", count=len(pending))


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
    providers: ProviderRegistry | None = None,
) -> FastAPI:
    """Build the application. ``database`` and ``providers`` can be injected by tests;
    otherwise they are created from ``settings`` at startup and disposed at shutdown."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_logs=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owns_database = database is None
        owns_providers = providers is None
        app.state.db = database or Database.from_settings(settings)
        app.state.providers = providers or ProviderRegistry(settings)
        app.state.pipeline = IngestionPipeline(
            app.state.db.sessionmaker, app.state.providers, settings
        )
        app.state.agent = None
        logger.info(
            "startup",
            environment=settings.environment,
            llm_provider=settings.llm_provider,
            llm_model=settings.llm_model,
            embedding_provider=settings.embedding_provider,
            embedding_model=settings.embedding_model,
            reranker_provider=settings.reranker_provider,
            demo_mode=settings.demo_mode,
        )
        try:
            await app.state.pipeline.recover_interrupted()
        except Exception as exc:  # the DB may be down; readiness will report it
            logger.warning("startup_recovery_skipped", error_type=type(exc).__name__)
        await start_agent(app)
        try:
            yield
        finally:
            await _drain_background_runs(app)
            await shutdown_agent(app)
            if owns_providers:
                await app.state.providers.aclose()
            if owns_database:
                await app.state.db.dispose()
            logger.info("shutdown")

    app = FastAPI(
        title=f"{settings.app_name} API",
        version="1.0.0",
        description=(
            "Multi-source agentic RAG platform: ingestion, hybrid retrieval, a LangGraph agent "
            "with validated citations, evaluation, and run telemetry."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.limiter = SlidingWindowLimiter()
    app.state.background_runs = set()  # streaming chat runs outliving their request
    app.state.active_conversations = set()  # conversations with a run in progress

    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens, not cookies
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
    )
    # Added last, so it is outermost: CORS preflight responses also carry a request ID.
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    configure_observability(app, settings)
    app.include_router(health.router)
    app.include_router(api_router)
    return app
