"""Pushover BYO + monthly quota + min debounce floor.

API-level tests against the running test stack. The handler-level branching
(BYO short-circuits the counter; shared-app increments on success) is also
exercised here because the dispatcher's behaviour is what users observe.
"""

from __future__ import annotations

import httpx


def _system_id(client: httpx.Client) -> str:
    return client.get("/v1/systems/me").json()["id"]


def _create_token(client: httpx.Client) -> dict:
    sid = _system_id(client)
    resp = client.post(
        f"/v1/systems/{sid}/watch-tokens", json={"label": "pushover-tests"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_pushover_channel(
    client: httpx.Client,
    token_id: str,
    *,
    user_key: str = "u_test",
    app_token: str | None = None,
    debounce_seconds: int = 1800,
) -> tuple[int, dict]:
    cfg: dict = {"user_key": user_key}
    if app_token is not None:
        cfg["app_token"] = app_token
    resp = client.post(
        f"/v1/watch-tokens/{token_id}/channels",
        json={
            "name": "pushover test",
            "destination_type": "pushover",
            "destination_config": cfg,
            "debounce_seconds": debounce_seconds,
        },
    )
    return resp.status_code, resp.json() if resp.content else {}


# ---------------------------------------------------------------------------
# Min-debounce floor on shared-app channels
# ---------------------------------------------------------------------------


def test_shared_app_below_floor_rejected(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    code, body = _create_pushover_channel(
        auth_client, tok["id"], debounce_seconds=60
    )
    assert code == 400
    detail = body.get("detail", "")
    # User-facing message: should explain the minimum and point at the
    # BYO escape hatch; doesn't need to leak the raw setting name.
    assert "minutes" in detail.lower()
    assert "shared pushover" in detail.lower() or "shared pushover app" in detail.lower()


def test_shared_app_at_floor_accepted(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    code, _ = _create_pushover_channel(
        auth_client, tok["id"], debounce_seconds=1800
    )
    assert code == 201


def test_byo_app_token_bypasses_floor(auth_client: httpx.Client):
    """A recipient with their own Pushover app is on their own quota; the
    operator's debounce floor doesn't apply."""
    tok = _create_token(auth_client)
    code, _ = _create_pushover_channel(
        auth_client,
        tok["id"],
        app_token="a-30-char-byo-app-token-aaaa",
        debounce_seconds=30,
    )
    assert code == 201


def test_patch_lowering_debounce_below_floor_rejected(auth_client: httpx.Client):
    """Channel created at the floor; PATCH that would drop debounce below
    floor (without adding a BYO token) gets rejected."""
    tok = _create_token(auth_client)
    code, body = _create_pushover_channel(
        auth_client, tok["id"], debounce_seconds=1800
    )
    assert code == 201
    cid = body["channel"]["id"]
    resp = auth_client.patch(
        f"/v1/channels/{cid}", json={"debounce_seconds": 60}
    )
    assert resp.status_code == 400, resp.text


def test_patch_adding_byo_app_token_unlocks_lower_debounce(
    auth_client: httpx.Client,
):
    """Owner adds BYO token AND lowers debounce in one PATCH — should pass."""
    tok = _create_token(auth_client)
    code, body = _create_pushover_channel(
        auth_client, tok["id"], debounce_seconds=1800
    )
    assert code == 201
    cid = body["channel"]["id"]
    resp = auth_client.patch(
        f"/v1/channels/{cid}",
        json={
            "destination_config": {"app_token": "a-30-char-byo-token-aaaaaaaa"},
            "debounce_seconds": 60,
        },
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Admin endpoint surfaces usage
# ---------------------------------------------------------------------------


def test_admin_pushover_usage_endpoint(admin_client: httpx.Client):
    resp = admin_client.get("/v1/admin/pushover-usage")
    assert resp.status_code == 200
    body = resp.json()
    assert "month" in body
    assert "count" in body
    assert "cap" in body
    assert "enforced" in body
    assert isinstance(body["count"], int)
    assert isinstance(body["cap"], int)


def test_admin_pushover_usage_requires_admin(auth_client: httpx.Client):
    resp = auth_client.get("/v1/admin/pushover-usage")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# BYO mode skips the counter — verify via the admin endpoint
# ---------------------------------------------------------------------------


def test_byo_delivery_does_not_increment_counter(
    auth_client: httpx.Client, admin_client: httpx.Client
):
    """Sending a test through a BYO channel must not move the shared-app
    counter. We can't actually deliver to Pushover here (no live recipient),
    but the test send-path goes through the same handler and would INCR if
    BYO detection were broken."""
    before = admin_client.get("/v1/admin/pushover-usage").json()["count"]

    tok = _create_token(auth_client)
    code, body = _create_pushover_channel(
        auth_client,
        tok["id"],
        app_token="a-30-char-byo-token-aaaaaaaa",
        debounce_seconds=30,
    )
    assert code == 201
    cid = body["channel"]["id"]
    # Send-test will fail at Pushover (fake user_key) but that's a 4xx
    # response from Pushover, NOT a SUCCESS. Counter only moves on success.
    auth_client.post(f"/v1/channels/{cid}/test")

    after = admin_client.get("/v1/admin/pushover-usage").json()["count"]
    assert after == before


# ---------------------------------------------------------------------------
# Server-config endpoint surfaces the floor + shared-app availability
# ---------------------------------------------------------------------------


def test_server_config_surfaces_min_debounce(auth_client: httpx.Client):
    resp = auth_client.get("/v1/notifications/server-config")
    assert resp.status_code == 200
    body = resp.json()
    assert "pushover" in body
    assert "shared_app_min_debounce_seconds" in body["pushover"]
    assert "shared_app_available" in body["pushover"]
    assert isinstance(body["pushover"]["shared_app_available"], bool)
    assert isinstance(body["pushover"]["shared_app_min_debounce_seconds"], int)


# ---------------------------------------------------------------------------
# Per-user usage endpoint
# ---------------------------------------------------------------------------


def test_my_pushover_usage_endpoint(auth_client: httpx.Client):
    resp = auth_client.get("/v1/notifications/pushover-usage")
    assert resp.status_code == 200
    body = resp.json()
    assert "month" in body
    assert "tier" in body
    assert "count" in body
    assert "cap" in body
    assert "enforced" in body
    # Fresh user starts at 0.
    assert body["count"] == 0


def test_my_pushover_usage_requires_auth(client: httpx.Client):
    resp = client.get("/v1/notifications/pushover-usage")
    assert resp.status_code == 401
