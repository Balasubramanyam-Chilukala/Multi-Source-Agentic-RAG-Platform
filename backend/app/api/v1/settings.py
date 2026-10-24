from fastapi import APIRouter, Request

from app.api.deps import AdminDep, PrincipalDep, SessionDep, SettingsDep
from app.core.config import OPENROUTER_FREE_SUFFIX
from app.core.errors import BadRequestError
from app.db.models.tenancy import Tenant
from app.schemas.settings import (
    ProviderStatus,
    SystemInfo,
    WorkspaceSettings,
    WorkspaceSettingsUpdate,
)
from app.services.common import audit

router = APIRouter(prefix="/settings", tags=["settings"])


async def load_workspace_settings(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep
) -> WorkspaceSettings:
    tenant = await session.get(Tenant, principal.tenant_id)
    return WorkspaceSettings.effective(settings, tenant.settings if tenant else {})


@router.get("", response_model=WorkspaceSettings)
async def get_workspace_settings(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep
) -> WorkspaceSettings:
    return await load_workspace_settings(session, settings, principal)


@router.put("", response_model=WorkspaceSettings)
async def update_workspace_settings(
    body: WorkspaceSettingsUpdate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
) -> WorkspaceSettings:
    """Replace the workspace overrides. Unset fields fall back to server defaults."""
    if body.llm_provider and body.llm_provider not in settings.configured_chat_providers():
        raise BadRequestError(f"Provider {body.llm_provider!r} is not configured on this server")
    if (
        body.llm_provider == "openrouter"
        and body.llm_model
        and not body.llm_model.endswith(OPENROUTER_FREE_SUFFIX)
    ):
        raise BadRequestError("Only free OpenRouter models (ids ending in ':free') are allowed")
    if body.retrieval_mode == "hybrid_rerank" and settings.reranker_provider == "none":
        raise BadRequestError("Reranking is not available: no reranker is configured on the server")
    if body.llm_provider and body.llm_provider != settings.llm_provider and not body.llm_model:
        raise BadRequestError("llm_model is required when choosing a non-default provider")
    tenant = await session.get(Tenant, principal.tenant_id)
    if tenant is None:  # pragma: no cover - principal implies the tenant exists
        raise BadRequestError("Workspace not found")
    tenant.settings = body.model_dump(exclude_none=True)
    effective = WorkspaceSettings.effective(settings, tenant.settings)
    if effective.chunk_overlap * 2 > effective.chunk_size:
        raise BadRequestError("chunk_overlap must be at most half of chunk_size")
    audit(
        session,
        action="settings.update",
        principal=principal,
        resource_type="tenant",
        resource_id=principal.tenant_id,
        request_id=request.state.request_id,
        details={"fields": sorted(tenant.settings)},
    )
    return effective


@router.get("/system", response_model=SystemInfo)
async def system_info(settings: SettingsDep, principal: PrincipalDep) -> SystemInfo:
    configured = settings.configured_chat_providers()
    return SystemInfo(
        environment=settings.environment,
        demo_mode=settings.demo_mode,
        default_llm_provider=settings.llm_provider,
        default_llm_model=settings.llm_model,
        fallback_chain=list(settings.llm_fallback_chain),
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model
        if settings.embedding_provider != "mock"
        else "hashing-embed",
        embedding_dimension=settings.embedding_dimension
        if settings.embedding_provider != "mock"
        else 384,
        reranker_provider=settings.reranker_provider,
        reranker_model=settings.reranker_model,
        providers=[
            ProviderStatus(name="groq", configured="groq" in configured, capabilities=["chat"]),
            ProviderStatus(name="gemini", configured="gemini" in configured, capabilities=["chat"]),
            ProviderStatus(
                name="openrouter", configured="openrouter" in configured, capabilities=["chat"]
            ),
            ProviderStatus(
                name="nvidia",
                configured="nvidia" in configured,
                capabilities=["chat", "embeddings", "rerank"],
            ),
            ProviderStatus(
                name="mock",
                configured="mock" in configured,
                capabilities=["chat", "embeddings", "rerank"],
            ),
        ],
        max_upload_bytes=settings.max_upload_bytes,
        agent_max_retrieval_attempts=settings.agent_max_retrieval_attempts,
        agent_max_llm_calls=settings.agent_max_llm_calls,
    )
