"""End-to-end tests for the security-event log and admin search tools.

Covers:
  - the auth funnel writing events (login success/failure, register,
    password change) with the originating IP
  - per-account timeline (isolated by user_id, so robust against the
    shared test table)
  - IP / subnet lookup + input validation + admin gate
  - the credential-stuffing view
  - admin audit rows capturing the acting admin's IP
  - the privacy guard: unknown-user attempts store no account, no email

These hit a running server (see conftest); the security-event row is
committed inline before each auth response returns, so it is queryable
immediately after.
"""

from __future__ import annotations

import uuid

import httpx

from .conftest import BASE_URL

PASSWORD = "correct-horse-battery"


def _register(email_prefix: str) -> str:
    email = f"{email_prefix}-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = httpx.post(
        f"{BASE_URL}/v1/auth/register",
        json={"email": email, "password": PASSWORD},
        timeout=10,
    )
    assert resp.status_code in (200, 201), resp.text
    return email


def _find_user_id(admin_client: httpx.Client, email: str) -> str:
    users = admin_client.get("/v1/admin/users").json()
    match = next(u for u in users if u["email"] == email)
    return match["id"]


def _login(email: str, password: str) -> httpx.Response:
    return httpx.post(
        f"{BASE_URL}/v1/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )


def _timeline(admin_client: httpx.Client, user_id: str) -> list[dict]:
    resp = admin_client.post(
        f"/v1/admin/users/{user_id}/security-events",
        json={"reason": "test"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["events"]


# ---------------------------------------------------------------------------
# Auth funnel -> events (isolated by user_id)
# ---------------------------------------------------------------------------

def test_failed_then_success_login_recorded(admin_client: httpx.Client):
    email = _register("sec-login")
    assert _login(email, "wrong-password").status_code == 401
    assert _login(email, PASSWORD).status_code == 200

    uid = _find_user_id(admin_client, email)
    events = _timeline(admin_client, uid)

    outcomes = [e["outcome"] for e in events if e["event_type"] == "login"]
    assert "password_incorrect" in outcomes
    assert "success" in outcomes
    # Every recorded event carries the originating IP and is attributed
    # to this account.
    for e in events:
        assert e["user_id"] == uid
        assert e["ip"]


def test_register_recorded(admin_client: httpx.Client):
    email = _register("sec-register")
    uid = _find_user_id(admin_client, email)
    events = _timeline(admin_client, uid)
    assert any(
        e["event_type"] == "register" and e["outcome"] == "success"
        for e in events
    )


def test_password_change_recorded(admin_client: httpx.Client):
    # Register through a dedicated client so we hold a live session.
    email = f"sec-pwchange-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=BASE_URL) as c:
        reg = c.post(
            "/v1/auth/register", json={"email": email, "password": PASSWORD}
        )
        assert reg.status_code == 201, reg.text
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        changed = c.post(
            "/v1/auth/change-password",
            json={
                "current_password": PASSWORD,
                "new_password": "a-brand-new-password-9",
            },
        )
        assert changed.status_code == 200, changed.text

    uid = _find_user_id(admin_client, email)
    events = _timeline(admin_client, uid)
    assert any(
        e["event_type"] == "password_change" and e["outcome"] == "success"
        for e in events
    )


# ---------------------------------------------------------------------------
# Privacy guard: unknown-user attempts
# ---------------------------------------------------------------------------

def test_unknown_user_login_stores_no_account_or_email(
    admin_client: httpx.Client,
):
    # Learn this client's IP from a known account first.
    seed_email = _register("sec-seed")
    assert _login(seed_email, PASSWORD).status_code == 200
    uid = _find_user_id(admin_client, seed_email)
    ip = _timeline(admin_client, uid)[0]["ip"]

    # Attempt against an address that was never registered.
    ghost = f"ghost-{uuid.uuid4().hex}@sheaf.dev"
    assert _login(ghost, "whatever").status_code == 401

    resp = admin_client.post(
        "/v1/admin/security/ip-lookup",
        json={"target": ip, "reason": "test"},
    )
    assert resp.status_code == 200, resp.text
    not_found = [
        e
        for e in resp.json()["events"]
        if e["event_type"] == "login" and e["outcome"] == "user_not_found"
    ]
    assert not_found, "expected a recorded user_not_found attempt"
    for e in not_found:
        assert e["user_id"] is None
        # No attempted address is retained anywhere on the row.
        assert e["detail"] is None
        assert ghost not in (str(e.get("detail")) or "")


# ---------------------------------------------------------------------------
# IP / subnet lookup
# ---------------------------------------------------------------------------

def test_ip_lookup_subnet_and_exact(admin_client: httpx.Client):
    for target in ("10.1.2.3", "10.1.2.0/24"):
        resp = admin_client.post(
            "/v1/admin/security/ip-lookup",
            json={"target": target, "reason": "test"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["query"] == target
        assert body["is_subnet"] is ("/" in target)


def test_ip_lookup_rejects_garbage(admin_client: httpx.Client):
    for bad in ("not-an-ip", "999.999.999.999", "10.0.0.0/99"):
        resp = admin_client.post(
            "/v1/admin/security/ip-lookup",
            json={"target": bad, "reason": "test"},
        )
        assert resp.status_code == 400, f"{bad}: {resp.text}"


def test_security_endpoints_require_admin(auth_client: httpx.Client):
    # auth_client is a normal authenticated (non-admin) user.
    assert (
        auth_client.post(
            "/v1/admin/security/ip-lookup",
            json={"target": "10.0.0.1", "reason": "x"},
        ).status_code
        == 403
    )
    assert auth_client.get("/v1/admin/security/stuffing").status_code == 403
    assert (
        auth_client.post(
            f"/v1/admin/users/{uuid.uuid4()}/security-events",
            json={"reason": "x"},
        ).status_code
        == 403
    )


# ---------------------------------------------------------------------------
# Stuffing view
# ---------------------------------------------------------------------------

def test_stuffing_view_surfaces_failing_ip(admin_client: httpx.Client):
    # Generate failures against several distinct accounts from this IP.
    for _ in range(3):
        email = _register("sec-stuff")
        _login(email, "wrong-password")

    resp = admin_client.get(
        "/v1/admin/security/stuffing?hours=1&min_failures=1"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window_hours"] == 1
    assert body["offenders"], "expected at least one failing IP"
    top = body["offenders"][0]
    assert top["ip"]
    assert top["failures"] >= 1
    assert top["distinct_accounts"] >= 0


# ---------------------------------------------------------------------------
# Admin audit captures origin
# ---------------------------------------------------------------------------

def test_ip_lookup_is_audited_with_ip(admin_client: httpx.Client):
    admin_client.post(
        "/v1/admin/security/ip-lookup",
        json={"target": "192.0.2.1", "reason": "audit-ip-check"},
    )
    rows = admin_client.get(
        "/v1/admin/audit-events?action=security_ip_lookup"
    ).json()
    assert rows, "expected an audit row for the lookup"
    latest = rows[0]
    assert latest["action"] == "security_ip_lookup"
    assert latest["reason"] == "audit-ip-check"
    # The acting admin's origin is captured on the audit row.
    assert latest["ip"]


def test_user_security_events_is_audited(admin_client: httpx.Client):
    email = _register("sec-audit")
    uid = _find_user_id(admin_client, email)
    _timeline(admin_client, uid)

    rows = admin_client.get(
        f"/v1/admin/audit-events?action=security_history_view&target_user_id={uid}"
    ).json()
    assert any(r["target_user_id"] == uid and r["ip"] for r in rows)


# ---------------------------------------------------------------------------
# Access-request inclusion (Article 15 self-service + admin dossier)
# ---------------------------------------------------------------------------

def test_account_data_includes_security_events():
    # The Article 15 endpoint, not the portable Article 20 export, is where
    # the IP-bearing security log belongs.
    email = f"sec-a15-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=BASE_URL) as c:
        reg = c.post(
            "/v1/auth/register", json={"email": email, "password": PASSWORD}
        )
        assert reg.status_code == 201, reg.text
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        resp = c.post("/v1/account/data", json={"password": PASSWORD})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "security_events" in body
        assert "security_events_truncated" in body
        # Registration alone produces at least the register event.
        assert any(
            e["event_type"] == "register" and e["ip"]
            for e in body["security_events"]
        )


def test_dossier_includes_security_events(admin_client: httpx.Client):
    email = _register("sec-dossier")
    uid = _find_user_id(admin_client, email)
    resp = admin_client.post(
        f"/v1/admin/users/{uid}/dossier", json={"reason": "test"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "security_events" in body
    assert any(e["event_type"] == "register" for e in body["security_events"])
