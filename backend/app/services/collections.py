import uuid

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import Principal
from app.core.errors import BadRequestError, ConflictError
from app.db.models.knowledge import Collection, Document, DocumentStatus, EmbeddingModel
from app.schemas.common import Page
from app.schemas.knowledge import CollectionCreate, CollectionOut, CollectionUpdate
from app.services.common import audit, get_owned


def _stats_query(tenant_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return (
        select(
            Collection,
            func.count(Document.id).label("document_count"),
            func.count(case((Document.status == DocumentStatus.READY, 1))).label("ready_count"),
            func.coalesce(func.sum(Document.chunk_count), 0).label("chunk_count"),
            EmbeddingModel.model.label("embedding_model"),
            EmbeddingModel.version.label("embedding_version"),
        )
        .outerjoin(Document, Document.collection_id == Collection.id)
        .outerjoin(EmbeddingModel, EmbeddingModel.id == Collection.active_embedding_model_id)
        .where(Collection.tenant_id == tenant_id)
        .group_by(Collection.id, EmbeddingModel.model, EmbeddingModel.version)
    )


def _to_out(row) -> CollectionOut:  # type: ignore[no-untyped-def]
    collection = row[0]
    model = f"{row.embedding_model}@{row.embedding_version}" if row.embedding_model else None
    return CollectionOut(
        id=collection.id,
        name=collection.name,
        description=collection.description,
        created_at=collection.created_at,
        updated_at=collection.updated_at,
        document_count=row.document_count,
        ready_document_count=row.ready_count,
        chunk_count=int(row.chunk_count),
        active_embedding_model=model,
    )


RESERVED_PREFIX = "[eval]"


def _check_name(name: str) -> None:
    if name.lower().startswith(RESERVED_PREFIX):
        raise BadRequestError(f"Collection names starting with {RESERVED_PREFIX!r} are reserved")


async def create_collection(
    session: AsyncSession, principal: Principal, data: CollectionCreate, request_id: str | None
) -> CollectionOut:
    _check_name(data.name)
    collection = Collection(
        tenant_id=principal.tenant_id,
        name=data.name,
        description=data.description,
        created_by=principal.user_id,
    )
    session.add(collection)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        raise ConflictError("A collection with this name already exists") from None
    audit(
        session,
        action="collection.create",
        principal=principal,
        resource_type="collection",
        resource_id=collection.id,
        request_id=request_id,
    )
    return await get_collection(session, principal, collection.id)


async def list_collections(
    session: AsyncSession, principal: Principal, limit: int, offset: int
) -> Page[CollectionOut]:
    total = await session.scalar(
        select(func.count())
        .select_from(Collection)
        .where(Collection.tenant_id == principal.tenant_id)
    )
    rows = (
        await session.execute(
            _stats_query(principal.tenant_id)
            .order_by(Collection.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page(items=[_to_out(r) for r in rows], total=total or 0, limit=limit, offset=offset)


async def get_collection(
    session: AsyncSession, principal: Principal, collection_id: uuid.UUID
) -> CollectionOut:
    await get_owned(session, Collection, collection_id, principal, "Collection")
    row = (
        await session.execute(
            _stats_query(principal.tenant_id).where(Collection.id == collection_id)
        )
    ).one()
    return _to_out(row)


async def update_collection(
    session: AsyncSession, principal: Principal, collection_id: uuid.UUID, data: CollectionUpdate
) -> CollectionOut:
    collection = await get_owned(session, Collection, collection_id, principal, "Collection")
    if data.name is not None:
        _check_name(data.name.strip())
        collection.name = data.name.strip()
    if data.description is not None:
        collection.description = data.description
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        raise ConflictError("A collection with this name already exists") from None
    return await get_collection(session, principal, collection_id)


async def delete_collection(
    session: AsyncSession, principal: Principal, collection_id: uuid.UUID, request_id: str | None
) -> None:
    collection = await get_owned(session, Collection, collection_id, principal, "Collection")
    await session.delete(collection)  # documents, chunks, vectors cascade in the database
    audit(
        session,
        action="collection.delete",
        principal=principal,
        resource_type="collection",
        resource_id=collection_id,
        request_id=request_id,
        details={"name": collection.name},
    )
