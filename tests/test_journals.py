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
# Bio revision capture + list + restore
# ---------------------------------------------------------------------------


def test_bio_edit_captures_revision(auth_client: httpx.Client):
    member = auth_client.post(
        "/v1/members", json={"name": "Z", "description": "v1 bio"}
    ).json()
    auth_client.patch(
        f"/v1/members/{member['id']}", json={"description": "v2 bio"}
    )

    revs = auth_client.get(f"/v1/members/{member['id']}/revisions").json()
    assert len(revs) == 1
    assert revs[0]["body"] == "v1 bio"
    assert revs[0]["target_type"] == "member_bio"


def test_member_has_bio_revisions_flag(auth_client: httpx.Client):
    """`has_bio_revisions` lights up once the bio has been edited at least
    once. Lets the members UI grey out the History button on members
    whose bio has never changed."""
    fresh = auth_client.post(
        "/v1/members", json={"name": "Fresh", "description": "first bio"}
    ).json()
    # Right after create, no revisions exist yet.
    assert fresh["has_bio_revisions"] is False
    got = auth_client.get(f"/v1/members/{fresh['id']}").json()
    assert got["has_bio_revisions"] is False
    listed = auth_client.get("/v1/members").json()
    matching = next(m for m in listed if m["id"] == fresh["id"])
    assert matching["has_bio_revisions"] is False

    # Edit the bio — a revision is captured.
    edited = auth_client.patch(
        f"/v1/members/{fresh['id']}", json={"description": "second bio"}
    ).json()
    assert edited["has_bio_revisions"] is True
    got_after = auth_client.get(f"/v1/members/{fresh['id']}").json()
    assert got_after["has_bio_revisions"] is True
    listed_after = auth_client.get("/v1/members").json()
    matching_after = next(m for m in listed_after if m["id"] == fresh["id"])
    assert matching_after["has_bio_revisions"] is True


def test_member_has_bio_revisions_unaffected_by_non_description_edit(
    auth_client: httpx.Client,
):
    """Editing a non-description field doesn't write a revision, so the
    flag stays False."""
    member = auth_client.post(
        "/v1/members", json={"name": "PronounEdit", "description": "stable"}
    ).json()
    edited = auth_client.patch(
        f"/v1/members/{member['id']}", json={"pronouns": "they/them"}
    ).json()
    assert edited["has_bio_revisions"] is False


def test_bio_revisions_isolated_per_member(auth_client: httpx.Client):
    a = auth_client.post(
        "/v1/members", json={"name": "A", "description": "a-v1"}
    ).json()
    b = auth_client.post(
        "/v1/members", json={"name": "B", "description": "b-v1"}
    ).json()
    auth_client.patch(f"/v1/members/{a['id']}", json={"description": "a-v2"})
    auth_client.patch(f"/v1/members/{b['id']}", json={"description": "b-v2"})

    a_revs = auth_client.get(f"/v1/members/{a['id']}/revisions").json()
    b_revs = auth_client.get(f"/v1/members/{b['id']}/revisions").json()
    assert [r["body"] for r in a_revs] == ["a-v1"]
    assert [r["body"] for r in b_revs] == ["b-v1"]


def test_bio_revisions_cross_tenant_404(client: httpx.Client):
    _register(client)
    member = client.post(
        "/v1/members", json={"name": "Z", "description": "x"}
    ).json()
    client.patch(f"/v1/members/{member['id']}", json={"description": "y"})

    other = httpx.Client(base_url=str(client.base_url))
    _register(other)
    resp = other.get(f"/v1/members/{member['id']}/revisions")
    assert resp.status_code == 404
    other.close()


def test_restore_bio_revision(auth_client: httpx.Client):
    member = auth_client.post(
        "/v1/members", json={"name": "Z", "description": "v1 bio"}
    ).json()
    auth_client.patch(f"/v1/members/{member['id']}", json={"description": "v2 bio"})

    revs = auth_client.get(f"/v1/members/{member['id']}/revisions").json()
    revision_id = revs[0]["id"]

    resp = auth_client.post(
        f"/v1/members/{member['id']}/restore-revision",
        json={"revision_id": revision_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "v1 bio"

    # Pre-restore content captured as a new revision; original revision row
    # remains in place.
    revs_after = auth_client.get(f"/v1/members/{member['id']}/revisions").json()
    assert len(revs_after) == 2
    bodies = sorted(r["body"] for r in revs_after)
    assert bodies == ["v1 bio", "v2 bio"]


def test_restore_bio_rejects_journal_revision(auth_client: httpx.Client):
    member = auth_client.post(
        "/v1/members", json={"name": "Z", "description": "v1"}
    ).json()
    auth_client.patch(f"/v1/members/{member['id']}", json={"description": "v2"})

    entry = auth_client.post(
        "/v1/journals", json={"body": "journal-v1"}
    ).json()
    auth_client.patch(
        f"/v1/journals/{entry['id']}", json={"body": "journal-v2"}
    )
    journal_revs = auth_client.get(f"/v1/journals/{entry['id']}/revisions").json()

    resp = auth_client.post(
        f"/v1/members/{member['id']}/restore-revision",
        json={"revision_id": journal_revs[0]["id"]},
    )
    assert resp.status_code == 404


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
