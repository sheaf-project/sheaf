"""POST /v1/fronts/{id}/replace: swap one open front atomically.

Ends the target front and starts a replacement in a single transaction,
leaving every other open front alone, so a co-front swap is one history-
correct operation that emits a single front-change notification instead of a
separate stop and start.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx


def _member(client: httpx.Client, name: str) -> str:
    resp = client.post("/v1/members", json={"name": name, "privacy": "public"})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _start_front(client: httpx.Client, member_ids: list[str], **extra) -> dict:
    resp = client.post(
        "/v1/fronts",
        json={"member_ids": member_ids, "replace_fronts": False, **extra},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _current_sets(client: httpx.Client) -> list[set[str]]:
    resp = client.get("/v1/fronts/current")
    assert resp.status_code == 200, resp.text
    return [set(f["member_ids"]) for f in resp.json()]


def test_replace_swaps_target_and_leaves_other_fronts(auth_client: httpx.Client):
    a = _member(auth_client, "Ana")
    b = _member(auth_client, "Bex")
    c = _member(auth_client, "Cy")
    d = _member(auth_client, "Del")

    ac = _start_front(auth_client, [a, c])
    _start_front(auth_client, [d])

    resp = auth_client.post(
        f"/v1/fronts/{ac['id']}/replace", json={"member_ids": [a, b]}
    )
    assert resp.status_code == 201, resp.text
    new_front = resp.json()
    assert set(new_front["member_ids"]) == {a, b}
    assert new_front["id"] != ac["id"]  # a fresh entry, not the edited row

    # The {A,C} front is gone; {A,B} and the untouched {D} remain.
    sets = _current_sets(auth_client)
    assert {a, b} in sets
    assert {d} in sets
    assert {a, c} not in sets
    assert len(sets) == 2


def test_replace_carries_custom_status_over_by_default(auth_client: httpx.Client):
    a = _member(auth_client, "Ana")
    b = _member(auth_client, "Bex")
    front = _start_front(auth_client, [a], custom_status="at work")

    resp = auth_client.post(
        f"/v1/fronts/{front['id']}/replace", json={"member_ids": [a, b]}
    )
    assert resp.status_code == 201, resp.text
    # custom_status omitted -> carried onto the new front.
    assert resp.json()["custom_status"] == "at work"


def test_replace_explicit_null_clears_status(auth_client: httpx.Client):
    a = _member(auth_client, "Ana")
    b = _member(auth_client, "Bex")
    front = _start_front(auth_client, [a], custom_status="at work")

    resp = auth_client.post(
        f"/v1/fronts/{front['id']}/replace",
        json={"member_ids": [a, b], "custom_status": None},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["custom_status"] is None


def test_replace_sets_new_status(auth_client: httpx.Client):
    a = _member(auth_client, "Ana")
    front = _start_front(auth_client, [a], custom_status="at work")

    resp = auth_client.post(
        f"/v1/fronts/{front['id']}/replace",
        json={"member_ids": [a], "custom_status": "home"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["custom_status"] == "home"


def test_replace_rejects_duplicate_of_other_open_front(auth_client: httpx.Client):
    a = _member(auth_client, "Ana")
    b = _member(auth_client, "Bex")
    c = _member(auth_client, "Cy")
    _start_front(auth_client, [a, b])
    cfront = _start_front(auth_client, [c])

    # Replacing {C} with {A,B} would duplicate the existing {A,B} front.
    resp = auth_client.post(
        f"/v1/fronts/{cfront['id']}/replace", json={"member_ids": [a, b]}
    )
    assert resp.status_code == 409, resp.text


def test_replace_rejects_empty_member_ids(auth_client: httpx.Client):
    a = _member(auth_client, "Ana")
    front = _start_front(auth_client, [a])
    resp = auth_client.post(
        f"/v1/fronts/{front['id']}/replace", json={"member_ids": []}
    )
    assert resp.status_code == 422, resp.text


def test_replace_unknown_front_404(auth_client: httpx.Client):
    a = _member(auth_client, "Ana")
    resp = auth_client.post(
        f"/v1/fronts/{uuid.uuid4()}/replace", json={"member_ids": [a]}
    )
    assert resp.status_code == 404, resp.text


def test_replace_already_ended_front_409(auth_client: httpx.Client):
    a = _member(auth_client, "Ana")
    b = _member(auth_client, "Bex")
    front = _start_front(auth_client, [a])
    # End it by replacing all open fronts with a different one.
    end = auth_client.post(
        "/v1/fronts", json={"member_ids": [b], "replace_fronts": True}
    )
    assert end.status_code == 201, end.text
    # The original front is now ended; replacing it is a conflict.
    resp = auth_client.post(
        f"/v1/fronts/{front['id']}/replace", json={"member_ids": [a]}
    )
    assert resp.status_code == 409, resp.text


def test_replace_belonging_to_another_system_404(
    auth_client: httpx.Client, client: httpx.Client
):
    # A second, unrelated account.
    email = f"other-{uuid.uuid4().hex[:8]}@sheaf.dev"
    reg = client.post(
        "/v1/auth/register", json={"email": email, "password": "testpassword123"}
    )
    assert reg.status_code == 201, reg.text
    client.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
    other_member = _member(client, "Zed")
    other_front = _start_front(client, [other_member])

    a = _member(auth_client, "Ana")
    # auth_client must not be able to touch the other system's front.
    resp = auth_client.post(
        f"/v1/fronts/{other_front['id']}/replace", json={"member_ids": [a]}
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# The whole point: one notification, not two. Inspect the outbox directly.
# ---------------------------------------------------------------------------


def _system_id(client: httpx.Client) -> str:
    return client.get("/v1/systems/me").json()["id"]


def _create_webhook_channel(client: httpx.Client) -> str:
    sid = _system_id(client)
    tok = client.post(
        f"/v1/systems/{sid}/watch-tokens", json={"label": "swap"}
    )
    assert tok.status_code == 201, tok.text
    resp = client.post(
        f"/v1/watch-tokens/{tok.json()['id']}/channels",
        json={
            "name": "swap webhook",
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


def _front_change_payloads(channel_id: str) -> list[dict]:
    async def _run() -> list[dict]:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.models.notification_outbox import NotificationOutboxRow

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with async_session() as db:
                rows = (
                    await db.execute(
                        select(NotificationOutboxRow)
                        .where(
                            NotificationOutboxRow.channel_id
                            == uuid.UUID(channel_id),
                            NotificationOutboxRow.event_type == "front_change",
                        )
                        .order_by(NotificationOutboxRow.enqueued_at)
                    )
                ).scalars().all()
                return [dict(r.event_payload) for r in rows]
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_replace_emits_single_net_notification(auth_client: httpx.Client):
    a = _member(auth_client, "Ana")
    b = _member(auth_client, "Bex")
    c = _member(auth_client, "Cy")

    # Start {A, C} BEFORE the channel exists, so its own emit finds no
    # channel and leaves no row; only the replace should produce one.
    ac = _start_front(auth_client, [a, c])
    channel_id = _create_webhook_channel(auth_client)

    resp = auth_client.post(
        f"/v1/fronts/{ac['id']}/replace", json={"member_ids": [a, b]}
    )
    assert resp.status_code == 201, resp.text

    payloads = _front_change_payloads(channel_id)
    # Exactly one outbox row: the swap is a single net transition, not a
    # stop followed by a start.
    assert len(payloads) == 1, payloads
    assert set(payloads[0]["fronting_before"]) == {a, c}
    assert set(payloads[0]["fronting_after"]) == {a, b}
