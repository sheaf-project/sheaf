"""System Safety integration for notification channel + watch token deletes."""

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
    email = f"notifsafety-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return email


def _create_token(client: httpx.Client) -> dict:
    sid = client.get("/v1/systems/me").json()["id"]
    resp = client.post(
        f"/v1/systems/{sid}/watch-tokens", json={"label": "test"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_ntfy_channel(client: httpx.Client, token_id: str) -> dict:
    resp = client.post(
        f"/v1/watch-tokens/{token_id}/channels",
        json={
            "name": "safety-test",
            "destination_type": "ntfy",
            "destination_config": {
                "server_url": "https://ntfy.sh",
                "topic": f"safety-{uuid.uuid4().hex[:8]}",
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["channel"]


# ---------------------------------------------------------------------------
# Channel delete
# ---------------------------------------------------------------------------


def test_channel_delete_immediate_when_off(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    chan = _create_ntfy_channel(auth_client, tok["id"])

    resp = auth_client.request("DELETE", f"/v1/channels/{chan['id']}")
    assert resp.status_code == 204, resp.text
    assert auth_client.get(f"/v1/channels/{chan['id']}").status_code == 404


def test_channel_delete_queues_when_safeguarded(client: httpx.Client):
    email = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_notifications=True,
    )
    tok = _create_token(client)
    chan = _create_ntfy_channel(client, tok["id"])

    resp = client.request("DELETE", f"/v1/channels/{chan['id']}")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "pending_action_id" in body
    assert "finalize_after" in body

    # Channel still exists during grace.
    assert client.get(f"/v1/channels/{chan['id']}").status_code == 200

    # Pending action surfaces in /system/safety.
    pending = client.get("/v1/system/safety").json()["pending_actions"]
    assert any(p["action_type"] == "channel_delete" for p in pending)


# ---------------------------------------------------------------------------
# Watch token revoke
# ---------------------------------------------------------------------------


def test_watch_token_revoke_immediate_when_off(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    resp = auth_client.request("DELETE", f"/v1/watch-tokens/{tok['id']}")
    assert resp.status_code == 204, resp.text
    fresh = auth_client.get(f"/v1/watch-tokens/{tok['id']}").json()
    assert fresh["revoked_at"] is not None


def test_watch_token_revoke_queues_when_safeguarded(client: httpx.Client):
    email = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_notifications=True,
    )
    tok = _create_token(client)

    resp = client.request("DELETE", f"/v1/watch-tokens/{tok['id']}")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "pending_action_id" in body

    # Token NOT yet revoked during grace.
    fresh = client.get(f"/v1/watch-tokens/{tok['id']}").json()
    assert fresh["revoked_at"] is None

    pending = client.get("/v1/system/safety").json()["pending_actions"]
    assert any(p["action_type"] == "watch_token_revoke" for p in pending)


def test_watch_token_revoke_idempotent_when_already_revoked(
    client: httpx.Client,
):
    """Re-revoking an already-revoked token should not 202-queue under safety,
    just no-op 204. Otherwise toggling safety on after revocation would let
    the user accidentally queue a meaningless action."""
    email = _register(client)
    tok = _create_token(client)
    # Revoke once (safety off).
    client.request("DELETE", f"/v1/watch-tokens/{tok['id']}")
    # Now revoke again with safety on.
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_notifications=True,
    )
    resp = client.request("DELETE", f"/v1/watch-tokens/{tok['id']}")
    assert resp.status_code == 204
