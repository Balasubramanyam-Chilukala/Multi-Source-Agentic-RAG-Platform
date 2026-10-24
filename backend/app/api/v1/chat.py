"""Chat endpoints: a JSON request/response variant and a Server-Sent Events stream."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.agent.runtime import get_agent
from app.api.deps import PrincipalDep, ProvidersDep, SessionDep, SettingsDep, user_rate_limit
from app.api.v1.search import retrieval_config
from app.api.v1.settings import load_workspace_settings
from app.core.errors import ConflictError, ServiceUnavailableError, UnprocessableError
from app.core.logging import get_logger
from app.providers.factory import ProviderConfigurationError
from app.retrieval.service import Retriever
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.settings import WorkspaceSettings
from app.services.chat import ChatDependencies, ChatRunner, prepare_chat

router = APIRouter(prefix="/chat", tags=["chat"])
logger = get_logger("atlas.api.chat")
ChatLimit = Depends(user_rate_limit("chat"))
HEARTBEAT_SECONDS = 15.0


async def _runner(
    request: Request,
    settings: SettingsDep,
    providers: ProvidersDep,
    principal: PrincipalDep,
    workspace: Annotated[WorkspaceSettings, Depends(load_workspace_settings)],
) -> ChatRunner:
    try:
        chat = providers.chat(workspace.llm_provider, workspace.llm_model)
        embedder = providers.embeddings()
    except ProviderConfigurationError as exc:
        raise ServiceUnavailableError(f"The AI provider is not configured: {exc}") from None
    reranker = providers.reranker() if workspace.retrieval_mode == "hybrid_rerank" else None
    try:
        agent = await get_agent(request.app)
    except Exception as exc:
        logger.error("agent_unavailable", error_type=type(exc).__name__)
        raise ServiceUnavailableError(
            "The conversation store is unavailable. Please retry shortly."
        ) from None
    deps = ChatDependencies(
        sessionmaker=request.app.state.db.sessionmaker,
        agent=agent,
        chat=chat,
        retriever=Retriever(embedder, reranker),
        retrieval_config=retrieval_config(settings, workspace, None, None),
        settings=settings,
    )
    return ChatRunner(deps, principal, request.state.request_id)


RunnerDep = Annotated[ChatRunner, Depends(_runner)]


def _validate_length(body: ChatRequest, settings: SettingsDep) -> None:
    # Checked before anything is stored, so oversized questions never reach the history.
    if len(body.message.strip()) > settings.max_question_chars:
        raise UnprocessableError(
            f"Questions are limited to {settings.max_question_chars} characters"
        )


def _claim(request: Request, conversation_id: uuid.UUID) -> None:
    """One run at a time per conversation: concurrent turns would fork the checkpoint and
    interleave messages. In-process, matching the single-instance deployment."""
    active: set[uuid.UUID] = request.app.state.active_conversations
    if conversation_id in active:
        raise ConflictError("An answer is still being generated in this conversation")
    active.add(conversation_id)


def _release(request: Request, conversation_id: uuid.UUID) -> None:
    request.app.state.active_conversations.discard(conversation_id)


@router.post("", response_model=ChatResponse, dependencies=[ChatLimit])
async def chat(
    body: ChatRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    runner: RunnerDep,
) -> ChatResponse:
    _validate_length(body, settings)
    if body.conversation_id is not None:
        _claim(request, body.conversation_id)
    try:
        prepared = await prepare_chat(
            session,
            principal,
            message=body.message,
            conversation_id=body.conversation_id,
            collection_ids=body.collection_ids,
        )
        await session.commit()
        if body.conversation_id is None:
            _claim(request, prepared.conversation_id)
        try:
            return await runner.run(prepared)
        finally:
            _release(request, prepared.conversation_id)
    finally:
        if body.conversation_id is not None:
            _release(request, body.conversation_id)


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


@router.post(
    "/stream",
    dependencies=[ChatLimit],
    responses={200: {"content": {"text/event-stream": {}}, "description": "Server-Sent Events"}},
)
async def chat_stream(
    body: ChatRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    runner: RunnerDep,
) -> StreamingResponse:
    """Stream the run as SSE events:

    - ``meta`` — conversation and user-message ids
    - ``step`` — a graph node started/finished
    - ``token`` — answer text delta (``token_reset`` discards the draft before a regeneration)
    - ``final`` — the validated answer, citations, evidence, and run summary (same shape as
      ``POST /chat``). Streamed tokens are provisional: citation validation may replace them.
    - ``error`` — the run failed before producing a final answer

    The run continues and is persisted even if the client disconnects.
    """
    _validate_length(body, settings)
    if body.conversation_id is not None:
        _claim(request, body.conversation_id)
    try:
        prepared = await prepare_chat(
            session,
            principal,
            message=body.message,
            conversation_id=body.conversation_id,
            collection_ids=body.collection_ids,
        )
        await session.commit()
    except Exception:
        if body.conversation_id is not None:
            _release(request, body.conversation_id)
        raise
    if body.conversation_id is None:
        _claim(request, prepared.conversation_id)
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def run() -> None:
        try:
            response = await runner.run(
                prepared, on_event=lambda e: queue.put_nowait((e.get("type", "step"), e))
            )
            queue.put_nowait(("final", response.model_dump(mode="json", by_alias=True)))
        except Exception as exc:
            logger.exception("stream_run_failed", error_type=type(exc).__name__)
            queue.put_nowait(
                (
                    "error",
                    {"code": "internal_error", "message": "The answer could not be completed."},
                )
            )
        finally:
            _release(request, prepared.conversation_id)
            queue.put_nowait(None)

    task = asyncio.create_task(run())
    tasks: set[asyncio.Task[None]] = request.app.state.background_runs
    tasks.add(task)  # keep a strong reference until the run finishes, even after a disconnect
    task.add_done_callback(tasks.discard)

    async def events() -> AsyncIterator[bytes]:
        yield _sse(
            "meta",
            {
                "conversation_id": str(prepared.conversation_id),
                "user_message_id": str(prepared.user_message_id),
            },
        )
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                yield b": keep-alive\n\n"
                continue
            if item is None:
                return
            event, data = item
            yield _sse(event, data)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
