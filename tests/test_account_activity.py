"""Account activity log: the self-only GET /v1/account/activity surface
and a representative end-to-end record (creating an API key logs an
`api_key_created` event)."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from sheaf.api.v1.account import list_account_activity


def test_activity_requires_auth(client: httpx.Client):
    assert client.get("/v1/account/activity").status_code in (401, 403)


async def test_activity_refuses_api_key_auth():
    """A leaked API key of any scope must not read the account's security /
    2FA / session timeline. This account router sits outside scope-gating on
    the assumption each endpoint refuses keys inline, mirroring
    get_account_data. Driven directly (no stack) so the inline guard is
    covered without the full HTTP round-trip."""
    request = SimpleNamespace(state=SimpleNamespace(auth_method="api_key"))
    with pytest.raises(HTTPException) as exc:
        await list_account_activity(
            request=request,
            user=SimpleNamespace(id="unused"),
            db=None,
        )
    assert exc.value.status_code == 403
    assert "API key" in exc.value.detail


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


def test_activity_included_in_account_data_export(auth_client: httpx.Client):
    """The activity log is part of the Article 15 access data, so a DSAR is
    complete (it is also live on the account-activity endpoint)."""
    auth_client.post(
        "/v1/auth/keys",
        json={"name": "dsar-key", "scopes": ["system:read"]},
    )
    resp = auth_client.post(
        "/v1/account/data", json={"password": "testpassword123"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "activity_events" in data
    assert any(
        e["action"] == "api_key_created" and e["target_label"] == "dsar-key"
        for e in data["activity_events"]
    )
