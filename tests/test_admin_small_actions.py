"""End-to-end tests for the PR 3 admin small-actions batch.

Covers:
  - /admin/users/{id}/explain (dossier read; no audit row)
  - /admin/users/{id}/sessions (list; no audit row)
  - /admin/users/{id}/sessions/{sid}/terminate (revoke + log)
  - /admin/users/{id}/api-keys/rotate-all (revoke all + log)
  - /admin/approvals/bulk-approve (per-user audit + partial success)
  - /admin/users?signup_ip=... (exact-match filter)
"""

from __future__ import annotations

import uuid

import httpx


def _register(base_url: str, email_prefix: str) -> tuple[str, str]:
    email = f"{email_prefix}-{uuid.uuid4().hex[:8]}@sheaf.dev"
    password = "correct-horse-battery"
    resp = httpx.post(
        f"{base_url}/v1/auth/register",
        json={"email": email, "password": password},
        timeout=10,
    )
    assert resp.status_code in (200, 201), resp.text
    return email, password


def _find_user_id(admin_client: httpx.Client, email: str) -> str:
    users = admin_client.get("/v1/admin/users").json()
    match = next(u for u in users if u["email"] == email)
    return match["id"]


# ---------------------------------------------------------------------------
# Explain account
# ---------------------------------------------------------------------------

def test_explain_account_returns_dossier(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    resp = admin_client.get(f"/v1/admin/users/{me['id']}/explain")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["user_id"] == me["id"]
    assert "email" in data
    assert "active_session_count" in data
    assert "api_key_count" in data
    assert isinstance(data["recent_admin_audit"], list)


def test_explain_account_does_not_log(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    before = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={me['id']}"
    ).json()
    admin_client.get(f"/v1/admin/users/{me['id']}/explain")
    after = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={me['id']}"
    ).json()
    assert len(after) == len(before)


def test_explain_account_404_for_missing(admin_client: httpx.Client):
    resp = admin_client.get(f"/v1/admin/users/{uuid.uuid4()}/explain")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# List user sessions (admin view)
# ---------------------------------------------------------------------------

def test_list_user_sessions_returns_sessions(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    resp = admin_client.get(f"/v1/admin/users/{me['id']}/sessions")
    assert resp.status_code == 200, resp.text
    sessions = resp.json()
    # auth_client logged in to set up the fixture, so at least 1 session
    # exists.
    assert isinstance(sessions, list)
    assert len(sessions) >= 1
    assert all("id" in s for s in sessions)


# ---------------------------------------------------------------------------
# Terminate session
# ---------------------------------------------------------------------------

def test_terminate_session_revokes_and_logs(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    sessions = admin_client.get(
        f"/v1/admin/users/{me['id']}/sessions"
    ).json()
    assert sessions, "expected at least one session to terminate"
    sid = sessions[0]["id"]

    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/sessions/{sid}/terminate",
        json={"reason": "user requested logout"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"revoked": True}

    # Audit row landed.
    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={me['id']}&action=user_session_revoke"
    ).json()
    assert any(e["reason"] == "user requested logout" for e in events)


def test_terminate_session_requires_reason(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    sessions = admin_client.get(
        f"/v1/admin/users/{me['id']}/sessions"
    ).json()
    sid = sessions[0]["id"] if sessions else "deadbeef"
    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/sessions/{sid}/terminate",
        json={"reason": ""},
    )
    assert resp.status_code == 422


def test_terminate_unknown_session_404s(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/sessions/not-a-real-session/terminate",
        json={"reason": "smoke"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Force-rotate API keys
# ---------------------------------------------------------------------------

def test_rotate_api_keys_empty_is_zero(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    # First rotation drains any leftover keys; second is the assertion.
    admin_client.post(
        f"/v1/admin/users/{me['id']}/api-keys/rotate-all",
        json={"reason": "pre-clean"},
    )

    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/api-keys/rotate-all",
        json={"reason": "no-op verify"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"revoked_count": 0}

    # Even a no-op writes an audit row.
    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={me['id']}&action=user_api_keys_rotate_all"
    ).json()
    assert len(events) >= 1


def test_rotate_api_keys_revokes_all(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    # Mint two keys.
    for n in ("test-a", "test-b"):
        resp = auth_client.post(
            "/v1/auth/keys",
            json={"name": n, "scopes": ["members:read"]},
        )
        assert resp.status_code in (200, 201), resp.text

    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/api-keys/rotate-all",
        json={"reason": "user reported leak"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["revoked_count"] >= 2

    # User has no keys after the rotation.
    keys = auth_client.get("/v1/auth/keys").json()
    assert keys == []


# ---------------------------------------------------------------------------
# Bulk approve
# ---------------------------------------------------------------------------

def test_bulk_approve_partial_success(admin_client: httpx.Client):
    """Mix valid pending users with a stale id; the stale id is reported
    in `results` but doesn't 4xx the call."""
    # Register two users; they'll land in pending_approval if the test
    # config requires approval, or active otherwise. Either way, the
    # second case exercises the not_pending path so this is robust.
    base_url = str(admin_client.base_url)
    email_a, _ = _register(base_url, "bulk-a")
    email_b, _ = _register(base_url, "bulk-b")
    uid_a = _find_user_id(admin_client, email_a)
    uid_b = _find_user_id(admin_client, email_b)

    payload_ids = [uid_a, uid_b, str(uuid.uuid4())]
    resp = admin_client.post(
        "/v1/admin/approvals/bulk-approve",
        json={"user_ids": payload_ids},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["results"]) == 3

    # The fake UUID is always not_found.
    fake_row = next(
        r for r in data["results"] if r["user_id"] == payload_ids[2]
    )
    assert fake_row["approved"] is False
    assert fake_row["reason"] == "not_found"


def test_bulk_approve_requires_at_least_one_id(admin_client: httpx.Client):
    resp = admin_client.post(
        "/v1/admin/approvals/bulk-approve",
        json={"user_ids": []},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Signup IP filter on /admin/users
# ---------------------------------------------------------------------------

def test_admin_users_signup_ip_filter(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    full = admin_client.get("/v1/admin/users").json()
    me_row = next(u for u in full if u["id"] == me["id"])
    ip = me_row["signup_ip"]
    if not ip:
        # Test env may not have populated signup_ip; skip cleanly.
        return

    filtered = admin_client.get(
        f"/v1/admin/users?signup_ip={ip}"
    ).json()
    assert all(u["signup_ip"] == ip for u in filtered)
    assert any(u["id"] == me["id"] for u in filtered)


def test_admin_users_signup_ip_nonmatch_empty(
    admin_client: httpx.Client,
):
    resp = admin_client.get(
        "/v1/admin/users?signup_ip=10.255.255.250"
    )
    assert resp.status_code == 200
    assert resp.json() == []
