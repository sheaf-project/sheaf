"""End-to-end tests for the admin emergency-support endpoints.

Covers:
  - reset-safety clears all safety_applies_to_* + grace_period + delete_confirmation
  - reset-safety writes a USER_SAFETY_RESET audit row with reason + diff
  - reset-safety on a system already at default is a no-op (no diff in audit row)
  - bypass-pending finalises queued pending_actions immediately
  - bypass-pending on an empty queue is a no-op
  - bypass-pending writes per-action audit rows + a summary row
  - import-job summary listing browses without logging
  - import-job detail view requires a reason + writes IMPORT_LOG_VIEW
  - reason is required (empty / missing -> 422)
"""

from __future__ import annotations

import json
import uuid

import httpx


def _make_user(admin_client: httpx.Client) -> str:
    email = f"emergency-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = httpx.post(
        f"{admin_client.base_url}/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery"},
        timeout=10,
    )
    assert resp.status_code in (200, 201), resp.text
    users = admin_client.get("/v1/admin/users").json()
    match = next(u for u in users if u["email"] == email)
    return match["id"]


# ---------------------------------------------------------------------------
# Reset-safety
# ---------------------------------------------------------------------------

def test_reset_safety_requires_reason(admin_client: httpx.Client, auth_client: httpx.Client):
    me = auth_client.get("/v1/auth/me").json()
    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/reset-safety",
        json={"reason": ""},
    )
    assert resp.status_code == 422, resp.text


def test_reset_safety_no_op_when_already_default(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/reset-safety",
        json={"reason": "smoke test"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reset"] is True
    assert data["changed_fields"] == []

    # Audit row exists with the reason; diff fields are null because nothing moved.
    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={me['id']}&action=user_safety_reset"
    ).json()
    assert len(events) == 1
    assert events[0]["reason"] == "smoke test"
    assert events[0]["before_json"] is None
    assert events[0]["after_json"] is None


def test_reset_safety_clears_enabled_safeguards(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    # User turns on a few safeguards via the System Safety endpoint.
    patch_resp = auth_client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_members": True,
            "applies_to_journals": True,
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text

    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/reset-safety",
        json={"reason": "user accidentally locked self out"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reset"] is True
    assert "safety_grace_period_days" in data["changed_fields"]
    assert "safety_applies_to_members" in data["changed_fields"]

    # The system endpoint reflects the cleared state.
    safety = auth_client.get("/v1/system/safety").json()
    assert safety["settings"]["grace_period_days"] == 0
    assert safety["settings"]["applies_to_members"] is False
    assert safety["settings"]["applies_to_journals"] is False

    # Audit row has the diff.
    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={me['id']}&action=user_safety_reset"
    ).json()
    assert len(events) >= 1
    latest = events[0]
    assert latest["before_json"]["safety_grace_period_days"] == 7
    assert latest["after_json"]["safety_grace_period_days"] == 0


# ---------------------------------------------------------------------------
# Bypass-pending
# ---------------------------------------------------------------------------

def test_bypass_pending_empty_queue_is_noop(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/bypass-pending",
        json={"reason": "no-op check"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"finalized_count": 0, "by_type": {}}


def test_bypass_pending_drains_queued_actions(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    # Enable members safeguard + 7 day grace so a member delete queues
    # instead of finalising immediately.
    patch_resp = auth_client.patch(
        "/v1/system/safety",
        json={
            "grace_period_days": 7,
            "applies_to_members": True,
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    # Create + queue-delete a member.
    member = auth_client.post(
        "/v1/members", json={"name": "ToDrain"}
    ).json()
    del_resp = auth_client.delete(f"/v1/members/{member['id']}")
    assert del_resp.status_code == 202, del_resp.text

    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/bypass-pending",
        json={"reason": "support ticket #1"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["finalized_count"] == 1
    # The action_type label format may include the enum name; just
    # check that at least one type was finalised.
    assert sum(data["by_type"].values()) == 1

    # The member is actually gone now.
    members = auth_client.get("/v1/members").json()
    assert not any(m["id"] == member["id"] for m in members)

    # Per-action + summary audit rows landed.
    bypass_events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={me['id']}&action=user_pending_bypass"
    ).json()
    # 1 per-action row + 1 summary row = 2.
    assert len(bypass_events) >= 2
    # Summary row has after_json.finalized_count.
    summary = next(
        e for e in bypass_events if e["target_type"] == "user"
    )
    assert summary["after_json"]["finalized_count"] == 1


# ---------------------------------------------------------------------------
# Import-job log view
# ---------------------------------------------------------------------------

def test_list_user_import_jobs_does_not_log(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    # No import jobs needed for this test - just verify the browse
    # endpoint returns an array and doesn't write any audit rows.
    resp = admin_client.get(f"/v1/admin/users/{me['id']}/import-jobs")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    view_events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={me['id']}&action=import_log_view"
    ).json()
    assert view_events == []


def test_view_import_job_detail_requires_reason(admin_client: httpx.Client):
    # 404 below — irrelevant to the reason check, which fires first.
    fake = str(uuid.uuid4())
    resp = admin_client.post(
        f"/v1/admin/import-jobs/{fake}",
        json={"reason": ""},
    )
    assert resp.status_code == 422, resp.text


def test_view_import_job_detail_writes_audit_row(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    # Queue a real import job so we have something to look at.
    payload = json.dumps({
        "version": 2,
        "system": {"name": "Audit-View Target"},
        "members": [{"id": "m1", "name": "Alice"}],
        "fronts": [],
        "groups": [],
        "tags": [],
        "custom_fields": [],
    }).encode()
    job_resp = auth_client.post(
        "/v1/imports/file",
        files={"file": ("sheaf.json", payload, "application/json")},
        data={"source": "sheaf_file", "idempotency_key": str(uuid.uuid4())},
    )
    assert job_resp.status_code == 202, job_resp.text
    job_id = job_resp.json()["id"]

    # Admin views the detail with a reason.
    detail = admin_client.post(
        f"/v1/admin/import-jobs/{job_id}",
        json={"reason": "user reported import looked weird"},
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["id"] == job_id
    assert isinstance(body["events"], list)

    # The view itself created an audit row.
    view_events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={me['id']}&action=import_log_view"
    ).json()
    assert len(view_events) >= 1
    assert view_events[0]["reason"] == "user reported import looked weird"
    assert view_events[0]["target_type"] == "import_job"
    assert view_events[0]["target_id"] == job_id
