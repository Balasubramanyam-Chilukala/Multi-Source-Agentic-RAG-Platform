import asyncio
import os
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic.config import Config
from sqlalchemy import make_url

from app.core.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://atlas:atlas@localhost:5432/atlas_test"
)


def pytest_asyncio_loop_factories(config: pytest.Config, item: pytest.Item) -> Mapping[str, Any]:
    # psycopg's async mode cannot run on the Proactor loop Windows uses by default.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """Build settings from explicit values only, ignoring the developer's .env and environment."""

    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "environment": "test",
            "llm_provider": "mock",
            "embedding_provider": "mock",
            "database_url": TEST_DATABASE_URL,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return factory


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep real provider keys or DATABASE_URL in the shell from leaking into tests."""
    for name in list(os.environ):
        if name.lower() in Settings.model_fields:
            monkeypatch.delenv(name, raising=False)


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    return config


def _recreate_database(database_url: str) -> None:
    url = make_url(database_url)
    admin_dsn = url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=3) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{url.database}"')


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    """A freshly created, empty test database. Skips integration tests if Postgres is down."""
    try:
        _recreate_database(TEST_DATABASE_URL)
    except psycopg.OperationalError as exc:
        pytest.skip(
            f"PostgreSQL not reachable for integration tests ({type(exc).__name__}); "
            "run `docker compose up -d db` or set TEST_DATABASE_URL"
        )
    yield TEST_DATABASE_URL
