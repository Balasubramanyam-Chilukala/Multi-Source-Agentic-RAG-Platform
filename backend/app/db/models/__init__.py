"""Import every model so ``Base.metadata`` is complete for Alembic and tests."""

from app.db.models.audit import AuditLog
from app.db.models.conversation import AgentRun, Conversation, Feedback, Message
from app.db.models.evaluation import EvaluationCaseResult, EvaluationRun
from app.db.models.knowledge import (
    Chunk,
    Collection,
    Document,
    DocumentSource,
    EmbeddingModel,
    IngestionJob,
)
from app.db.models.tenancy import Membership, Tenant, User

__all__ = [
    "AgentRun",
    "AuditLog",
    "Chunk",
    "Collection",
    "Conversation",
    "Document",
    "DocumentSource",
    "EmbeddingModel",
    "EvaluationCaseResult",
    "EvaluationRun",
    "Feedback",
    "IngestionJob",
    "Membership",
    "Message",
    "Tenant",
    "User",
]
