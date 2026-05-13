"""Integration tests for the mobile push device-token endpoints."""

from __future__ import annotations

import httpx


def test_register_creates_row(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/devices/push",
        json={
            "platform": "fcm",
            "token": "tok-fcm-1",
            "install_id": "install-A",
            "app_version": "0.1.0",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["platform"] == "fcm"
    assert body["install_id"] == "install-A"
    assert body["app_version"] == "0.1.0"
    # Tokens are never returned.
    assert "token" not in body


def test_list_returns_metadata_only(auth_client: httpx.Client):
    auth_client.post(
        "/v1/devices/push",
        json={"platform": "fcm", "token": "tok-list-1", "install_id": "i1"},
    )
    auth_client.post(
        "/v1/devices/push",
        json={"platform": "apns_prod", "token": "tok-list-2", "install_id": "i2"},
    )
    resp = auth_client.get("/v1/devices/push")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 2
    for row in rows:
        assert "token" not in row
        assert row["platform"] in {"fcm", "apns_dev", "apns_prod"}


def test_register_idempotent_on_exact_match(auth_client: httpx.Client):
    """Re-registering the same (platform, token) bumps last_seen_at and
    returns the same row id, not a duplicate."""
    first = auth_client.post(
        "/v1/devices/push",
        json={"platform": "fcm", "token": "tok-idem", "install_id": "i-idem"},
    ).json()
    second = auth_client.post(
        "/v1/devices/push",
        json={"platform": "fcm", "token": "tok-idem", "install_id": "i-idem"},
    ).json()
    assert first["id"] == second["id"]


def test_install_id_match_treats_new_token_as_rotation(auth_client: httpx.Client):
    """Same install_id + different token = update in place, no extra row."""
    initial = auth_client.post(
        "/v1/devices/push",
        json={
            "platform": "fcm",
            "token": "tok-rotate-old",
            "install_id": "rotate-install",
        },
    ).json()
    rotated = auth_client.post(
        "/v1/devices/push",
        json={
            "platform": "fcm",
            "token": "tok-rotate-new",
            "install_id": "rotate-install",
        },
    ).json()
    assert initial["id"] == rotated["id"]
    # And the old token is gone (delete by old token returns 204 either
    # way, but listing only finds one device for this install_id).
    rows = auth_client.get("/v1/devices/push").json()
    matching = [r for r in rows if r["install_id"] == "rotate-install"]
    assert len(matching) == 1


def test_delete_clears_row_idempotently(auth_client: httpx.Client):
    auth_client.post(
        "/v1/devices/push",
        json={"platform": "fcm", "token": "tok-del", "install_id": "del-1"},
    )
    resp1 = auth_client.request(
        "DELETE",
        "/v1/devices/push",
        json={"token": "tok-del"},
    )
    assert resp1.status_code == 204
    # Idempotent: re-DELETEing returns 204 too.
    resp2 = auth_client.request(
        "DELETE",
        "/v1/devices/push",
        json={"token": "tok-del"},
    )
    assert resp2.status_code == 204


def test_lru_eviction_at_cap(auth_client: httpx.Client):
    """When the per-account cap is hit, the oldest-`last_seen_at` row is
    evicted to make room for a new one."""
    import asyncio
    import os
    import uuid as _uuid

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from sheaf.config import settings
    from sheaf.models.push_device_token import PushDeviceToken

    # Force a tight cap for this test by registering past the default 20.
    # Use unique account by registering a fresh user.
    email = f"lru-{_uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as c:
        reg = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert reg.status_code == 201, reg.text
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"

        # Register 22 distinct tokens — default cap is 20.
        for i in range(22):
            r = c.post(
                "/v1/devices/push",
                json={
                    "platform": "fcm",
                    "token": f"lru-tok-{i}",
                    "install_id": f"lru-install-{i}",
                },
            )
            assert r.status_code == 200, r.text

        rows = c.get("/v1/devices/push").json()
        assert len(rows) == 20, f"expected 20 after eviction, got {len(rows)}"

    # The two oldest install ids should have been evicted.
    async def _check() -> set[str]:
        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as db:
            from sheaf.crypto import blind_index
            from sheaf.models.user import User

            user = (
                await db.execute(
                    select(User).where(User.email_hash == blind_index(email))
                )
            ).scalar_one()
            result = await db.execute(
                select(PushDeviceToken.install_id).where(
                    PushDeviceToken.account_id == user.id,
                )
            )
            ids = {row[0] for row in result.all()}
        await engine.dispose()
        return ids

    install_ids = asyncio.run(_check())
    # The oldest two (lru-install-0, lru-install-1) were evicted.
    assert "lru-install-0" not in install_ids
    assert "lru-install-1" not in install_ids
    assert "lru-install-21" in install_ids


def test_unauthenticated_requests_rejected(client: httpx.Client):
    resp = client.post(
        "/v1/devices/push",
        json={"platform": "fcm", "token": "anon-tok"},
    )
    assert resp.status_code in {401, 403}


# --- enabled / label / per-id PATCH + DELETE ---------------------------------


def test_register_accepts_label(auth_client: httpx.Client):
    """The mobile app supplies a user-visible device name at
    registration; the server stores it and `enabled` defaults to True."""
    resp = auth_client.post(
        "/v1/devices/push",
        json={
            "platform": "fcm",
            "token": "tok-labelled",
            "install_id": "i-lbl",
            "label": "Sarah's Pixel",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["label"] == "Sarah's Pixel"
    assert body["enabled"] is True


def test_patch_toggles_enabled(auth_client: httpx.Client):
    """Recipient mutes a device by flipping `enabled` to False; the
    row stays registered so re-enabling later is a one-click action."""
    created = auth_client.post(
        "/v1/devices/push",
        json={"platform": "fcm", "token": "tok-mute", "install_id": "i-mute"},
    ).json()
    assert created["enabled"] is True

    patched = auth_client.patch(
        f"/v1/devices/push/{created['id']}", json={"enabled": False}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["enabled"] is False

    listing = auth_client.get("/v1/devices/push").json()
    row = next(r for r in listing if r["id"] == created["id"])
    assert row["enabled"] is False

    # And back on.
    auth_client.patch(
        f"/v1/devices/push/{created['id']}", json={"enabled": True}
    )
    listing = auth_client.get("/v1/devices/push").json()
    row = next(r for r in listing if r["id"] == created["id"])
    assert row["enabled"] is True


def test_patch_renames_device(auth_client: httpx.Client):
    """Recipient renames a device from the Receiving tab. Empty string
    clears the label back to platform-derived default at render time."""
    created = auth_client.post(
        "/v1/devices/push",
        json={
            "platform": "fcm",
            "token": "tok-rename",
            "install_id": "i-rn",
            "label": "Old name",
        },
    ).json()
    patched = auth_client.patch(
        f"/v1/devices/push/{created['id']}", json={"label": "New name"}
    ).json()
    assert patched["label"] == "New name"

    cleared = auth_client.patch(
        f"/v1/devices/push/{created['id']}", json={"label": ""}
    ).json()
    assert cleared["label"] is None


def test_patch_404_for_other_account(auth_client: httpx.Client, client: httpx.Client):
    """A device row belonging to another user 404s — no leak of the
    existence of cross-account rows."""
    import uuid as _uuid

    other_email = f"patch-other-{_uuid.uuid4().hex[:8]}@sheaf.dev"
    reg = client.post(
        "/v1/auth/register",
        json={"email": other_email, "password": "testpassword123"},
    )
    client.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
    other = client.post(
        "/v1/devices/push",
        json={
            "platform": "fcm",
            "token": "tok-other",
            "install_id": "i-other",
        },
    ).json()

    resp = auth_client.patch(
        f"/v1/devices/push/{other['id']}", json={"enabled": False}
    )
    assert resp.status_code == 404


def test_delete_by_id_removes_row(auth_client: httpx.Client):
    """DELETE /v1/devices/push/{id} drops the row — the web UI variant
    of the existing logout-time token-based DELETE."""
    created = auth_client.post(
        "/v1/devices/push",
        json={"platform": "fcm", "token": "tok-drop", "install_id": "i-drop"},
    ).json()

    resp = auth_client.delete(f"/v1/devices/push/{created['id']}")
    assert resp.status_code == 204

    listing = auth_client.get("/v1/devices/push").json()
    assert not any(r["id"] == created["id"] for r in listing)


def test_delete_by_id_is_idempotent(auth_client: httpx.Client):
    """Deleting a non-existent device id returns 204 too — matches the
    token-based DELETE's idempotent semantics so retries don't 404."""
    import uuid as _uuid

    resp = auth_client.delete(f"/v1/devices/push/{_uuid.uuid4()}")
    assert resp.status_code == 204
