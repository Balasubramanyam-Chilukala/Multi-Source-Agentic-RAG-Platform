"""The main user journey through the real API, database, ingestion pipeline, and agent."""

import json
import uuid
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db.models import AuditLog, Chunk
from tests.fixtures import make_pdf
from tests.integration.conftest import (
    HANDBOOK,
    PASSWORD,
    create_collection,
    register,
    running_app,
    upload,
)

pytestmark = pytest.mark.integration


async def test_auth_flow(api: httpx.AsyncClient, migrated_database_url: str) -> None:
    user = await register(api)
    me = await api.get("/api/v1/auth/me", headers=user.headers)
    assert me.status_code == 200 and me.json()["role"] == "owner"

    ok = await api.post(
        "/api/v1/auth/login", json={"email": user.email.upper(), "password": PASSWORD}
    )
    assert ok.status_code == 200

    bad = await api.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "wrong-password"}
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "unauthenticated"

    assert (await api.get("/api/v1/collections")).status_code == 401
    assert (
        await api.get("/api/v1/collections", headers={"Authorization": "Bearer junk"})
    ).status_code == 401

    engine = create_async_engine(migrated_database_url)
    async with engine.connect() as conn:
        failures = await conn.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "auth.login_failed")
        )
    await engine.dispose()
    assert failures and failures >= 1  # the failed attempt was audited despite the 401


async def test_duplicate_registration_conflicts(api: httpx.AsyncClient) -> None:
    user = await register(api)
    again = await api.post(
        "/api/v1/auth/register",
        json={
            "email": user.email,
            "password": PASSWORD,
            "display_name": "x",
            "workspace_name": "y",
        },
    )
    assert again.status_code == 409


