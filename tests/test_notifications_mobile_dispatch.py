"""Tests for the mobile-push dispatch handler (`_deliver_mobile_push`).

These cover the orchestration logic — fan-out across an account's
push_device_tokens, dead-row eviction, success / transient aggregation —
by stubbing the FCM and APNs send_to_token primitives. The transport
modules themselves get separate, narrower tests against a mocked httpx.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest


def _system_id(client: httpx.Client) -> str:
    return client.get("/v1/systems/me").json()["id"]


def _create_token_and_channel(
    client: httpx.Client, *, destination_type: str
) -> tuple[str, str]:
    sid = _system_id(client)
    tok = client.post(
        f"/v1/systems/{sid}/watch-tokens", json={"label": "x"}
    ).json()
    resp = client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={"name": "phone", "destination_type": destination_type},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    code = body["activation_url"].split("code=", 1)[1]
    if "&" in code:
        code = code.split("&", 1)[0]
    return body["channel"]["id"], code


def _redeem_and_register_devices(
    *, code: str, email: str, platform: str, device_tokens: list[str]
) -> None:
    """Single-client flow: register a user (sets session cookie + bearer
    on the client), redeem the activation code (uses the cookie), then
    register the device tokens (uses the bearer). The redemption-time
    session check is what forces a single-client setup here."""
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as c:
        reg = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert reg.status_code == 201, reg.text
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        # Redeem (uses session cookie set by register).
        red = c.post("/v1/notifications/redeem", json={"activation_code": code})
        assert red.status_code == 200, red.text
        for tok in device_tokens:
            r = c.post(
                "/v1/devices/push",
                json={
                    "platform": platform,
                    "token": tok,
                    "install_id": tok[-8:],
                },
            )
            assert r.status_code == 200, r.text


# --- Unit tests on _deliver_mobile_push -----------------------------------


def _setup_channel_with_devices(
    auth_client: httpx.Client,
    *,
    destination_type: str,
    platform: str,
    device_tokens: list[str],
) -> tuple[uuid.UUID, uuid.UUID]:
    """Register a recipient, redeem a channel for them, register N
    devices. Returns (channel_id, recipient_user_id)."""
    channel_id, code = _create_token_and_channel(
        auth_client, destination_type=destination_type
    )
    recipient_email = f"recipient-{uuid.uuid4().hex[:8]}@sheaf.dev"
    _redeem_and_register_devices(
        code=code,
        email=recipient_email,
        platform=platform,
        device_tokens=device_tokens,
    )

    # Look up the recipient user_id for the test's later use.
    from sheaf.crypto import blind_index

    async def _lookup() -> uuid.UUID:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.models.user import User

        engine = create_async_engine(os.environ["SHEAF_TEST_DB_URL"])
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as db:
            user = (
                await db.execute(
                    select(User).where(User.email_hash == blind_index(recipient_email))
                )
            ).scalar_one()
        await engine.dispose()
        return user.id

    user_id = asyncio.run(_lookup())
    return uuid.UUID(channel_id), user_id


async def _run_dispatch(
    channel_id: uuid.UUID,
) -> object:
    """Invoke _deliver_mobile_push directly with a fresh DB session and
    a synthetic RenderedMessage. Returns the DeliveryResult."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from sheaf.models.notification_channel import NotificationChannel
    from sheaf.services.notifications.handlers import _deliver_mobile_push
    from sheaf.services.notifications.payload import RenderedMessage

    engine = create_async_engine(os.environ["SHEAF_TEST_DB_URL"])
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with async_session() as db:
            channel = (
                await db.execute(
                    select(NotificationChannel).where(
                        NotificationChannel.id == channel_id
                    )
                )
            ).scalar_one()
            platform = channel.destination_type
            if platform == "fcm":
                handler_platform = "fcm"
            elif platform in {"apns_dev", "apns_prod"}:
                handler_platform = platform
            else:
                pytest.fail(f"unexpected destination_type {platform}")
            return await _deliver_mobile_push(
                channel,
                RenderedMessage(title="hello", body="world"),
                event_id=str(uuid.uuid4()),
                db=db,
                platform=handler_platform,
            )
    finally:
        await engine.dispose()


def test_fcm_dispatch_fan_out_all_succeed(auth_client, monkeypatch):
    channel_id, _ = _setup_channel_with_devices(
        auth_client,
        destination_type="fcm",
        platform="fcm",
        device_tokens=["fcm-tok-A", "fcm-tok-B", "fcm-tok-C"],
    )

    sent: list[str] = []

    async def fake_send(*, device_token, title, body, event_id):
        from sheaf.services.notifications.fcm import FcmSendResult

        sent.append(device_token)
        return FcmSendResult(ok=True)

    monkeypatch.setattr("sheaf.services.notifications.fcm.send_to_token", fake_send)

    result = asyncio.run(_run_dispatch(channel_id))
    assert result.ok, result.error
    assert sorted(sent) == ["fcm-tok-A", "fcm-tok-B", "fcm-tok-C"]


