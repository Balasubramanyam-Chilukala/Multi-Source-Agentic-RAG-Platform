import json
import logging

import pytest
import structlog

from app.core.logging import REDACTED, configure_logging, get_logger, redact_sensitive

GROQ_KEY = "gsk_" + "a" * 40
NVIDIA_KEY = "nvapi-" + "b" * 40


def _redact(**event: object) -> dict[str, object]:
    return dict(redact_sensitive(None, "info", dict(event)))


def test_redacts_sensitive_keys() -> None:
    result = _redact(event="x", api_key="anything", groq_api_key="v", password="p", token="t")
    assert result["api_key"] == REDACTED
    assert result["groq_api_key"] == REDACTED
    assert result["password"] == REDACTED
    assert result["token"] == REDACTED
    assert result["event"] == "x"


def test_redacts_nested_headers() -> None:
    result = _redact(headers={"Authorization": "Bearer abc", "Accept": "application/json"})
    assert result["headers"] == {"Authorization": REDACTED, "Accept": "application/json"}


def test_redacts_credential_shaped_values_under_any_key() -> None:
    result = _redact(
        message=f"provider rejected key {GROQ_KEY}",
        detail=[f"nvidia {NVIDIA_KEY}", "Bearer eyJhbGciOi.payload.sig"],
        url="postgresql+psycopg://atlas:hunter2@db:5432/atlas",
    )
    assert GROQ_KEY not in str(result)
    assert NVIDIA_KEY not in str(result)
    assert "eyJhbGciOi" not in str(result)
    assert "hunter2" not in str(result)
    assert str(result["url"]).endswith("db:5432/atlas")


def test_leaves_ordinary_content_untouched() -> None:
    result = _redact(event="document_ingested", chunk_count=12, title="Quarterly report")
    assert result == {"event": "document_ingested", "chunk_count": 12, "title": "Quarterly report"}


def test_json_output_includes_context_and_never_secrets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO", json_logs=True)
    structlog.contextvars.bind_contextvars(request_id="req-123")
    try:
        get_logger("test").info("provider_call", groq_api_key=GROQ_KEY, note=f"used {NVIDIA_KEY}")
        logging.getLogger("third.party").warning("stdlib message with %s", GROQ_KEY)
    finally:
        structlog.contextvars.clear_contextvars()

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert lines[0]["event"] == "provider_call"
    assert lines[0]["request_id"] == "req-123"
    assert lines[0]["level"] == "info"
    assert "timestamp" in lines[0]
    assert lines[1]["event"] == f"stdlib message with {REDACTED}"
    output = json.dumps(lines)
    assert GROQ_KEY not in output
    assert NVIDIA_KEY not in output


def test_redacts_bare_jwts_and_sk_keys() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3OCJ9.c2lnbmF0dXJlX3ZhbHVl"
    result = _redact(note=f"token={jwt}", other="sk-" + "z" * 30)
    assert jwt not in str(result) and "sk-zzzz" not in str(result)
