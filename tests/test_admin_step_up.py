"""Tests for admin dashboard step-up authentication.

Tests marked admin_auth_password / admin_auth_totp are skipped unless the
server is running with the matching ADMIN_AUTH_LEVEL. run_tests.sh handles
starting the server in each configuration and setting SHEAF_TEST_ADMIN_AUTH_LEVEL
accordingly.
"""

import os

import httpx
import pyotp
import pytest

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")


# ---------------------------------------------------------------------------
# Auth status endpoint (always runs)
# ---------------------------------------------------------------------------

def test_auth_status_requires_admin(auth_client: httpx.Client):
    resp = auth_client.get("/v1/admin/auth")
    assert resp.status_code == 403


def test_auth_status_unauthenticated(client: httpx.Client):
    resp = client.get("/v1/admin/auth")
    assert resp.status_code in (401, 403)


def test_auth_status_returns_level(admin_client: httpx.Client):
    resp = admin_client.get("/v1/admin/auth")
    assert resp.status_code == 200
    data = resp.json()
    assert data["level"] in ("none", "password", "totp")
    assert isinstance(data["verified"], bool)
    assert isinstance(data["totp_enabled"], bool)


def test_auth_status_verified_after_step_up(admin_client: httpx.Client):
    # admin_client already completed step-up in fixture setup
    resp = admin_client.get("/v1/admin/auth")
    assert resp.status_code == 200
    assert resp.json()["verified"] is True


# ---------------------------------------------------------------------------
# Password step-up enforcement (requires ADMIN_AUTH_LEVEL=password)
# ---------------------------------------------------------------------------

@pytest.mark.admin_auth_password
def test_password_step_up_required(raw_admin_client: httpx.Client):
    resp = raw_admin_client.get("/v1/admin/stats")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_step_up_required"


@pytest.mark.admin_auth_password
def test_password_step_up_wrong_password_rejected(raw_admin_client: httpx.Client):
    resp = raw_admin_client.post("/v1/admin/auth", json={"password": "wrongpassword"})
    assert resp.status_code == 401


@pytest.mark.admin_auth_password
def test_password_step_up_missing_password_rejected(raw_admin_client: httpx.Client):
    resp = raw_admin_client.post("/v1/admin/auth", json={})
    assert resp.status_code == 422


@pytest.mark.admin_auth_password
def test_password_step_up_grants_access(raw_admin_client: httpx.Client):
    # Before step-up: blocked
    assert raw_admin_client.get("/v1/admin/stats").status_code == 403

    # Complete step-up
    resp = raw_admin_client.post("/v1/admin/auth", json={"password": "testpassword123"})
    assert resp.status_code == 200
    assert resp.json()["verified"] is True

    # After step-up: accessible
    assert raw_admin_client.get("/v1/admin/stats").status_code == 200


@pytest.mark.admin_auth_password
def test_password_step_up_per_user(raw_admin_client: httpx.Client, admin_client: httpx.Client):
    """Step-up for one user doesn't grant access to another."""
    # admin_client has step-up; raw_admin_client does not
    assert raw_admin_client.get("/v1/admin/stats").status_code == 403
    assert admin_client.get("/v1/admin/stats").status_code == 200


# ---------------------------------------------------------------------------
# TOTP step-up enforcement (requires ADMIN_AUTH_LEVEL=totp)
# ---------------------------------------------------------------------------

@pytest.mark.admin_auth_totp
def test_totp_step_up_required(raw_admin_client: httpx.Client):
    resp = raw_admin_client.get("/v1/admin/stats")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_step_up_required"


@pytest.mark.admin_auth_totp
def test_totp_step_up_without_totp_enrolled_blocked(raw_admin_client: httpx.Client):
    resp = raw_admin_client.post("/v1/admin/auth", json={"totp_code": "000000"})
    assert resp.status_code == 403
    assert "TOTP must be enabled" in resp.json()["detail"]


@pytest.mark.admin_auth_totp
def test_totp_step_up_wrong_code_rejected(raw_admin_client: httpx.Client):
    # Enrol TOTP first
    setup = raw_admin_client.post("/v1/auth/totp/setup").json()
    secret = setup["secret"]
    totp = pyotp.TOTP(secret)
    raw_admin_client.post("/v1/auth/totp/verify", json={"code": totp.now()})

    # Wrong code
    resp = raw_admin_client.post("/v1/admin/auth", json={"totp_code": "000000"})
    assert resp.status_code == 401


@pytest.mark.admin_auth_totp
def test_totp_step_up_missing_code_rejected(raw_admin_client: httpx.Client):
    setup = raw_admin_client.post("/v1/auth/totp/setup").json()
    secret = setup["secret"]
    totp = pyotp.TOTP(secret)
    raw_admin_client.post("/v1/auth/totp/verify", json={"code": totp.now()})

    resp = raw_admin_client.post("/v1/admin/auth", json={})
    assert resp.status_code == 422


@pytest.mark.admin_auth_totp
def test_totp_step_up_grants_access(raw_admin_client: httpx.Client):
    # Before step-up: blocked
    assert raw_admin_client.get("/v1/admin/stats").status_code == 403

    # Enrol TOTP and complete step-up
    setup = raw_admin_client.post("/v1/auth/totp/setup").json()
    secret = setup["secret"]
    totp = pyotp.TOTP(secret)
    raw_admin_client.post("/v1/auth/totp/verify", json={"code": totp.now()})

    resp = raw_admin_client.post("/v1/admin/auth", json={"totp_code": totp.now()})
    assert resp.status_code == 200
    assert resp.json()["verified"] is True

    # After step-up: accessible
    assert raw_admin_client.get("/v1/admin/stats").status_code == 200


@pytest.mark.admin_auth_totp
def test_totp_step_up_per_user(raw_admin_client: httpx.Client, admin_client: httpx.Client):
    """Step-up for one user doesn't grant access to another."""
    assert raw_admin_client.get("/v1/admin/stats").status_code == 403
    assert admin_client.get("/v1/admin/stats").status_code == 200
