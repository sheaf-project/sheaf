"""Job runner rework: leader election, lease reclaim, stale-export
recovery, wake cadence, and the import-enqueue NOTIFY listener.

Mixed host-side unit tests (direct DB / pure functions) and e2e tests
through the running container.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")


def _test_engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    from sheaf.config import settings

    db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
    return create_async_engine(db_url)


# ---------------------------------------------------------------------------
# Leader election


# The app container holds the real LEADER_LOCK_KEY (its own leader loop
# is live against the same database), which is itself proof the election
# engages. These tests exercise the mechanics on a separate key so they
# don't fight the running app for leadership.
_TEST_LOCK_KEY = 0x53484541_46_7E


def test_advisory_lock_is_exclusive():
    """Two connections compete for a leader key; only one wins, and
    release hands it over."""

    async def run() -> None:
        engine = _test_engine()
        try:
            async with engine.connect() as a, engine.connect() as b:
                got_a = (
                    await a.execute(
                        text("SELECT pg_try_advisory_lock(:k)"),
                        {"k": _TEST_LOCK_KEY},
                    )
                ).scalar()
                assert got_a is True

                got_b = (
                    await b.execute(
                        text("SELECT pg_try_advisory_lock(:k)"),
                        {"k": _TEST_LOCK_KEY},
                    )
                ).scalar()
                assert got_b is False

                released = (
                    await a.execute(
                        text("SELECT pg_advisory_unlock(:k)"),
                        {"k": _TEST_LOCK_KEY},
                    )
                ).scalar()
                assert released is True

                got_b2 = (
                    await b.execute(
                        text("SELECT pg_try_advisory_lock(:k)"),
                        {"k": _TEST_LOCK_KEY},
                    )
                ).scalar()
                assert got_b2 is True
                await b.execute(
                    text("SELECT pg_advisory_unlock(:k)"),
                    {"k": _TEST_LOCK_KEY},
                )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_leader_loop_runs_loops_only_while_holding_the_lock():
    """leader_loop starts its loops when the lock is free, and a standby
    leader_loop starts nothing while another connection holds the lock."""
    from unittest.mock import patch

    import sheaf.database as database_module
    from sheaf.services.leader import leader_loop

    async def run() -> None:
        engine = _test_engine()
        started = asyncio.Event()

        async def dummy_loop() -> None:
            started.set()
            while True:
                await asyncio.sleep(3600)

        try:
            with patch.object(database_module, "engine", engine):
                # Free lock: the loop should start our dummy quickly.
                task = asyncio.create_task(
                    leader_loop([("dummy", dummy_loop)], lock_key=_TEST_LOCK_KEY)
                )
                await asyncio.wait_for(started.wait(), timeout=5)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

                # Held lock: a standby should NOT start the dummy.
                started.clear()
                async with engine.connect() as holder:
                    got = (
                        await holder.execute(
                            text("SELECT pg_try_advisory_lock(:k)"),
                            {"k": _TEST_LOCK_KEY},
                        )
                    ).scalar()
                    assert got is True
                    standby = asyncio.create_task(
                        leader_loop([("dummy", dummy_loop)], lock_key=_TEST_LOCK_KEY)
                    )
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(started.wait(), timeout=2)
                    standby.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await standby
                    await holder.execute(
                        text("SELECT pg_advisory_unlock(:k)"),
                        {"k": _TEST_LOCK_KEY},
                    )
        finally:
            await engine.dispose()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# P4: wake cadence follows the fastest enabled job


def test_compute_wake_seconds_tracks_fastest_enabled_job():
    from sheaf.services import jobs as jobs_module

    saved = dict(jobs_module._REGISTRY)
    try:
        jobs_module._REGISTRY.clear()
        jobs_module.register_job(
            name="fast", description="", func=None,
            interval_seconds=lambda: 60,
        )
        jobs_module.register_job(
            name="slow", description="", func=None,
            interval_seconds=lambda: 86400,
        )
        assert jobs_module._compute_wake_seconds() == 60

        # A disabled fast job doesn't drag the cadence down.
        jobs_module.register_job(
            name="disabled-faster", description="", func=None,
            interval_seconds=lambda: 5,
            enabled=lambda: False,
        )
        assert jobs_module._compute_wake_seconds() == 60

        # An enabled too-fast job is floored at 15s.
        jobs_module.register_job(
            name="too-fast", description="", func=None,
            interval_seconds=lambda: 1,
        )
        assert jobs_module._compute_wake_seconds() == 15
    finally:
        jobs_module._REGISTRY.clear()
        jobs_module._REGISTRY.update(saved)


def test_compute_wake_seconds_empty_registry_uses_ceiling():
    from sheaf.config import settings
    from sheaf.services import jobs as jobs_module

    saved = dict(jobs_module._REGISTRY)
    try:
        jobs_module._REGISTRY.clear()
        assert (
            jobs_module._compute_wake_seconds()
            == settings.job_check_interval_minutes * 60
        )
    finally:
        jobs_module._REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# P2: dispatcher lease reclaim


def _system_id_for(client: httpx.Client) -> str:
    return client.get("/v1/systems/me").json()["id"]


def test_stale_claimed_outbox_rows_are_reclaimed(auth_client: httpx.Client):
    """A row claimed by a worker that died is eligible again after the
    lease; a freshly-claimed row is not."""
    system_id = _system_id_for(auth_client)

    async def run() -> tuple[set[str], str, str]:
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker

        from sheaf.models.notification_channel import NotificationChannel
        from sheaf.models.notification_outbox import NotificationOutboxRow
        from sheaf.models.watch_token import WatchToken
        from sheaf.services.notifications.dispatcher import _claim_batch

        engine = _test_engine()
        session_factory = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        now = datetime.now(UTC)
        try:
            async with session_factory() as db:
                token = WatchToken(
                    id=uuid.uuid4(),
                    system_id=uuid.UUID(system_id),
                    label="lease-test",
                )
                db.add(token)
                await db.flush()
                channel = NotificationChannel(
                    id=uuid.uuid4(),
                    watch_token_id=token.id,
                    name="lease-test",
                    destination_type="webhook",
                    destination_config={"url": "https://example.com/hook"},
                )
                db.add(channel)
                await db.flush()

                def _row(claimed_delta_minutes: int | None) -> NotificationOutboxRow:
                    return NotificationOutboxRow(
                        id=uuid.uuid4(),
                        event_id=uuid.uuid4(),
                        channel_id=channel.id,
                        event_type="front_change",
                        event_payload={},
                        enqueued_at=now - timedelta(hours=1),
                        deliver_after=now - timedelta(hours=1),
                        claimed_at=(
                            None
                            if claimed_delta_minutes is None
                            else now - timedelta(minutes=claimed_delta_minutes)
                        ),
                        claimed_by=(
                            None if claimed_delta_minutes is None else "dead-worker"
                        ),
                    )

                stale = _row(60)       # claimed an hour ago: lease expired
                fresh = _row(1)        # claimed a minute ago: still leased
                unclaimed = _row(None)
                db.add_all([stale, fresh, unclaimed])
                await db.commit()
                stale_id, fresh_id = str(stale.id), str(fresh.id)

            async with session_factory() as db:
                claimed = await _claim_batch(db, worker_id="lease-test-worker")
                claimed_ids = {str(r.id) for r in claimed}

            # Cleanup: cascade from the watch token.
            async with session_factory() as db:
                row = await db.get(WatchToken, token.id)
                if row is not None:
                    await db.delete(row)
                    await db.commit()
            return claimed_ids, stale_id, fresh_id
        finally:
            await engine.dispose()

    claimed_ids, stale_id, fresh_id = asyncio.run(run())
    assert stale_id in claimed_ids, "lease-expired claim was not reclaimed"
    assert fresh_id not in claimed_ids, "live claim was stolen inside its lease"


# ---------------------------------------------------------------------------
# P3: stale-RUNNING export recovery


def _set_export_state(
    job_id: str, *, minutes_ago: int, failed_attempts: int = 0
) -> None:
    async def run() -> None:
        engine = _test_engine()
        try:
            async with engine.connect() as conn:
                await conn.execute(
                    text(
                        "UPDATE export_jobs SET status = 'running', "
                        "started_at = :ts, failed_attempts = :fa "
                        "WHERE id = :id"
                    ),
                    {
                        "ts": datetime.now(UTC) - timedelta(minutes=minutes_ago),
                        "fa": failed_attempts,
                        "id": job_id,
                    },
                )
                await conn.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())


def _enqueue_export(client: httpx.Client) -> str:
    resp = client.post(
        "/v1/export/jobs",
        json={"password": "testpassword123", "include_images": False},
    )
    assert resp.status_code in (200, 201, 202), resp.text
    return resp.json()["id"]


def test_stale_running_export_is_reset_to_pending(
    auth_client: httpx.Client, admin_client: httpx.Client,
):
    job_id = _enqueue_export(auth_client)
    _set_export_state(job_id, minutes_ago=120)

    resp = admin_client.post("/v1/admin/jobs/recover_stale_exports/run")
    assert resp.status_code == 200, resp.text

    detail = auth_client.get(f"/v1/export/jobs/{job_id}").json()
    assert detail["status"] == "pending", detail


def test_crash_looping_export_is_parked_failed(
    auth_client: httpx.Client, admin_client: httpx.Client,
):
    job_id = _enqueue_export(auth_client)
    _set_export_state(job_id, minutes_ago=120, failed_attempts=2)

    resp = admin_client.post("/v1/admin/jobs/recover_stale_exports/run")
    assert resp.status_code == 200, resp.text

    detail = auth_client.get(f"/v1/export/jobs/{job_id}").json()
    assert detail["status"] == "failed", detail


def test_fresh_running_export_is_left_alone(
    auth_client: httpx.Client, admin_client: httpx.Client,
):
    job_id = _enqueue_export(auth_client)
    _set_export_state(job_id, minutes_ago=1)

    resp = admin_client.post("/v1/admin/jobs/recover_stale_exports/run")
    assert resp.status_code == 200, resp.text

    detail = auth_client.get(f"/v1/export/jobs/{job_id}").json()
    assert detail["status"] == "running", detail


# ---------------------------------------------------------------------------
# Leader + import observability metrics


def _sample(name: str) -> float | None:
    from sheaf.observability.registry import get_registry

    return get_registry().get_sample_value(name)


@pytest.mark.skipif(
    bool(os.environ.get("PROMETHEUS_MULTIPROC_DIR")),
    reason="gauge read needs the single-process registry",
)
def test_leader_is_leader_gauge_and_transitions():
    """Acquiring leadership flips sheaf_leader_is_leader to 1 and bumps the
    transitions counter; standing down returns it to 0."""
    from unittest.mock import patch

    import sheaf.database as database_module
    from sheaf.services.leader import leader_loop

    async def run() -> None:
        engine = _test_engine()
        started = asyncio.Event()

        async def dummy() -> None:
            started.set()
            while True:
                await asyncio.sleep(3600)

        try:
            with patch.object(database_module, "engine", engine):
                before = _sample("sheaf_leader_transitions_total") or 0.0
                task = asyncio.create_task(
                    leader_loop([("dummy", dummy)], lock_key=_TEST_LOCK_KEY)
                )
                await asyncio.wait_for(started.wait(), timeout=5)

                # Leadership held.
                assert _sample("sheaf_leader_is_leader") == 1.0
                assert (
                    _sample("sheaf_leader_transitions_total") or 0.0
                ) == before + 1

                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

                # Stood down.
                assert _sample("sheaf_leader_is_leader") == 0.0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_imports_oldest_pending_gauge_reflects_backlog(auth_client: httpx.Client):
    """A pending import the runner hasn't claimed shows up as a non-zero
    oldest-pending age once the gauge refresher runs; an empty queue reads
    0."""
    import os as _os

    if _os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        pytest.skip("gauge read needs the single-process registry")

    me = auth_client.get("/v1/auth/me").json()

    async def run() -> float | None:
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy.orm import sessionmaker

        from sheaf.models.import_job import (
            ImportJob,
            ImportJobSource,
            ImportJobStatus,
        )
        from sheaf.observability.gauges import _refresh_imports_in_progress

        engine = _test_engine()
        session_factory = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        job_id = uuid.uuid4()
        try:
            async with session_factory() as db:
                db.add(
                    ImportJob(
                        id=job_id,
                        user_id=uuid.UUID(me["id"]),
                        source=ImportJobSource.PLURALKIT_FILE.value,
                        status=ImportJobStatus.PENDING.value,
                        idempotency_key=str(uuid.uuid4()),
                        payload_storage_key=f"imports/{job_id}.json",
                        payload_metadata=None,
                        counts={},
                        events=[],
                        created_at=datetime.now(UTC) - timedelta(seconds=120),
                    )
                )
                await db.commit()

            async with session_factory() as db:
                await _refresh_imports_in_progress(db)
            age = _sample("sheaf_imports_oldest_pending_seconds")

            # Cleanup so we don't leave a perpetual pending row behind.
            async with session_factory() as db:
                row = await db.get(ImportJob, job_id)
                if row is not None:
                    await db.delete(row)
                    await db.commit()
            return age
        finally:
            await engine.dispose()

    age = asyncio.run(run())
    assert age is not None and age >= 100, age


# ---------------------------------------------------------------------------
# Import-enqueue NOTIFY listener


def test_enqueue_notify_wakes_the_listener():
    """The LISTEN connection sets the wake event when an enqueue NOTIFY
    fires; unrelated time passing does not."""
    from unittest.mock import patch

    import sheaf.database as database_module
    from sheaf.services.import_runner import _listen_for_enqueues

    async def run() -> None:
        engine = _test_engine()
        wake = asyncio.Event()
        try:
            with patch.object(database_module, "engine", engine):
                listener = asyncio.create_task(_listen_for_enqueues(wake))
                # Give the listener a beat to attach.
                await asyncio.sleep(0.5)
                assert not wake.is_set()

                async with engine.connect() as conn:
                    await conn.execute(text("NOTIFY sheaf_import_enqueued"))
                    await conn.commit()

                await asyncio.wait_for(wake.wait(), timeout=5)
                listener.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await listener
        finally:
            await engine.dispose()

    asyncio.run(run())
