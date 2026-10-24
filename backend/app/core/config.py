"""Application settings loaded from environment variables (and an optional .env file).

Secrets are typed as ``SecretStr`` so they are masked in ``repr``/``model_dump`` output and
never end up in logs by accident. Validation runs once at startup; an invalid configuration
fails fast with a message that names the offending setting but never echoes its value.
"""

import json
import secrets
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "production"]
LLMProvider = Literal["groq", "nvidia", "gemini", "openrouter", "mock"]
CHAT_PROVIDERS: tuple[str, ...] = ("groq", "nvidia", "gemini", "openrouter", "mock")
EmbeddingProvider = Literal["nvidia", "mock"]
# "local" is a CPU cross-encoder (fastembed/ONNX, optional `rerank-local` extra).
RerankerProvider = Literal["none", "nvidia", "local", "mock"]
RetrievalMode = Literal["dense", "lexical", "hybrid", "hybrid_rerank"]

_PROVIDER_KEY_FIELDS: dict[str, str] = {
    "groq": "groq_api_key",
    "nvidia": "nvidia_api_key",
    "gemini": "gemini_api_key",
    "openrouter": "openrouter_api_key",
}
# OpenRouter bills per token for every model without this suffix. Requiring it means a
# misconfiguration can never silently start spending money.
OPENROUTER_FREE_SUFFIX = ":free"


def parse_chain_entry(entry: str) -> tuple[str, str]:
    """``"provider:model"`` → ``(provider, model)``. Splits on the first colon only, because
    OpenRouter model ids contain one (``vendor/model:free``)."""
    provider, sep, model = entry.strip().partition(":")
    if not sep or not provider or not model:
        raise ValueError(f"LLM_FALLBACK_CHAIN entry {entry!r} must look like 'provider:model'")
    if provider not in CHAT_PROVIDERS:
        raise ValueError(f"LLM_FALLBACK_CHAIN entry {entry!r}: unknown provider {provider!r}")
    return provider, model


