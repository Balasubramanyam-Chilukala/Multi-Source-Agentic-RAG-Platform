"""Direct retrieval endpoint: inspect what each retrieval mode returns, without an LLM call."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import PrincipalDep, ProvidersDep, SessionDep, SettingsDep, user_rate_limit
from app.api.v1.settings import load_workspace_settings
from app.core.errors import BadRequestError
from app.db.models.knowledge import Collection
from app.retrieval.service import Retriever
from app.retrieval.types import RetrievalConfig, RetrievalModeName
from app.schemas.settings import WorkspaceSettings

router = APIRouter(prefix="/search", tags=["retrieval"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    collection_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    mode: RetrievalModeName | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)


class SearchResponse(BaseModel):
    query: str
    mode: str
    chunks: list[dict[str, Any]]
    sufficiency: dict[str, Any]
    timings_ms: dict[str, float]
    warnings: list[str]
    embedding_model: str | None
    reranker: str | None


async def resolve_collections(
    session: SessionDep, principal: PrincipalDep, requested: list[uuid.UUID] | None
) -> list[uuid.UUID]:
    """Restrict requested collection ids to the caller's tenant (all of them if none given)."""
    query = select(Collection.id).where(Collection.tenant_id == principal.tenant_id)
    if requested:
        query = query.where(Collection.id.in_(requested))
    else:
        # Managed collections (the evaluation corpus) are only searched when asked for explicitly.
        query = query.where(Collection.settings["managed"].astext.is_(None))
    ids = list(await session.scalars(query))
    if requested and len(ids) != len(set(requested)):
        raise BadRequestError("One or more collections were not found")
    return ids


def retrieval_config(
    settings: SettingsDep, workspace: WorkspaceSettings, mode: str | None, top_k: int | None
) -> RetrievalConfig:
    return RetrievalConfig(
        mode=mode or workspace.retrieval_mode,  # type: ignore[arg-type]
        top_k=top_k or workspace.top_k,
        candidate_k=max(settings.retrieval_candidate_k, top_k or workspace.top_k),
        rrf_k=settings.rrf_k,
        min_dense_similarity=workspace.min_dense_similarity,
        min_lexical_coverage=settings.min_lexical_coverage,
        min_rerank_score=settings.min_rerank_score,
    )


@router.post("", response_model=SearchResponse, dependencies=[Depends(user_rate_limit("chat"))])
async def search(
    body: SearchRequest,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    providers: ProvidersDep,
    workspace: Annotated[WorkspaceSettings, Depends(load_workspace_settings)],
) -> SearchResponse:
    if body.mode == "hybrid_rerank" and providers.reranker() is None:
        raise BadRequestError("Reranking is not available: no reranker is configured")
    collection_ids = await resolve_collections(session, principal, body.collection_ids)
    retriever = Retriever(providers.embeddings(), providers.reranker())
    result = await retriever.retrieve(
        session,
        tenant_id=principal.tenant_id,
        collection_ids=collection_ids,
        query=body.query,
        config=retrieval_config(settings, workspace, body.mode, body.top_k),
    )
    return SearchResponse(
        query=result.query,
        mode=result.config.mode,
        chunks=[c.to_dict() for c in result.chunks],
        sufficiency=vars(result.sufficiency),
        timings_ms=result.timings_ms,
        warnings=result.warnings,
        embedding_model=result.embedding_model,
        reranker=result.reranker,
    )
