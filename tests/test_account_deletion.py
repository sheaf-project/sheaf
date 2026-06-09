"""Tests for account deletion flow — request, cancel, /me status, admin cancel."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")


def _set_deletion_requested_at(email: str, days_ago: int) -> None:
    """Directly set deletion_requested_at to a past date via DB."""
    from sqlalchemy import select

    from sheaf.crypto import blind_index

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.user import AccountStatus, User

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            email_hash = blind_index(email)
            result = await db.execute(select(User).where(User.email_hash == email_hash))
            user = result.scalar_one()
            user.account_status = AccountStatus.PENDING_DELETION
            user.deletion_requested_at = datetime.now(UTC) - timedelta(days=days_ago)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _get_user_status(email: str) -> str | None:
    """Read account_status directly from DB."""
    from sqlalchemy import select

    from sheaf.crypto import blind_index

    async def _run() -> str | None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.user import User

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as db:
            email_hash = blind_index(email)
            result = await db.execute(select(User).where(User.email_hash == email_hash))
            user = result.scalar_one_or_none()
            status = user.account_status if user else None
        await engine.dispose()
        return status

    return asyncio.run(_run())


def _user_exists(email: str) -> bool:
    """Check if user exists in DB."""
    return _get_user_status(email) is not None


# ---------------------------------------------------------------------------
# Request deletion
# ---------------------------------------------------------------------------


def test_request_deletion(auth_client: httpx.Client):
    from sheaf.config import settings

    resp = auth_client.post(
        "/v1/auth/delete-account",
        json={"password": "testpassword123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "deletion_scheduled_for" in data
    assert data["grace_days"] == settings.account_deletion_grace_days


def test_request_deletion_wrong_password(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/delete-account",
        json={"password": "wrongpassword"},
    )
    # 403, not 401: caller is authenticated; the step-up gate denies
    # the action. 401 would trip the frontend's silent-refresh-retry.
    assert resp.status_code == 403


def test_request_deletion_already_pending(auth_client: httpx.Client):
    auth_client.post(
        "/v1/auth/delete-account",
        json={"password": "testpassword123"},
    )
    resp = auth_client.post(
        "/v1/auth/delete-account",
        json={"password": "testpassword123"},
    )
    assert resp.status_code == 400
    assert "already" in resp.json()["detail"].lower()


def test_me_shows_deletion_requested_at(auth_client: httpx.Client):
    # Before deletion — should be null
    me = auth_client.get("/v1/auth/me").json()
    assert me["deletion_requested_at"] is None

    # Request deletion
    auth_client.post(
        "/v1/auth/delete-account",
        json={"password": "testpassword123"},
    )

    # After deletion — should have a timestamp
    me = auth_client.get("/v1/auth/me").json()
    assert me["deletion_requested_at"] is not None
    assert me["account_status"] == "pending_deletion"


# ---------------------------------------------------------------------------
# Cancel deletion
# ---------------------------------------------------------------------------


def test_cancel_deletion(auth_client: httpx.Client):
    auth_client.post(
        "/v1/auth/delete-account",
        json={"password": "testpassword123"},
    )
    resp = auth_client.post("/v1/auth/cancel-deletion")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True

    me = auth_client.get("/v1/auth/me").json()
    assert me["account_status"] == "active"
    assert me["deletion_requested_at"] is None


def test_cancel_deletion_not_pending(auth_client: httpx.Client):
    resp = auth_client.post("/v1/auth/cancel-deletion")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Admin cancel deletion
# ---------------------------------------------------------------------------


def test_admin_cancel_deletion(admin_client: httpx.Client, auth_client: httpx.Client):
    user_id = auth_client.get("/v1/auth/me").json()["id"]

    # User requests deletion
    auth_client.post(
        "/v1/auth/delete-account",
        json={"password": "testpassword123"},
    )

    # Admin cancels it
    resp = admin_client.post(
        f"/v1/admin/users/{user_id}/cancel-deletion",
        json={"reason": "support ticket"},
    )
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True

    # User should be active again
    me = auth_client.get("/v1/auth/me").json()
    assert me["account_status"] == "active"


def test_admin_cancel_deletion_not_pending(admin_client: httpx.Client, auth_client: httpx.Client):
    user_id = auth_client.get("/v1/auth/me").json()["id"]
    resp = admin_client.post(
        f"/v1/admin/users/{user_id}/cancel-deletion",
        json={"reason": "support ticket"},
    )
    assert resp.status_code == 400


def test_admin_cancel_deletion_requires_admin(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/admin/users/00000000-0000-0000-0000-000000000000/cancel-deletion",
        json={"reason": "support ticket"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Job-based deletion (DB manipulation to simulate past grace period)
# ---------------------------------------------------------------------------


def test_job_deletes_account_past_grace_period(admin_client: httpx.Client, client: httpx.Client):
    """Register a user, backdate their deletion, trigger the job, verify gone."""
    email = f"delete-me-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201

    # Backdate deletion_requested_at well past any reasonable grace period
    _set_deletion_requested_at(email, days_ago=15)

    # Trigger the deletion job
    resp = admin_client.post("/v1/admin/jobs/process_account_deletions/run")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"

    # User should be gone
    assert not _user_exists(email)


def test_job_does_not_delete_within_grace_period(admin_client: httpx.Client, client: httpx.Client):
    """Register a user, set deletion to 5 days ago (within grace), verify still exists."""
    email = f"keep-me-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201

    # Set deletion_requested_at to 5 days ago (within default 7-day grace)
    _set_deletion_requested_at(email, days_ago=5)

    # Trigger the deletion job
    admin_client.post("/v1/admin/jobs/process_account_deletions/run")

    # User should still exist
    assert _user_exists(email)
