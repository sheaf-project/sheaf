"""Tests for the mobile-push (FCM / APNs) channel creation + redemption
flow. The test stack injects dummy FCM + APNs creds at startup so the
feature-flag gate passes; the actual transport is stubbed in dispatch
handler tests."""

from __future__ import annotations

import os
import uuid as _uuid

import httpx


def _system_id(client: httpx.Client) -> str:
    resp = client.get("/v1/systems/me")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _create_token(client: httpx.Client) -> dict:
    sid = _system_id(client)
    resp = client.post(
        f"/v1/systems/{sid}/watch-tokens", json={"label": "test"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_channel(
    client: httpx.Client, *, destination_type: str
) -> tuple[str, str]:
    """Create a channel of the given type, return (channel_id, activation_code)."""
    tok = _create_token(client)
    resp = client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={"name": "phone", "destination_type": destination_type},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    activation_url = body["activation_url"]
    code = activation_url.split("code=", 1)[1]
    if "&" in code:
        code = code.split("&", 1)[0]
    return body["channel"]["id"], code


def test_fcm_channel_creates_pending(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    resp = auth_client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={
            "name": "phone",
            "destination_type": "fcm",
            "base_all_members": True,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["channel"]["destination_state"] == "pending_registration"
    assert body["activation_url"] is not None
    assert "code=" in body["activation_url"]


def test_apns_channels_create_pending(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    for dt in ("apns_dev", "apns_prod"):
        resp = auth_client.post(
            f"/v1/watch-tokens/{tok['id']}/channels",
            json={"name": f"phone-{dt}", "destination_type": dt},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["channel"]["destination_state"] == "pending_registration"


def test_redeem_mobile_requires_session(auth_client: httpx.Client):
    """Mobile-push redemption is account-anchored — the redeemer must be
    logged in (session cookie). Anonymous redemption is rejected."""
    _, code = _create_channel(auth_client, destination_type="fcm")
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as anon:
        resp = anon.post(
            "/v1/notifications/redeem",
            json={"activation_code": code},
        )
    assert resp.status_code == 401, resp.text
    assert "login required" in resp.json()["detail"].lower()


def test_redeem_mobile_rejects_push_subscription(auth_client: httpx.Client):
    """push_subscription is meaningless for mobile push (transport lives
    on push_device_tokens, not the channel) and must be refused."""
    _, code = _create_channel(auth_client, destination_type="fcm")
    email = f"mobile-redeem-{_uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as c:
        reg = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert reg.status_code == 201, reg.text
        resp = c.post(
            "/v1/notifications/redeem",
            json={
                "activation_code": code,
                "push_subscription": {
                    "endpoint": "https://example.invalid/push",
                    "keys": {"p256dh": "x", "auth": "y"},
                },
            },
        )
    assert resp.status_code == 400, resp.text
    assert "mobile push" in resp.json()["detail"].lower()


def test_redeem_mobile_succeeds_with_session(auth_client: httpx.Client):
    """Logged-in redemption with no push_subscription succeeds, binds
    redeemed_by_account_id, and returns an empty management_url (mobile
    channels do not get an anonymous /manage URL)."""
    channel_id, code = _create_channel(auth_client, destination_type="apns_prod")
    email = f"mobile-redeem-ok-{_uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as c:
        reg = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert reg.status_code == 201, reg.text
        resp = c.post(
            "/v1/notifications/redeem",
            json={"activation_code": code},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["management_url"] == ""
    # The channel is now ACTIVE and bound to the redeeming account.
    fresh = auth_client.get(f"/v1/channels/{channel_id}").json()
    assert fresh["destination_state"] == "active"
    assert fresh["redeemed_by_account_id"] is not None


def test_unit_validate_destination_rejects_unconfigured_fcm(monkeypatch):
    """Unit test for the feature-flag gate: when FCM creds are absent,
    channel creation rejects with 501. Integration tests can't easily
    flip container-side env vars at runtime; this exercises the validator
    directly."""
    from sheaf.api.v1 import notification_channels as nc
    from sheaf.config import settings

    monkeypatch.setattr(settings, "fcm_service_account_path", "")
    monkeypatch.setattr(settings, "fcm_service_account_json", "")

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        nc._validate_destination("fcm")
    assert exc.value.status_code == 501


def test_unit_validate_destination_rejects_unconfigured_apns(monkeypatch):
    from sheaf.api.v1 import notification_channels as nc
    from sheaf.config import settings

    # Wipe out every APNs setting so the configured() helper returns False.
    monkeypatch.setattr(settings, "apns_team_id", "")
    monkeypatch.setattr(settings, "apns_key_id", "")
    monkeypatch.setattr(settings, "apns_bundle_id", "")
    monkeypatch.setattr(settings, "apns_p8_path", "")
    monkeypatch.setattr(settings, "apns_p8_key", "")

    import pytest
    from fastapi import HTTPException

    for dt in ("apns_dev", "apns_prod"):
        with pytest.raises(HTTPException) as exc:
            nc._validate_destination(dt)
        assert exc.value.status_code == 501
