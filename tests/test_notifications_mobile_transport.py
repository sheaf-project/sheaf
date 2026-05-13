"""Tests for the FCM and APNs transport modules.

These mock httpx via httpx.MockTransport, then drive the per-message
send_to_token primitive directly. Covers the response-classification
logic and the OAuth2 / JWT-bearer flows for FCM."""

from __future__ import annotations

import json

import pytest

from sheaf.config import settings

# Generated for these tests only; not a real key. P-256 in PKCS#8 PEM —
# valid enough for jwt.encode to sign with, which is all we need.
_TEST_P8 = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgevZzL1gdAFr88hb2
OF/2NxApJCzGCEDdfSp6VQO30hyhRANCAAQRWz+jn65BtOMvdyHKcvjBeBSDZH2r
1RTwjmYSi9R/zpBnuQ4EiMnCqfMPWiZqB4QdbAd0E7oH50VpuZ1P087G
-----END PRIVATE KEY-----
"""


def _fake_service_account_json() -> str:
    """Build a service-account JSON good enough for the FCM module's
    parsing. The private_key field is the same fake P-256 key — RS256
    JWT signing in jwt.encode actually requires RSA, but we never reach
    the real Google endpoint in these tests so mock-classification
    happens before signing matters."""
    return json.dumps(
        {
            "project_id": "sheaf-test",
            "client_email": "test@sheaf-test.iam.gserviceaccount.com",
            "private_key": _TEST_P8,
        }
    )


# --- FCM transport ---------------------------------------------------------


@pytest.fixture
def fcm_settings(monkeypatch):
    monkeypatch.setattr(settings, "fcm_service_account_path", "")
    monkeypatch.setattr(
        settings, "fcm_service_account_json", _fake_service_account_json()
    )
    # Ensure the FCM access-token cache is empty between tests so each
    # one drives the OAuth2 path fresh.
    from sheaf.services.notifications import fcm

    fcm._reset_cache_for_tests()


def _patch_async_client(monkeypatch, *, oauth_response, send_response):
    """Replace httpx.AsyncClient with a stub whose `post` returns the
    appropriate fake response based on URL.

    `oauth_response` and `send_response` are tuples (status_code, text).
    """

    class _FakeResp:
        def __init__(self, status_code: int, text: str):
            self.status_code = status_code
            self.text = text

        def json(self):
            return json.loads(self.text) if self.text else {}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def post(self, url, *args, **kwargs):
            if "oauth2.googleapis.com" in url:
                code, body = oauth_response
            else:
                code, body = send_response
            return _FakeResp(code, body)

    monkeypatch.setattr("sheaf.services.notifications.fcm.httpx.AsyncClient", _FakeClient)


@pytest.mark.asyncio
async def test_fcm_send_success(fcm_settings, monkeypatch):
    _patch_async_client(
        monkeypatch,
        oauth_response=(200, json.dumps({"access_token": "fake-access-token"})),
        send_response=(200, json.dumps({"name": "projects/x/messages/abc"})),
    )
    # Skip JWT signing (the test key is P-256 but FCM wants RS256). The
    # cache fetch goes via _fetch_access_token -> jwt.encode -> client.post;
    # patch jwt.encode in the fcm module to return a fixed string so we
    # don't fail on key-type mismatch.
    monkeypatch.setattr(
        "sheaf.services.notifications.fcm.jwt.encode",
        lambda *a, **kw: "fake.jwt.assertion",
    )

    from sheaf.services.notifications.fcm import send_to_token

    result = await send_to_token(
        device_token="dev-tok",
        title="hi",
        body="there",
        event_id="evt-1",
        channel_id="ch-1",
        channel_name="test",
        event_type="front_change",
    )
    assert result.ok, result.error


@pytest.mark.asyncio
async def test_fcm_send_404_unregistered_marks_dead(fcm_settings, monkeypatch):
    _patch_async_client(
        monkeypatch,
        oauth_response=(200, json.dumps({"access_token": "fake-access-token"})),
        send_response=(404, json.dumps({"error": {"status": "NOT_FOUND"}})),
    )
    monkeypatch.setattr(
        "sheaf.services.notifications.fcm.jwt.encode",
        lambda *a, **kw: "fake.jwt.assertion",
    )

    from sheaf.services.notifications.fcm import send_to_token

    result = await send_to_token(
        device_token="dev-dead",
        title="x",
        body="y",
        event_id="e",
        channel_id="ch-1",
        channel_name="test",
        event_type="front_change",
    )
    assert result.dead
    assert not result.ok


@pytest.mark.asyncio
async def test_fcm_send_5xx_marks_transient(fcm_settings, monkeypatch):
    _patch_async_client(
        monkeypatch,
        oauth_response=(200, json.dumps({"access_token": "fake-access-token"})),
        send_response=(503, "service unavailable"),
    )
    monkeypatch.setattr(
        "sheaf.services.notifications.fcm.jwt.encode",
        lambda *a, **kw: "fake.jwt.assertion",
    )

    from sheaf.services.notifications.fcm import send_to_token

    result = await send_to_token(
        device_token="dev-flaky",
        title="x",
        body="y",
        event_id="e",
        channel_id="ch-1",
        channel_name="test",
        event_type="front_change",
    )
    assert result.transient
    assert not result.ok
    assert "503" in (result.error or "")


@pytest.mark.asyncio
async def test_fcm_unconfigured_returns_transient(monkeypatch):
    monkeypatch.setattr(settings, "fcm_service_account_path", "")
    monkeypatch.setattr(settings, "fcm_service_account_json", "")
    from sheaf.services.notifications import fcm

    fcm._reset_cache_for_tests()

    result = await fcm.send_to_token(
        device_token="x",
        title="t",
        body="b",
        event_id="e",
        channel_id="ch-1",
        channel_name="test",
        event_type="front_change",
    )
    assert result.transient
    assert "FCM" in (result.error or "")


# --- APNs transport --------------------------------------------------------


@pytest.fixture
def apns_settings(monkeypatch):
    monkeypatch.setattr(settings, "apns_team_id", "TESTTEAM01")
    monkeypatch.setattr(settings, "apns_key_id", "TESTKEY001")
    monkeypatch.setattr(settings, "apns_bundle_id", "com.sheaftest.app")
    monkeypatch.setattr(settings, "apns_bundle_id_dev", "")
    monkeypatch.setattr(settings, "apns_p8_path", "")
    monkeypatch.setattr(settings, "apns_p8_key", _TEST_P8)
    from sheaf.services.notifications import apns

    apns._reset_cache_for_tests()


def _patch_apns_client(monkeypatch, *, status_code: int, body: str):
    class _FakeResp:
        def __init__(self, status_code: int, text: str):
            self.status_code = status_code
            self.text = text

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            self.captured: list[dict] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def post(self, url, *args, **kwargs):
            return _FakeResp(status_code, body)

    monkeypatch.setattr(
        "sheaf.services.notifications.apns.httpx.AsyncClient", _FakeClient
    )


@pytest.mark.asyncio
async def test_apns_dev_routes_to_sandbox_host(apns_settings, monkeypatch):
    captured_url: list[str] = []

    class _FakeResp:
        status_code = 200
        text = ""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def post(self, url, *args, **kwargs):
            captured_url.append(url)
            return _FakeResp()

    monkeypatch.setattr(
        "sheaf.services.notifications.apns.httpx.AsyncClient", _FakeClient
    )

    from sheaf.services.notifications.apns import send_to_token

    result = await send_to_token(
        platform="apns_dev",
        device_token="abc123",
        title="t",
        body="b",
        event_id="e",
        channel_id="ch-1",
        channel_name="test",
        event_type="front_change",
    )
    assert result.ok, result.error
    assert "api.sandbox.push.apple.com" in captured_url[0]


@pytest.mark.asyncio
async def test_apns_prod_routes_to_prod_host(apns_settings, monkeypatch):
    captured_url: list[str] = []

    class _FakeResp:
        status_code = 200
        text = ""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def post(self, url, *args, **kwargs):
            captured_url.append(url)
            return _FakeResp()

    monkeypatch.setattr(
        "sheaf.services.notifications.apns.httpx.AsyncClient", _FakeClient
    )

    from sheaf.services.notifications.apns import send_to_token

    result = await send_to_token(
        platform="apns_prod",
        device_token="def456",
        title="t",
        body="b",
        event_id="e",
        channel_id="ch-1",
        channel_name="test",
        event_type="front_change",
    )
    assert result.ok, result.error
    assert "api.push.apple.com" in captured_url[0]
    # And NOT the sandbox host.
    assert "sandbox" not in captured_url[0]


@pytest.mark.asyncio
async def test_apns_410_marks_dead(apns_settings, monkeypatch):
    _patch_apns_client(monkeypatch, status_code=410, body='{"reason":"Unregistered"}')
    from sheaf.services.notifications.apns import send_to_token

    result = await send_to_token(
        platform="apns_prod",
        device_token="dead",
        title="t",
        body="b",
        event_id="e",
        channel_id="ch-1",
        channel_name="test",
        event_type="front_change",
    )
    assert result.dead


@pytest.mark.asyncio
async def test_apns_400_bad_device_token_marks_dead(apns_settings, monkeypatch):
    _patch_apns_client(
        monkeypatch, status_code=400, body='{"reason":"BadDeviceToken"}'
    )
    from sheaf.services.notifications.apns import send_to_token

    result = await send_to_token(
        platform="apns_prod",
        device_token="bad",
        title="t",
        body="b",
        event_id="e",
        channel_id="ch-1",
        channel_name="test",
        event_type="front_change",
    )
    assert result.dead


@pytest.mark.asyncio
async def test_apns_5xx_marks_transient(apns_settings, monkeypatch):
    _patch_apns_client(monkeypatch, status_code=503, body="overloaded")
    from sheaf.services.notifications.apns import send_to_token

    result = await send_to_token(
        platform="apns_prod",
        device_token="x",
        title="t",
        body="b",
        event_id="e",
        channel_id="ch-1",
        channel_name="test",
        event_type="front_change",
    )
    assert result.transient


@pytest.mark.asyncio
async def test_apns_dev_uses_dev_bundle_when_set(monkeypatch):
    """If APNS_BUNDLE_ID_DEV is set, it overrides APNS_BUNDLE_ID for the
    apns-topic header on apns_dev devices. Prod devices still use the
    base bundle id."""
    monkeypatch.setattr(settings, "apns_team_id", "TESTTEAM01")
    monkeypatch.setattr(settings, "apns_key_id", "TESTKEY001")
    monkeypatch.setattr(settings, "apns_bundle_id", "com.example.sheaf")
    monkeypatch.setattr(settings, "apns_bundle_id_dev", "com.example.sheaf.dev")
    monkeypatch.setattr(settings, "apns_p8_path", "")
    monkeypatch.setattr(settings, "apns_p8_key", _TEST_P8)
    from sheaf.services.notifications import apns

    apns._reset_cache_for_tests()

    captured: list[dict] = []

    class _FakeResp:
        status_code = 200
        text = ""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def post(self, url, *args, **kwargs):
            captured.append(kwargs.get("headers", {}))
            return _FakeResp()

    monkeypatch.setattr(
        "sheaf.services.notifications.apns.httpx.AsyncClient", _FakeClient
    )

    from sheaf.services.notifications.apns import send_to_token

    await send_to_token(
        platform="apns_dev",
        device_token="t",
        title="t",
        body="b",
        event_id="e",
        channel_id="ch-1",
        channel_name="test",
        event_type="front_change",
    )
    await send_to_token(
        platform="apns_prod",
        device_token="t",
        title="t",
        body="b",
        event_id="e",
        channel_id="ch-1",
        channel_name="test",
        event_type="front_change",
    )
    assert captured[0]["apns-topic"] == "com.example.sheaf.dev"
    assert captured[1]["apns-topic"] == "com.example.sheaf"


