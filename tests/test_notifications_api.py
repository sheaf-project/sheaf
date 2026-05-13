"""Front-change notifications: API surface tests.

Covers the owner-side CRUD + activation + duplicate + reissue flows, the
recipient-side redeem/manage flow, and the filter-resolution preview.

Dispatcher behaviour (debounce, quiet hours, SSRF guards, cofront detection)
is exercised in `test_notifications_resolution.py` and
`test_notifications_dispatch.py` against the same running server.
"""

from __future__ import annotations

import httpx


def _create_member(client: httpx.Client, name: str, *, privacy: str = "public") -> str:
    resp = client.post("/v1/members", json={"name": name, "privacy": privacy})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _system_id(client: httpx.Client) -> str:
    resp = client.get("/v1/systems/me")
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _create_token(client: httpx.Client, label: str | None = "Mara") -> dict:
    sid = _system_id(client)
    resp = client.post(
        f"/v1/systems/{sid}/watch-tokens", json={"label": label}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_webhook_channel(
    client: httpx.Client,
    token_id: str,
    *,
    url: str = "https://example.com/webhook",
    name: str = "Test webhook",
    base_all: bool = True,
) -> dict:
    resp = client.post(
        f"/v1/watch-tokens/{token_id}/channels",
        json={
            "name": name,
            "destination_type": "webhook",
            "destination_config": {"url": url},
            "webhook_secret": "supersecret",
            "base_all_members": base_all,
            "trigger_on_start": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------------------ tokens


def test_create_and_list_watch_tokens(auth_client: httpx.Client):
    sid = _system_id(auth_client)
    resp = auth_client.post(
        f"/v1/systems/{sid}/watch-tokens", json={"label": "Mara"}
    )
    assert resp.status_code == 201
    tok = resp.json()
    assert tok["label"] == "Mara"
    assert tok["revoked_at"] is None
    assert tok["channel_count"] == 0

    listing = auth_client.get(f"/v1/systems/{sid}/watch-tokens")
    assert listing.status_code == 200
    assert any(t["id"] == tok["id"] for t in listing.json())


def test_revoke_watch_token(auth_client: httpx.Client):
    tok = _create_token(auth_client, "Nora")
    resp = auth_client.delete(f"/v1/watch-tokens/{tok['id']}")
    assert resp.status_code == 204

    fresh = auth_client.get(f"/v1/watch-tokens/{tok['id']}")
    assert fresh.status_code == 200
    assert fresh.json()["revoked_at"] is not None


# ---------------------------------------------------------------- channels


def test_create_webhook_channel_active_immediately(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    body = _create_webhook_channel(auth_client, tok["id"])
    assert body["activation_url"] is None
    assert body["channel"]["destination_state"] == "active"
    assert body["channel"]["destination_config"]["url"] == "https://example.com/webhook"


def test_create_web_push_channel_pending(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    resp = auth_client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={
            "name": "phone",
            "destination_type": "web_push",
            "base_all_members": True,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["activation_url"] is not None
    assert "code=" in body["activation_url"]
    assert body["channel"]["destination_state"] == "pending_registration"


def test_create_email_channel_rejected(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    resp = auth_client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={"name": "x", "destination_type": "email"},
    )
    # Pydantic's Literal validator rejects unknown destination types with 422
    # before the handler runs. The handler's own 501 path is defensive but
    # unreachable while the schema literal excludes reserved values.
    assert resp.status_code == 422


def test_webhook_requires_url(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    resp = auth_client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={
            "name": "x",
            "destination_type": "webhook",
            "destination_config": {},
        },
    )
    assert resp.status_code == 400


def test_update_channel_replaces_rules(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    body = _create_webhook_channel(auth_client, tok["id"])
    cid = body["channel"]["id"]
    member = _create_member(auth_client, "Alex")

    patch = auth_client.patch(
        f"/v1/channels/{cid}",
        json={"member_rules": [{"member_id": member, "rule": "exclude"}]},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["member_rules"] == [
        {"member_id": member, "rule": "exclude"}
    ]


def test_duplicate_channel(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    body = _create_webhook_channel(auth_client, tok["id"])
    cid = body["channel"]["id"]

    dup = auth_client.post(f"/v1/channels/{cid}/duplicate")
    assert dup.status_code == 201, dup.text
    clone = dup.json()["channel"]
    assert clone["id"] != cid
    assert clone["name"].endswith("(copy)")
    # Webhook duplicates land in pending_registration with empty config.
    assert clone["destination_state"] == "pending_registration"
    assert clone["destination_config"] == {}


def test_reissue_activation(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    resp = auth_client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={"name": "phone", "destination_type": "web_push"},
    )
    cid = resp.json()["channel"]["id"]
    first_url = resp.json()["activation_url"]

    again = auth_client.post(f"/v1/channels/{cid}/reissue-activation")
    assert again.status_code == 200, again.text
    assert again.json()["activation_url"] != first_url


def test_reissue_rejects_active_channel(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    body = _create_webhook_channel(auth_client, tok["id"])
    cid = body["channel"]["id"]
    resp = auth_client.post(f"/v1/channels/{cid}/reissue-activation")
    # Webhook is active immediately + isn't push-style, so re-issue is invalid.
    assert resp.status_code == 400


def test_delete_channel(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    body = _create_webhook_channel(auth_client, tok["id"])
    cid = body["channel"]["id"]
    resp = auth_client.delete(f"/v1/channels/{cid}")
    assert resp.status_code == 204
    assert auth_client.get(f"/v1/channels/{cid}").status_code == 404


# ----------------------------------------------------------- preview / resolution


def test_preview_includes_public_member_with_l1_all(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    body = _create_webhook_channel(auth_client, tok["id"], base_all=True)
    cid = body["channel"]["id"]
    member = _create_member(auth_client, "Alex", privacy="public")

    resp = auth_client.post(f"/v1/channels/{cid}/preview")
    assert resp.status_code == 200, resp.text
    pv = resp.json()
    inc_ids = [m["member_id"] for m in pv["included"]]
    assert member in inc_ids


def test_preview_excludes_private_unless_opted_in(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    body = _create_webhook_channel(auth_client, tok["id"], base_all=True)
    cid = body["channel"]["id"]
    private = _create_member(auth_client, "Hidden", privacy="private")

    pv = auth_client.post(f"/v1/channels/{cid}/preview").json()
    assert private not in [m["member_id"] for m in pv["included"]]
    # base_include_private=true should pull them in.
    auth_client.patch(f"/v1/channels/{cid}", json={"base_include_private": True})
    pv = auth_client.post(f"/v1/channels/{cid}/preview").json()
    assert private in [m["member_id"] for m in pv["included"]]


def test_preview_l3_overrides_l1(auth_client: httpx.Client):
    tok = _create_token(auth_client)
    body = _create_webhook_channel(auth_client, tok["id"], base_all=True)
    cid = body["channel"]["id"]
    excluded = _create_member(auth_client, "Bob", privacy="public")

    auth_client.patch(
        f"/v1/channels/{cid}",
        json={"member_rules": [{"member_id": excluded, "rule": "exclude"}]},
    )
    pv = auth_client.post(f"/v1/channels/{cid}/preview").json()
    excluded_block = [m["member_id"] for m in pv["excluded"]]
    assert excluded in excluded_block
    found = next(m for m in pv["excluded"] if m["member_id"] == excluded)
    assert found["attribution"] == "L3 rule"


# --------------------------------------------------------- redeem / manage


def test_redeem_web_push_flow(auth_client: httpx.Client, client: httpx.Client):
    tok = _create_token(auth_client)
    resp = auth_client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={"name": "phone", "destination_type": "web_push"},
    )
    activation_url = resp.json()["activation_url"]
    code = activation_url.split("code=")[1].split("&")[0]

    redeem = client.post(
        "/v1/notifications/redeem",
        json={
            "activation_code": code,
            "push_subscription": {
                "endpoint": "https://push.example.com/abc",
                "keys": {"p256dh": "x", "auth": "y"},
            },
        },
    )
    assert redeem.status_code == 200, redeem.text
    body = redeem.json()
    assert body["channel_name"] == "phone"
    assert (
        body["management_url"].startswith("/notifications/manage/")
        or "manage/" in body["management_url"]
    )

    mgmt_token = body["management_url"].rstrip("/").split("/")[-1]
    view = client.get(f"/v1/notifications/manage/{mgmt_token}")
    assert view.status_code == 200, view.text
    assert view.json()["channel_name"] == "phone"
    assert view.json()["destination_state"] == "active"

    unsub = client.post(f"/v1/notifications/manage/{mgmt_token}/unsubscribe")
    assert unsub.status_code == 204
    after = client.get(f"/v1/notifications/manage/{mgmt_token}")
    assert after.json()["destination_state"] == "disabled"


def test_redeem_invalid_code(client: httpx.Client):
    resp = client.post(
        "/v1/notifications/redeem",
        json={"activation_code": "definitely-not-a-real-code"},
    )
    assert resp.status_code == 404


def test_redeem_used_code_rejected(auth_client: httpx.Client, client: httpx.Client):
    tok = _create_token(auth_client)
    resp = auth_client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={"name": "phone", "destination_type": "web_push"},
    )
    code = resp.json()["activation_url"].split("code=")[1].split("&")[0]
    body = {
        "activation_code": code,
        "push_subscription": {
            "endpoint": "https://push.example.com/abc",
            "keys": {"p256dh": "x", "auth": "y"},
        },
    }
    first = client.post("/v1/notifications/redeem", json=body)
    assert first.status_code == 200
    second = client.post("/v1/notifications/redeem", json=body)
    assert second.status_code == 404  # Code is invalidated after redemption


# -------------------------------------------------------- isolation / authz


def test_owner_pause_sets_paused_by_sender(auth_client: httpx.Client):
    """`POST /channels/{id}/disable` (owner action) flips the channel
    into the DISABLED state AND sets paused_by_sender=true. Recipient
    UIs use the flag to render "Paused by sender" instead of
    "Unsubscribed"."""
    tok = _create_token(auth_client)
    channel = _create_webhook_channel(auth_client, tok["id"])
    channel_id = channel["channel"]["id"]

    resp = auth_client.post(f"/v1/channels/{channel_id}/disable")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["destination_state"] == "disabled"
    assert body["paused_by_sender"] is True

    # Re-enable clears the flag — next disable gets its own attribution.
    resp = auth_client.post(f"/v1/channels/{channel_id}/enable")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["destination_state"] == "active"
    assert body["paused_by_sender"] is False


def test_other_user_cannot_see_token(auth_client: httpx.Client):
    tok = _create_token(auth_client, "Mine")

    # Spawn a second auth client by forking via the conftest fixture pattern.
    import uuid

    other_email = f"other-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=auth_client.base_url) as c:
        reg = c.post(
            "/v1/auth/register",
            json={"email": other_email, "password": "testpassword123"},
        )
        assert reg.status_code == 201
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        resp = c.get(f"/v1/watch-tokens/{tok['id']}")
        assert resp.status_code == 404
