"""Database-level guarantees the application relies on, verified against real PostgreSQL."""

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator

import pytest
from alembic import command
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import Chunk, Collection, Document, EmbeddingModel, Tenant, User
from app.db.models.knowledge import SourceType
from tests.conftest import alembic_config

pytestmark = pytest.mark.integration


@pytest.fixture
async def session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    """A session inside an outer transaction that is rolled back, so tests never interfere."""
    await asyncio.to_thread(command.upgrade, alembic_config(test_database_url), "head")
    engine = create_async_engine(test_database_url)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint") as session:
            yield session
        await transaction.rollback()
    await engine.dispose()


async def _document(session: AsyncSession, dimension: int = 3) -> tuple[Document, EmbeddingModel]:
    tenant = Tenant(name="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}")
    session.add(tenant)
    await session.flush()
    model = EmbeddingModel(
        provider="mock", model=f"mock-{uuid.uuid4().hex[:8]}", dimension=dimension
    )
    collection = Collection(tenant_id=tenant.id, name="Handbook")
    session.add_all([model, collection])
    await session.flush()
    document = Document(
        tenant_id=tenant.id,
        collection_id=collection.id,
        title="Leave policy",
        source_type=SourceType.TEXT,
        content_hash=hashlib.sha256(b"leave policy").hexdigest(),
    )
    session.add(document)
    await session.flush()
    return document, model


def _chunk(document: Document, ordinal: int, body: str, **kwargs: object) -> Chunk:
    return Chunk(
        tenant_id=document.tenant_id,
        document_id=document.id,
        collection_id=document.collection_id,
        ordinal=ordinal,
        text=body,
        **kwargs,
    )


async def test_chunk_with_matching_dimension_is_stored(session: AsyncSession) -> None:
    document, model = await _document(session)
    session.add(
        _chunk(
            document,
            0,
            "Employees accrue vacation days monthly.",
            embedding=[0.1, 0.2, 0.3],
            embedding_model_id=model.id,
            embedding_dim=model.dimension,
        )
    )
    await session.flush()


async def test_embedding_dimension_mismatch_is_rejected(session: AsyncSession) -> None:
    document, model = await _document(session, dimension=3)
    session.add(
        _chunk(
            document,
            0,
            "text",
            embedding=[0.1, 0.2, 0.3, 0.4],
            embedding_model_id=model.id,
            embedding_dim=3,
        )
    )
    with pytest.raises(IntegrityError, match="ck_chunks_embedding_consistency"):
        await session.flush()


async def test_embedding_without_model_is_rejected(session: AsyncSession) -> None:
    document, _ = await _document(session)
    session.add(_chunk(document, 0, "text", embedding=[0.1, 0.2, 0.3], embedding_dim=3))
    with pytest.raises(IntegrityError, match="ck_chunks_embedding_consistency"):
        await session.flush()


async def test_full_text_vector_is_generated_and_stemmed(session: AsyncSession) -> None:
    document, _ = await _document(session)
    session.add_all(
        [
            _chunk(document, 0, "Employees accrue vacation days every month."),
            _chunk(document, 1, "Expense reports are due on Fridays."),
        ]
    )
    await session.flush()

    query = func.websearch_to_tsquery("english", "accruing vacations")
    matches = (
        await session.scalars(
            select(Chunk.ordinal).where(Chunk.document_id == document.id, Chunk.tsv.op("@@")(query))
        )
    ).all()
    assert matches == [0]


async def test_deleting_document_cascades_to_chunks(session: AsyncSession) -> None:
    document, _ = await _document(session)
    session.add_all([_chunk(document, i, f"chunk {i}") for i in range(3)])
    await session.flush()

    await session.delete(document)
    await session.flush()

    remaining = await session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.document_id == document.id)
    )
    assert remaining == 0


async def test_duplicate_content_hash_in_collection_is_rejected(session: AsyncSession) -> None:
    document, _ = await _document(session)
    session.add(
        Document(
            tenant_id=document.tenant_id,
            collection_id=document.collection_id,
            title="Same bytes, different title",
            source_type=SourceType.UPLOAD,
            content_hash=document.content_hash,
        )
    )
    with pytest.raises(IntegrityError, match="uq_documents_collection_id_content_hash"):
        await session.flush()


async def test_user_email_is_unique_case_insensitively(session: AsyncSession) -> None:
    session.add(User(email="ada@example.com", display_name="Ada"))
    await session.flush()
    session.add(User(email="ADA@Example.com", display_name="Ada again"))
    with pytest.raises(IntegrityError, match="uq_users_email_lower"):
        await session.flush()


async def test_status_check_constraint_rejects_unknown_values(session: AsyncSession) -> None:
    document, _ = await _document(session)
    with pytest.raises(IntegrityError, match="ck_documents_document_status"):
        await session.execute(
            text("UPDATE documents SET status = 'bogus' WHERE id = :id"), {"id": document.id}
        )
