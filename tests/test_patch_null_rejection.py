"""Tests that PATCH endpoints reject explicit `null` on NOT-NULL columns.

The bug surfaced via PATCH /v1/systems/me sending `date_format: null`,
which the schema accepted (because `date_format: DateFormat | None = None`
is the standard "presence-in-body" pattern) and which then crashed at
the DB with a NotNullViolationError.

This file covers every Update schema that backs a NOT-NULL column,
asserting 422 (schema-layer rejection) instead of 500."""

from __future__ import annotations

import os
import uuid as _uuid

import httpx


def _system_id(client: httpx.Client) -> str:
    return client.get("/v1/systems/me").json()["id"]


def test_systems_patch_rejects_null_date_format(auth_client: httpx.Client):
    """The original bug from the test instance: omitting + sending null
    for a NOT-NULL column 500'd. Now 422'd at the schema layer."""
    resp = auth_client.patch(
        "/v1/systems/me",
        json={"name": "test", "date_format": None},
    )
    assert resp.status_code == 422


def test_systems_patch_rejects_null_replace_fronts_default(
    auth_client: httpx.Client,
):
    resp = auth_client.patch(
        "/v1/systems/me",
        json={"replace_fronts_default": None},
    )
    assert resp.status_code == 422


def test_systems_patch_rejects_null_privacy(auth_client: httpx.Client):
    resp = auth_client.patch("/v1/systems/me", json={"privacy": None})
    assert resp.status_code == 422


def test_systems_patch_allows_null_for_nullable_columns(auth_client: httpx.Client):
    """Sanity check: the nullable columns (description, tag, color,
    avatar_url) still accept null, since null = "clear" there."""
    resp = auth_client.patch(
        "/v1/systems/me",
        json={"description": None, "tag": None, "color": None},
    )
    assert resp.status_code == 200, resp.text


def test_member_patch_rejects_null_name(auth_client: httpx.Client):
    member = auth_client.post(
        "/v1/members", json={"name": "Original"}
    ).json()
    resp = auth_client.patch(
        f"/v1/members/{member['id']}", json={"name": None}
    )
    assert resp.status_code == 422


def test_member_patch_rejects_null_privacy(auth_client: httpx.Client):
    member = auth_client.post(
        "/v1/members", json={"name": "PrivacyTest"}
    ).json()
    resp = auth_client.patch(
        f"/v1/members/{member['id']}", json={"privacy": None}
    )
    assert resp.status_code == 422


def test_member_patch_rejects_null_is_custom_front(auth_client: httpx.Client):
    member = auth_client.post(
        "/v1/members", json={"name": "CFTest"}
    ).json()
    resp = auth_client.patch(
        f"/v1/members/{member['id']}", json={"is_custom_front": None}
    )
    assert resp.status_code == 422


def test_group_patch_rejects_null_name(auth_client: httpx.Client):
    group = auth_client.post("/v1/groups", json={"name": "Original"}).json()
    resp = auth_client.patch(
        f"/v1/groups/{group['id']}", json={"name": None}
    )
    assert resp.status_code == 422


def test_tag_patch_rejects_null_name(auth_client: httpx.Client):
    tag = auth_client.post("/v1/tags", json={"name": "Original"}).json()
    resp = auth_client.patch(f"/v1/tags/{tag['id']}", json={"name": None})
    assert resp.status_code == 422


def test_custom_field_patch_rejects_null_name(auth_client: httpx.Client):
    field = auth_client.post(
        "/v1/fields",
        json={"name": "f", "field_type": "text"},
    ).json()
    resp = auth_client.patch(
        f"/v1/fields/{field['id']}", json={"name": None}
    )
    assert resp.status_code == 422


def test_front_patch_rejects_null_member_ids(auth_client: httpx.Client):
    member = auth_client.post(
        "/v1/members", json={"name": "FrontTest"}
    ).json()
    front = auth_client.post(
        "/v1/fronts", json={"member_ids": [member["id"]]}
    ).json()
    resp = auth_client.patch(
        f"/v1/fronts/{front['id']}", json={"member_ids": None}
    )
    assert resp.status_code == 422


def test_system_safety_rejects_null_grace_period(auth_client: httpx.Client):
    resp = auth_client.patch(
        "/v1/system/safety", json={"grace_period_days": None}
    )
    assert resp.status_code == 422


def test_system_safety_rejects_null_applies_to_members(auth_client: httpx.Client):
    resp = auth_client.patch(
        "/v1/system/safety", json={"applies_to_members": None}
    )
    assert resp.status_code == 422


def test_reminder_patch_rejects_null_name(auth_client: httpx.Client):
    # Reminder creation needs a channel. Skip if no convenient setup.
    # Schema-level rejection happens before any DB lookup, so we can
    # PATCH a non-existent id and still get 422.
    resp = auth_client.patch(
        f"/v1/reminders/{_uuid.uuid4()}", json={"name": None}
    )
    assert resp.status_code == 422


def test_channel_patch_rejects_null_name(auth_client: httpx.Client):
    resp = auth_client.patch(
        f"/v1/channels/{_uuid.uuid4()}", json={"name": None}
    )
    assert resp.status_code == 422


def test_channel_patch_rejects_null_debounce_seconds(auth_client: httpx.Client):
    resp = auth_client.patch(
        f"/v1/channels/{_uuid.uuid4()}", json={"debounce_seconds": None}
    )
    assert resp.status_code == 422