def test_fcm_dispatch_dead_token_evicted(auth_client, monkeypatch):
    channel_id, recipient_id = _setup_channel_with_devices(
        auth_client,
        destination_type="fcm",
        platform="fcm",
        device_tokens=["fcm-good", "fcm-dead"],
    )

    async def fake_send(*, device_token, title, body, event_id):
        from sheaf.services.notifications.fcm import FcmSendResult

        if device_token == "fcm-dead":
            return FcmSendResult(dead=True, error="UNREGISTERED")
        return FcmSendResult(ok=True)

    monkeypatch.setattr("sheaf.services.notifications.fcm.send_to_token", fake_send)

    result = asyncio.run(_run_dispatch(channel_id))
    assert result.ok, result.error

    # The dead row was deleted in-line.
    async def _remaining() -> list[str]:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.models.push_device_token import PushDeviceToken

        engine = create_async_engine(os.environ["SHEAF_TEST_DB_URL"])
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as db:
            rows = (
                await db.execute(
                    select(PushDeviceToken.token).where(
                        PushDeviceToken.account_id == recipient_id,
                    )
                )
            ).all()
        await engine.dispose()
        return sorted(r[0] for r in rows)

    remaining = asyncio.run(_remaining())
    assert remaining == ["fcm-good"]


def test_fcm_dispatch_no_devices_is_success(auth_client, monkeypatch):
    """Channel with zero registered devices is success-with-no-effect,
    not a failure."""
    channel_id, _ = _setup_channel_with_devices(
        auth_client,
        destination_type="fcm",
        platform="fcm",
        device_tokens=[],
    )

    async def fake_send(*args, **kwargs):
        pytest.fail("send_to_token should not be invoked when there are no devices")

    monkeypatch.setattr("sheaf.services.notifications.fcm.send_to_token", fake_send)

    result = asyncio.run(_run_dispatch(channel_id))
    assert result.ok, result.error


def test_fcm_dispatch_all_transient_fails_transient(auth_client, monkeypatch):
    channel_id, _ = _setup_channel_with_devices(
        auth_client,
        destination_type="fcm",
        platform="fcm",
        device_tokens=["fcm-x", "fcm-y"],
    )

    async def fake_send(*, device_token, title, body, event_id):
        from sheaf.services.notifications.fcm import FcmSendResult

        return FcmSendResult(transient=True, error="upstream 503")

    monkeypatch.setattr("sheaf.services.notifications.fcm.send_to_token", fake_send)

    result = asyncio.run(_run_dispatch(channel_id))
    assert not result.ok
    assert result.transient
    assert "503" in (result.error or "")


def test_apns_dispatch_routes_per_platform(auth_client, monkeypatch):
    """apns_dev channel only fans out to apns_dev tokens, not apns_prod
    tokens (or vice versa). Cross-platform routing matters because dev
    tokens delivered to the prod host would bounce."""
    # Set up an apns_dev channel + two apns_dev devices on the recipient.
    channel_id, recipient_id = _setup_channel_with_devices(
        auth_client,
        destination_type="apns_dev",
        platform="apns_dev",
        device_tokens=["apns-dev-A", "apns-dev-B"],
    )
    # Also register an apns_prod device on the same recipient to confirm
    # the apns_dev channel ignores it. The API path would require
    # re-authenticating the recipient's session (we discarded the client
    # after _setup_channel_with_devices), so we insert via the DB
    # directly — same end state, less ceremony.
    async def _insert_extra() -> None:
        from datetime import UTC, datetime

        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.models.push_device_token import PushDeviceToken

        engine = create_async_engine(os.environ["SHEAF_TEST_DB_URL"])
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as db:
            db.add(
                PushDeviceToken(
                    id=uuid.uuid4(),
                    account_id=recipient_id,
                    platform="apns_prod",
                    token="apns-prod-X",
                    install_id="prod-X",
                    last_seen_at=datetime.now(UTC),
                )
            )
            await db.commit()
        await engine.dispose()

    asyncio.run(_insert_extra())

    sent: list[tuple[str, str]] = []

    async def fake_apns(*, platform, device_token, title, body, event_id):
        from sheaf.services.notifications.apns import ApnsSendResult

        sent.append((platform, device_token))
        return ApnsSendResult(ok=True)

    monkeypatch.setattr("sheaf.services.notifications.apns.send_to_token", fake_apns)

    result = asyncio.run(_run_dispatch(channel_id))
    assert result.ok, result.error
    # Only the two dev tokens were used; the prod one was filtered out.
    assert {(p, t) for p, t in sent} == {
        ("apns_dev", "apns-dev-A"),
        ("apns_dev", "apns-dev-B"),
    }
