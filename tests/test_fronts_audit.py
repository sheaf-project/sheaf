"""Tests for the front-entry audit log + extended PATCH /v1/fronts/{id}.

Covers the SP-parity edit surface (started_at, ended_at, member_ids,
custom_status, reopen via ended_at=null) and the append-only audit
log that captures who edited what and when.
"""

from __future__ import annotations

import httpx


def _create_member(client: httpx.Client, name: str) -> str:
    resp = client.post("/v1/members", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _open_front(client: httpx.Client, member_ids: list[str]) -> dict:
    resp = client.post("/v1/fronts", json={"member_ids": member_ids})
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- Extended PATCH surface ------------------------------------------------


def test_patch_can_set_started_at(auth_client: httpx.Client):
    m = _create_member(auth_client, "ShiftLeft")
    front = _open_front(auth_client, [m])
    new_started_at = "2026-01-01T10:00:00+00:00"
    resp = auth_client.patch(
        f"/v1/fronts/{front['id']}", json={"started_at": new_started_at}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["started_at"].startswith("2026-01-01T10:00")


def test_patch_rejects_null_started_at(auth_client: httpx.Client):
    m = _create_member(auth_client, "NullStart")
    front = _open_front(auth_client, [m])
    resp = auth_client.patch(
        f"/v1/fronts/{front['id']}", json={"started_at": None}
    )
    assert resp.status_code == 400


def test_patch_can_reopen_closed_front(auth_client: httpx.Client):
    """Sending `ended_at: null` explicitly reopens a closed front. The
    previous behaviour was to ignore null (only None vs missing was
    distinguishable in the body parser); now `model_fields_set` carries
    the difference."""
    from datetime import UTC, datetime, timedelta

    m = _create_member(auth_client, "Reopen")
    front = _open_front(auth_client, [m])
    # Close it (use a forward time so the validator doesn't trip).
    end_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    closed = auth_client.patch(
        f"/v1/fronts/{front['id']}",
        json={"ended_at": end_at},
    ).json()
    assert closed["ended_at"] is not None
    # Reopen it.
    reopened = auth_client.patch(
        f"/v1/fronts/{front['id']}", json={"ended_at": None}
    ).json()
    assert reopened["ended_at"] is None


def test_patch_rejects_ended_before_started(auth_client: httpx.Client):
    m = _create_member(auth_client, "BackwardsTime")
    front = _open_front(auth_client, [m])
    # Front started at "now"; set ended_at to before that.
    resp = auth_client.patch(
        f"/v1/fronts/{front['id']}",
        json={"ended_at": "2020-01-01T00:00:00+00:00"},
    )
    assert resp.status_code == 400
    assert "earlier" in resp.json()["detail"].lower()


def test_patch_allows_overlap_with_adjacent_entry(auth_client: httpx.Client):
    """SP parity: editing started_at / ended_at to overlap an adjacent
    front is allowed. Front history is a record of self-reported state,
    not a system-enforced timeline."""
    from datetime import UTC, datetime, timedelta

    m = _create_member(auth_client, "Overlapper")
    # Two fronts back-to-back. Close the first (forward in time), open
    # the second, then walk the second's started_at back into the first.
    first = _open_front(auth_client, [m])
    first_end = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    auth_client.patch(
        f"/v1/fronts/{first['id']}", json={"ended_at": first_end}
    )
    second = _open_front(auth_client, [m])
    overlap_start = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    resp = auth_client.patch(
        f"/v1/fronts/{second['id']}", json={"started_at": overlap_start}
    )
    assert resp.status_code == 200, resp.text


# --- Audit log -------------------------------------------------------------


def test_audit_empty_for_unedited_front(auth_client: httpx.Client):
    m = _create_member(auth_client, "Untouched")
    front = _open_front(auth_client, [m])
    resp = auth_client.get(f"/v1/fronts/{front['id']}/audit")
    assert resp.status_code == 200
    assert resp.json() == []


def test_audit_captures_member_set_change(auth_client: httpx.Client):
    m1 = _create_member(auth_client, "A")
    m2 = _create_member(auth_client, "B")
    front = _open_front(auth_client, [m1])
    auth_client.patch(
        f"/v1/fronts/{front['id']}", json={"member_ids": [m1, m2]}
    )

    audit = auth_client.get(f"/v1/fronts/{front['id']}/audit").json()
    assert len(audit) == 1
    row = audit[0]
    assert set(row["before"]["member_ids"]) == {m1}
    assert set(row["after"]["member_ids"]) == {m1, m2}
    assert row["actor_user_id"] is not None


def test_audit_captures_custom_status_change(auth_client: httpx.Client):
    m = _create_member(auth_client, "Statusee")
    front = _open_front(auth_client, [m])
    auth_client.patch(
        f"/v1/fronts/{front['id']}",
        json={"custom_status": "during a job interview"},
    )
    audit = auth_client.get(f"/v1/fronts/{front['id']}/audit").json()
    assert len(audit) == 1
    assert audit[0]["before"]["custom_status"] is None
    assert audit[0]["after"]["custom_status"] == "during a job interview"


def test_audit_no_row_for_noop_patch(auth_client: httpx.Client):
    """An empty PATCH body, or a PATCH where the resulting snapshot
    exactly equals the prior one, doesn't pollute the audit log."""
    m = _create_member(auth_client, "Noop")
    front = _open_front(auth_client, [m])
    # Empty body.
    auth_client.patch(f"/v1/fronts/{front['id']}", json={})
    # Same member set (no-op even though field is set).
    auth_client.patch(f"/v1/fronts/{front['id']}", json={"member_ids": [m]})
    audit = auth_client.get(f"/v1/fronts/{front['id']}/audit").json()
    assert audit == []


def test_audit_orders_newest_first(auth_client: httpx.Client):
    m1 = _create_member(auth_client, "Sequenced1")
    m2 = _create_member(auth_client, "Sequenced2")
    front = _open_front(auth_client, [m1])
    auth_client.patch(
        f"/v1/fronts/{front['id']}",
        json={"custom_status": "first edit"},
    )
    auth_client.patch(
        f"/v1/fronts/{front['id']}", json={"member_ids": [m1, m2]}
    )
    audit = auth_client.get(f"/v1/fronts/{front['id']}/audit").json()
    assert len(audit) == 2
    # Newest first: the member-set change is row 0, the status change row 1.
    assert set(audit[0]["after"]["member_ids"]) == {m1, m2}
    assert audit[1]["after"]["custom_status"] == "first edit"


def test_audit_captures_fronting_member_ids_at_edit_time(
    auth_client: httpx.Client,
):
    """fronting_member_ids snapshot: who was at front when the edit
    happened. Editing an old closed entry while Alice is currently
    fronting records [Alice], not the edited entry's members."""
    alice = _create_member(auth_client, "AliceFronting")
    bob = _create_member(auth_client, "BobOldEntry")

    # Old entry to be edited (closed).
    old_front = _open_front(auth_client, [bob])
    auth_client.patch(
        f"/v1/fronts/{old_front['id']}",
        json={"ended_at": "2026-04-01T00:00:00+00:00"},
    )

    # Alice is currently fronting.
    _open_front(auth_client, [alice])

    # Edit the old (Bob) entry; the audit row should record Alice's id.
    auth_client.patch(
        f"/v1/fronts/{old_front['id']}",
        json={"custom_status": "thinking back on this"},
    )
    audit = auth_client.get(f"/v1/fronts/{old_front['id']}/audit").json()
    assert len(audit) == 1
    assert audit[0]["fronting_member_ids"] == [alice]


def test_audit_cascades_on_front_delete(auth_client: httpx.Client):
    """Deleting the front entry deletes its audit history (ON DELETE
    CASCADE). The audit log is bound to the entry, not the system."""
    m = _create_member(auth_client, "DeleteMe")
    front = _open_front(auth_client, [m])
    auth_client.patch(
        f"/v1/fronts/{front['id']}",
        json={"custom_status": "doomed entry"},
    )
    # Confirm the audit row exists.
    pre = auth_client.get(f"/v1/fronts/{front['id']}/audit").json()
    assert len(pre) == 1

    # Delete the front; auth tier in test env is "none", so no confirm
    # body needed.
    resp = auth_client.delete(f"/v1/fronts/{front['id']}")
    assert resp.status_code in {200, 202, 204}

    # 404 on audit (front gone). Anything stored should be gone too.
    audit_after = auth_client.get(f"/v1/fronts/{front['id']}/audit")
    assert audit_after.status_code == 404


def test_audit_ownership_other_systems_get_404(auth_client: httpx.Client):
    """A front from another user's system must 404 on /audit, not leak
    history (and not 403, which would confirm existence)."""
    import os
    import uuid as _uuid

    # Create the front under auth_client's system.
    m = _create_member(auth_client, "Mine")
    front = _open_front(auth_client, [m])

    # Register a fresh user, hit /audit for the other system's front.
    email = f"audit-other-{_uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as other:
        reg = other.post(
            "/v1/auth/register",
            json={"email": email, "password": "testpassword123"},
        )
        assert reg.status_code == 201, reg.text
        other.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        resp = other.get(f"/v1/fronts/{front['id']}/audit")
    assert resp.status_code == 404
