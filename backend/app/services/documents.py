"""Document lifecycle: creation (upload, pasted text, URL), listing, retry, deletion.

Creation only validates and records the document; parsing and embedding happen in
``IngestionPipeline.process`` after the request commits.
"""

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import Principal
from app.core.config import Settings
from app.core.errors import BadRequestError, ConflictError, NotFoundError, PayloadTooLargeError
from app.core.logging import get_logger
from app.db.models.knowledge import (
    Chunk,
    Collection,
    Document,
    DocumentSource,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    SourceType,
)
from app.ingestion.parsers import ParseError, detect_kind
from app.ingestion.url_fetch import UrlFetchError, UrlRejectedError, fetch_url
from app.schemas.common import Page
from app.schemas.knowledge import ChunkOut, DocumentDetail, DocumentOut, IngestionJobOut
from app.services.common import audit, get_owned

logger = get_logger("atlas.documents")

CONTENT_TYPE_EXTENSIONS = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "application/json": ".json",
    "application/pdf": ".pdf",
}


@dataclass
class CreatedDocument:
    document: Document
    job_id: uuid.UUID


async def _create(
    session: AsyncSession,
    principal: Principal,
    settings: Settings,
    *,
    collection_id: uuid.UUID,
    content: bytes,
    filename: str,
    mime_type: str | None,
    title: str,
    source_type: SourceType,
    source_uri: str | None,
    request_id: str | None,
) -> CreatedDocument:
    await get_owned(session, Collection, collection_id, principal, "Collection")
    if not content:
        raise BadRequestError("The document is empty")
    if len(content) > settings.max_upload_bytes:
        raise PayloadTooLargeError(
            f"The document exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit"
        )
    try:
        detect_kind(filename, mime_type, content)
    except ParseError as exc:
        raise BadRequestError(str(exc)) from None

    digest = hashlib.sha256(content).hexdigest()
    existing = await session.scalar(
        select(Document.id).where(
            Document.collection_id == collection_id, Document.content_hash == digest
        )
    )
    if existing is not None:
        raise ConflictError(
            "This exact content is already in the collection",
            details=[{"existing_document_id": str(existing)}],
        )

    document = Document(
        tenant_id=principal.tenant_id,
        collection_id=collection_id,
        title=title[:500],
        source_type=source_type,
        source_uri=source_uri,
        mime_type=mime_type,
        size_bytes=len(content),
        content_hash=digest,
        status=DocumentStatus.PENDING,
        created_by=principal.user_id,
    )
    session.add(document)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:  # a concurrent upload of the same bytes won the race
        raise ConflictError("This exact content is already in the collection") from None
    session.add(DocumentSource(document_id=document.id, filename=filename, content=content))
    job = IngestionJob(
        tenant_id=principal.tenant_id, document_id=document.id, status=JobStatus.QUEUED
    )
    session.add(job)
    audit(
        session,
        action="document.create",
        principal=principal,
        resource_type="document",
        resource_id=document.id,
        request_id=request_id,
        details={"source_type": source_type.value, "size_bytes": len(content)},
    )
    await session.flush()
    return CreatedDocument(document, job.id)


async def create_from_upload(
    session: AsyncSession,
    principal: Principal,
    settings: Settings,
    *,
    collection_id: uuid.UUID,
    filename: str,
    mime_type: str | None,
    content: bytes,
    title: str | None,
    request_id: str | None,
) -> CreatedDocument:
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1][:255] or "upload"
    return await _create(
        session,
        principal,
        settings,
        collection_id=collection_id,
        content=content,
        filename=safe_name,
        mime_type=mime_type,
        title=(title or safe_name).strip(),
        source_type=SourceType.UPLOAD,
        source_uri=None,
        request_id=request_id,
    )


async def create_from_text(
    session: AsyncSession,
    principal: Principal,
    settings: Settings,
    *,
    collection_id: uuid.UUID,
    title: str,
    text: str,
    markdown: bool,
    request_id: str | None,
) -> CreatedDocument:
    extension = ".md" if markdown else ".txt"
    return await _create(
        session,
        principal,
        settings,
        collection_id=collection_id,
        content=text.encode("utf-8"),
        filename=f"pasted{extension}",
        mime_type="text/markdown" if markdown else "text/plain",
        title=title.strip(),
        source_type=SourceType.TEXT,
        source_uri=None,
        request_id=request_id,
    )


