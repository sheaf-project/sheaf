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
import pytest

from .conftest import BASE_URL
from .test_api_keys import _create_key, _key_client

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


def test_email_change_step_up_recorded(admin_client: httpx.Client):
    # The email-change step-up gate is one of the sensitive re-auth gates that
    # now record a security event: a failed re-auth (takeover-attempt signal)
    # and the successful change are both in the trail.
    email = f"sec-emailchange-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=BASE_URL) as c:
        reg = c.post(
            "/v1/auth/register", json={"email": email, "password": PASSWORD}
        )
        assert reg.status_code == 201, reg.text
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        uid = _find_user_id(admin_client, email)

        new_email = f"sec-emailnew-{uuid.uuid4().hex[:8]}@sheaf.dev"
        # Wrong current password -> rejected and recorded.
        bad = c.post(
            "/v1/auth/change-email",
            json={"new_email": new_email, "current_password": "wrong-password"},
        )
        assert bad.status_code == 401, bad.text
        # Correct password -> succeeds and is recorded.
        ok = c.post(
            "/v1/auth/change-email",
            json={"new_email": new_email, "current_password": PASSWORD},
        )
        assert ok.status_code == 200, ok.text

    events = _timeline(admin_client, uid)
    outcomes = [e["outcome"] for e in events if e["event_type"] == "email_change"]
    assert "password_incorrect" in outcomes
    assert "success" in outcomes


def test_api_key_export_recorded(admin_client: httpx.Client):
    """Exporting with an API key stays allowed - scripted backups are a
    sanctioned use case - but each one leaves a row, which is what makes a
    leaked key auditable after the fact. The session path is not recorded:
    the event exists to mark the programmatic reads."""
    email = f"sec-keyexport-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=BASE_URL) as c:
        reg = c.post(
            "/v1/auth/register", json={"email": email, "password": PASSWORD}
        )
        assert reg.status_code == 201, reg.text
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        key = _create_key(c, "export-bot", scopes=["export:read"])["key"]
        assert c.get("/v1/export").status_code == 200
        with _key_client(key) as kc:
            assert kc.get("/v1/export").status_code == 200

    uid = _find_user_id(admin_client, email)
    events = _timeline(admin_client, uid)
    exports = [e for e in events if e["event_type"] == "data_export"]
    assert len(exports) == 1, exports
    assert exports[0]["outcome"] == "api_key"
    assert exports[0]["ip"]


def test_refresh_reuse_kill_recorded(
    admin_client: httpx.Client, monkeypatch: pytest.MonkeyPatch
):
    """Reuse of a consumed refresh token outside the rotation grace window is
    read as probable token theft: the session is killed and the caller gets a
    generic 401. Nobody is told anything, so the security-event row is the
    only signal a responder has that it happened."""
    import asyncio

    import jwt

    from tests.test_shield_mode import (
        _patch_redis_url_for_host,
        _reset_redis_singleton,
    )

    email = f"sec-reuse-{uuid.uuid4().hex[:8]}@sheaf.dev"
    with httpx.Client(base_url=BASE_URL) as c:
        reg = c.post(
            "/v1/auth/register", json={"email": email, "password": PASSWORD}
        )
        assert reg.status_code == 201, reg.text
        refresh_jwt = reg.json()["refresh_token"]
        access = reg.json()["access_token"]
        jti = jwt.decode(refresh_jwt, options={"verify_signature": False})["jti"]

        # Burn the jti without ever caching a rotation, which is what a
        # genuinely stolen-and-replayed token looks like: the grace-window
        # poll finds nothing and the endpoint falls through to the kill.
        _patch_redis_url_for_host(monkeypatch)
        _reset_redis_singleton()
        from sheaf.auth.sessions import consume_refresh_jti

        assert asyncio.run(consume_refresh_jti(jti)) is not None

        resp = c.post("/v1/auth/refresh", json={"refresh_token": refresh_jwt})
        assert resp.status_code == 401, resp.text
        # The session really is gone: its access token no longer authenticates.
        me = c.get("/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert me.status_code == 401, me.text

    uid = _find_user_id(admin_client, email)
    events = _timeline(admin_client, uid)
    kills = [e for e in events if e["event_type"] == "refresh_reuse"]
    assert kills, events
    assert kills[0]["outcome"] == "session_killed"
    assert kills[0]["ip"]
    # No token material anywhere on the row.
    assert kills[0]["detail"] is None


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
