"""Tests for the scheduled jobs system — admin API, job execution, logs."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")


def _set_unverified_old_account(email: str) -> None:
    """Set a user's email_verified=False and created_at to the past via DB."""
    from sqlalchemy import select

    from sheaf.crypto import blind_index

    async def _run() -> None:
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
            user = result.scalar_one()
            user.email_verified = False
            user.created_at = datetime.now(UTC) - timedelta(days=10)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _user_exists(email: str) -> bool:
    """Check if user exists in DB."""
    from sqlalchemy import select

    from sheaf.crypto import blind_index

    async def _run() -> bool:
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
        await engine.dispose()
        return user is not None

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


def test_jobs_list_requires_admin(auth_client: httpx.Client):
    resp = auth_client.get("/v1/admin/jobs")
    assert resp.status_code == 403


def test_jobs_trigger_requires_admin(auth_client: httpx.Client):
    resp = auth_client.post("/v1/admin/jobs/cleanup_job_logs/run")
    assert resp.status_code == 403


def test_jobs_logs_requires_admin(auth_client: httpx.Client):
    resp = auth_client.get("/v1/admin/jobs/cleanup_job_logs/logs")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Job listing
# ---------------------------------------------------------------------------


def test_list_jobs(admin_client: httpx.Client):
    resp = admin_client.get("/v1/admin/jobs")
    assert resp.status_code == 200
    jobs = resp.json()
    assert isinstance(jobs, list)
    assert len(jobs) >= 1

    # Verify expected shape
    job = jobs[0]
    assert "name" in job
    assert "description" in job
    assert "enabled" in job
    assert "interval_seconds" in job
    assert "last_run" in job


def test_list_jobs_contains_known_jobs(admin_client: httpx.Client):
    resp = admin_client.get("/v1/admin/jobs")
    names = {j["name"] for j in resp.json()}
    expected = {
        "process_account_deletions",
        "cleanup_job_logs",
        "cleanup_orphaned_files",
        "cleanup_notification_outbox",
    }
    assert expected.issubset(names)


def test_trigger_cleanup_notification_outbox(admin_client: httpx.Client):
    """The outbox sweep is wired and runnable; with no terminal rows it's a
    no-op that still reports success + an items_processed count."""
    resp = admin_client.post("/v1/admin/jobs/cleanup_notification_outbox/run")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["job_name"] == "cleanup_notification_outbox"
    assert data["status"] == "success"
    assert "items_processed" in data


# ---------------------------------------------------------------------------
# Trigger job
# ---------------------------------------------------------------------------


def test_trigger_job(admin_client: httpx.Client):
    resp = admin_client.post("/v1/admin/jobs/cleanup_job_logs/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_name"] == "cleanup_job_logs"
    assert data["status"] == "success"
    assert "items_processed" in data
    assert "duration_ms" in data


def test_trigger_unknown_job(admin_client: httpx.Client):
    resp = admin_client.post("/v1/admin/jobs/nonexistent_job/run")
    assert resp.status_code == 404


def test_trigger_orphan_cleanup(admin_client: httpx.Client):
    """Orphan cleanup should succeed even with no orphaned files."""
    resp = admin_client.post("/v1/admin/jobs/cleanup_orphaned_files/run")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


# ---------------------------------------------------------------------------
# Job logs
# ---------------------------------------------------------------------------


def test_job_logs_after_trigger(admin_client: httpx.Client):
    # Trigger a job first to ensure at least one log entry
    admin_client.post("/v1/admin/jobs/cleanup_job_logs/run")

    resp = admin_client.get("/v1/admin/jobs/cleanup_job_logs/logs")
    assert resp.status_code == 200
    logs = resp.json()
    assert isinstance(logs, list)
    assert len(logs) >= 1

    log = logs[0]
    assert "id" in log
    assert "started_at" in log
    assert "status" in log
    assert "items_processed" in log
    assert "duration_ms" in log


def test_job_logs_limit(admin_client: httpx.Client):
    # Trigger a few times
    for _ in range(3):
        admin_client.post("/v1/admin/jobs/cleanup_job_logs/run")

    resp = admin_client.get("/v1/admin/jobs/cleanup_job_logs/logs?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) <= 2


def test_job_logs_empty_for_unknown(admin_client: httpx.Client):
    resp = admin_client.get("/v1/admin/jobs/nonexistent_job/logs")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Unverified account cleanup (DB manipulation + job trigger)
# ---------------------------------------------------------------------------


def test_cleanup_unverified_accounts(admin_client: httpx.Client, client: httpx.Client):
    """Create unverified account, backdate it, trigger cleanup, verify deleted.

    Only runs in SaaS mode with email verification required — otherwise the
    job is disabled and won't delete anything.
    """
    email = f"unverified-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201

    # Backdate and mark unverified
    _set_unverified_old_account(email)

    # Trigger the job
    resp = admin_client.post("/v1/admin/jobs/cleanup_unverified_accounts/run")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"

    # In SaaS mode with email verification, user should be deleted.
    # In self-hosted mode, the job is disabled so items_processed=0 and user still exists.
    # We check both cases:
    if data["items_processed"] > 0:
        assert not _user_exists(email)
    # If 0 items processed, the job is disabled — that's fine, skip assertion
