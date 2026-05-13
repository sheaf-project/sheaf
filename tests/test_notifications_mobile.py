"""Tests for the mobile_push channel creation + redemption flow.

`mobile_push` is the single platform-agnostic mobile destination — the
channel binds to a Sheaf account at redemption, and the dispatcher fans
out across every push_device_tokens row for that account at delivery
time. The legacy fcm / apns_dev / apns_prod destination types are
rejected at channel creation; a migration collapsed any existing rows
to mobile_push.

The test stack injects dummy FCM + APNs creds at startup so the
configured() gate passes; the actual transport is stubbed in dispatch
handler tests.
"""

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
    client: httpx.Client, *, destination_type: str = "mobile_push"
) -> tuple[str, str]:
    """Create a channel and return (channel_id, activation_code)."""
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


def test_mobile_push_channel_creates_pending(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    resp = auth_client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={
            "name": "phone",
            "destination_type": "mobile_push",
            "base_all_members": True,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["channel"]["destination_type"] == "mobile_push"
    assert body["channel"]["destination_state"] == "pending_registration"
    assert body["activation_url"] is not None
    assert "code=" in body["activation_url"]


def test_mobile_push_activation_url_routes_through_universal_link_host(
    auth_client: httpx.Client,
):
    """mobile_push activation URLs must funnel through the shared
    Universal Link host (settings.mobile_link_base_url) rather than the
    instance's own frontend, since only that host is trusted by the
    apps' associated-domains / asset-links entitlements at build time.
    The instance origin is carried as a query param so the app can call
    the right `/v1/notifications/redeem` after intercepting."""
    from sheaf.config import settings

    tok = _create_token(auth_client)
    resp = auth_client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={"name": "phone", "destination_type": "mobile_push"},
    )
    assert resp.status_code == 201, resp.text
    url = resp.json()["activation_url"]
    assert url.startswith(settings.mobile_link_base_url.rstrip("/") + "/redeem?"), (
        f"expected mobile_push URL to route through "
        f"{settings.mobile_link_base_url}, got {url}"
    )
    assert "code=" in url
    assert "channel=" in url
    assert "instance=" in url


def test_web_push_activation_url_stays_on_instance(auth_client: httpx.Client):
    """web_push, by contrast, redeems via the instance's own frontend —
    no Universal Link funnel involved, since it's a browser-side
    service-worker flow scoped to whichever origin the recipient opens."""
    tok = _create_token(auth_client)
    resp = auth_client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={"name": "browser", "destination_type": "web_push"},
    )
    assert resp.status_code == 201, resp.text
    url = resp.json()["activation_url"]
    assert "/notifications/redeem?" in url
    assert "instance=" not in url


def test_legacy_mobile_types_rejected(auth_client: httpx.Client):
    """fcm / apns_dev / apns_prod are no longer accepted at channel
    creation — clients should use mobile_push. The error message points
    at the replacement so callers know what to switch to."""
    tok = _create_token(auth_client)
    for dt in ("fcm", "apns_dev", "apns_prod"):
        resp = auth_client.post(
            f"/v1/watch-tokens/{tok['id']}/channels",
            json={"name": f"legacy-{dt}", "destination_type": dt},
        )
        assert resp.status_code == 400, f"{dt}: {resp.text}"
        assert "mobile_push" in resp.json()["detail"]


def test_redeem_mobile_requires_session(auth_client: httpx.Client):
    """Mobile-push redemption is account-anchored — the redeemer must be
    logged in (session cookie or Bearer token). Anonymous redemption is
    rejected."""
    _, code = _create_channel(auth_client)
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
    _, code = _create_channel(auth_client)
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


def test_redeem_mobile_succeeds_with_bearer_token(auth_client: httpx.Client):
    """Mobile clients authenticate with Bearer access tokens, not cookies.
    Redemption must accept Bearer auth for the mobile sheaf:// deep-link
    handoff to work — the native app has no session cookie to send."""
    channel_id, code = _create_channel(auth_client)
    email = f"mobile-redeem-bearer-{_uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as register_c:
        reg = register_c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert reg.status_code == 201, reg.text
        access_token = reg.json()["access_token"]
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as bearer_c:
        bearer_c.headers["Authorization"] = f"Bearer {access_token}"
        resp = bearer_c.post(
            "/v1/notifications/redeem",
            json={"activation_code": code},
        )
    assert resp.status_code == 200, resp.text
    fresh = auth_client.get(f"/v1/channels/{channel_id}").json()
    assert fresh["destination_state"] == "active"
    assert fresh["redeemed_by_account_id"] is not None


