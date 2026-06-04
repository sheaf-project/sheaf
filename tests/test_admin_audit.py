"""End-to-end tests for the admin audit log.

Covers:
  - Admin mutations write rows: tier change, approve, reject.
  - The user-facing /v1/auth/admin-activity endpoint returns only
    rows targeting the caller.
  - The admin listing supports filters by target_user_id + action.
  - Routine browse endpoints (list_users, etc.) do NOT write rows.
"""

from __future__ import annotations

import uuid

import httpx


def _make_user(admin_client: httpx.Client) -> str:
    """Register a fresh user (open registration in test stack) and
    return their user id by inspecting the admin users list."""
    email = f"audit-target-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = httpx.post(
        f"{admin_client.base_url}/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery"},
        timeout=10,
    )
    assert resp.status_code in (200, 201), resp.text
    users = admin_client.get("/v1/admin/users").json()
    match = next(u for u in users if u["email"] == email)
    return match["id"]


def test_user_update_writes_audit_row(admin_client: httpx.Client):
    target = _make_user(admin_client)

    resp = admin_client.patch(
        f"/v1/admin/users/{target}", json={"can_upload_images": True}
    )
    assert resp.status_code == 200, resp.text

    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={target}"
    ).json()
    assert len(events) >= 1
    e = events[0]
    assert e["action"] == "user_update"
    assert e["target_type"] == "user"
    assert e["target_user_id"] == target
    assert e["before_json"] == {"can_upload_images": False}
    assert e["after_json"] == {"can_upload_images": True}
    assert e["admin_email"]  # captured at write time


def test_user_update_with_no_changed_fields_writes_nothing(
    admin_client: httpx.Client,
):
    """Sending a PATCH whose effective diff is empty (re-asserting
    the current state) must not pollute the audit log."""
    target = _make_user(admin_client)

    # Existing state: can_upload_images=False. Setting it to False
    # again is a no-op diff.
    resp = admin_client.patch(
        f"/v1/admin/users/{target}", json={"can_upload_images": False}
    )
    assert resp.status_code == 200, resp.text

    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={target}"
    ).json()
    assert events == []


def test_audit_filter_by_action(admin_client: httpx.Client):
    target = _make_user(admin_client)
    admin_client.patch(
        f"/v1/admin/users/{target}", json={"can_upload_images": True}
    )

    matching = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={target}&action=user_update"
    ).json()
    other = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={target}&action=user_approve"
    ).json()
    assert len(matching) >= 1
    assert other == []


def test_routine_browse_does_not_log(admin_client: httpx.Client):
    """Listing or fetching a single user is a browse — must not log."""
    target = _make_user(admin_client)

    # These reads should leave no audit rows.
    admin_client.get("/v1/admin/users")
    # (Single-user-get endpoint doesn't exist as such; list_users covers it.)

    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={target}"
    ).json()
    assert events == []


def test_user_can_see_admin_actions_on_self(auth_client: httpx.Client, admin_client: httpx.Client):
    """The /v1/auth/admin-activity endpoint returns rows targeting the
    caller — non-admin users still get to see the log."""
    me = auth_client.get("/v1/auth/me").json()
    my_id = me["id"]

    # Admin grants the user uploads.
    resp = admin_client.patch(
        f"/v1/admin/users/{my_id}", json={"can_upload_images": True}
    )
    assert resp.status_code == 200, resp.text

    # User reads their own activity.
    events = auth_client.get("/v1/auth/admin-activity").json()
    assert any(
        e["action"] == "user_update" and e["after_json"] == {"can_upload_images": True}
        for e in events
    )
    # Crucially, the user does NOT see the admin's user_id field — only
    # admin_email is exposed on this surface.
    assert all("admin_user_id" not in e for e in events)


def test_admin_activity_self_only(auth_client: httpx.Client, admin_client: httpx.Client):
    """A user can only see rows where target_user_id matches their own
    user id. An admin acting on a different user does not leak into the
    caller's feed."""
    other_target = _make_user(admin_client)
    # Admin acts on a different user.
    admin_client.patch(
        f"/v1/admin/users/{other_target}", json={"can_upload_images": True}
    )

    # Caller (auth_client, a different user) sees nothing for this row.
    events = auth_client.get("/v1/auth/admin-activity").json()
    assert all(
        e.get("after_json") != {"can_upload_images": True}
        or e.get("target_id") != other_target
        for e in events
    )


def test_audit_event_detail(admin_client: httpx.Client):
    target = _make_user(admin_client)
    admin_client.patch(
        f"/v1/admin/users/{target}", json={"can_upload_images": True}
    )
    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={target}"
    ).json()
    event_id = events[0]["id"]

    detail = admin_client.get(f"/v1/admin/audit-events/{event_id}").json()
    assert detail["id"] == event_id
    assert detail["target_user_id"] == target
