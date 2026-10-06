"""Clearing a channel's quiet hours through PATCH.

Regression for a report that turning quiet hours off in the web app showed
"Channel updated" while the window stayed set. The client sends
`{"quiet_hours": null}`; the handler treated the null as "field not
provided" and left the column alone. `quiet_hours` is the one genuinely
nullable field on the channel update, so presence in the body has to be the
signal, not the value. The omitted case must keep working too.
"""

from __future__ import annotations

import httpx

from tests.test_notifications_aggregation import _create_channel, _create_token

QUIET = {"start": "22:00", "end": "07:00", "tz": "UTC"}


def _set_quiet_hours(client: httpx.Client, channel_id: str) -> None:
    resp = client.patch(f"/v1/channels/{channel_id}", json={"quiet_hours": QUIET})
    assert resp.status_code == 200, resp.text
    assert resp.json()["quiet_hours"] == QUIET


def test_explicit_null_clears_quiet_hours(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client, _create_token(auth_client))
    _set_quiet_hours(auth_client, channel_id)

    resp = auth_client.patch(
        f"/v1/channels/{channel_id}", json={"quiet_hours": None}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["quiet_hours"] is None

    # And it stuck, rather than only the response saying so.
    fetched = auth_client.get(f"/v1/channels/{channel_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["quiet_hours"] is None


def test_omitting_quiet_hours_leaves_them_alone(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client, _create_token(auth_client))
    _set_quiet_hours(auth_client, channel_id)

    resp = auth_client.patch(
        f"/v1/channels/{channel_id}", json={"debounce_seconds": 45}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["debounce_seconds"] == 45
    assert resp.json()["quiet_hours"] == QUIET


def test_quiet_hours_can_be_changed_and_then_cleared(auth_client: httpx.Client):
    channel_id = _create_channel(auth_client, _create_token(auth_client))
    _set_quiet_hours(auth_client, channel_id)

    changed = {"start": "23:30", "end": "06:00", "tz": "Europe/London"}
    resp = auth_client.patch(
        f"/v1/channels/{channel_id}", json={"quiet_hours": changed}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["quiet_hours"] == changed

    resp = auth_client.patch(
        f"/v1/channels/{channel_id}", json={"quiet_hours": None}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["quiet_hours"] is None


# --- the sibling: a watch token's label is the other nullable field patched
# the same way, and had the same silent no-op on null ------------------------------


def test_explicit_null_clears_a_watch_token_label(auth_client: httpx.Client):
    token_id = _create_token(auth_client)
    assert auth_client.get(f"/v1/watch-tokens/{token_id}").json()["label"] == "agg"

    resp = auth_client.patch(f"/v1/watch-tokens/{token_id}", json={"label": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["label"] is None
    assert auth_client.get(f"/v1/watch-tokens/{token_id}").json()["label"] is None

    # Omitting it leaves whatever is there alone.
    resp = auth_client.patch(f"/v1/watch-tokens/{token_id}", json={"label": "named"})
    assert resp.json()["label"] == "named"
    resp = auth_client.patch(f"/v1/watch-tokens/{token_id}", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["label"] == "named"
