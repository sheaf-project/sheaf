
import time

import httpx
import pyotp


def test_totp_setup_returns_qr_data(auth_client: httpx.Client):
    resp = auth_client.post("/v1/auth/totp/setup", json={"password": "testpassword123"})
    assert resp.status_code == 200
    data = resp.json()
    assert "secret" in data
    assert "provisioning_uri" in data
    assert data["provisioning_uri"].startswith("otpauth://totp/")


def test_totp_verify_rejects_bad_code(auth_client: httpx.Client):
    auth_client.post("/v1/auth/totp/setup", json={"password": "testpassword123"})
    resp = auth_client.post("/v1/auth/totp/verify", json={"code": "000000"})
    assert resp.status_code == 400


def test_totp_verify_rejects_wrong_length(auth_client: httpx.Client):
    auth_client.post("/v1/auth/totp/setup", json={"password": "testpassword123"})
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
    r1 = auth_client.post("/v1/auth/totp/setup", json={"password": "testpassword123"})
    r2 = auth_client.post("/v1/auth/totp/setup", json={"password": "testpassword123"})
    assert r1.status_code == 200
    assert r2.status_code == 200


def test_totp_setup_requires_password(auth_client: httpx.Client):
    """Enabling 2FA is password-gated — a stolen session alone can't
    enrol an attacker-controlled factor."""
    resp = auth_client.post("/v1/auth/totp/setup", json={})
    assert resp.status_code == 422

    resp = auth_client.post(
        "/v1/auth/totp/setup", json={"password": "definitelywrong"}
    )
    assert resp.status_code == 401


def test_totp_code_replay_rejected(auth_client: httpx.Client):
    """An accepted TOTP code is single-use across the whole API — a code
    observed in transit can't be replayed at another gate inside its
    validity window."""
    setup = auth_client.post(
        "/v1/auth/totp/setup", json={"password": "testpassword123"}
    )
    assert setup.status_code == 200, setup.text
    totp = pyotp.TOTP(setup.json()["secret"])

    code = totp.now()
    verify = auth_client.post("/v1/auth/totp/verify", json={"code": code})
    assert verify.status_code == 204, verify.text

    # Replaying the code consumed by enrolment at a different TOTP gate
    # must fail even though the code is still inside its drift window.
    resp = auth_client.post(
        "/v1/auth/totp/regenerate-recovery-codes", json={"code": code}
    )
    assert resp.status_code == 400, resp.text

    # The next timestep's code (also valid under ±1 drift) is fresh and
    # goes through — proving the rejection above was the replay guard,
    # not a bad code.
    resp = auth_client.post(
        "/v1/auth/totp/regenerate-recovery-codes",
        json={"code": totp.at(time.time() + 30)},
    )
    assert resp.status_code == 200, resp.text


def test_totp_replay_message_distinct_from_invalid(auth_client: httpx.Client):
    """A replayed (already-spent) code is reported differently from a wrong
    one, so the user is told to wait for the next code rather than that they
    typed it wrong."""
    setup = auth_client.post(
        "/v1/auth/totp/setup", json={"password": "testpassword123"}
    )
    assert setup.status_code == 200, setup.text
    totp = pyotp.TOTP(setup.json()["secret"])

    code = totp.now()
    # Enrolment consumes the code.
    assert auth_client.post(
        "/v1/auth/totp/verify", json={"code": code}
    ).status_code == 204

    # Replaying the spent code at another gate: replay-specific message.
    replay = auth_client.post(
        "/v1/auth/totp/regenerate-recovery-codes", json={"code": code}
    )
    assert replay.status_code == 400, replay.text
    replay_detail = replay.json()["detail"]
    assert "already been used" in replay_detail.lower()

    # A plain wrong code: the generic message, distinct from the replay one.
    wrong = auth_client.post(
        "/v1/auth/totp/regenerate-recovery-codes", json={"code": "000000"}
    )
    assert wrong.status_code == 400, wrong.text
    assert wrong.json()["detail"] == "Invalid TOTP code"
    assert wrong.json()["detail"] != replay_detail
