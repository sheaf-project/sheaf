
import httpx


def test_totp_setup_returns_qr_data(auth_client: httpx.Client):
    resp = auth_client.post("/v1/auth/totp/setup")
    assert resp.status_code == 200
    data = resp.json()
    assert "secret" in data
    assert "provisioning_uri" in data
    assert data["provisioning_uri"].startswith("otpauth://totp/")


def test_totp_verify_rejects_bad_code(auth_client: httpx.Client):
    auth_client.post("/v1/auth/totp/setup")
    resp = auth_client.post("/v1/auth/totp/verify", json={"code": "000000"})
    assert resp.status_code == 400


def test_totp_verify_rejects_wrong_length(auth_client: httpx.Client):
    auth_client.post("/v1/auth/totp/setup")
    resp = auth_client.post("/v1/auth/totp/verify", json={"code": "123"})
    assert resp.status_code in (400, 422)


def test_totp_disable_requires_password(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/totp/disable",
        json={"email": "anything@sheaf.dev", "password": "wrongpassword", "totp_code": "000000"},
    )
    assert resp.status_code in (400, 401)


def test_totp_not_enabled_by_default(auth_client: httpx.Client):
    resp = auth_client.get("/v1/auth/me")
    assert resp.json()["totp_enabled"] is False


def test_totp_setup_idempotent(auth_client: httpx.Client):
    """Calling setup twice should return a (new) secret without error."""
    r1 = auth_client.post("/v1/auth/totp/setup")
    r2 = auth_client.post("/v1/auth/totp/setup")
    assert r1.status_code == 200
    assert r2.status_code == 200