def test_redeem_mobile_succeeds_with_session(auth_client: httpx.Client):
    """Logged-in redemption with no push_subscription succeeds, binds
    redeemed_by_account_id, and returns an empty management_url (mobile
    channels do not get an anonymous /manage URL)."""
    channel_id, code = _create_channel(auth_client)
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
    fresh = auth_client.get(f"/v1/channels/{channel_id}").json()
    assert fresh["destination_state"] == "active"
    assert fresh["redeemed_by_account_id"] is not None


def test_redeem_preview_returns_destination_type(auth_client: httpx.Client):
    """Public preview reveals destination_type so the recipient page can
    branch (web push -> in-browser flow; mobile_push -> deep link)."""
    _, code = _create_channel(auth_client)
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as anon:
        preview = anon.get(
            "/v1/notifications/redeem-preview", params={"code": code}
        )
    assert preview.status_code == 200, preview.text
    assert preview.json()["destination_type"] == "mobile_push"


def test_redeem_preview_does_not_consume_code(auth_client: httpx.Client):
    """Preview is read-only — the same code can still be redeemed
    afterward."""
    _, code = _create_channel(auth_client)

    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as anon:
        preview = anon.get(
            "/v1/notifications/redeem-preview", params={"code": code}
        )
    assert preview.status_code == 200

    email = f"preview-noconsume-{_uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as c:
        reg = c.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert reg.status_code == 201, reg.text
        resp = c.post("/v1/notifications/redeem", json={"activation_code": code})
    assert resp.status_code == 200, resp.text


def test_redeem_preview_invalid_code_returns_404(auth_client: httpx.Client):
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as anon:
        resp = anon.get(
            "/v1/notifications/redeem-preview", params={"code": "not-a-real-code"}
        )
    assert resp.status_code == 404


def test_redeem_preview_for_web_push_channel(auth_client: httpx.Client):
    """The same preview endpoint works for web push channels too — the
    destination_type field is what the client branches on."""
    _, code = _create_channel(auth_client, destination_type="web_push")

    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as anon:
        resp = anon.get(
            "/v1/notifications/redeem-preview", params={"code": code}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["destination_type"] == "web_push"


def test_unit_validate_destination_rejects_when_no_mobile_provider(monkeypatch):
    """Unit test for the configured() gate: when neither FCM nor APNs
    credentials are present, mobile_push channel creation rejects with
    501. Integration tests can't easily flip container-side env vars at
    runtime; this exercises the validator directly."""
    from sheaf.api.v1 import notification_channels as nc
    from sheaf.config import settings

    monkeypatch.setattr(settings, "fcm_service_account_path", "")
    monkeypatch.setattr(settings, "fcm_service_account_json", "")
    monkeypatch.setattr(settings, "apns_team_id", "")
    monkeypatch.setattr(settings, "apns_key_id", "")
    monkeypatch.setattr(settings, "apns_bundle_id", "")
    monkeypatch.setattr(settings, "apns_p8_path", "")
    monkeypatch.setattr(settings, "apns_p8_key", "")

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        nc._validate_destination("mobile_push")
    assert exc.value.status_code == 501
    assert "not configured" in exc.value.detail.lower()


def test_unit_validate_destination_allows_single_provider(monkeypatch):
    """A deployment with only FCM configured (or only APNs) still
    accepts mobile_push channels — fan-out at delivery time gracefully
    no-ops for missing-provider devices. We don't require both."""
    from sheaf.api.v1 import notification_channels as nc
    from sheaf.config import settings

    # FCM only.
    monkeypatch.setattr(
        settings,
        "fcm_service_account_json",
        '{"project_id":"x","client_email":"y","private_key":"z"}',
    )
    monkeypatch.setattr(settings, "apns_team_id", "")
    monkeypatch.setattr(settings, "apns_key_id", "")
    monkeypatch.setattr(settings, "apns_bundle_id", "")
    monkeypatch.setattr(settings, "apns_p8_path", "")
    monkeypatch.setattr(settings, "apns_p8_key", "")
    nc._validate_destination("mobile_push")  # does not raise
