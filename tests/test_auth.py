import uuid

import httpx


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
