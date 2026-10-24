"""Demo workspace seeding for local runs and the public demo deployment.

Creates (idempotently) a workspace with an owner account and a "Company Handbook" collection
containing the sample documents from the evaluation dataset.
"""

import hashlib

from sqlalchemy import func, select

from app.auth.security import hash_password
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models.knowledge import (
    Collection,
    Document,
    DocumentSource,
    DocumentStatus,
    IngestionJob,
    JobStatus,
    SourceType,
)
from app.db.models.tenancy import Membership, MembershipRole, Tenant, User
from app.db.session import Database
from app.evaluation.dataset import load_dataset
from app.ingestion.pipeline import IngestionPipeline
from app.providers.factory import ProviderRegistry

logger = get_logger("atlas.demo")
DEMO_SLUG = "northwind-demo"
MIME = {
    "md": "text/markdown",
    "txt": "text/plain",
    "html": "text/html",
    "csv": "text/csv",
    "json": "application/json",
}


async def seed_demo(settings: Settings, *, email: str, password: str) -> None:
    if len(password) < 10:
        raise SystemExit("demo password must be at least 10 characters")
    database = Database.from_settings(settings)
    providers = ProviderRegistry(settings)
    pipeline = IngestionPipeline(database.sessionmaker, providers, settings, max_concurrency=1)
    try:
        pending: list[tuple[object, object]] = []
        async with database.sessionmaker() as session, session.begin():
            tenant = await session.scalar(select(Tenant).where(Tenant.slug == DEMO_SLUG))
            if tenant is None:
                tenant = Tenant(name="Northwind Robotics (demo)", slug=DEMO_SLUG)
                session.add(tenant)
                await session.flush()
            user = await session.scalar(select(User).where(func.lower(User.email) == email.lower()))
            if user is None:
                user = User(
                    email=email.lower(),
                    display_name="Demo User",
                    password_hash=hash_password(password),
                )
                session.add(user)
                await session.flush()
                session.add(
                    Membership(tenant_id=tenant.id, user_id=user.id, role=MembershipRole.OWNER)
                )
            else:
                user.password_hash = hash_password(password)
            collection = await session.scalar(
                select(Collection).where(
                    Collection.tenant_id == tenant.id, Collection.name == "Company Handbook"
                )
            )
            if collection is None:
                collection = Collection(
                    tenant_id=tenant.id,
                    name="Company Handbook",
                    created_by=user.id,
                    description="Northwind Robotics policies, runbooks, and products (fictional).",
                )
                session.add(collection)
                await session.flush()
            for path in load_dataset("atlas-handbook-v1").documents:
                content = path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                exists = await session.scalar(
                    select(Document.id).where(
                        Document.collection_id == collection.id, Document.content_hash == digest
                    )
                )
                if exists is not None:
                    continue
                document = Document(
                    tenant_id=tenant.id,
                    collection_id=collection.id,
                    title=path.name,
                    source_type=SourceType.UPLOAD,
                    mime_type=MIME.get(path.suffix.lstrip("."), "text/plain"),
                    size_bytes=len(content),
                    content_hash=digest,
                    status=DocumentStatus.PENDING,
                    created_by=user.id,
                )
                session.add(document)
                await session.flush()
                session.add(
                    DocumentSource(document_id=document.id, filename=path.name, content=content)
                )
                job = IngestionJob(
                    tenant_id=tenant.id, document_id=document.id, status=JobStatus.QUEUED
                )
                session.add(job)
                await session.flush()
                pending.append((document.id, job.id))
        for document_id, job_id in pending:
            await pipeline.process(document_id, job_id)  # type: ignore[arg-type]
        logger.info("demo_seeded", email=email.lower(), new_documents=len(pending))
        print(
            f"Demo workspace ready: sign in as {email.lower()} ({len(pending)} documents ingested)"
        )
    finally:
        await providers.aclose()
        await database.dispose()