async def test_ingestion_search_chat_feedback_and_history(api: httpx.AsyncClient) -> None:
    user = await register(api)
    collection = await create_collection(api, user)
    assert (
        await api.post("/api/v1/collections", json={"name": "Handbook"}, headers=user.headers)
    ).status_code == 409

    created = await upload(api, user, collection, "travel.md", HANDBOOK)
    assert created.status_code == 202, created.text
    document_id = created.json()["id"]

    document = (await api.get(f"/api/v1/documents/{document_id}", headers=user.headers)).json()
    assert document["status"] == "ready", document
    assert document["title"] == "Northwind Travel Policy"
    assert document["chunk_count"] >= 3
    assert document["jobs"][0]["status"] == "succeeded"

    chunks = (await api.get(f"/api/v1/documents/{document_id}/chunks", headers=user.headers)).json()
    sections = {c["section"] for c in chunks["items"]}
    assert "Northwind Travel Policy > Hotels" in sections

    duplicate = await upload(api, user, collection, "copy.md", HANDBOOK)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["details"][0]["existing_document_id"] == document_id

    for mode in ("dense", "lexical", "hybrid"):
        search = await api.post(
            "/api/v1/search",
            json={"query": "hotel reimbursement in London", "mode": mode},
            headers=user.headers,
        )
        assert search.status_code == 200, search.text
        body = search.json()
        assert body["mode"] == mode
        assert body["chunks"], mode
        assert "250 EUR in London" in body["chunks"][0]["text"], mode
    rerank = await api.post(
        "/api/v1/search", json={"query": "hotels", "mode": "hybrid_rerank"}, headers=user.headers
    )
    assert rerank.status_code == 400  # no reranker configured

    answer = await api.post(
        "/api/v1/chat",
        json={"message": "How much is the hotel limit in London?"},
        headers=user.headers,
    )
    assert answer.status_code == 200, answer.text
    chat = answer.json()
    assert chat["run"]["outcome"] == "answered"
    assert "250 EUR" in chat["message"]["content"]
    assert chat["message"]["citations"]
    assert chat["message"]["citations"][0]["document_id"] == document_id
    assert chat["evidence"]
    conversation_id = chat["conversation_id"]

    abstain = await api.post(
        "/api/v1/chat",
        json={
            "message": "What is the parental leave entitlement for new fathers?",
            "conversation_id": conversation_id,
        },
        headers=user.headers,
    )
    assert abstain.json()["run"]["outcome"] == "abstained"
    assert abstain.json()["message"]["citations"] == []

    message_id = chat["message"]["id"]
    for rating in (1, -1):
        feedback = await api.post(
            "/api/v1/feedback",
            json={"message_id": message_id, "rating": rating},
            headers=user.headers,
        )
        assert feedback.status_code == 201, feedback.text
    assert (
        await api.post(
            "/api/v1/feedback", json={"message_id": message_id, "rating": 5}, headers=user.headers
        )
    ).status_code == 422

    history = (
        await api.get(f"/api/v1/conversations/{conversation_id}", headers=user.headers)
    ).json()
    roles = [m["role"] for m in history["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert history["messages"][1]["feedback"] == -1  # upserted
    assert history["messages"][1]["citations"][0]["snippet"]

    listing = (await api.get("/api/v1/conversations", headers=user.headers)).json()
    assert listing["items"][0]["id"] == conversation_id
    assert listing["items"][0]["message_count"] == 4

    run_id = chat["run"]["id"]
    run = (await api.get(f"/api/v1/runs/{run_id}", headers=user.headers)).json()
    assert [n["node"] for n in run["node_trace"]][:3] == ["validate_input", "route", "retrieve"]
    assert run["details"]["retrieval_attempts"][0]["chunk_ids"]
    assert "text" not in json.dumps(run["details"]["retrieval_attempts"][0]["scores"])

    dashboard = (await api.get("/api/v1/dashboard", headers=user.headers)).json()
    assert dashboard["documents_by_status"] == {"ready": 1}
    assert dashboard["runs_last_7_days"]["answered"] >= 1


async def test_streaming_chat_events(api: httpx.AsyncClient) -> None:
    user = await register(api)
    collection = await create_collection(api, user)
    await upload(api, user, collection, "travel.md", HANDBOOK)

    events: list[tuple[str, dict]] = []  # type: ignore[type-arg]
    async with api.stream(
        "POST",
        "/api/v1/chat/stream",
        json={"message": "What is the daily meal allowance?"},
        headers=user.headers,
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        event = None
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event:
                events.append((event, json.loads(line[6:])))
    kinds = [e for e, _ in events]
    assert kinds[0] == "meta"
    assert "step" in kinds and "token" in kinds
    assert kinds[-1] == "final"
    final = events[-1][1]
    assert final["run"]["outcome"] == "answered"
    assert "60 EUR" in final["message"]["content"]


async def test_failed_unsupported_retry_and_delete(
    api: httpx.AsyncClient, migrated_database_url: str
) -> None:
    user = await register(api)
    collection = await create_collection(api, user)

    fake = await upload(api, user, collection, "fake.pdf", b"not really a pdf", "application/pdf")
    assert fake.status_code == 400

    broken = await upload(
        api,
        user,
        collection,
        "broken.pdf",
        b"%PDF-1.4\n garbage without objects",
        "application/pdf",
    )
    assert broken.status_code == 202
    broken_id = broken.json()["id"]
    detail = (await api.get(f"/api/v1/documents/{broken_id}", headers=user.headers)).json()
    assert detail["status"] == "failed"
    assert detail["error_message"]

    retried = await api.post(f"/api/v1/documents/{broken_id}/retry", headers=user.headers)
    assert retried.status_code == 202
    detail = (await api.get(f"/api/v1/documents/{broken_id}", headers=user.headers)).json()
    assert detail["status"] == "failed"
    assert [j["attempt"] for j in detail["jobs"]] == [2, 1]

    import io  # noqa: PLC0415

    from pypdf import PdfWriter  # noqa: PLC0415

    writer, buffer = PdfWriter(), io.BytesIO()
    writer.add_blank_page(width=100, height=100)
    writer.write(buffer)
    scanned = await upload(api, user, collection, "scan.pdf", buffer.getvalue(), "application/pdf")
    scanned_detail = (
        await api.get(f"/api/v1/documents/{scanned.json()['id']}", headers=user.headers)
    ).json()
    assert scanned_detail["status"] == "unsupported"
    assert "OCR" in scanned_detail["error_message"]
    assert (
        await api.post(f"/api/v1/documents/{scanned.json()['id']}/retry", headers=user.headers)
    ).status_code == 409

    good = await upload(
        api,
        user,
        collection,
        "report.pdf",
        make_pdf(["Quarterly revenue grew 12 percent."]),
        "application/pdf",
    )
    good_id = good.json()["id"]
    good_detail = (await api.get(f"/api/v1/documents/{good_id}", headers=user.headers)).json()
    assert good_detail["status"] == "ready" and good_detail["page_count"] == 1

    assert (
        await api.delete(f"/api/v1/documents/{good_id}", headers=user.headers)
    ).status_code == 204
    assert (await api.get(f"/api/v1/documents/{good_id}", headers=user.headers)).status_code == 404
    engine = create_async_engine(migrated_database_url)
    async with engine.connect() as conn:
        remaining = await conn.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.document_id == uuid.UUID(good_id))
        )
        source = await conn.scalar(
            text("SELECT count(*) FROM document_sources WHERE document_id = :d"), {"d": good_id}
        )
    await engine.dispose()
    assert remaining == 0 and source == 0


async def test_text_and_url_ingestion_validation(api: httpx.AsyncClient) -> None:
    user = await register(api)
    collection = await create_collection(api, user)
    pasted = await api.post(
        "/api/v1/documents/text",
        json={
            "collection_id": collection,
            "title": "Office hours",
            "text": "The office opens at 8am on weekdays.",
        },
        headers=user.headers,
    )
    assert pasted.status_code == 202
    assert (await api.get(f"/api/v1/documents/{pasted.json()['id']}", headers=user.headers)).json()[
        "status"
    ] == "ready"

    for url in (
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "http://localhost:8000/health/live",
    ):
        response = await api.post(
            "/api/v1/documents/url",
            json={"collection_id": collection, "url": url},
            headers=user.headers,
        )
        assert response.status_code in (400, 422), (url, response.text)


async def test_upload_size_limit(api_settings: Callable[..., Settings]) -> None:
    async with running_app(api_settings(max_upload_bytes=2048)) as (_, client):
        user = await register(client)
        collection = await create_collection(client, user)
        too_big = await upload(client, user, collection, "big.txt", b"x" * 5000, "text/plain")
        assert too_big.status_code == 413
        huge = await upload(
            client, user, collection, "huge.txt", b"x" * (2 * 1024 * 1024), "text/plain"
        )
        assert huge.status_code == 413  # rejected by the body-size middleware before parsing


async def test_rate_limit_returns_429_with_retry_after(
    api_settings: Callable[..., Settings],
) -> None:
    async with running_app(api_settings(rate_limit_chat_per_minute=2)) as (_, client):
        user = await register(client)
        statuses = []
        for _ in range(3):
            response = await client.post(
                "/api/v1/chat", json={"message": "hello"}, headers=user.headers
            )
            statuses.append(response.status_code)
        assert statuses == [200, 200, 429]
        assert int(response.headers["retry-after"]) >= 1
        assert response.json()["error"]["code"] == "rate_limited"


async def test_conversations_and_memory_survive_restart(
    api_settings: Callable[..., Settings],
) -> None:
    settings = api_settings()
    async with running_app(settings) as (_, client):
        user = await register(client)
        collection = await create_collection(client, user)
        await upload(client, user, collection, "travel.md", HANDBOOK)
        first = (
            await client.post(
                "/api/v1/chat",
                json={"message": "What is the hotel limit in London?"},
                headers=user.headers,
            )
        ).json()
    # A brand-new application instance: new engine, new checkpointer pool.
    async with running_app(api_settings()) as (_, client):
        listing = (await client.get("/api/v1/conversations", headers=user.headers)).json()
        assert listing["items"][0]["id"] == first["conversation_id"]
        follow_up = (
            await client.post(
                "/api/v1/chat",
                json={
                    "message": "And what about meals during travel?",
                    "conversation_id": first["conversation_id"],
                },
                headers=user.headers,
            )
        ).json()
        run = (
            await client.get(f"/api/v1/runs/{follow_up['run']['id']}", headers=user.headers)
        ).json()
        route = next(n for n in run["node_trace"] if n["node"] == "route")
        assert (
            route["decided_by"] == "llm"
        )  # history from the persisted checkpoint triggered LLM routing
        assert follow_up["run"]["outcome"] == "answered"

        deleted = await client.delete(
            f"/api/v1/conversations/{first['conversation_id']}", headers=user.headers
        )
        assert deleted.status_code == 204
        assert (
            await client.get(
                f"/api/v1/conversations/{first['conversation_id']}", headers=user.headers
            )
        ).status_code == 404


async def test_interrupted_ingestion_is_recovered_on_startup(
    api_settings: Callable[..., Settings], migrated_database_url: str
) -> None:
    async with running_app(api_settings()) as (_, client):
        user = await register(client)
        collection = await create_collection(client, user)
    engine = create_async_engine(migrated_database_url)
    async with engine.begin() as conn:
        document_id = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO documents (id, tenant_id, collection_id, title, source_type, "
                "content_hash, status, chunk_count, metadata) "
                "VALUES (:id, :t, :c, 'stuck', 'text', :h, 'processing', 0, '{}')"
            ),
            {"id": document_id, "t": user.tenant_id, "c": collection, "h": uuid.uuid4().hex},
        )
    await engine.dispose()
    async with running_app(api_settings()) as (_, client):
        detail = (await client.get(f"/api/v1/documents/{document_id}", headers=user.headers)).json()
        assert detail["status"] == "failed"
        assert "restart" in detail["error_message"]


