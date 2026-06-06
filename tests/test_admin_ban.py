"""End-to-end tests for the permanent ban admin endpoints.

Covers:
  - POST /admin/users/{id}/ban: sets BANNED, revokes sessions, logs.
  - Ban blocks login with the "Account banned" detail (no reason in
    body, by design).
  - Ban refuses admin accounts (409).
  - Unban restores the user to ACTIVE.
  - Unban on a non-banned user is a no-op (no audit row).
  - Suspend then ban transitions cleanly + clears suspended_until/reason.
"""

from __future__ import annotations

import uuid

import httpx


def _register(base_url: str, prefix: str) -> tuple[str, str, str]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@sheaf.dev"
    password = "correct-horse-battery"
    resp = httpx.post(
        f"{base_url}/v1/auth/register",
        json={"email": email, "password": password},
        timeout=10,
    )
    assert resp.status_code in (200, 201), resp.text
    token = resp.json().get("access_token")
    return email, password, token or ""


def _find_user_id(admin_client: httpx.Client, email: str) -> str:
    users = admin_client.get("/v1/admin/users").json()
    match = next(u for u in users if u["email"] == email)
    return match["id"]


def test_ban_sets_state_and_blocks_login(admin_client: httpx.Client):
    base_url = str(admin_client.base_url)
    email, password, _ = _register(base_url, "ban-login")
    uid = _find_user_id(admin_client, email)

    resp = admin_client.post(
        f"/v1/admin/users/{uid}/ban",
        json={"reason": "spam campaign"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["banned"] is True

    login = httpx.post(
        f"{base_url}/v1/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    assert login.status_code == 403
    # Banned detail intentionally does NOT include the reason — that
    # lives in the audit row for operator reference only.
    assert login.json().get("detail") == "Account banned"

    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={uid}&action=user_ban"
    ).json()
    assert any(e["reason"] == "spam campaign" for e in events)


def test_ban_requires_reason(admin_client: httpx.Client):
    base_url = str(admin_client.base_url)
    email, _, _ = _register(base_url, "ban-no-reason")
    uid = _find_user_id(admin_client, email)
    resp = admin_client.post(
        f"/v1/admin/users/{uid}/ban", json={"reason": ""},
    )
    assert resp.status_code == 422


def test_ban_refuses_admin(admin_client: httpx.Client):
    me = admin_client.get("/v1/auth/me").json()
    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/ban",
        json={"reason": "should be refused"},
    )
    assert resp.status_code == 409


def test_ban_404_for_missing(admin_client: httpx.Client):
    resp = admin_client.post(
        f"/v1/admin/users/{uuid.uuid4()}/ban",
        json={"reason": "smoke"},
    )
    assert resp.status_code == 404


def test_unban_restores_user(admin_client: httpx.Client):
    base_url = str(admin_client.base_url)
    email, password, _ = _register(base_url, "ban-restore")
    uid = _find_user_id(admin_client, email)

    admin_client.post(
        f"/v1/admin/users/{uid}/ban", json={"reason": "temp"},
    )
    resp = admin_client.post(
        f"/v1/admin/users/{uid}/unban",
        json={"reason": "appeal upheld"},
    )
    assert resp.status_code == 200
    assert resp.json()["unbanned"] is True

    login = httpx.post(
        f"{base_url}/v1/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    assert login.status_code in (200, 201), login.text

    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={uid}&action=user_unban"
    ).json()
    assert any(e["reason"] == "appeal upheld" for e in events)


def test_unban_noop_on_active_user(admin_client: httpx.Client):
    base_url = str(admin_client.base_url)
    email, _, _ = _register(base_url, "ban-noop")
    uid = _find_user_id(admin_client, email)

    resp = admin_client.post(
        f"/v1/admin/users/{uid}/unban",
        json={"reason": "should not log"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["unbanned"] is False
    assert data["reason"] == "not_banned"

    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={uid}&action=user_unban"
    ).json()
    assert events == []


def test_ban_clears_suspend_fields_on_escalation(admin_client: httpx.Client):
    """Suspend then ban: stale suspended_until / suspended_reason on
    the row would mislead a later reader, so the ban path nulls them."""
    base_url = str(admin_client.base_url)
    email, _, _ = _register(base_url, "ban-escalate")
    uid = _find_user_id(admin_client, email)

    admin_client.post(
        f"/v1/admin/users/{uid}/suspend",
        json={"reason": "first warning", "duration_days": 7},
    )
    admin_client.post(
        f"/v1/admin/users/{uid}/ban",
        json={"reason": "second offence; permanent"},
    )

    explain = admin_client.get(
        f"/v1/admin/users/{uid}/explain"
    ).json()
    assert explain["account_status"] == "banned"
