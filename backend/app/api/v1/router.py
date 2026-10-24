from fastapi import APIRouter

from app.api.v1 import (
    auth,
    chat,
    collections,
    conversations,
    dashboard,
    documents,
    evaluations,
    search,
    settings,
    workspace,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(collections.router)
api_router.include_router(documents.router)
api_router.include_router(documents.chunk_router)
api_router.include_router(search.router)
api_router.include_router(chat.router)
api_router.include_router(conversations.router)
api_router.include_router(conversations.feedback_router)
api_router.include_router(conversations.runs_router)
api_router.include_router(evaluations.router)
api_router.include_router(settings.router)
api_router.include_router(workspace.router)
