"""Dead-lettering of outbox rows that keep being re-claimed without progress.

Runs the dispatcher's claim query against the test database directly. A
delivery that fails cleanly releases its claim (claimed_at back to NULL and
a new deliver_after), so it never looks like an expired lease; only a row
whose processing raised, or whose worker died, comes back with a stale
claim. Those are counted, and past the cap the row is marked done with the
reason recorded rather than retried at every lease expiry forever.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

WORKER = "dead-letter-test"


def _create_channel(client: httpx.Client) -> str:
    sid = client.get("/v1/systems/me").json()["id"]
    token = client.post(f"/v1/systems/{sid}/watch-tokens", json={"label": "dl"})
    assert token.status_code == 201, token.text
    resp = client.post(
        f"/v1/watch-tokens/{token.json()['id']}/channels",
        json={
            "name": "dead letter webhook",
            "destination_type": "webhook",
            "destination_config": {"url": "https://example.com/webhook"},
            "webhook_secret": "supersecret",
            "base_all_members": True,
            "trigger_on_start": True,
            "trigger_on_stop": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["channel"]["id"]


def _run(channel_id: str, cap: int) -> dict:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from sheaf.config import settings
    from sheaf.models.notification_outbox import NotificationOutboxRow
    from sheaf.services.notifications import dispatcher

    cid = uuid.UUID(channel_id)
    now = datetime.now(UTC)
    stale = now - timedelta(hours=2)  # well past the claim lease

    def row(**kw) -> NotificationOutboxRow:
        base = dict(
            event_id=uuid.uuid4(),
            channel_id=cid,
            event_type="reminder",
            event_payload={"title": "x", "body": "y"},
            enqueued_at=now - timedelta(minutes=5),
            deliver_after=now - timedelta(minutes=1),
        )
        base.update(kw)
        return NotificationOutboxRow(**base)

    fresh = row()
    once_stale = row(claimed_at=stale, claimed_by="dead", reclaim_count=cap - 2)
    at_cap = row(claimed_at=stale, claimed_by="dead", reclaim_count=cap - 1)
    # A clean delivery failure looks like this: claim released, retry
    # scheduled, attempts counted. It must not count as a re-claim.
    retrying = row(failed_attempts=3, last_error="transient: webhook 5xx (503)")
    mine = [fresh, once_stale, at_cap, retrying]

    async def go() -> dict:
        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with session() as db:
                db.add_all(mine)
                await db.commit()
                ids = {r.id for r in mine}

            async with session() as db:
                claimed = await dispatcher._claim_batch(db, worker_id=WORKER)
                claimed_ids = {r.id for r in claimed}
                # Rows other tests left due are not ours to hold: release them.
                for r in claimed:
                    if r.id not in ids:
                        r.claimed_at = None
                        r.claimed_by = None
                await db.commit()

            async with session() as db:
                result = await db.execute(
                    select(NotificationOutboxRow).where(
                        NotificationOutboxRow.id.in_(ids)
                    )
                )
                after = {r.id: r for r in result.scalars().all()}
                return {
                    "claimed": claimed_ids,
                    "fresh": after[fresh.id],
                    "once_stale": after[once_stale.id],
                    "at_cap": after[at_cap.id],
                    "retrying": after[retrying.id],
                }
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_rows_past_the_reclaim_cap_are_dead_lettered(
    auth_client: httpx.Client, monkeypatch: pytest.MonkeyPatch
):
    from sheaf.config import settings

    monkeypatch.setattr(settings, "notifications_dead_letter_after_reclaims", 3)
    out = _run(_create_channel(auth_client), cap=3)

    # Fresh and cleanly-retrying rows are claimed and untouched otherwise.
    for key in ("fresh", "retrying"):
        r = out[key]
        assert r.id in out["claimed"], key
        assert r.reclaim_count == 0
        assert r.delivered_at is None
        assert r.claimed_by == WORKER

    # A stale claim below the cap is counted and claimed again.
    r = out["once_stale"]
    assert r.id in out["claimed"]
    assert r.reclaim_count == 2
    assert r.delivered_at is None
    assert r.claimed_by == WORKER

    # A stale claim at the cap is given up on: terminal, reason recorded,
    # not handed back for processing.
    r = out["at_cap"]
    assert r.id not in out["claimed"]
    assert r.reclaim_count == 3
    assert r.delivered_at is not None
    assert r.last_error.startswith("dead-lettered: lease expired 3 times")
    assert r.claimed_at is None and r.claimed_by is None
