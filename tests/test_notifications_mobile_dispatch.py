"""Tests for the mobile-push dispatch handler (`_deliver_mobile_push`).

The post-collapse fan-out: a single `mobile_push` channel iterates every
`push_device_tokens` row matching the redeemed account, routing each row
to FCM (Android) or APNs (iOS, sandbox vs prod per-token) by the device's
own `platform` column. Channel never picks a platform — the channel is
account-anchored and the devices carry the platform metadata.

Stubs the FCM and APNs send_to_token primitives. The transport modules
themselves get separate, narrower tests against a mocked httpx.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest


def _system_id(client: httpx.Client) -> str:
    return client.get("/v1/systems/me").json()["id"]


def _create_mobile_channel(client: httpx.Client) -> tuple[str, str]:
    sid = _system_id(client)
    tok = client.post(
        f"/v1/systems/{sid}/watch-tokens", json={"label": "x"}
    ).json()
    resp = client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={"name": "phone", "destination_type": "mobile_push"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    code = body["activation_url"].split("code=", 1)[1]
    if "&" in code:
        code = code.split("&", 1)[0]
    return body["channel"]["id"], code


def _redeem_and_register_devices(
    *,
    code: str,
    email: str,
    devices: list[tuple[str, str]],
) -> None:
    """Register a recipient, redeem the activation code, register the
    given (platform, token) device pairs. The redemption-time session
    check is what forces a single-client setup here."""
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as c:
        reg = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert reg.status_code == 201, reg.text
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        red = c.post("/v1/notifications/redeem", json={"activation_code": code})
        assert red.status_code == 200, red.text
        for platform, tok in devices:
            r = c.post(
                "/v1/devices/push",
                json={
                    "platform": platform,
                    "token": tok,
                    "install_id": tok[-8:],
                },
            )
            assert r.status_code == 200, r.text


def _setup_channel_with_devices(
    auth_client: httpx.Client,
    *,
    devices: list[tuple[str, str]],
) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (channel_id, recipient_user_id)."""
    channel_id, code = _create_mobile_channel(auth_client)
    recipient_email = f"recipient-{uuid.uuid4().hex[:8]}@sheaf.dev"
    _redeem_and_register_devices(
        code=code, email=recipient_email, devices=devices
    )

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
                    select(User).where(
                        User.email_hash == blind_index(recipient_email)
                    )
                )
            ).scalar_one()
        await engine.dispose()
        return user.id

    user_id = asyncio.run(_lookup())
    return uuid.UUID(channel_id), user_id


async def _run_dispatch(channel_id: uuid.UUID) -> object:
    """Invoke _deliver_mobile_push directly with a fresh DB session."""
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
            return await _deliver_mobile_push(
                channel,
                RenderedMessage(title="hello", body="world"),
                event_id=str(uuid.uuid4()),
                db=db,
            )
    finally:
        await engine.dispose()


# --- Tests ----------------------------------------------------------------


def test_fan_out_to_all_fcm_devices(auth_client, monkeypatch):
    """All FCM devices on the account receive."""
    channel_id, _ = _setup_channel_with_devices(
        auth_client,
        devices=[
            ("fcm", "fcm-tok-A"),
            ("fcm", "fcm-tok-B"),
            ("fcm", "fcm-tok-C"),
        ],
    )

    sent: list[str] = []

    async def fake_send(*, device_token, title, body, event_id, **_):
        from sheaf.services.notifications.fcm import FcmSendResult

        sent.append(device_token)
        return FcmSendResult(ok=True)

    monkeypatch.setattr("sheaf.services.notifications.fcm.send_to_token", fake_send)

    result = asyncio.run(_run_dispatch(channel_id))
    assert result.ok, result.error
    assert sorted(sent) == ["fcm-tok-A", "fcm-tok-B", "fcm-tok-C"]


def test_dead_token_evicted_from_account(auth_client, monkeypatch):
    """A 410 / Unregistered response permanently drops the token row so
    the next dispatch doesn't re-attempt it."""
    channel_id, recipient_id = _setup_channel_with_devices(
        auth_client,
        devices=[("fcm", "fcm-good"), ("fcm", "fcm-dead")],
    )

    async def fake_send(*, device_token, title, body, event_id, **_):
        from sheaf.services.notifications.fcm import FcmSendResult

        if device_token == "fcm-dead":
            return FcmSendResult(dead=True, error="UNREGISTERED")
        return FcmSendResult(ok=True)

    monkeypatch.setattr("sheaf.services.notifications.fcm.send_to_token", fake_send)
    result = asyncio.run(_run_dispatch(channel_id))
    assert result.ok, result.error

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
                        PushDeviceToken.account_id == recipient_id
                    )
                )
            ).scalars().all()
        await engine.dispose()
        return sorted(rows)

    remaining = asyncio.run(_remaining())
    assert remaining == ["fcm-good"]


def test_no_devices_is_success(auth_client, monkeypatch):
    """Channel with zero registered devices is success-with-no-effect."""
    channel_id, _ = _setup_channel_with_devices(auth_client, devices=[])

    async def fake_send(*args, **kwargs):
        pytest.fail(
            "send_to_token should not be invoked when there are no devices"
        )

    monkeypatch.setattr("sheaf.services.notifications.fcm.send_to_token", fake_send)
    monkeypatch.setattr("sheaf.services.notifications.apns.send_to_token", fake_send)

    result = asyncio.run(_run_dispatch(channel_id))
    assert result.ok, result.error


