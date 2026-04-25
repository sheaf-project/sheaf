import uuid

import httpx
import pyotp


def test_register(client: httpx.Client):
    email = f"reg-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "securepassword"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_register_duplicate_email(client: httpx.Client):
    email = f"dupe-{uuid.uuid4().hex[:8]}@sheaf.dev"
    client.post("/v1/auth/register", json={"email": email, "password": "securepassword"})
    resp = client.post("/v1/auth/register", json={"email": email, "password": "otherpassword"})
    assert resp.status_code == 409


def test_register_short_password(client: httpx.Client):
    resp = client.post(
        "/v1/auth/register",
        json={"email": "short@sheaf.dev", "password": "abc"},
    )
    assert resp.status_code == 422


def test_login(client: httpx.Client):
    email = f"login-{uuid.uuid4().hex[:8]}@sheaf.dev"
    client.post("/v1/auth/register", json={"email": email, "password": "securepassword"})
    resp = client.post("/v1/auth/login", json={"email": email, "password": "securepassword"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_wrong_password(client: httpx.Client):
    email = f"wrong-{uuid.uuid4().hex[:8]}@sheaf.dev"
    client.post("/v1/auth/register", json={"email": email, "password": "securepassword"})
    resp = client.post("/v1/auth/login", json={"email": email, "password": "wrongpassword"})
    assert resp.status_code == 401


def test_me(auth_client: httpx.Client):
    resp = auth_client.get("/v1/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert "@sheaf.dev" in data["email"]
    assert data["totp_enabled"] is False


def test_unauthenticated(client: httpx.Client):
    resp = client.get("/v1/systems/me")
    assert resp.status_code in (401, 403)


def test_refresh_token(client: httpx.Client):
    email = f"refresh-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post("/v1/auth/register", json={"email": email, "password": "securepassword"})
    refresh_token = resp.json()["refresh_token"]
    resp = client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def _register_and_login(client: httpx.Client, email: str, password: str) -> str:
    """Register a user and log in via cookie session. Returns access token."""
    r = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    r = client.post("/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_change_password_success(client: httpx.Client):
    email = f"chpw-{uuid.uuid4().hex[:8]}@sheaf.dev"
    token = _register_and_login(client, email, "oldpassword123")
    client.headers["Authorization"] = f"Bearer {token}"

    resp = client.post(
        "/v1/auth/change-password",
        json={"current_password": "oldpassword123", "new_password": "newpassword456"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] is True

    # Old password no longer logs in.
    r = client.post("/v1/auth/login", json={"email": email, "password": "oldpassword123"})
    assert r.status_code == 401
    # New password does.
    r = client.post("/v1/auth/login", json={"email": email, "password": "newpassword456"})
    assert r.status_code == 200


def test_change_password_wrong_current(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/change-password",
        json={"current_password": "wrong", "new_password": "newpassword456"},
    )
    assert resp.status_code == 401


def test_change_password_same_as_current(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/change-password",
        json={"current_password": "testpassword123", "new_password": "testpassword123"},
    )
    assert resp.status_code == 400


def test_change_password_too_short(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/change-password",
        json={"current_password": "testpassword123", "new_password": "short"},
    )
    assert resp.status_code == 400


def test_change_password_unauthenticated(client: httpx.Client):
    resp = client.post(
        "/v1/auth/change-password",
        json={"current_password": "x", "new_password": "newpassword456"},
    )
    assert resp.status_code in (401, 403)


def test_change_password_with_totp(client: httpx.Client):
    email = f"chpw-totp-{uuid.uuid4().hex[:8]}@sheaf.dev"
    token = _register_and_login(client, email, "oldpassword123")
    client.headers["Authorization"] = f"Bearer {token}"

    setup = client.post("/v1/auth/totp/setup")
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    totp = pyotp.TOTP(secret)
    verify = client.post("/v1/auth/totp/verify", json={"code": totp.now()})
    assert verify.status_code == 204, verify.text

    # Missing TOTP -> 401 with X-Sheaf-2FA header.
    resp = client.post(
        "/v1/auth/change-password",
        json={"current_password": "oldpassword123", "new_password": "newpassword456"},
    )
    assert resp.status_code == 401
    assert resp.headers.get("X-Sheaf-2FA") == "required"

    # Wrong TOTP -> 401, no header (the gate is past).
    resp = client.post(
        "/v1/auth/change-password",
        json={
            "current_password": "oldpassword123",
            "new_password": "newpassword456",
            "totp_code": "000000",
        },
    )
    assert resp.status_code == 401

    # Correct TOTP -> success.
    resp = client.post(
        "/v1/auth/change-password",
        json={
            "current_password": "oldpassword123",
            "new_password": "newpassword456",
            "totp_code": totp.now(),
        },
    )
    assert resp.status_code == 200, resp.text


def test_change_email_success(client: httpx.Client):
    email = f"chem-{uuid.uuid4().hex[:8]}@sheaf.dev"
    new_email = f"chem-new-{uuid.uuid4().hex[:8]}@sheaf.dev"
    token = _register_and_login(client, email, "testpassword123")
    client.headers["Authorization"] = f"Bearer {token}"

    resp = client.post(
        "/v1/auth/change-email",
        json={"new_email": new_email, "current_password": "testpassword123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == new_email

    # Old email no longer logs in.
    r = client.post("/v1/auth/login", json={"email": email, "password": "testpassword123"})
    assert r.status_code == 401
    # New email does.
    r = client.post(
        "/v1/auth/login", json={"email": new_email, "password": "testpassword123"},
    )
    assert r.status_code == 200


def test_change_email_wrong_password(auth_client: httpx.Client):
    new_email = f"chem-bad-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = auth_client.post(
        "/v1/auth/change-email",
        json={"new_email": new_email, "current_password": "wrong"},
    )
    assert resp.status_code == 401


def test_change_email_same_as_current(auth_client: httpx.Client):
    me = auth_client.get("/v1/auth/me").json()
    resp = auth_client.post(
        "/v1/auth/change-email",
        json={"new_email": me["email"], "current_password": "testpassword123"},
    )
    assert resp.status_code == 400


def test_change_email_invalid_format(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/change-email",
        json={"new_email": "not-an-email", "current_password": "testpassword123"},
    )
    assert resp.status_code == 422


def test_change_email_conflict(client: httpx.Client):
    a = f"chem-a-{uuid.uuid4().hex[:8]}@sheaf.dev"
    b = f"chem-b-{uuid.uuid4().hex[:8]}@sheaf.dev"
    # Register both users.
    client.post("/v1/auth/register", json={"email": a, "password": "testpassword123"})
    token_b = _register_and_login(client, b, "testpassword123")

    # User B tries to take user A's email.
    client.headers["Authorization"] = f"Bearer {token_b}"
    resp = client.post(
        "/v1/auth/change-email",
        json={"new_email": a, "current_password": "testpassword123"},
    )
    assert resp.status_code == 409


def test_change_email_with_totp(client: httpx.Client):
    email = f"chem-totp-{uuid.uuid4().hex[:8]}@sheaf.dev"
    new_email = f"chem-totp-new-{uuid.uuid4().hex[:8]}@sheaf.dev"
    token = _register_and_login(client, email, "testpassword123")
    client.headers["Authorization"] = f"Bearer {token}"

    setup = client.post("/v1/auth/totp/setup")
    secret = setup.json()["secret"]
    totp = pyotp.TOTP(secret)
    client.post("/v1/auth/totp/verify", json={"code": totp.now()})

    # Missing TOTP -> 401 with X-Sheaf-2FA header.
    resp = client.post(
        "/v1/auth/change-email",
        json={"new_email": new_email, "current_password": "testpassword123"},
    )
    assert resp.status_code == 401
    assert resp.headers.get("X-Sheaf-2FA") == "required"

    # Correct TOTP -> success.
    resp = client.post(
        "/v1/auth/change-email",
        json={
            "new_email": new_email,
            "current_password": "testpassword123",
            "totp_code": totp.now(),
        },
    )
    assert resp.status_code == 200


def test_change_password_revokes_other_sessions(client: httpx.Client):
    email = f"chpw-rev-{uuid.uuid4().hex[:8]}@sheaf.dev"
    password = "oldpassword123"

    # Session A: register (also logs in). Cookies are Secure, so over plain
    # HTTP httpx won't auto-send them — pull the session id out of the
    # response and pass it explicitly on later requests.
    r = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201
    access_a = r.json()["access_token"]
    session_a = r.cookies.get("sheaf_session")
    assert session_a

    # Session B: log in from a separate client.
    with httpx.Client(base_url=str(client.base_url)) as other:
        r = other.post("/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200
        refresh_b = r.json()["refresh_token"]

        # Session A changes the password (bearer + session cookie).
        resp = client.post(
            "/v1/auth/change-password",
            json={"current_password": password, "new_password": "newpassword456"},
            headers={"Authorization": f"Bearer {access_a}"},
            cookies={"sheaf_session": session_a},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["revoked_other_sessions"] >= 1

        # Session B's refresh token now fails — its session was wiped, so
        # /refresh's session-existence check misses.
        r = other.post("/v1/auth/refresh", json={"refresh_token": refresh_b})
        assert r.status_code == 401
