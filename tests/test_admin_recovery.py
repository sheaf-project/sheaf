"""Tests for admin account recovery tools."""

import os
import uuid

import httpx

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")


def _key_client(plaintext: str) -> httpx.Client:
    """Bare client authenticated only with the given API key."""
    return httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {plaintext}"},
    )


def _create_target_user(client: httpx.Client) -> tuple[str, str, str]:
    """Register a fresh user, return (user_id, email, password)."""
    email = f"target-{uuid.uuid4().hex[:8]}@sheaf.dev"
    password = "testpassword123"
    resp = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    # Get user ID via /me
    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    user_id = me.json()["id"]
    return user_id, email, password


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


def test_reset_password_requires_admin(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/admin/users/00000000-0000-0000-0000-000000000000/reset-password",
        json={},
    )
    assert resp.status_code == 403


def test_change_email_requires_admin(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/admin/users/00000000-0000-0000-0000-000000000000/change-email",
        json={"new_email": "x@sheaf.dev"},
    )
    assert resp.status_code == 403


def test_disable_totp_requires_admin(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/admin/users/00000000-0000-0000-0000-000000000000/disable-totp",
    )
    assert resp.status_code == 403


def test_verify_email_requires_admin(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/admin/users/00000000-0000-0000-0000-000000000000/verify-email",
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Reset password
# ---------------------------------------------------------------------------


def test_reset_password_generates_random(admin_client: httpx.Client, client: httpx.Client):
    user_id, email, _old_pw = _create_target_user(client)

    resp = admin_client.post(f"/v1/admin/users/{user_id}/reset-password", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "password" in data
    assert len(data["password"]) > 0
    assert "sessions_revoked" in data

    # Can log in with the new password
    login = client.post("/v1/auth/login", json={"email": email, "password": data["password"]})
    assert login.status_code == 200


def test_reset_password_custom(admin_client: httpx.Client, client: httpx.Client):
    user_id, email, _old_pw = _create_target_user(client)
    new_pw = "my-custom-password-42"

    resp = admin_client.post(
        f"/v1/admin/users/{user_id}/reset-password",
        json={"new_password": new_pw},
    )
    assert resp.status_code == 200
    assert resp.json()["password"] == new_pw

    # Can log in with custom password
    login = client.post("/v1/auth/login", json={"email": email, "password": new_pw})
    assert login.status_code == 200


def test_reset_password_old_password_invalid(admin_client: httpx.Client, client: httpx.Client):
    user_id, email, old_pw = _create_target_user(client)

    admin_client.post(f"/v1/admin/users/{user_id}/reset-password", json={})

    # Old password no longer works
    login = client.post("/v1/auth/login", json={"email": email, "password": old_pw})
    assert login.status_code == 401


def test_reset_password_nonexistent_user(admin_client: httpx.Client):
    resp = admin_client.post(
        "/v1/admin/users/00000000-0000-0000-0000-000000000000/reset-password",
        json={},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Change email
# ---------------------------------------------------------------------------


def test_change_email(admin_client: httpx.Client, client: httpx.Client):
    user_id, _old_email, password = _create_target_user(client)
    new_email = f"changed-{uuid.uuid4().hex[:8]}@sheaf.dev"

    resp = admin_client.post(
        f"/v1/admin/users/{user_id}/change-email",
        json={"new_email": new_email},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == new_email

    # Can log in with new email
    login = client.post("/v1/auth/login", json={"email": new_email, "password": password})
    assert login.status_code == 200


def test_change_email_old_email_invalid(admin_client: httpx.Client, client: httpx.Client):
    user_id, old_email, password = _create_target_user(client)
    new_email = f"changed-{uuid.uuid4().hex[:8]}@sheaf.dev"

    admin_client.post(
        f"/v1/admin/users/{user_id}/change-email",
        json={"new_email": new_email},
    )

    # Old email no longer works
    login = client.post("/v1/auth/login", json={"email": old_email, "password": password})
    assert login.status_code == 401


def test_change_email_conflict(admin_client: httpx.Client, client: httpx.Client):
    """Changing to an email already in use by another account returns 409."""
    user_id_a, _email_a, _pw_a = _create_target_user(client)
    _user_id_b, email_b, _pw_b = _create_target_user(client)

    resp = admin_client.post(
        f"/v1/admin/users/{user_id_a}/change-email",
        json={"new_email": email_b},
    )
    assert resp.status_code == 409


def test_change_email_marks_verified(admin_client: httpx.Client, client: httpx.Client):
    """Admin email change sets email_verified=True."""
    user_id, _email, _pw = _create_target_user(client)
    new_email = f"verified-{uuid.uuid4().hex[:8]}@sheaf.dev"

    admin_client.post(
        f"/v1/admin/users/{user_id}/change-email",
        json={"new_email": new_email},
    )

    # Check via admin user list that user shows as not needing verification
    users = admin_client.get("/v1/admin/users").json()
    target = next(u for u in users if u["id"] == user_id)
    assert target["email_verified"] is True


def test_change_email_nonexistent_user(admin_client: httpx.Client):
    resp = admin_client.post(
        "/v1/admin/users/00000000-0000-0000-0000-000000000000/change-email",
        json={"new_email": "nope@sheaf.dev"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Disable TOTP
# ---------------------------------------------------------------------------


def test_disable_totp(admin_client: httpx.Client, client: httpx.Client):
    """Admin can disable TOTP on a user who has it enabled."""
    user_id, email, password = _create_target_user(client)

    # Log in as the target user to set up TOTP
    login = client.post("/v1/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]
    target_client = httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {token}"},
    )

    # Set up TOTP
    setup_resp = target_client.post("/v1/auth/totp/setup")
    assert setup_resp.status_code == 200
    totp_secret = setup_resp.json()["secret"]

    # Verify TOTP to enable it
    import pyotp

    totp = pyotp.TOTP(totp_secret)
    verify_resp = target_client.post("/v1/auth/totp/verify", json={"code": totp.now()})
    assert verify_resp.status_code in (200, 204)
    target_client.close()

    # Admin disables TOTP
    resp = admin_client.post(f"/v1/admin/users/{user_id}/disable-totp")
    assert resp.status_code == 200
    assert resp.json()["disabled"] is True

    # User can now log in without TOTP
    login2 = client.post("/v1/auth/login", json={"email": email, "password": password})
    assert login2.status_code == 200
    assert "access_token" in login2.json()


def test_disable_totp_not_enabled(admin_client: httpx.Client, client: httpx.Client):
    """Disabling TOTP on a user without it returns 400."""
    user_id, _email, _pw = _create_target_user(client)

    resp = admin_client.post(f"/v1/admin/users/{user_id}/disable-totp")
    assert resp.status_code == 400


def test_disable_totp_nonexistent_user(admin_client: httpx.Client):
    resp = admin_client.post(
        "/v1/admin/users/00000000-0000-0000-0000-000000000000/disable-totp",
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Verify email
# ---------------------------------------------------------------------------


def test_verify_email(admin_client: httpx.Client, client: httpx.Client):
    """Admin can force-verify an unverified user's email."""
    user_id, _email, _pw = _create_target_user(client)

    # Check current state — depends on server email_verification config
    users = admin_client.get("/v1/admin/users").json()
    target = next(u for u in users if u["id"] == user_id)

    if target["email_verified"]:
        # If already verified (e.g. email_verification=none), we can't test this
        # endpoint meaningfully — it would return 400
        return

    resp = admin_client.post(f"/v1/admin/users/{user_id}/verify-email")
    assert resp.status_code == 200
    assert resp.json()["verified"] is True

    # Confirm via admin user list
    users = admin_client.get("/v1/admin/users").json()
    target = next(u for u in users if u["id"] == user_id)
    assert target["email_verified"] is True


def test_verify_email_already_verified(admin_client: httpx.Client, client: httpx.Client):
    """Verifying an already-verified email returns 400."""
    user_id, _email, _pw = _create_target_user(client)
    new_email = f"preverified-{uuid.uuid4().hex[:8]}@sheaf.dev"

    # Use change-email to ensure verified=True
    admin_client.post(
        f"/v1/admin/users/{user_id}/change-email",
        json={"new_email": new_email},
    )

    resp = admin_client.post(f"/v1/admin/users/{user_id}/verify-email")
    assert resp.status_code == 400


def test_verify_email_nonexistent_user(admin_client: httpx.Client):
    resp = admin_client.post(
        "/v1/admin/users/00000000-0000-0000-0000-000000000000/verify-email",
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# API key scope enforcement
# ---------------------------------------------------------------------------


def test_recovery_endpoints_require_admin_write_scope(
    admin_client: httpx.Client, client: httpx.Client,
):
    """admin:read scope cannot use recovery endpoints (they require admin:write)."""
    user_id, _email, _pw = _create_target_user(client)

    key = admin_client.post(
        "/v1/auth/keys",
        json={"name": "admin-read-only", "scopes": ["admin:read"]},
    ).json()["key"]

    with _key_client(key) as kc:
        assert kc.post(f"/v1/admin/users/{user_id}/reset-password", json={}).status_code == 403
        assert kc.post(
            f"/v1/admin/users/{user_id}/change-email", json={"new_email": "x@sheaf.dev"},
        ).status_code == 403
        assert kc.post(f"/v1/admin/users/{user_id}/disable-totp").status_code == 403
        assert kc.post(f"/v1/admin/users/{user_id}/verify-email").status_code == 403


def test_recovery_endpoints_work_with_admin_write_key(
    admin_client: httpx.Client, client: httpx.Client,
):
    """admin:write scoped API key can use recovery endpoints."""
    user_id, _email, _pw = _create_target_user(client)

    key = admin_client.post(
        "/v1/auth/keys",
        json={"name": "admin-write-key", "scopes": ["admin:write"]},
    ).json()["key"]

    with _key_client(key) as kc:
        resp = kc.post(f"/v1/admin/users/{user_id}/reset-password", json={})
        assert resp.status_code == 200
        assert "password" in resp.json()
