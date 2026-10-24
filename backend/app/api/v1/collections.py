import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Request, status
from pydantic import BaseModel

from app.api.deps import AdminDep, MemberDep, PrincipalDep, SessionDep
from app.db.models.knowledge import Collection
from app.schemas.common import Page
from app.schemas.knowledge import CollectionCreate, CollectionOut, CollectionUpdate
from app.services import collections as service
from app.services.common import audit, get_owned

router = APIRouter(prefix="/collections", tags=["collections"])


@router.post("", response_model=CollectionOut, status_code=status.HTTP_201_CREATED)
async def create_collection(
    body: CollectionCreate, request: Request, session: SessionDep, principal: MemberDep
) -> CollectionOut:
    return await service.create_collection(session, principal, body, request.state.request_id)


@router.get("", response_model=Page[CollectionOut])
async def list_collections(
    session: SessionDep,
    principal: PrincipalDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[CollectionOut]:
    return await service.list_collections(session, principal, limit, offset)


@router.get("/{collection_id}", response_model=CollectionOut)
async def get_collection(
    collection_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> CollectionOut:
    return await service.get_collection(session, principal, collection_id)


@router.patch("/{collection_id}", response_model=CollectionOut)
async def update_collection(
    collection_id: uuid.UUID, body: CollectionUpdate, session: SessionDep, principal: MemberDep
) -> CollectionOut:
    return await service.update_collection(session, principal, collection_id, body)


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: uuid.UUID, request: Request, session: SessionDep, principal: AdminDep
) -> None:
    await service.delete_collection(session, principal, collection_id, request.state.request_id)


class ReembedResponse(BaseModel):
    status: str = "scheduled"


@router.post(
    "/{collection_id}/reembed", response_model=ReembedResponse, status_code=status.HTTP_202_ACCEPTED
)
async def reembed_collection(
    collection_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: AdminDep,
    background: BackgroundTasks,
) -> ReembedResponse:
    """Re-embed every chunk with the currently configured embedding model (see ADR 0004)."""
    await get_owned(session, Collection, collection_id, principal, "Collection")
    audit(
        session,
        action="collection.reembed",
        principal=principal,
        resource_type="collection",
        resource_id=collection_id,
        request_id=request.state.request_id,
    )
    await session.commit()
    background.add_task(request.app.state.pipeline.reembed_collection, collection_id)
    return ReembedResponse()
