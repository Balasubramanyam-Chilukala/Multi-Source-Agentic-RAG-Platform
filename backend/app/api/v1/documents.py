import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Query,
    Request,
    UploadFile,
    status,
)

from app.api.deps import MemberDep, PrincipalDep, SessionDep, SettingsDep, user_rate_limit
from app.core.errors import PayloadTooLargeError
from app.db.models.knowledge import DocumentStatus
from app.schemas.common import Page
from app.schemas.knowledge import (
    ChunkOut,
    DocumentDetail,
    DocumentOut,
    TextDocumentCreate,
    UrlDocumentCreate,
)
from app.services import documents as service
from app.services.documents import CreatedDocument

router = APIRouter(prefix="/documents", tags=["documents"])
IngestLimit = Depends(user_rate_limit("ingest"))
READ_CHUNK = 1024 * 1024


async def _schedule(
    created: CreatedDocument, request: Request, session: SessionDep, background: BackgroundTasks
) -> DocumentOut:
    # Commit before scheduling so the background task sees the document and its source bytes.
    await session.commit()
    background.add_task(request.app.state.pipeline.process, created.document.id, created.job_id)
    return DocumentOut.model_validate(created.document)


@router.post(
    "/upload",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[IngestLimit],
)
async def upload_document(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: MemberDep,
    background: BackgroundTasks,
    collection_id: Annotated[uuid.UUID, Form()],
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form(max_length=500)] = None,
) -> DocumentOut:
    content = bytearray()
    while piece := await file.read(READ_CHUNK):  # stop reading as soon as the limit is passed
        content.extend(piece)
        if len(content) > settings.max_upload_bytes:
            raise PayloadTooLargeError(
                f"The file exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit"
            )
    created = await service.create_from_upload(
        session,
        principal,
        settings,
        collection_id=collection_id,
        filename=file.filename or "upload",
        mime_type=file.content_type,
        content=bytes(content),
        title=title,
        request_id=request.state.request_id,
    )
    return await _schedule(created, request, session, background)


@router.post(
    "/text",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[IngestLimit],
)
async def create_text_document(
    body: TextDocumentCreate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: MemberDep,
    background: BackgroundTasks,
) -> DocumentOut:
    created = await service.create_from_text(
        session,
        principal,
        settings,
        collection_id=body.collection_id,
        title=body.title,
        text=body.text,
        markdown=body.format == "markdown",
        request_id=request.state.request_id,
    )
    return await _schedule(created, request, session, background)


@router.post(
    "/url",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[IngestLimit],
)
async def create_url_document(
    body: UrlDocumentCreate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: MemberDep,
    background: BackgroundTasks,
) -> DocumentOut:
    created = await service.create_from_url(
        session,
        principal,
        settings,
        collection_id=body.collection_id,
        url=str(body.url),
        title=body.title,
        request_id=request.state.request_id,
    )
    return await _schedule(created, request, session, background)


@router.get("", response_model=Page[DocumentOut])
async def list_documents(
    session: SessionDep,
    principal: PrincipalDep,
    collection_id: uuid.UUID | None = None,
    status_filter: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DocumentOut]:
    return await service.list_documents(
        session,
        principal,
        collection_id=collection_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> DocumentDetail:
    return await service.get_document(session, principal, document_id)


@router.get("/{document_id}/chunks", response_model=Page[ChunkOut])
async def list_document_chunks(
    document_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ChunkOut]:
    return await service.list_chunks(session, principal, document_id, limit, offset)


@router.post(
    "/{document_id}/retry",
    response_model=DocumentOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[IngestLimit],
)
async def retry_document(
    document_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: MemberDep,
    background: BackgroundTasks,
) -> DocumentOut:
    created = await service.retry_document(
        session, principal, document_id, request.state.request_id
    )
    return await _schedule(created, request, session, background)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, request: Request, session: SessionDep, principal: MemberDep
) -> None:
    await service.delete_document(session, principal, document_id, request.state.request_id)


chunk_router = APIRouter(prefix="/chunks", tags=["documents"])


@chunk_router.get("/{chunk_id}", response_model=ChunkOut)
async def get_chunk(chunk_id: uuid.UUID, session: SessionDep, principal: PrincipalDep) -> ChunkOut:
    """A single passage, used by the citation inspector."""
    return await service.get_chunk(session, principal, chunk_id)
