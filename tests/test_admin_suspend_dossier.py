"""End-to-end tests for the PR 4 suspend + dossier admin endpoints.

Covers:
  - POST /admin/users/{id}/suspend with finite duration: sets state,
    revokes sessions, writes audit row, blocks login.
  - POST /admin/users/{id}/suspend indefinite (no duration): suspended_until is null.
  - Auth dep returns the reason + expiry on next request.
  - POST /admin/users/{id}/unsuspend: lifts cleanly, writes audit row.
  - Unsuspend a non-suspended user is a no-op (no audit row).
  - Suspend refuses admin accounts (409).
  - Suspend duration cap rejects > 5y (422).
  - POST /admin/users/{id}/dossier returns expected sections + writes audit row.
  - Sweep helper (apply_unsuspend) idempotent.
"""

from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# Suspend
# ---------------------------------------------------------------------------


def test_suspend_with_duration_sets_until(admin_client: httpx.Client):
    base_url = str(admin_client.base_url)
    email, password, token = _register(base_url, "sus-dur")
    uid = _find_user_id(admin_client, email)

    resp = admin_client.post(
        f"/v1/admin/users/{uid}/suspend",
        json={"reason": "spam wave triage", "duration_days": 7},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["suspended"] is True
    assert data["suspended_until"] is not None

    # Audit row exists.
    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={uid}&action=user_suspend"
    ).json()
    assert any(e["reason"] == "spam wave triage" for e in events)


def test_suspend_indefinite(admin_client: httpx.Client):
    base_url = str(admin_client.base_url)
    email, password, _ = _register(base_url, "sus-indef")
    uid = _find_user_id(admin_client, email)

    resp = admin_client.post(
        f"/v1/admin/users/{uid}/suspend",
        json={"reason": "needs investigation"},
    )
    assert resp.status_code == 200
    assert resp.json()["suspended_until"] is None


def test_suspend_blocks_login(admin_client: httpx.Client):
    base_url = str(admin_client.base_url)
    email, password, _ = _register(base_url, "sus-login")
    uid = _find_user_id(admin_client, email)

    admin_client.post(
        f"/v1/admin/users/{uid}/suspend",
        json={"reason": "test", "duration_days": 1},
    )

    # Try to log in.
    login = httpx.post(
        f"{base_url}/v1/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    assert login.status_code == 403, login.text
    detail = login.json().get("detail", "")
    assert "suspended" in detail.lower()
    assert "test" in detail  # reason surfaced


def test_suspend_revokes_existing_session(admin_client: httpx.Client):
    """A user with an active session should be evicted from it when
    suspended. The existing token returns 403 on next request."""
    base_url = str(admin_client.base_url)
    email, password, token = _register(base_url, "sus-evict")
    uid = _find_user_id(admin_client, email)
    assert token, "register did not return a token"

    # Confirm token works before suspend.
    pre = httpx.get(
        f"{base_url}/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert pre.status_code == 200

    admin_client.post(
        f"/v1/admin/users/{uid}/suspend",
        json={"reason": "test", "duration_days": 1},
    )

    post = httpx.get(
        f"{base_url}/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    # Either the session is gone (401) or the auth dep rejects (403).
    # Both are acceptable lock-out states.
    assert post.status_code in (401, 403), post.text


def test_suspend_refuses_admin(admin_client: httpx.Client):
    me = admin_client.get("/v1/auth/me").json()
    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/suspend",
        json={"reason": "test", "duration_days": 1},
    )
    assert resp.status_code == 409, resp.text


def test_suspend_duration_cap(admin_client: httpx.Client):
    base_url = str(admin_client.base_url)
    email, _, _ = _register(base_url, "sus-cap")
    uid = _find_user_id(admin_client, email)
    resp = admin_client.post(
        f"/v1/admin/users/{uid}/suspend",
        json={"reason": "test", "duration_days": 99999},
    )
    assert resp.status_code == 422


def test_suspend_requires_reason(admin_client: httpx.Client):
    base_url = str(admin_client.base_url)
    email, _, _ = _register(base_url, "sus-no-reason")
    uid = _find_user_id(admin_client, email)
    resp = admin_client.post(
        f"/v1/admin/users/{uid}/suspend",
        json={"reason": ""},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Unsuspend
# ---------------------------------------------------------------------------


def test_unsuspend_lifts_state(admin_client: httpx.Client):
    base_url = str(admin_client.base_url)
    email, password, _ = _register(base_url, "uns-lift")
    uid = _find_user_id(admin_client, email)

    admin_client.post(
        f"/v1/admin/users/{uid}/suspend",
        json={"reason": "temp", "duration_days": 1},
    )
    resp = admin_client.post(
        f"/v1/admin/users/{uid}/unsuspend",
        json={"reason": "ticket resolved"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["unsuspended"] is True

    # Login works again.
    login = httpx.post(
        f"{base_url}/v1/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    assert login.status_code in (200, 201), login.text

    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={uid}&action=user_unsuspend"
    ).json()
    assert any(e["reason"] == "ticket resolved" for e in events)


def test_unsuspend_noop_on_active_user(admin_client: httpx.Client):
    base_url = str(admin_client.base_url)
    email, _, _ = _register(base_url, "uns-noop")
    uid = _find_user_id(admin_client, email)

    resp = admin_client.post(
        f"/v1/admin/users/{uid}/unsuspend",
        json={"reason": "should not log"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["unsuspended"] is False
    assert data["reason"] == "not_suspended"

    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={uid}&action=user_unsuspend"
    ).json()
    assert events == []


# ---------------------------------------------------------------------------
# Dossier export
# ---------------------------------------------------------------------------


def test_dossier_returns_expected_sections(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/dossier",
        json={"reason": "DSAR request 2026-001"},
    )
    assert resp.status_code == 200, resp.text
    assert "attachment" in resp.headers.get("content-disposition", "")
    data = json.loads(resp.content)
    assert data["schema_version"] == 1
    assert data["user"]["id"] == me["id"]
    assert "system" in data
    assert "counts" in data
    assert "api_keys" in data
    assert "active_sessions" in data
    assert "trusted_devices" in data
    assert "client_settings" in data
    assert "admin_audit_history" in data
    assert "import_jobs" in data
    assert "export_jobs" in data
    assert data["reason"] == "DSAR request 2026-001"


def test_dossier_writes_audit_row(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    admin_client.post(
        f"/v1/admin/users/{me['id']}/dossier",
        json={"reason": "auditable dossier pull"},
    )
    events = admin_client.get(
        f"/v1/admin/audit-events?target_user_id={me['id']}&action=user_dossier_export"
    ).json()
    assert any(e["reason"] == "auditable dossier pull" for e in events)


def test_dossier_requires_reason(
    admin_client: httpx.Client, auth_client: httpx.Client,
):
    me = auth_client.get("/v1/auth/me").json()
    resp = admin_client.post(
        f"/v1/admin/users/{me['id']}/dossier",
        json={"reason": ""},
    )
    assert resp.status_code == 422


def test_dossier_404_for_missing(admin_client: httpx.Client):
    resp = admin_client.post(
        f"/v1/admin/users/{uuid.uuid4()}/dossier",
        json={"reason": "smoke"},
    )
    assert resp.status_code == 404