MOCK_CHAT_MODEL = "mock-extractive"
# pgvector HNSW indexes support at most 2,000 dimensions for the `vector` type.
MAX_INDEXED_DIMENSION = 2000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Atlas AI"
    environment: Environment = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True
    # Allows mock providers outside tests; clearly labelled in the UI/API as demo output.
    demo_mode: bool = False

    database_url: SecretStr = SecretStr("postgresql+psycopg://atlas:atlas@localhost:5432/atlas")
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout_seconds: float = Field(default=10.0, gt=0)
    db_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)

    # Accepts a JSON list, a comma-separated list, or a single URL (see _parse_list).
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # --- Authentication ---
    # Required in production. In development a random per-process secret is generated, which
    # means tokens stop working after a restart.
    jwt_secret: SecretStr | None = None
    access_token_ttl_minutes: int = Field(default=12 * 60, ge=5, le=7 * 24 * 60)
    allow_registration: bool = True

    # --- Providers ---
    llm_provider: LLMProvider = "groq"
    llm_model: str = "openai/gpt-oss-120b"
    # Ordered chat fallbacks, tried only when the previous one is rate-limited, out of quota,
    # unavailable, timing out, missing the model, or returning an empty/malformed answer.
    # One account per provider. Example (JSON list):
    # ["groq:openai/gpt-oss-20b", "gemini:gemini-3.6-flash",
    #  "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free",
    #  "nvidia:nvidia/nemotron-3-ultra-550b-a55b"]
    llm_fallback_chain: Annotated[list[str], NoDecode] = []
    # Within a chain, fail over quickly instead of waiting on one provider's rate-limit reset.
    chain_max_retries: int = Field(default=1, ge=0, le=5)
    chain_max_backoff_seconds: float = Field(default=2.0, gt=0, le=30)
    llm_temperature: float = Field(default=0.1, ge=0, le=2)
    llm_max_tokens: int = Field(default=1024, ge=64, le=8192)
    # Sent only to models that accept it (Groq gpt-oss, Gemini); keeps hidden reasoning tokens low.
    llm_reasoning_effort: Literal["low", "medium", "high"] | None = "low"

    embedding_provider: EmbeddingProvider = "nvidia"
    embedding_model: str = "nvidia/nemotron-3.5-embed-1b"
    # Requested output dimension (Matryoshka truncation). Must be <= 2000 to be indexable.
    embedding_dimension: int = Field(default=1024, ge=8, le=MAX_INDEXED_DIMENSION)
    embedding_batch_size: int = Field(default=32, ge=1, le=256)

    reranker_provider: RerankerProvider = "none"
    reranker_model: str | None = None
    nvidia_rerank_url: str = "https://ai.api.nvidia.com/v1/retrieval/{model_path}/reranking"

    groq_api_key: SecretStr | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    # GOOGLE_API_KEY is accepted as an alias (Google AI Studio's own variable name).
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("gemini_api_key", "google_api_key")
    )
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    provider_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    provider_max_retries: int = Field(default=3, ge=0, le=10)
    # Never wait longer than this for a rate-limit reset; fail with a clear error instead.
    provider_max_backoff_seconds: float = Field(default=20.0, gt=0, le=120)

    # --- Retrieval defaults (tenants can override some in Settings) ---
    retrieval_mode: RetrievalMode = "hybrid"
    retrieval_top_k: int = Field(default=6, ge=1, le=50)
    retrieval_candidate_k: int = Field(default=24, ge=1, le=200)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    # Evidence-sufficiency heuristics; see docs/retrieval-and-evaluation.md for calibration.
    # 0.25 is calibrated for nvidia/nemotron-3.5-embed-1b@1024 on atlas-handbook-v1: it lets
    # through every answerable question and pre-filters clearly off-topic ones. Similarity scales
    # differ per embedding model, so re-calibrate when the model changes.
    min_dense_similarity: float = Field(default=0.25, ge=-1, le=1)
    min_lexical_coverage: float = Field(default=0.5, ge=0, le=1)
    min_rerank_score: float = -4.0

    # --- Agent budgets ---
    agent_max_retrieval_attempts: int = Field(default=2, ge=1, le=5)
    agent_max_llm_calls: int = Field(default=4, ge=1, le=10)
    agent_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    agent_history_turns: int = Field(default=6, ge=0, le=50)
    max_question_chars: int = Field(default=2000, ge=10, le=20000)

    # --- Ingestion ---
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)
    max_pdf_pages: int = Field(default=500, ge=1)
    chunk_size: int = Field(default=1000, ge=100, le=8000)
    chunk_overlap: int = Field(default=150, ge=0)
    url_allowed_hosts: Annotated[list[str], NoDecode] = []  # empty = any public host
    url_fetch_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    url_max_redirects: int = Field(default=3, ge=0, le=10)

    # --- Abuse controls (per user or client IP, per minute; in-process) ---
    rate_limit_chat_per_minute: int = Field(default=20, ge=1)
    rate_limit_ingest_per_minute: int = Field(default=30, ge=1)
    rate_limit_auth_per_minute: int = Field(default=10, ge=1)
    rate_limit_evaluation_per_hour: int = Field(default=6, ge=1)

    # --- Observability ---
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "atlas-ai"
    otel_enabled: bool = False
    otel_service_name: str = "atlas-backend"

    @field_validator(
        "groq_api_key",
        "nvidia_api_key",
        "gemini_api_key",
        "openrouter_api_key",
        "langsmith_api_key",
        "jwt_secret",
        "reranker_model",
        "llm_reasoning_effort",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # `GROQ_API_KEY=` in an env file means "not configured", not "an empty key".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("cors_origins", "llm_fallback_chain", "url_allowed_hosts", mode="before")
    @classmethod
    def _parse_list(cls, value: object) -> object:
        """Lists from env vars: a JSON list (``["a","b"]``), comma-separated (``a,b``), or a single
        value. Dashboards make the JSON form easy to get wrong, and a typo must not crash boot."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                raise ValueError(
                    "must be a JSON list, a comma-separated list, or a single value"
                ) from None
            return parsed
        return [item.strip() for item in text.split(",") if item.strip()]

    @field_validator("cors_origins")
    @classmethod
    def _normalize_origins(cls, origins: list[str]) -> list[str]:
        # Browsers send Origin without a trailing slash, so "https://x.com/" would never match.
        return [origin.strip().rstrip("/") for origin in origins]

    @field_validator("database_url")
    @classmethod
    def _require_psycopg_driver(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use the 'postgresql+psycopg://' scheme")
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        if self.llm_provider == "mock":
            # The mock has a single model; never report a real model id for mock output.
            self.llm_model = MOCK_CHAT_MODEL
        if self.chunk_overlap * 2 > self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be at most half of CHUNK_SIZE")
        if self.retrieval_top_k > self.retrieval_candidate_k:
            raise ValueError("RETRIEVAL_TOP_K must not exceed RETRIEVAL_CANDIDATE_K")

        uses_mock = "mock" in (self.llm_provider, self.embedding_provider, self.reranker_provider)
        if uses_mock and self.environment == "production" and not self.demo_mode:
            raise ValueError("mock providers are not allowed in production unless DEMO_MODE=true")

        chain = [parse_chain_entry(entry) for entry in self.llm_fallback_chain]
        for provider, model in [(self.llm_provider, self.llm_model), *chain]:
            if provider == "openrouter" and not model.endswith(OPENROUTER_FREE_SUFFIX):
                raise ValueError(
                    f"OpenRouter model {model!r} is not a free model; only ids ending in "
                    f"{OPENROUTER_FREE_SUFFIX!r} are allowed (paid models would incur charges)"
                )
            if provider == "mock" and self.environment == "production" and not self.demo_mode:
                raise ValueError(
                    "mock providers are not allowed in production unless DEMO_MODE=true"
                )

        selected: set[str] = {self.llm_provider, self.embedding_provider, self.reranker_provider}
        selected.update(provider for provider, _ in chain)
        for provider in selected:
            key_field = _PROVIDER_KEY_FIELDS.get(provider)
            if key_field and getattr(self, key_field) is None:
                raise ValueError(
                    f"{key_field.upper()} is required when a {provider} provider is selected"
                )

        if self.reranker_provider != "none" and not self.reranker_model:
            raise ValueError("RERANKER_MODEL is required when RERANKER_PROVIDER is set")
        if self.retrieval_mode == "hybrid_rerank" and self.reranker_provider == "none":
            raise ValueError("RETRIEVAL_MODE=hybrid_rerank requires a RERANKER_PROVIDER")

        if self.environment == "production" and self.jwt_secret is None:
            raise ValueError("JWT_SECRET is required in production")
        if self.jwt_secret is not None and len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")

        if self.langsmith_tracing and self.langsmith_api_key is None:
            raise ValueError("LANGSMITH_API_KEY is required when LANGSMITH_TRACING=true")
        return self

    @property
    def signing_key(self) -> str:
        if self.jwt_secret is None:
            # Development/test only (production requires JWT_SECRET, enforced above).
            self.jwt_secret = SecretStr(secrets.token_urlsafe(48))
        return self.jwt_secret.get_secret_value()

    def configured_chat_providers(self) -> list[str]:
        """Chat providers usable right now (key present), for the settings UI."""
        available = ["mock"] if self.demo_mode or self.environment != "production" else []
        if self.groq_api_key is not None:
            available.append("groq")
        for provider in ("nvidia", "gemini", "openrouter"):
            if getattr(self, f"{provider}_api_key") is not None:
                available.append(provider)
        return available

    def chat_chain(self) -> list[tuple[str, str]]:
        """The configured fallbacks as ``(provider, model)`` pairs, in order."""
        return [parse_chain_entry(entry) for entry in self.llm_fallback_chain]


@lru_cache
def get_settings() -> Settings:
    return Settings()
