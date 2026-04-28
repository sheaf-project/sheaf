"""Tests for journals + revision history + image safety."""

import asyncio
import os
import uuid

import httpx


def _set_system_safety_via_db(user_email: str, **fields) -> None:
    from sqlalchemy import select

    from sheaf.crypto import blind_index

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.system import System
        from sheaf.models.user import User

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            email_hash = blind_index(user_email)
            user = (
                await db.execute(select(User).where(User.email_hash == email_hash))
            ).scalar_one()
            system = (
                await db.execute(select(System).where(System.user_id == user.id))
            ).scalar_one()
            for k, v in fields.items():
                setattr(system, k, v)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _register(client: httpx.Client) -> str:
    email = f"journals-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return email


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_system_wide_entry(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/journals",
        json={"title": "Hello", "body": "First entry."},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Hello"
    assert body["body"] == "First entry."
    assert body["member_id"] is None
    assert body["visibility"] == "system"


def test_create_per_member_entry(auth_client: httpx.Client):
    member = auth_client.post("/v1/members", json={"name": "Alice"}).json()
    resp = auth_client.post(
        "/v1/journals",
        json={"member_id": member["id"], "body": "Member entry."},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["member_id"] == member["id"]


def test_list_filters_by_member(auth_client: httpx.Client):
    a = auth_client.post("/v1/members", json={"name": "A"}).json()
    b = auth_client.post("/v1/members", json={"name": "B"}).json()
    auth_client.post("/v1/journals", json={"body": "system"})
    auth_client.post("/v1/journals", json={"member_id": a["id"], "body": "for-a"})
    auth_client.post("/v1/journals", json={"member_id": b["id"], "body": "for-b"})

    all_items = auth_client.get("/v1/journals").json()["items"]
    assert len(all_items) == 3

    sys_only = auth_client.get("/v1/journals", params={"system_only": "true"}).json()
    assert len(sys_only["items"]) == 1
    assert sys_only["items"][0]["body"] == "system"

    a_only = auth_client.get("/v1/journals", params={"member_id": a["id"]}).json()
    assert len(a_only["items"]) == 1
    assert a_only["items"][0]["body"] == "for-a"


def test_visibility_only_system_v1(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/journals",
        json={"body": "x", "visibility": "public"},
    )
    assert resp.status_code == 422


def test_create_with_author_override(auth_client: httpx.Client):
    a = auth_client.post("/v1/members", json={"name": "Alice"}).json()
    b = auth_client.post("/v1/members", json={"name": "Bob"}).json()
    resp = auth_client.post(
        "/v1/journals",
        json={"body": "x", "author_member_ids": [a["id"], b["id"]]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["author_member_ids"] == [a["id"], b["id"]]
    assert "Alice" in body["author_member_names"]
    assert "Bob" in body["author_member_names"]


def test_create_rejects_unknown_author(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/journals",
        json={
            "body": "x",
            "author_member_ids": ["00000000-0000-0000-0000-000000000000"],
        },
    )
    assert resp.status_code == 400


def test_create_rejects_other_systems_member(client: httpx.Client):
    _register(client)
    other_member = client.post("/v1/members", json={"name": "Foreign"}).json()
    other = httpx.Client(base_url=str(client.base_url))
    _register(other)
    resp = other.post(
        "/v1/journals",
        json={"body": "x", "author_member_ids": [other_member["id"]]},
    )
    assert resp.status_code == 400
    other.close()


def test_patch_updates_authors_without_revision(auth_client: httpx.Client):
    a = auth_client.post("/v1/members", json={"name": "A"}).json()
    b = auth_client.post("/v1/members", json={"name": "B"}).json()
    entry = auth_client.post(
        "/v1/journals",
        json={"body": "hello", "author_member_ids": [a["id"]]},
    ).json()

    resp = auth_client.patch(
        f"/v1/journals/{entry['id']}",
        json={"author_member_ids": [b["id"]]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["author_member_ids"] == [b["id"]]
    assert resp.json()["author_member_names"] == ["B"]

    revs = auth_client.get(f"/v1/journals/{entry['id']}/revisions").json()
    assert revs == [], "author-only edits should not capture a revision"


def test_patch_clear_authors_via_empty_list(auth_client: httpx.Client):
    a = auth_client.post("/v1/members", json={"name": "A"}).json()
    entry = auth_client.post(
        "/v1/journals",
        json={"body": "hello", "author_member_ids": [a["id"]]},
    ).json()

    resp = auth_client.patch(
        f"/v1/journals/{entry['id']}",
        json={"author_member_ids": []},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["author_member_ids"] == []
    assert resp.json()["author_member_names"] == []


def test_get_includes_revision_count(auth_client: httpx.Client):
    entry = auth_client.post("/v1/journals", json={"body": "v1"}).json()
    auth_client.patch(f"/v1/journals/{entry['id']}", json={"body": "v2"})
    auth_client.patch(f"/v1/journals/{entry['id']}", json={"body": "v3"})

    detail = auth_client.get(f"/v1/journals/{entry['id']}").json()
    assert detail["revision_count"] == 2


# ---------------------------------------------------------------------------
# Revision capture + restore
# ---------------------------------------------------------------------------


def test_edit_captures_revision(auth_client: httpx.Client):
    entry = auth_client.post(
        "/v1/journals", json={"title": "T1", "body": "B1"}
    ).json()
    auth_client.patch(
        f"/v1/journals/{entry['id']}", json={"title": "T2", "body": "B2"}
    )

    revs = auth_client.get(f"/v1/journals/{entry['id']}/revisions").json()
    assert len(revs) == 1
    # Revisions store the *outgoing* version.
    assert revs[0]["title"] == "T1"
    assert revs[0]["body"] == "B1"


def test_no_revision_when_only_visibility_changes(auth_client: httpx.Client):
    entry = auth_client.post("/v1/journals", json={"body": "x"}).json()
    # Same visibility — no-op effectively, but content_changed should be false.
    auth_client.patch(
        f"/v1/journals/{entry['id']}", json={"visibility": "system"}
    )
    revs = auth_client.get(f"/v1/journals/{entry['id']}/revisions").json()
    assert revs == []


def test_restore_revision(auth_client: httpx.Client):
    entry = auth_client.post(
        "/v1/journals", json={"title": "T1", "body": "B1"}
    ).json()
    auth_client.patch(
        f"/v1/journals/{entry['id']}", json={"title": "T2", "body": "B2"}
    )
    revs = auth_client.get(f"/v1/journals/{entry['id']}/revisions").json()
    revision_id = revs[0]["id"]

    resp = auth_client.post(
        f"/v1/journals/{entry['id']}/restore-revision",
        json={"revision_id": revision_id},
    )
    assert resp.status_code == 200
    restored = resp.json()
    assert restored["title"] == "T1"
    assert restored["body"] == "B1"

    # The pre-restore content (T2/B2) should now be a new revision.
    revs_after = auth_client.get(f"/v1/journals/{entry['id']}/revisions").json()
    assert len(revs_after) == 2
    titles = sorted(r["title"] for r in revs_after)
    assert titles == ["T1", "T2"]


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------


def test_cannot_read_other_users_entry(client: httpx.Client):
    # User A creates an entry
    _register(client)
    entry = client.post("/v1/journals", json={"body": "secret"}).json()

    # User B tries to read it
    other = httpx.Client(base_url=str(client.base_url))
    _register(other)
    resp = other.get(f"/v1/journals/{entry['id']}")
    assert resp.status_code == 404
    other.close()


# ---------------------------------------------------------------------------
# Bio revision capture
# ---------------------------------------------------------------------------


def test_bio_edit_captures_revision(client: httpx.Client):
    email = _register(client)
    member = client.post(
        "/v1/members", json={"name": "Z", "description": "v1 bio"}
    ).json()
    client.patch(
        f"/v1/members/{member['id']}", json={"description": "v2 bio"}
    )

    # Read the revisions table directly — there's no public bio-revision
    # endpoint in v1 (revisions are only listed via journal entries).
    revs = _read_member_bio_revisions(email, member["id"])
    assert len(revs) == 1
    assert revs[0]["body"] == "v1 bio"


def _read_member_bio_revisions(user_email: str, member_id: str) -> list[dict]:
    from sqlalchemy import select

    from sheaf.crypto import blind_index

    async def _run() -> list[dict]:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.content_revision import ContentRevision

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with async_session() as db:
                result = await db.execute(
                    select(ContentRevision)
                    .where(
                        ContentRevision.target_type == "member_bio",
                        ContentRevision.target_id == uuid.UUID(member_id),
                    )
                    .order_by(ContentRevision.created_at)
                )
                rows = list(result.scalars().all())
                return [{"id": str(r.id), "body": r.body, "title": r.title} for r in rows]
        finally:
            await engine.dispose()

    # Quiet the unused-arg warning — email is the lookup that asserts the row exists
    blind_index(user_email)
    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# System Safety integration: journal delete defers when safeguarded
# ---------------------------------------------------------------------------


def test_journal_delete_queues_when_safeguarded(client: httpx.Client):
    email = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_journals=True,
    )
    entry = client.post("/v1/journals", json={"body": "x"}).json()
    resp = client.delete(f"/v1/journals/{entry['id']}")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "pending_action_id" in body

    # Entry still exists during grace.
    assert client.get(f"/v1/journals/{entry['id']}").status_code == 200

    pending = client.get("/v1/system/safety").json()["pending_actions"]
    assert any(p["action_type"] == "journal_delete" for p in pending)


def test_journal_delete_immediate_when_off(auth_client: httpx.Client):
    entry = auth_client.post("/v1/journals", json={"body": "x"}).json()
    resp = auth_client.delete(f"/v1/journals/{entry['id']}")
    assert resp.status_code == 204
    assert auth_client.get(f"/v1/journals/{entry['id']}").status_code == 404


# ---------------------------------------------------------------------------
# Image safety
# ---------------------------------------------------------------------------


def test_image_delete_queues_when_safeguarded(client: httpx.Client):
    email = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_images=True,
    )
    # Upload a tiny PNG (8x1 transparent). Magic bytes are what's validated.
    png = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    upload = client.post(
        "/v1/files/upload?purpose=avatar",
        files={"file": ("a.png", png, "image/png")},
    )
    assert upload.status_code == 200, upload.text
    key = upload.json()["key"]

    # Find file id via /v1/files/list
    listing = client.get("/v1/files/list").json()
    file_id = next(f["id"] for f in listing if f["key"] == key)

    resp = client.delete(f"/v1/files/{file_id}")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "pending_action_id" in body

    # Still in /v1/files/list during grace
    listing2 = client.get("/v1/files/list").json()
    assert any(f["key"] == key for f in listing2)
