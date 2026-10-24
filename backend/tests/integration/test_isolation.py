"""Authorization: cross-tenant isolation, per-user conversation privacy, and roles."""

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.security import Principal, create_access_token
from app.db.models.tenancy import MembershipRole
from tests.integration.conftest import HANDBOOK, User, create_collection, register, upload

pytestmark = pytest.mark.integration


async def _populated(api: httpx.AsyncClient) -> tuple[User, str, str, dict]:  # type: ignore[type-arg]
    owner = await register(api, "Tenant A")
    collection = await create_collection(api, owner)
    document = (await upload(api, owner, collection, "travel.md", HANDBOOK)).json()["id"]
    chat = (
        await api.post(
            "/api/v1/chat",
            json={"message": "What is the hotel limit in London?"},
            headers=owner.headers,
        )
    ).json()
    return owner, collection, document, chat


async def test_other_tenant_cannot_read_or_modify_anything(api: httpx.AsyncClient) -> None:
    owner, collection, document, chat = await _populated(api)
    intruder = await register(api, "Tenant B")
    h = intruder.headers
    chunk_id = chat["message"]["citations"][0]["chunk_id"]

    for path in (
        f"/api/v1/collections/{collection}",
        f"/api/v1/documents/{document}",
        f"/api/v1/documents/{document}/chunks",
        f"/api/v1/chunks/{chunk_id}",
        f"/api/v1/conversations/{chat['conversation_id']}",
        f"/api/v1/runs/{chat['run']['id']}",
    ):
        response = await api.get(path, headers=h)
        assert response.status_code == 404, path  # existence is not revealed

    assert (await api.get("/api/v1/collections", headers=h)).json()["total"] == 0
    assert (await api.get("/api/v1/documents", headers=h)).json()["total"] == 0
    assert (await api.get("/api/v1/conversations", headers=h)).json()["total"] == 0
    assert (await api.get("/api/v1/runs", headers=h)).json()["total"] == 0

    assert (await api.delete(f"/api/v1/documents/{document}", headers=h)).status_code == 404
    assert (await api.delete(f"/api/v1/collections/{collection}", headers=h)).status_code == 404
    assert (await api.post(f"/api/v1/documents/{document}/retry", headers=h)).status_code == 404
    assert (await upload(api, intruder, collection, "x.md", b"# x\n\ny")).status_code == 404
    assert (
        await api.post(
            "/api/v1/feedback", json={"message_id": chat["message"]["id"], "rating": 1}, headers=h
        )
    ).status_code == 404

    search = await api.post(
        "/api/v1/search", json={"query": "hotel", "collection_ids": [collection]}, headers=h
    )
    assert search.status_code == 400
    leak = await api.post(
        "/api/v1/chat",
        json={"message": "hotel limit London?", "conversation_id": chat["conversation_id"]},
        headers=h,
    )
    assert leak.status_code == 404

    # Searching all of B's (empty) collections never returns A's passages.
    own_search = await api.post("/api/v1/search", json={"query": "hotel limit London"}, headers=h)
    assert own_search.json()["chunks"] == []
    own_chat = (
        await api.post(
            "/api/v1/chat", json={"message": "What is the hotel limit in London?"}, headers=h
        )
    ).json()
    assert own_chat["run"]["outcome"] == "abstained"
    assert "250" not in own_chat["message"]["content"]

    # The owner's data is untouched.
    assert (
        await api.get(f"/api/v1/documents/{document}", headers=owner.headers)
    ).status_code == 200


async def test_forged_token_for_another_tenant_is_rejected(api: httpx.AsyncClient) -> None:
    owner, collection, _, _ = await _populated(api)
    intruder = await register(api, "Tenant B")
    forged = create_access_token(
        Principal(
            uuid.UUID(intruder.user_id),
            uuid.UUID(owner.tenant_id),
            MembershipRole.OWNER,
            intruder.email,
        ),
        secret="integration-test-secret-" + "x" * 20,
        ttl_minutes=5,
    )
    # Correctly signed, but the user has no membership in that tenant.
    response = await api.get(
        f"/api/v1/collections/{collection}", headers={"Authorization": f"Bearer {forged}"}
    )
    assert response.status_code == 401

    wrong_key = create_access_token(
        Principal(
            uuid.UUID(owner.user_id), uuid.UUID(owner.tenant_id), MembershipRole.OWNER, owner.email
        ),
        secret="some-other-secret-" + "y" * 30,
        ttl_minutes=5,
    )
    assert (
        await api.get("/api/v1/collections", headers={"Authorization": f"Bearer {wrong_key}"})
    ).status_code == 401


async def _add_member(database_url: str, tenant_id: str, role: str) -> User:
    user_id, email = uuid.uuid4(), f"member-{uuid.uuid4().hex[:8]}@example.com"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (id, email, display_name, is_active) VALUES (:id, :e, 'M', true)"
            ),
            {"id": user_id, "e": email},
        )
        await conn.execute(
            text("INSERT INTO memberships (id, tenant_id, user_id, role) VALUES (:id, :t, :u, :r)"),
            {"id": uuid.uuid4(), "t": tenant_id, "u": user_id, "r": role},
        )
    await engine.dispose()
    token = create_access_token(
        Principal(user_id, uuid.UUID(tenant_id), MembershipRole(role), email),
        secret="integration-test-secret-" + "x" * 20,
        ttl_minutes=5,
    )
    return User(email, token, str(user_id), tenant_id)