async def create_from_url(
    session: AsyncSession,
    principal: Principal,
    settings: Settings,
    *,
    collection_id: uuid.UUID,
    url: str,
    title: str | None,
    request_id: str | None,
) -> CreatedDocument:
    await get_owned(session, Collection, collection_id, principal, "Collection")
    # Release the pooled connection while waiting on the network (up to timeout x redirects).
    await session.commit()
    try:
        fetched = await fetch_url(
            url,
            max_bytes=settings.max_upload_bytes,
            timeout=settings.url_fetch_timeout_seconds,
            max_redirects=settings.url_max_redirects,
            allowed_hosts=settings.url_allowed_hosts,
        )
    except UrlRejectedError as exc:
        # Logged rather than audited: the request transaction is rolled back on this error.
        logger.warning(
            "url_ingestion_rejected", tenant_id=str(principal.tenant_id), reason=str(exc)
        )
        raise BadRequestError(str(exc)) from None
    except UrlFetchError as exc:
        raise BadRequestError(f"Could not fetch the URL: {exc}") from None
    extension = CONTENT_TYPE_EXTENSIONS[fetched.content_type]
    return await _create(
        session,
        principal,
        settings,
        collection_id=collection_id,
        content=fetched.content,
        filename=f"page{extension}",
        mime_type=fetched.content_type,
        title=(title or fetched.final_url).strip(),
        source_type=SourceType.URL,
        source_uri=fetched.final_url,
        request_id=request_id,
    )


async def list_documents(
    session: AsyncSession,
    principal: Principal,
    *,
    collection_id: uuid.UUID | None,
    status: DocumentStatus | None,
    limit: int,
    offset: int,
) -> Page[DocumentOut]:
    query = select(Document).where(Document.tenant_id == principal.tenant_id)
    if collection_id is not None:
        query = query.where(Document.collection_id == collection_id)
    if status is not None:
        query = query.where(Document.status == status)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(
        query.order_by(Document.created_at.desc()).limit(limit).offset(offset)
    )
    return Page(
        items=[DocumentOut.model_validate(d) for d in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def get_document(
    session: AsyncSession, principal: Principal, document_id: uuid.UUID
) -> DocumentDetail:
    document = await get_owned(session, Document, document_id, principal, "Document")
    jobs = await session.scalars(
        select(IngestionJob)
        .where(IngestionJob.document_id == document_id)
        .order_by(IngestionJob.created_at.desc())
    )
    detail = DocumentDetail.model_validate(document)
    detail.jobs = [IngestionJobOut.model_validate(j) for j in jobs]
    return detail


async def list_chunks(
    session: AsyncSession, principal: Principal, document_id: uuid.UUID, limit: int, offset: int
) -> Page[ChunkOut]:
    await get_owned(session, Document, document_id, principal, "Document")
    base = select(Chunk).where(
        Chunk.document_id == document_id, Chunk.tenant_id == principal.tenant_id
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = await session.scalars(base.order_by(Chunk.ordinal).limit(limit).offset(offset))
    return Page(
        items=[ChunkOut.model_validate(c) for c in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


async def get_chunk(session: AsyncSession, principal: Principal, chunk_id: uuid.UUID) -> ChunkOut:
    chunk = await get_owned(session, Chunk, chunk_id, principal, "Passage")
    return ChunkOut.model_validate(chunk)


async def retry_document(
    session: AsyncSession, principal: Principal, document_id: uuid.UUID, request_id: str | None
) -> CreatedDocument:
    await get_owned(session, Document, document_id, principal, "Document")
    # Row lock: concurrent retries (double clicks) serialize here; the second sees PENDING.
    document = await session.scalar(
        select(Document).where(Document.id == document_id).with_for_update()
    )
    if document is None:  # pragma: no cover - deleted between the two statements
        raise NotFoundError("Document not found")
    if document.status != DocumentStatus.FAILED:
        raise ConflictError(
            f"Only failed documents can be retried (status: {document.status.value})"
        )
    if await session.get(DocumentSource, document_id) is None:
        raise NotFoundError("The original content is no longer stored; upload the document again")
    attempts = await session.scalar(
        select(func.count())
        .select_from(IngestionJob)
        .where(IngestionJob.document_id == document_id)
    )
    job = IngestionJob(
        tenant_id=principal.tenant_id,
        document_id=document_id,
        status=JobStatus.QUEUED,
        attempt=(attempts or 0) + 1,
    )
    session.add(job)
    document.status = DocumentStatus.PENDING
    document.error_message = None
    audit(
        session,
        action="document.retry",
        principal=principal,
        resource_type="document",
        resource_id=document_id,
        request_id=request_id,
    )
    await session.flush()
    return CreatedDocument(document, job.id)


async def delete_document(
    session: AsyncSession, principal: Principal, document_id: uuid.UUID, request_id: str | None
) -> None:
    document = await get_owned(session, Document, document_id, principal, "Document")
    await session.delete(document)  # chunks, vectors, tsvector, jobs, source bytes cascade
    audit(
        session,
        action="document.delete",
        principal=principal,
        resource_type="document",
        resource_id=document_id,
        request_id=request_id,
        details={"title": document.title},
    )
