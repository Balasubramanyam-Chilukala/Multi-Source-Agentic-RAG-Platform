"""Fixtures for API-level tests against a real PostgreSQL (``docker compose up -d db``).

Every test registers fresh users/workspaces, so tests are isolated by tenant without truncating
tables. Providers are the deterministic mocks; nothing calls an external API.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from alembic import command
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app
from app.providers.factory import ProviderRegistry
from tests.conftest import alembic_config

PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session")
async def migrated_database_url(test_database_url: str) -> str:
    await asyncio.to_thread(command.upgrade, alembic_config(test_database_url), "head")
    return test_database_url


@pytest.fixture
def api_settings(migrated_database_url: str) -> Callable[..., Settings]:
    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "environment": "test",
            "database_url": migrated_database_url,
            "llm_provider": "mock",
            "embedding_provider": "mock",
            "reranker_provider": "none",
            "jwt_secret": "integration-test-secret-" + "x" * 20,
            "log_json": True,
            "log_level": "WARNING",
            "rate_limit_chat_per_minute": 1000,
            "rate_limit_ingest_per_minute": 1000,
            "rate_limit_auth_per_minute": 1000,
            "min_dense_similarity": 0.3,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return factory


@asynccontextmanager
async def running_app(
    settings: Settings, providers: ProviderRegistry | None = None
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    app = create_app(settings, providers=providers)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=60
        ) as client:
            yield app, client


@pytest.fixture
async def api(api_settings: Callable[..., Settings]) -> AsyncIterator[httpx.AsyncClient]:
    async with running_app(api_settings()) as (_, client):
        yield client


@dataclass
class User:
    email: str
    token: str
    user_id: str
    tenant_id: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


async def register(client: httpx.AsyncClient, workspace: str = "Acme") -> User:
    email = f"user-{uuid.uuid4().hex[:10]}@example.com"
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": "Test User",
            "workspace_name": workspace,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return User(email, body["access_token"], body["user"]["user_id"], body["user"]["tenant_id"])


async def create_collection(client: httpx.AsyncClient, user: User, name: str = "Handbook") -> str:
    response = await client.post("/api/v1/collections", json={"name": name}, headers=user.headers)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def upload(
    client: httpx.AsyncClient,
    user: User,
    collection_id: str,
    filename: str,
    content: bytes,
    mime: str = "text/markdown",
) -> httpx.Response:
    return await client.post(
        "/api/v1/documents/upload",
        data={"collection_id": collection_id},
        files={"file": (filename, content, mime)},
        headers=user.headers,
    )


HANDBOOK = b"""# Northwind Travel Policy

## Flights

Employees must book economy class for flights shorter than six hours.
Business class is permitted for flights longer than six hours with written manager approval.

## Hotels

Hotel stays are reimbursed up to 180 EUR per night in most cities and 250 EUR in London.

## Meals

The daily meal allowance during travel is 60 EUR. Alcohol is never reimbursed.
"""
