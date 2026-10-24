from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

FAKE_GROQ_KEY = "gsk_" + "x" * 40


def test_mock_providers_are_valid_in_test_environment(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    assert settings.llm_provider == "mock"
    assert settings.groq_api_key is None


def test_selected_provider_requires_its_api_key(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="GROQ_API_KEY is required"):
        make_settings(llm_provider="groq")


def test_blank_api_key_counts_as_missing(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="GROQ_API_KEY is required"):
        make_settings(llm_provider="groq", groq_api_key="  ")


def test_env_example_parses_with_keys_filled_in(tmp_path: Path) -> None:
    example = (Path(__file__).resolve().parents[3] / ".env.example").read_text()
    filled = (
        example.replace("GROQ_API_KEY=", f"GROQ_API_KEY={FAKE_GROQ_KEY}")
        .replace("NVIDIA_API_KEY=", "NVIDIA_API_KEY=nvapi-" + "y" * 40)
        .replace("GEMINI_API_KEY=", "GEMINI_API_KEY=gem-" + "z" * 30)
        .replace("OPENROUTER_API_KEY=", "OPENROUTER_API_KEY=sk-or-v1-" + "w" * 40)
    )
    env_file = tmp_path / ".env"
    env_file.write_text(filled)

    settings = Settings(_env_file=env_file)

    assert settings.environment == "development"
    assert settings.embedding_provider == "nvidia"
    assert settings.reranker_provider == "none"
    assert settings.reranker_model is None
    assert settings.cors_origins == ["http://localhost:5173"]
    assert [p for p, _ in settings.chat_chain()] == [
        "groq",
        "gemini",
        "openrouter",
        "openrouter",
        "nvidia",
    ]


def test_nvidia_embeddings_require_nvidia_key(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="NVIDIA_API_KEY is required"):
        make_settings(embedding_provider="nvidia")


def test_secrets_are_masked_in_repr_and_dumps(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(llm_provider="groq", groq_api_key=FAKE_GROQ_KEY)

    assert settings.groq_api_key is not None
    assert settings.groq_api_key.get_secret_value() == FAKE_GROQ_KEY
    assert FAKE_GROQ_KEY not in repr(settings)
    assert FAKE_GROQ_KEY not in settings.model_dump_json()
    assert "atlas:atlas@" not in repr(settings)


def test_validation_error_does_not_echo_secret(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(database_url=f"mysql://user:{FAKE_GROQ_KEY}@db/atlas")
    assert FAKE_GROQ_KEY not in str(exc_info.value)


def test_mock_providers_rejected_in_production_without_demo_mode(
    make_settings: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError, match="DEMO_MODE"):
        make_settings(environment="production")

    secret = "s" * 40
    assert make_settings(environment="production", demo_mode=True, jwt_secret=secret).demo_mode


def test_production_requires_jwt_secret(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET is required"):
        make_settings(environment="production", demo_mode=True)


def test_chunk_overlap_must_be_smaller_than_chunk_size(
    make_settings: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP"):
        make_settings(chunk_size=500, chunk_overlap=500)


def test_reranker_requires_model(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="RERANKER_MODEL"):
        make_settings(reranker_provider="mock")


def test_langsmith_tracing_requires_key(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="LANGSMITH_API_KEY"):
        make_settings(langsmith_tracing=True)


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", FAKE_GROQ_KEY)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("CORS_ORIGINS", '["https://atlas.example.com"]')

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "groq"
    assert settings.cors_origins == ["https://atlas.example.com"]


def test_mock_provider_reports_the_mock_model(make_settings: Callable[..., Settings]) -> None:
    from app.schemas.settings import WorkspaceSettings  # noqa: PLC0415

    settings = make_settings(llm_provider="mock", llm_model="openai/gpt-oss-120b")
    assert settings.llm_model == "mock-extractive"
    assert WorkspaceSettings.effective(settings, {}).llm_model == "mock-extractive"