async def test_health_ready_in_full_app(api: httpx.AsyncClient) -> None:
    response = await api.get("/health/ready")
    assert response.status_code == 200, response.text


async def test_review_regressions_conflicts_lengths_and_reserved_names(
    api_settings: Callable[..., Settings],
) -> None:
    async with running_app(api_settings()) as (app, client):
        user = await register(client)
        first = (
            await client.post("/api/v1/chat", json={"message": "hello"}, headers=user.headers)
        ).json()
        conversation = uuid.UUID(first["conversation_id"])

        # One run at a time per conversation.
        app.state.active_conversations.add(conversation)
        busy = await client.post(
            "/api/v1/chat",
            json={"message": "hi again", "conversation_id": str(conversation)},
            headers=user.headers,
        )
        assert busy.status_code == 409
        app.state.active_conversations.discard(conversation)

        # Oversized questions are rejected before anything is stored.
        long = await client.post(
            "/api/v1/chat",
            json={"message": "x" * 2500, "conversation_id": str(conversation)},
            headers=user.headers,
        )
        assert long.status_code == 422
        history = (
            await client.get(f"/api/v1/conversations/{conversation}", headers=user.headers)
        ).json()
        assert all(len(m["content"]) < 2500 for m in history["messages"])

        # The evaluation corpus name is reserved.
        reserved = await client.post(
            "/api/v1/collections",
            json={"name": "[eval] atlas-handbook-v1@1.0.1"},
            headers=user.headers,
        )
        assert reserved.status_code == 400


async def test_stale_ingestion_job_does_not_clobber_a_ready_document(
    api_settings: Callable[..., Settings], migrated_database_url: str
) -> None:
    async with running_app(api_settings()) as (app, client):
        user = await register(client)
        collection = await create_collection(client, user)
        document_id = (await upload(client, user, collection, "travel.md", HANDBOOK)).json()["id"]
        engine = create_async_engine(migrated_database_url)
        async with engine.begin() as conn:
            stale_job = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO ingestion_jobs (id, tenant_id, document_id, status, attempt) "
                    "VALUES (:id, :t, :d, 'queued', 2)"
                ),
                {"id": stale_job, "t": user.tenant_id, "d": document_id},
            )
        await engine.dispose()
        await app.state.pipeline.process(
            uuid.UUID(document_id), stale_job
        )  # e.g. a double-clicked retry
        detail = (await client.get(f"/api/v1/documents/{document_id}", headers=user.headers)).json()
        assert detail["status"] == "ready" and detail["chunk_count"] > 0
        assert {j["error_code"] for j in detail["jobs"]} >= {"superseded"}
