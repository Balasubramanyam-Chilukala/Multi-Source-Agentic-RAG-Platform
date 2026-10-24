"""HTTP-level behaviour of the app shell: request IDs, error envelopes, and health probes."""

import re
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.core.errors import ConflictError
from app.db.session import Database, get_database
from app.main import create_app

UNREACHABLE_DB_URL = "postgresql+psycopg://atlas:s3cret-pw@127.0.0.1:1/atlas"


@pytest.fixture
def app(make_settings: Callable[..., Settings]) -> FastAPI:
    app = create_app(make_settings(log_json=True))

    @app.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("internal detail that must not leak")

    @app.get("/_test/conflict")
    async def conflict() -> None:
        raise ConflictError("Document already exists in this collection")

    @app.get("/_test/typed")
    async def typed(limit: int) -> dict[str, int]:
        return {"limit": limit}

    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # raise_app_exceptions=False: assert on the response a real client would receive.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_liveness(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_generates_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live")
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


async def test_propagates_valid_client_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": "trace-abc.123"})
    assert response.headers["X-Request-ID"] == "trace-abc.123"


@pytest.mark.parametrize("bad_id", ["a" * 65, "has space", "semi;colon", "inject\tvalue"])
async def test_replaces_unsafe_client_request_id(client: httpx.AsyncClient, bad_id: str) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": bad_id})
    assert response.headers["X-Request-ID"] != bad_id
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


async def test_unknown_route_uses_error_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_unhandled_exception_returns_generic_500(client: httpx.AsyncClient) -> None:
    response = await client.get("/_test/boom", headers={"X-Request-ID": "req-boom"})
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred",
            "request_id": "req-boom",
        }
    }
    assert "internal detail" not in response.text
    assert "Traceback" not in response.text
    assert response.headers["X-Request-ID"] == "req-boom"


async def test_app_error_maps_to_status_and_code(client: httpx.AsyncClient) -> None:
    response = await client.get("/_test/conflict")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert response.json()["error"]["message"] == "Document already exists in this collection"


async def test_validation_error_envelope_does_not_echo_input(client: httpx.AsyncClient) -> None:
    response = await client.get("/_test/typed", params={"limit": "not-a-number-xyz"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["query", "limit"]
    assert "not-a-number-xyz" not in response.text


async def test_readiness_reports_unavailable_database_without_leaking_dsn(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    database = Database(
        create_async_engine(UNREACHABLE_DB_URL, connect_args={"connect_timeout": 1})
    )
    app.dependency_overrides[get_database] = lambda: database
    try:
        response = await client.get("/health/ready")
    finally:
        await database.dispose()

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"]["status"] == "fail"
    assert "s3cret-pw" not in response.text
    assert "127.0.0.1" not in response.text


async def test_cors_preflight_allows_configured_origin(client: httpx.AsyncClient) -> None:
    response = await client.options(
        "/health/live",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "X-Request-ID" in response.headers


async def test_cors_rejects_unknown_origin(client: httpx.AsyncClient) -> None:
    response = await client.get("/health/live", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


async def test_openapi_documents_health_endpoints(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert {"/health/live", "/health/ready"} <= set(paths)
