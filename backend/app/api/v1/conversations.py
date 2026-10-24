import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from app.agent.runtime import get_agent
from app.api.deps import PrincipalDep, SessionDep
from app.core.logging import get_logger
from app.db.models.conversation import RunOutcome
from app.schemas.chat import (
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
    FeedbackCreate,
    FeedbackOut,
    RunDetail,
    RunOut,
)
from app.schemas.common import Page
from app.services import conversations as service

router = APIRouter(prefix="/conversations", tags=["conversations"])
feedback_router = APIRouter(prefix="/feedback", tags=["feedback"])
runs_router = APIRouter(prefix="/runs", tags=["runs"])
logger = get_logger("atlas.api.conversations")


@router.get("", response_model=Page[ConversationOut])
async def list_conversations(
    session: SessionDep,
    principal: PrincipalDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ConversationOut]:
    return await service.list_conversations(session, principal, limit, offset)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> ConversationDetail:
    return await service.get_conversation(session, principal, conversation_id)


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: uuid.UUID,
    body: ConversationUpdate,
    session: SessionDep,
    principal: PrincipalDep,
) -> ConversationOut:
    return await service.rename_conversation(session, principal, conversation_id, body.title)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, request: Request, session: SessionDep, principal: PrincipalDep
) -> None:
    await service.delete_conversation(session, principal, conversation_id, request.state.request_id)
    await session.commit()
    # Remove the short-term memory too. If the checkpoint store is unreachable the conversation
    # is already gone from the API; the orphaned checkpoint holds only that thread's turns.
    try:
        agent = await get_agent(request.app)
        await agent.delete_thread(str(conversation_id))
    except Exception as exc:
        logger.warning(
            "checkpoint_delete_failed",
            conversation_id=str(conversation_id),
            error_type=type(exc).__name__,
        )


@feedback_router.post("", response_model=FeedbackOut, status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    body: FeedbackCreate, session: SessionDep, principal: PrincipalDep
) -> FeedbackOut:
    return await service.submit_feedback(session, principal, body)


@runs_router.get("", response_model=Page[RunOut])
async def list_runs(
    session: SessionDep,
    principal: PrincipalDep,
    outcome: RunOutcome | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[RunOut]:
    return await service.list_runs(session, principal, outcome=outcome, limit=limit, offset=offset)


@runs_router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: uuid.UUID, session: SessionDep, principal: PrincipalDep) -> RunDetail:
    return await service.get_run(session, principal, run_id)
