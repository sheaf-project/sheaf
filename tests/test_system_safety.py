"""Tests for System Safety: grace periods, deferred safety changes, finalization."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx


def _set_system_safety_via_db(user_email: str, **fields) -> None:
    """Directly patch columns on the user's System row (bypasses re-auth + loosening delay)."""
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


def _backdate_pending_action(pending_id: str, days: int) -> None:
    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.pending_action import PendingAction

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            pending = await db.get(PendingAction, uuid.UUID(pending_id))
            assert pending is not None
            pending.finalize_after = datetime.now(UTC) - timedelta(days=days)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _backdate_safety_change(change_id: str, days: int) -> None:
    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.safety_change_request import SafetyChangeRequest

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            change = await db.get(SafetyChangeRequest, uuid.UUID(change_id))
            assert change is not None
            change.finalize_after = datetime.now(UTC) - timedelta(days=days)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _register(client: httpx.Client) -> tuple[str, str]:
    email = f"safety-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return email, "testpassword123"


# ---------------------------------------------------------------------------
# Baseline: safety off — delete is immediate, nothing queued
# ---------------------------------------------------------------------------


def test_delete_immediate_when_safety_off(auth_client: httpx.Client):
    member = auth_client.post("/v1/members", json={"name": "Alpha"}).json()
    resp = auth_client.delete(f"/v1/members/{member['id']}")
    assert resp.status_code == 204

    listing = auth_client.get("/v1/system/safety").json()
    assert listing["pending_actions"] == []


# ---------------------------------------------------------------------------
# Delete queues when safeguarded
# ---------------------------------------------------------------------------


def test_delete_queues_when_safety_on(client: httpx.Client):
    email, _ = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_members=True,
    )
    member = client.post("/v1/members", json={"name": "Beta"}).json()

    resp = client.delete(f"/v1/members/{member['id']}")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "pending_action_id" in body
    assert "finalize_after" in body

    # Member still exists
    assert client.get(f"/v1/members/{member['id']}").status_code == 200

    # Shows up in pending list
    listing = client.get("/v1/system/safety").json()
    assert len(listing["pending_actions"]) == 1
    assert listing["pending_actions"][0]["target_label"] == "Beta"


def test_delete_queues_captures_fronting_snapshot(client: httpx.Client):
    email, _ = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_members=True,
        safety_applies_to_fronts=True,
    )
    alice = client.post("/v1/members", json={"name": "Alice"}).json()
    bob = client.post("/v1/members", json={"name": "Bob"}).json()
    charlie = client.post("/v1/members", json={"name": "Charlie"}).json()

    # Start a front with Alice + Bob
    client.post("/v1/fronts", json={"member_ids": [alice["id"], bob["id"]]})

    # Queue Charlie's deletion
    resp = client.delete(f"/v1/members/{charlie['id']}")
    assert resp.status_code == 202

    listing = client.get("/v1/system/safety").json()
    pending = listing["pending_actions"][0]
    assert set(pending["fronting_member_names"]) == {"Alice", "Bob"}


# ---------------------------------------------------------------------------
# Category toggles: only enabled categories are safeguarded
# ---------------------------------------------------------------------------


def test_category_toggle_controls_safeguarding(client: httpx.Client):
    email, _ = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_members=True,
        safety_applies_to_tags=False,
    )
    member = client.post("/v1/members", json={"name": "Zed"}).json()
    tag = client.post("/v1/tags", json={"name": "TestTag"}).json()

    # Members is enabled — should queue
    assert client.delete(f"/v1/members/{member['id']}").status_code == 202

    # Tags is not enabled — immediate delete
    assert client.delete(f"/v1/tags/{tag['id']}").status_code == 204


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_pending_action(client: httpx.Client):
    email, _ = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_members=True,
    )
    member = client.post("/v1/members", json={"name": "Delta"}).json()
    resp = client.delete(f"/v1/members/{member['id']}")
    pending_id = resp.json()["pending_action_id"]

    cancel = client.delete(f"/v1/system/safety/pending-actions/{pending_id}")
    assert cancel.status_code == 204

    # Member still exists
    assert client.get(f"/v1/members/{member['id']}").status_code == 200
    # No longer pending
    listing = client.get("/v1/system/safety").json()
    assert listing["pending_actions"] == []


# ---------------------------------------------------------------------------
# Finalization via admin job trigger
# ---------------------------------------------------------------------------


def test_finalize_pending_action_runs(
    client: httpx.Client, admin_client: httpx.Client
):
    email, _ = _register(client)
    _set_system_safety_via_db(
        email,
        safety_grace_period_days=7,
        safety_applies_to_members=True,
    )
    member = client.post("/v1/members", json={"name": "Epsilon"}).json()
    pending_id = client.delete(f"/v1/members/{member['id']}").json()[
        "pending_action_id"
    ]

    _backdate_pending_action(pending_id, days=8)

    resp = admin_client.post("/v1/admin/jobs/finalize_pending_actions/run")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"

    # Member is gone
    assert client.get(f"/v1/members/{member['id']}").status_code == 404


# ---------------------------------------------------------------------------
# Asymmetric loosening delay
# ---------------------------------------------------------------------------


