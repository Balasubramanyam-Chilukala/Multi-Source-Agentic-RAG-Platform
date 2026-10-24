"""Background document processing: parse → normalize/chunk → embed → store.

Runs as an in-process background task after the upload request has committed. Each run is
idempotent (existing chunks for the document are replaced), so a failed job can be retried
safely. A semaphore bounds concurrent ingestions to protect memory and provider rate limits.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models.knowledge import (
    Chunk,
    Collection,
    Document,
    DocumentSource,
    DocumentStatus,
    IngestionJob,
    JobStatus,
)
from app.db.models.tenancy import Tenant
from app.ingestion.chunker import chunk_sections
from app.ingestion.parsers import NoExtractableTextError, ParseError, parse_document
from app.providers.base import EmbeddingProvider, ProviderError
from app.providers.factory import ProviderRegistry
from app.schemas.settings import WorkspaceSettings
from app.services.embedding_registry import ensure_embedding_model

logger = get_logger("atlas.ingestion")

INTERRUPTED_MESSAGE = "Processing was interrupted by a server restart. Retry to resume."


def embedding_text(title: str, section: str | None, body: str) -> str:
    """Text sent to the embedder: the chunk plus its document/section context."""
    header = f"{title} — {section}" if section else title
    return f"{header}\n\n{body}"


class IngestionPipeline:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        providers: ProviderRegistry,
        settings: Settings,
        *,
        max_concurrency: int = 2,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._providers = providers
        self._settings = settings
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def process(self, document_id: uuid.UUID, job_id: uuid.UUID) -> None:
        async with self._semaphore:
            try:
                await self._process(document_id, job_id)
            except Exception as exc:  # last-resort guard: never leave a job "running"
                logger.exception("ingestion_crashed", document_id=str(document_id))
                await self._fail(
                    document_id, job_id, "internal_error", "Unexpected ingestion error", exc
                )

    async def _process(self, document_id: uuid.UUID, job_id: uuid.UUID) -> None:
        async with self._sessionmaker() as session:
            document = await session.get(Document, document_id, with_for_update=True)
            source = await session.get(DocumentSource, document_id)
            job = await session.get(IngestionJob, job_id, with_for_update=True)
            if document is None or job is None:
                logger.warning("ingestion_target_missing", document_id=str(document_id))
                return
            # Claim the job: a duplicate or superseded job (e.g. two quick retries) must not
            # touch a document that another job already processed or is processing.
            if job.status != JobStatus.QUEUED or document.status != DocumentStatus.PENDING:
                logger.info(
                    "ingestion_job_skipped", job_id=str(job_id), status=document.status.value
                )
                if job.status == JobStatus.QUEUED:
                    job.status = JobStatus.FAILED
                    job.error_code, job.error_message = "superseded", "Superseded by another job"
                    await session.commit()
                return
            if source is None:
                await self._fail(
                    document_id,
                    job_id,
                    "source_missing",
                    "Original content is no longer available; upload the file again.",
                )
                return
            tenant = await session.get(Tenant, document.tenant_id)
            workspace = WorkspaceSettings.effective(
                self._settings, tenant.settings if tenant else {}
            )
            document.status = DocumentStatus.PROCESSING
            document.error_message = None
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(UTC)
            await session.commit()
            content, filename, mime = source.content, source.filename, document.mime_type
            title = document.title

        log = logger.bind(document_id=str(document_id), job_id=str(job_id))
        started = asyncio.get_running_loop().time()
        try:
            parsed = await asyncio.to_thread(
                parse_document,
                content,
                filename=filename,
                mime_type=mime,
                max_pdf_pages=self._settings.max_pdf_pages,
            )
            chunks = chunk_sections(
                parsed.sections,
                chunk_size=workspace.chunk_size,
                chunk_overlap=workspace.chunk_overlap,
            )
            if not chunks:
                raise NoExtractableTextError("No text remained after normalization")
            embedder = self._providers.embeddings()
            vectors = await embedder.embed(
                [embedding_text(parsed.title or title, c.section, c.text) for c in chunks],
                input_type="passage",
            )
        except NoExtractableTextError as exc:
            await self._fail(
                document_id, job_id, exc.code, str(exc), status=DocumentStatus.UNSUPPORTED
            )
            return
        except ParseError as exc:
            await self._fail(document_id, job_id, exc.code, str(exc))
            return
        except ProviderError as exc:
            await self._fail(
                document_id, job_id, exc.category, f"Embedding failed: {exc.user_message}"
            )
            return

        async with self._sessionmaker() as session, session.begin():
            model = await ensure_embedding_model(session, embedder)
            document = await session.get(Document, document_id, with_for_update=True)
            if document is None:  # deleted while processing
                return
            await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
            session.add_all(
                Chunk(
                    tenant_id=document.tenant_id,
                    document_id=document.id,
                    collection_id=document.collection_id,
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    token_count=chunk.token_estimate,
                    page_number=chunk.page,
                    section=chunk.section,
                    meta={"kind": parsed.kind},
                    embedding=vector,
                    embedding_model_id=model.id,
                    embedding_dim=model.dimension,
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            )
            if parsed.title and document.title == (filename or document.title):
                document.title = parsed.title[:500]
            document.status = DocumentStatus.READY
            document.chunk_count = len(chunks)
            document.page_count = parsed.page_count
            document.meta = {
                **document.meta,
                **parsed.metadata,
                "kind": parsed.kind,
                "embedding_model": f"{model.provider}/{model.model}@{model.version}",
            }
            collection = await session.get(Collection, document.collection_id)
            if collection is not None and collection.active_embedding_model_id is None:
                collection.active_embedding_model_id = model.id
            # Data minimization: raw bytes are no longer needed once chunks exist.
            await session.execute(
                delete(DocumentSource).where(DocumentSource.document_id == document_id)
            )
            await session.execute(
                update(IngestionJob)
                .where(IngestionJob.id == job_id)
                .values(
                    status=JobStatus.SUCCEEDED,
                    finished_at=datetime.now(UTC),
                    error_code=None,
                    error_message=None,
                )
            )
        log.info(
            "ingestion_succeeded",
            chunks=len(chunks),
            kind=parsed.kind,
            duration_ms=round((asyncio.get_running_loop().time() - started) * 1000, 1),
        )

    async def _fail(
        self,
        document_id: uuid.UUID,
        job_id: uuid.UUID,
        code: str,
        message: str,
        exc: BaseException | None = None,
        *,
        status: DocumentStatus = DocumentStatus.FAILED,
    ) -> None:
        logger.warning(
            "ingestion_failed",
            document_id=str(document_id),
            error_code=code,
            error_type=type(exc).__name__ if exc else None,
        )
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(status=status, error_message=message[:1000])
            )
            await session.execute(
                update(IngestionJob)
                .where(IngestionJob.id == job_id)
                .values(
                    status=JobStatus.FAILED,
                    error_code=code,
                    error_message=message[:1000],
                    finished_at=datetime.now(UTC),
                )
            )

    async def recover_interrupted(self) -> int:
        """Mark work left unfinished by a previous process as failed (and retryable).

        Assumes a single backend instance; see docs/deployment.md.
        """
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                update(Document)
                .where(Document.status.in_([DocumentStatus.PENDING, DocumentStatus.PROCESSING]))
                .values(status=DocumentStatus.FAILED, error_message=INTERRUPTED_MESSAGE)
                .returning(Document.id)
            )
            ids = list(result.scalars())
            await session.execute(
                update(IngestionJob)
                .where(IngestionJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
                .values(
                    status=JobStatus.FAILED,
                    error_code="interrupted",
                    error_message=INTERRUPTED_MESSAGE,
                )
            )
        if ids:
            logger.warning("ingestion_recovered_interrupted", count=len(ids))
        return len(ids)

    async def reembed_collection(self, collection_id: uuid.UUID) -> int:
        """Re-embed every chunk in a collection with the configured model, then switch the
        collection's active model. Runs in one transaction, so readers never see a mix."""
        embedder: EmbeddingProvider = self._providers.embeddings()
        async with self._sessionmaker() as session, session.begin():
            model = await ensure_embedding_model(session, embedder)
            rows = (
                await session.execute(
                    select(Chunk, Document.title)
                    .join(Document, Document.id == Chunk.document_id)
                    .where(Chunk.collection_id == collection_id)
                    .order_by(Chunk.id)
                )
            ).all()
            vectors = (
                await embedder.embed(
                    [embedding_text(title, c.section, c.text) for c, title in rows],
                    input_type="passage",
                )
                if rows
                else []
            )
            for (chunk, _), vector in zip(rows, vectors, strict=True):
                chunk.embedding = vector
                chunk.embedding_model_id = model.id
                chunk.embedding_dim = model.dimension
            collection = await session.get(Collection, collection_id)
            if collection is not None:
                collection.active_embedding_model_id = model.id
        return len(rows)