def test_all_transient_fails_transient(auth_client, monkeypatch):
    """If every device returns transient, the channel result is transient
    so the dispatcher retries later."""
    channel_id, _ = _setup_channel_with_devices(
        auth_client,
        devices=[("fcm", "fcm-x"), ("fcm", "fcm-y")],
    )

    async def fake_send(*, device_token, title, body, event_id, **_):
        from sheaf.services.notifications.fcm import FcmSendResult

        return FcmSendResult(transient=True, error="upstream 503")

    monkeypatch.setattr("sheaf.services.notifications.fcm.send_to_token", fake_send)

    result = asyncio.run(_run_dispatch(channel_id))
    assert not result.ok
    assert result.transient
    assert "503" in (result.error or "")


def test_fan_out_rings_both_fcm_and_apns(auth_client, monkeypatch):
    """One mobile_push channel rings every device on the account,
    regardless of platform. Replaces the old per-channel-platform
    routing — the account is the routing key now."""
    channel_id, _ = _setup_channel_with_devices(
        auth_client,
        devices=[
            ("fcm", "android-A"),
            ("fcm", "android-B"),
            ("apns_prod", "iphone-X"),
        ],
    )

    fcm_sent: list[str] = []
    apns_sent: list[tuple[str, str]] = []

    async def fake_fcm(*, device_token, title, body, event_id, **_):
        from sheaf.services.notifications.fcm import FcmSendResult

        fcm_sent.append(device_token)
        return FcmSendResult(ok=True)

    async def fake_apns(*, platform, device_token, title, body, event_id, **_):
        from sheaf.services.notifications.apns import ApnsSendResult

        apns_sent.append((platform, device_token))
        return ApnsSendResult(ok=True)

    monkeypatch.setattr("sheaf.services.notifications.fcm.send_to_token", fake_fcm)
    monkeypatch.setattr("sheaf.services.notifications.apns.send_to_token", fake_apns)

    result = asyncio.run(_run_dispatch(channel_id))
    assert result.ok, result.error
    assert sorted(fcm_sent) == ["android-A", "android-B"]
    assert apns_sent == [("apns_prod", "iphone-X")]


def test_disabled_devices_skipped(auth_client, monkeypatch):
    """A device with enabled=False is excluded from fan-out so the
    recipient can soft-mute a single device without unregistering it."""
    channel_id, recipient_id = _setup_channel_with_devices(
        auth_client,
        devices=[("fcm", "active-tok"), ("fcm", "muted-tok")],
    )

    # Flip the muted device's `enabled` to False directly via DB; the
    # API path is exercised separately in test_devices.py.
    async def _mute() -> None:
        from sqlalchemy import select, update
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.models.push_device_token import PushDeviceToken

        engine = create_async_engine(os.environ["SHEAF_TEST_DB_URL"])
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as db:
            await db.execute(
                update(PushDeviceToken)
                .where(
                    PushDeviceToken.account_id == recipient_id,
                    PushDeviceToken.token == "muted-tok",
                )
                .values(enabled=False)
            )
            await db.commit()
            # Sanity: confirm the row still exists.
            row = (
                await db.execute(
                    select(PushDeviceToken).where(
                        PushDeviceToken.account_id == recipient_id,
                        PushDeviceToken.token == "muted-tok",
                    )
                )
            ).scalar_one_or_none()
            assert row is not None
            assert row.enabled is False
        await engine.dispose()

    asyncio.run(_mute())

    sent: list[str] = []

    async def fake_fcm(*, device_token, title, body, event_id, **_):
        from sheaf.services.notifications.fcm import FcmSendResult

        sent.append(device_token)
        return FcmSendResult(ok=True)

    monkeypatch.setattr("sheaf.services.notifications.fcm.send_to_token", fake_fcm)

    result = asyncio.run(_run_dispatch(channel_id))
    assert result.ok, result.error
    assert sent == ["active-tok"], (
        f"muted device should be skipped, got sent={sent}"
    )


def test_channel_metadata_threaded_to_send_to_token(auth_client, monkeypatch):
    """Every dispatched push carries the originating channel's id, name,
    and event_type so the Android client can route into a per-subscription
    NotificationChannel rather than bucketing every push onto one fallback.
    Regression test for the Android v0.1.11+ contract."""
    channel_id, _ = _setup_channel_with_devices(
        auth_client,
        devices=[("fcm", "fcm-meta-A"), ("apns_prod", "apns-meta-B")],
    )

    fcm_calls: list[dict] = []
    apns_calls: list[dict] = []

    async def fake_fcm(**kwargs):
        from sheaf.services.notifications.fcm import FcmSendResult

        fcm_calls.append(kwargs)
        return FcmSendResult(ok=True)

    async def fake_apns(**kwargs):
        from sheaf.services.notifications.apns import ApnsSendResult

        apns_calls.append(kwargs)
        return ApnsSendResult(ok=True)

    monkeypatch.setattr("sheaf.services.notifications.fcm.send_to_token", fake_fcm)
    monkeypatch.setattr("sheaf.services.notifications.apns.send_to_token", fake_apns)

    result = asyncio.run(_run_dispatch(channel_id))
    assert result.ok, result.error

    for call in fcm_calls + apns_calls:
        assert call["channel_id"] == str(channel_id), (
            f"expected channel_id={channel_id!s}, got {call.get('channel_id')!r}"
        )
        # The channel's name is "phone" via _create_mobile_channel.
        assert call["channel_name"] == "phone"
        # event_type defaults to "front_change" on creation.
        assert call["event_type"] == "front_change"
