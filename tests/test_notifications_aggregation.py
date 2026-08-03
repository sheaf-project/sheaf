"""Time-window aggregation (`aggregation_window_seconds`).

Several separate front changes inside the window collapse into a single
outbox row carrying the NET transition; a change after the window opens a
fresh row. Channel setup goes through the API (token + active webhook
channel), then the enqueue folding is driven directly via `emit_front_change`
with an explicit `now`, so the window boundary is deterministic without
sleeping. `now` sits far in the future so the app's dispatcher never claims
these rows mid-test (deliver_after stays greater than wall-clock now).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx

WINDOW = 300


def _system_id(client: httpx.Client) -> str:
    resp = client.get("/v1/systems/me")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _create_token(client: httpx.Client) -> str:
    sid = _system_id(client)
    resp = client.post(
        f"/v1/systems/{sid}/watch-tokens", json={"label": "agg"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_channel(client: httpx.Client, token_id: str) -> str:
    resp = client.post(
        f"/v1/watch-tokens/{token_id}/channels",
        json={
            "name": "agg webhook",
            "destination_type": "webhook",
            "destination_config": {"url": "https://example.com/webhook"},
            "webhook_secret": "supersecret",
            "base_all_members": True,
            "trigger_on_start": True,
            "trigger_on_stop": True,
            "aggregation_window_seconds": WINDOW,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["channel"]["destination_state"] == "active", body
    assert body["channel"]["aggregation_window_seconds"] == WINDOW, body
    return body["channel"]["id"]


def _run_scenario(system_id: str, channel_id: str) -> list[list[dict]]:
    """Emit a sequence of front changes and snapshot the channel's undelivered
    front_change outbox rows after each. Returns one payload-list per phase."""

    async def _run() -> list[list[dict]]:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.notification_outbox import NotificationOutboxRow
        from sheaf.services.notifications.events import (
            emit_front_change,
            make_state,
        )

        sid = uuid.UUID(system_id)
        cid = uuid.UUID(channel_id)
        a, b = uuid.uuid4(), uuid.uuid4()
        front = uuid.uuid4()
        base = datetime(2099, 1, 1, 12, 0, tzinfo=UTC)

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        async def snapshot(db: AsyncSession) -> list[dict]:
            rows = (
                await db.execute(
                    select(NotificationOutboxRow)
                    .where(
                        NotificationOutboxRow.channel_id == cid,
                        NotificationOutboxRow.event_type == "front_change",
                        NotificationOutboxRow.delivered_at.is_(None),
                    )
                    .order_by(NotificationOutboxRow.deliver_after)
                )
            ).scalars().all()
            return [dict(r.event_payload) for r in rows]

        phases: list[list[dict]] = []
        try:
            async with async_session() as db:
                # e1: {} -> {A}. Opens a window (A started).
                await emit_front_change(
                    db,
                    system_id=sid,
                    before=make_state([]),
                    after=make_state([(front, [a])]),
                    now=base,
                )
                await db.commit()
                phases.append(await snapshot(db))

                # e2 (+10s, inside window): {A} -> {A, B}. Folds into the row.
                await emit_front_change(
                    db,
                    system_id=sid,
                    before=make_state([(front, [a])]),
                    after=make_state([(front, [a, b])]),
                    now=base + timedelta(seconds=10),
                )
                await db.commit()
                phases.append(await snapshot(db))

                # e3 (past window end): {A, B} -> {A}. Opens a fresh window.
                await emit_front_change(
                    db,
                    system_id=sid,
                    before=make_state([(front, [a, b])]),
                    after=make_state([(front, [a])]),
                    now=base + timedelta(seconds=WINDOW + 100),
                )
                await db.commit()
                phases.append(await snapshot(db))

            return phases
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_aggregation_folds_within_window_then_opens_new(auth_client: httpx.Client):
    token_id = _create_token(auth_client)
    channel_id = _create_channel(auth_client, token_id)

    phase1, phase2, phase3 = _run_scenario(
        _system_id(auth_client), channel_id
    )

    # e1 opened exactly one window row.
    assert len(phase1) == 1, phase1
    assert len(phase1[0]["fronting_after"]) == 1

    # e2 folded into the SAME row: still one, net after now has two members,
    # and the window's `before` stayed pinned at the open state (empty).
    assert len(phase2) == 1, phase2
    assert len(phase2[0]["fronting_after"]) == 2
    assert phase2[0]["fronting_before"] == []

    # e3 landed after the window closed -> a second, independent row.
    assert len(phase3) == 2, phase3