def test_tightening_applies_immediately(client: httpx.Client):
    _register(client)
    resp = client.patch(
        "/v1/system/safety",
        json={"grace_period_days": 7, "applies_to_members": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["applied"]) == {"grace_period_days", "applies_to_members"}
    assert body["deferred"] == []
    assert body["settings"]["grace_period_days"] == 7


def test_loosening_is_deferred(client: httpx.Client):
    email, password = _register(client)
    # Tighten first (immediate).
    client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_members": True,
            "auth_tier": "password",
        },
    )

    # Attempt to loosen (lower grace) — needs re-auth and should defer.
    resp = client.patch(
        "/v1/system/safety",
        json={"grace_period_days": 1, "password": password},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deferred"] == ["grace_period_days"]
    assert body["settings"]["grace_period_days"] == 7  # unchanged
    assert body["pending_change"] is not None


def test_loosening_requires_reauth(client: httpx.Client):
    _register(client)
    client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_members": True,
            "auth_tier": "password",
        },
    )

    # No password supplied — should be rejected (400 = client didn't
    # send the field; 401 is reserved for "wrong password").
    resp = client.patch("/v1/system/safety", json={"grace_period_days": 0})
    assert resp.status_code == 400


def test_cancel_pending_safety_change(client: httpx.Client):
    _, password = _register(client)
    client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_members": True,
            "auth_tier": "password",
        },
    )
    resp = client.patch(
        "/v1/system/safety",
        json={"grace_period_days": 0, "password": password},
    )
    change_id = resp.json()["pending_change"]["id"]

    cancel = client.delete(f"/v1/system/safety/pending-changes/{change_id}")
    assert cancel.status_code == 204

    current = client.get("/v1/system/safety").json()
    assert current["settings"]["grace_period_days"] == 7
    assert current["pending_changes"] == []


def test_finalize_safety_change_applies_loosening(
    client: httpx.Client, admin_client: httpx.Client
):
    _, password = _register(client)
    client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_members": True,
            "auth_tier": "password",
        },
    )
    resp = client.patch(
        "/v1/system/safety",
        json={"grace_period_days": 1, "password": password},
    )
    change_id = resp.json()["pending_change"]["id"]

    _backdate_safety_change(change_id, days=8)

    run = admin_client.post("/v1/admin/jobs/finalize_safety_changes/run")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "success"

    current = client.get("/v1/system/safety").json()
    assert current["settings"]["grace_period_days"] == 1


def test_mixed_change_splits_applied_and_deferred(client: httpx.Client):
    _, password = _register(client)
    client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_members": True,
            "auth_tier": "password",
        },
    )
    # Raise grace (tighten) + drop auth_tier to none (loosen) in one call.
    resp = client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 14,
            "auth_tier": "none",
            "password": password,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "grace_period_days" in body["applied"]
    assert "auth_tier" in body["deferred"]
    # Tightening visible now.
    assert body["settings"]["grace_period_days"] == 14
    # Loosening still pending.
    assert body["settings"]["auth_tier"] == "password"


def test_loosening_when_grace_zero_applies_immediately(client: httpx.Client):
    """If safety is fully off (grace=0), nothing to defer — loosening applies instantly."""
    _register(client)
    # Safety starts off (grace=0). Lowering auth tier or toggling off categories
    # should apply immediately since there's no grace to wait through.
    resp = client.patch(
        "/v1/system/safety",
        json={"applies_to_members": False},
    )
    assert resp.status_code == 200
    # Already False — nothing applied, nothing deferred.
    assert resp.json()["applied"] == []
    assert resp.json()["deferred"] == []


# ---------------------------------------------------------------------------
# Re-auth gate on destructive endpoints (delete_confirmation tier)
# ---------------------------------------------------------------------------


def test_delete_endpoint_requires_password_when_tier_set(client: httpx.Client):
    """delete_confirmation=password gates the delete endpoint regardless of grace."""
    email, password = _register(client)
    _set_system_safety_via_db(email, delete_confirmation="password")
    member = client.post("/v1/members", json={"name": "Gated"}).json()

    # No body → 400 Password required (missing input)
    no_body = client.delete(f"/v1/members/{member['id']}")
    assert no_body.status_code == 400
    assert no_body.json()["detail"] == "Password required"

    # Wrong password → 401 Incorrect password
    wrong = client.request(
        "DELETE",
        f"/v1/members/{member['id']}",
        json={"password": "not-the-password"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "Incorrect password"

    # Member still exists.
    assert client.get(f"/v1/members/{member['id']}").status_code == 200

    # Correct password → 204 (no safety grace configured)
    ok = client.request(
        "DELETE",
        f"/v1/members/{member['id']}",
        json={"password": password},
    )
    assert ok.status_code == 204


def test_delete_endpoint_reauth_then_queues_when_safeguarded(client: httpx.Client):
    """Re-auth must pass first, *then* the safeguard queues — both gates apply."""
    email, password = _register(client)
    _set_system_safety_via_db(
        email,
        delete_confirmation="password",
        safety_grace_period_days=7,
        safety_applies_to_members=True,
    )
    member = client.post("/v1/members", json={"name": "DoubleGated"}).json()

    # Wrong password is rejected before the safeguard even considers queuing.
    wrong = client.request(
        "DELETE",
        f"/v1/members/{member['id']}",
        json={"password": "nope"},
    )
    assert wrong.status_code == 401
    assert client.get("/v1/system/safety").json()["pending_actions"] == []

    # Correct password → 202 pending (safeguard takes over).
    ok = client.request(
        "DELETE",
        f"/v1/members/{member['id']}",
        json={"password": password},
    )
    assert ok.status_code == 202
    assert client.get(f"/v1/members/{member['id']}").status_code == 200
    assert len(client.get("/v1/system/safety").json()["pending_actions"]) == 1