async def test_roles_and_per_user_conversation_privacy(
    api: httpx.AsyncClient, migrated_database_url: str
) -> None:
    owner, collection, document, chat = await _populated(api)
    viewer = await _add_member(migrated_database_url, owner.tenant_id, "viewer")
    member = await _add_member(migrated_database_url, owner.tenant_id, "member")

    # Same workspace: shared knowledge is visible...
    assert (
        await api.get(f"/api/v1/collections/{collection}", headers=viewer.headers)
    ).status_code == 200
    viewer_chat = (
        await api.post(
            "/api/v1/chat",
            json={"message": "What is the hotel limit in London?"},
            headers=viewer.headers,
        )
    ).json()
    assert viewer_chat["run"]["outcome"] == "answered"
    # ...but conversations and runs are private to their author.
    assert (
        await api.get(f"/api/v1/conversations/{chat['conversation_id']}", headers=viewer.headers)
    ).status_code == 404
    assert (
        await api.get(f"/api/v1/runs/{chat['run']['id']}", headers=member.headers)
    ).status_code == 404
    # Admins (and owners) can inspect all runs in their workspace for debugging.
    assert (
        await api.get(f"/api/v1/runs/{viewer_chat['run']['id']}", headers=owner.headers)
    ).status_code == 200

    # Viewers cannot modify knowledge; members cannot delete collections or change settings.
    assert (await upload(api, viewer, collection, "v.md", b"# v\n\ntext")).status_code == 403
    assert (
        await api.delete(f"/api/v1/documents/{document}", headers=viewer.headers)
    ).status_code == 403
    assert (
        await api.delete(f"/api/v1/collections/{collection}", headers=member.headers)
    ).status_code == 403
    assert (
        await api.put("/api/v1/settings", json={"top_k": 3}, headers=member.headers)
    ).status_code == 403
    assert (await upload(api, member, collection, "m.md", b"# m\n\nmember text")).status_code == 202

    # Deactivation takes effect immediately, even with an unexpired token.
    engine = create_async_engine(migrated_database_url)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET is_active = false WHERE id = :id"), {"id": member.user_id}
        )
    await engine.dispose()
    assert (await api.get("/api/v1/collections", headers=member.headers)).status_code == 401


async def test_workspace_settings_are_validated_and_scoped(api: httpx.AsyncClient) -> None:
    owner = await register(api, "Tenant A")
    other = await register(api, "Tenant B")
    updated = await api.put(
        "/api/v1/settings", json={"top_k": 3, "retrieval_mode": "lexical"}, headers=owner.headers
    )
    assert updated.status_code == 200 and updated.json()["top_k"] == 3
    assert (await api.get("/api/v1/settings", headers=other.headers)).json()["top_k"] != 3
    assert (
        await api.put(
            "/api/v1/settings",
            json={"chunk_size": 400, "chunk_overlap": 300},
            headers=owner.headers,
        )
    ).status_code == 422
    assert (
        await api.put(
            "/api/v1/settings", json={"retrieval_mode": "hybrid_rerank"}, headers=owner.headers
        )
    ).status_code == 400
    assert (
        await api.put("/api/v1/settings", json={"api_key": "x"}, headers=owner.headers)
    ).status_code == 422
    system = (await api.get("/api/v1/settings/system", headers=owner.headers)).json()
    assert "key" not in str({k: v for k, v in system.items() if k != "providers"}).lower()


async def test_workspace_deletion_removes_all_tenant_data(
    api: httpx.AsyncClient, migrated_database_url: str
) -> None:
    owner, _, _, chat = await _populated(api)
    bystander, *_ = await _populated(api)
    me = (await api.get("/api/v1/auth/me", headers=owner.headers)).json()
    wrong = await api.request(
        "DELETE", "/api/v1/workspace", json={"confirm_name": "nope"}, headers=owner.headers
    )
    assert wrong.status_code == 400
    deleted = await api.request(
        "DELETE", "/api/v1/workspace", json={"confirm_name": "Tenant A"}, headers=owner.headers
    )
    assert deleted.status_code == 204, deleted.text

    engine = create_async_engine(migrated_database_url)
    async with engine.connect() as conn:
        for table in (
            "collections",
            "documents",
            "chunks",
            "conversations",
            "messages",
            "agent_runs",
            "memberships",
        ):
            count = await conn.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"),  # noqa: S608
                {"t": me["tenant_id"]},
            )
            assert count == 0, table
        checkpoints = await conn.scalar(
            text("SELECT count(*) FROM checkpoints WHERE thread_id = :t"),
            {"t": chat["conversation_id"]},
        )
        assert checkpoints == 0
    await engine.dispose()
    assert (await api.get("/api/v1/collections", headers=owner.headers)).status_code == 401
    # Another workspace is unaffected.
    assert (await api.get("/api/v1/collections", headers=bystander.headers)).json()["total"] == 1
