"""Account activity log: the self-only GET /v1/account/activity surface
and a representative end-to-end record (creating an API key logs an
`api_key_created` event)."""

import httpx


def test_activity_requires_auth(client: httpx.Client):
    assert client.get("/v1/account/activity").status_code in (401, 403)


def test_activity_starts_empty_and_is_a_list(auth_client: httpx.Client):
    resp = auth_client.get("/v1/account/activity")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    # A fresh account has done nothing consequential yet.
    assert body == []


def test_activity_records_api_key_creation(auth_client: httpx.Client):
    created = auth_client.post(
        "/v1/auth/keys",
        json={"name": "ci-activity-key", "scopes": ["system:read"]},
    )
    assert created.status_code in (200, 201), created.text

    events = auth_client.get("/v1/account/activity").json()
    key_events = [e for e in events if e["action"] == "api_key_created"]
    assert key_events, f"expected an api_key_created event, got {events}"
    e = key_events[0]
    assert e["actor_type"] == "user"
    # The key name is a fine label; never a secret.
    assert e["target_label"] == "ci-activity-key"
    # The endpoint is self-only by construction (WHERE user_id == self.id),
    # the same scoping as the admin-activity surface.
