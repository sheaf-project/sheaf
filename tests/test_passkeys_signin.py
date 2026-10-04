"""Passkey sign-in over the API: `/v1/auth/passkeys/sign-in/*`.

Against the test stack, whose loopback base URL keeps the feature available.
Each case pins one rule from the passkey design:

* begin takes no body and mints discoverable options; complete turns an
  honest assertion into exactly the session a password login would mint;
* the instance advertises the feature through `/v1/auth/config`;
* a challenge is consumed exactly once, and every credential-shaped refusal
  (unknown, deleted, no user verification, wrong origin, bad signature,
  stale RP ID) answers with the same generic 401;
* failed assertions never feed the lockout counter, but an existing lock and
  the suspended refusal are honoured exactly as login honours them;
* the signature counter is logged, never fatal: a regression still signs in
  and leaves a security event; a synced passkey at zero signs in twice;
* and the two things the design says to assert rather than assume: System
  Safety still demands the tier credential after a passkey sign-in, and a
  password reset does not delete passkeys. Plus the stolen-laptop case: a
  passkey-minted session cannot change the password without knowing it.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests._passkey_helpers import SoftwareAuthenticator
from tests._totp_helpers import clear_totp_replay
from tests.conftest import BASE_URL
from tests.test_account_lockout import _enrol_totp
from tests.test_passkeys_enrolment import ORIGIN, PASSWORD, _enrol
from tests.test_system_safety import _set_system_safety_via_db

SIGNIN_FAILED = "Passkey sign-in failed. Try again, or sign in with your password."


# --- helpers ---------------------------------------------------------------------


def _register_with_passkey(
    authenticator: SoftwareAuthenticator | None = None,
) -> tuple[str, SoftwareAuthenticator, str]:
    """A fresh account with one enrolled passkey. Returns (email,
    authenticator, passkey id). The client used to enrol is discarded; the
    tests sign in anonymously."""
    with httpx.Client(base_url=BASE_URL) as c:
        email = f"pk-signin-{uuid.uuid4().hex[:8]}@sheaf.dev"
        reg = c.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
        assert reg.status_code == 201, reg.text
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        created, authenticator = _enrol(c, authenticator, nickname="signin key")
        assert created.status_code == 201, created.text
        return email, authenticator, created.json()["id"]


def _begin(client: httpx.Client) -> dict:
    resp = client.post("/v1/auth/passkeys/sign-in/begin")
    assert resp.status_code == 200, resp.text
    return resp.json()["options"]


def _sign_in(
    client: httpx.Client, authenticator: SoftwareAuthenticator, **get_kwargs
) -> httpx.Response:
    options = _begin(client)
    get_kwargs.setdefault("origin", ORIGIN)
    credential = authenticator.get(options, **get_kwargs)
    return client.post("/v1/auth/passkeys/sign-in/complete", json={"credential": credential})


def _run_db(fn) -> None:
    """Run `await fn(db)` on a session against the test database."""

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            await fn(db)
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _patch_user(email: str, **fields) -> None:
    async def _fn(db):
        from sqlalchemy import select

        from sheaf.crypto import blind_index
        from sheaf.models.user import User

        user = (
            await db.execute(select(User).where(User.email_hash == blind_index(email)))
        ).scalar_one()
        for key, value in fields.items():
            setattr(user, key, value)

    _run_db(_fn)


def _patch_passkey(passkey_id: str, **fields) -> None:
    async def _fn(db):
        from sheaf.models.passkey_credential import PasskeyCredential

        row = await db.get(PasskeyCredential, uuid.UUID(passkey_id))
        for key, value in fields.items():
            setattr(row, key, value)

    _run_db(_fn)


def _plant_reset_token(email: str) -> str:
    token = secrets.token_urlsafe(32)
    from sheaf.crypto import hash_mail_token

    _patch_user(
        email,
        password_reset_token=hash_mail_token(token),
        password_reset_sent_at=datetime.now(UTC),
    )
    return token


# --- the ordinary path -----------------------------------------------------------


def test_config_advertises_passkeys_on_this_instance(client: httpx.Client):
    config = client.get("/v1/auth/config").json()
    assert config["passkeys_available"] is True
    assert config["passkeys_unavailable_reason"] is None


def test_begin_takes_no_body_and_mints_discoverable_options(client: httpx.Client):
    options = _begin(client)
    assert options["rpId"] == "localhost"
    assert options["userVerification"] == "required"
    assert not options.get("allowCredentials")
    assert "challenge" in options


def test_a_passkey_signs_in_and_mints_a_working_session(client: httpx.Client):
    email, authenticator, passkey_id = _register_with_passkey()

    resp = _sign_in(client, authenticator)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    # The same cookies a password login sets.
    assert "sheaf_session" in resp.cookies
    assert "sheaf_refresh" in resp.cookies

    client.headers["Authorization"] = f"Bearer {body['access_token']}"
    me = client.get("/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email

    # The credential records the use.
    listed = client.get("/v1/auth/passkeys").json()
    assert [p["id"] for p in listed] == [passkey_id]
    assert listed[0]["last_used_at"] is not None


def test_sign_in_lands_in_the_security_log_as_a_passkey_login(client: httpx.Client):
    _, authenticator, _ = _register_with_passkey()
    resp = _sign_in(client, authenticator)
    assert resp.status_code == 200, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    data = client.post("/v1/account/data", json={"password": PASSWORD})
    assert data.status_code == 200, data.text
    outcomes = [
        e["outcome"] for e in data.json()["security_events"] if e["event_type"] == "login"
    ]
    assert "passkey" in outcomes


# --- refusals --------------------------------------------------------------------


def test_a_challenge_is_consumed_exactly_once(client: httpx.Client):
    _, authenticator, _ = _register_with_passkey()
    options = _begin(client)
    credential = authenticator.get(options, origin=ORIGIN)
    first = client.post("/v1/auth/passkeys/sign-in/complete", json={"credential": credential})
    assert first.status_code == 200, first.text
    again = client.post("/v1/auth/passkeys/sign-in/complete", json={"credential": credential})
    assert again.status_code == 400, again.text
    assert "expired or was already used" in again.json()["detail"]


def test_an_unknown_credential_is_a_generic_401(client: httpx.Client):
    never_enrolled = SoftwareAuthenticator()
    resp = _sign_in(client, never_enrolled)
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == SIGNIN_FAILED


def test_a_deleted_passkey_no_longer_signs_in(client: httpx.Client):
    _, authenticator, passkey_id = _register_with_passkey()
    first = _sign_in(client, authenticator)
    assert first.status_code == 200, first.text
    client.headers["Authorization"] = f"Bearer {first.json()['access_token']}"
    assert client.delete(f"/v1/auth/passkeys/{passkey_id}").status_code == 204
    del client.headers["Authorization"]
    again = _sign_in(client, authenticator)
    assert again.status_code == 401, again.text
    assert again.json()["detail"] == SIGNIN_FAILED


@pytest.mark.parametrize(
    "dishonesty",
    [
        {"user_verified": False},
        {"origin": "https://evil.example.net"},
        {"tamper_signature": True},
        {"rp_id": "other.example.net"},
    ],
    ids=["no-user-verification", "other-origin", "tampered-signature", "other-rp-id"],
)
def test_a_dishonest_assertion_is_a_generic_401(client: httpx.Client, dishonesty):
    _, authenticator, _ = _register_with_passkey()
    resp = _sign_in(client, authenticator, **dishonesty)
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == SIGNIN_FAILED


def test_a_credential_from_another_rp_id_is_a_generic_401(client: httpx.Client):
    _, authenticator, passkey_id = _register_with_passkey()
    _patch_passkey(passkey_id, rp_id="old.example.net")
    resp = _sign_in(client, authenticator)
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == SIGNIN_FAILED


def test_garbage_is_a_400_not_a_500(client: httpx.Client):
    resp = client.post(
        "/v1/auth/passkeys/sign-in/complete",
        json={"credential": {"id": "nope", "response": {"clientDataJSON": "!!"}}},
    )
    assert resp.status_code == 400, resp.text


# --- lockout and account standing ---------------------------------------------------


def test_failed_assertions_do_not_feed_the_lockout(client: httpx.Client):
    email, authenticator, _ = _register_with_passkey()
    for _ in range(20):
        bad = _sign_in(client, authenticator, tamper_signature=True)
        assert bad.status_code == 401
    # Twenty failures would have locked a password login several times over.
    # The password still works, and so does the honest passkey.
    login = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    good = _sign_in(client, authenticator)
    assert good.status_code == 200, good.text


def test_an_existing_lock_is_honoured(client: httpx.Client):
    email, authenticator, _ = _register_with_passkey()
    _patch_user(email, locked_until=datetime.now(UTC) + timedelta(minutes=10))
    resp = _sign_in(client, authenticator)
    assert resp.status_code == 423, resp.text


def test_a_suspended_account_is_refused_as_login_refuses_it(client: httpx.Client):
    from sheaf.models.user import AccountStatus

    email, authenticator, _ = _register_with_passkey()
    _patch_user(
        email, account_status=AccountStatus.SUSPENDED, suspended_reason="testing"
    )
    resp = _sign_in(client, authenticator)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"].startswith("Account suspended")
    login = client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 403


# --- the signature counter ---------------------------------------------------------


def test_a_counter_regression_signs_in_and_is_logged(client: httpx.Client):
    _, authenticator, _ = _register_with_passkey()
    first = _sign_in(client, authenticator, sign_count=5)
    assert first.status_code == 200, first.text
    second = _sign_in(client, authenticator, sign_count=3)
    assert second.status_code == 200, second.text

    client.headers["Authorization"] = f"Bearer {second.json()['access_token']}"
    data = client.post("/v1/account/data", json={"password": PASSWORD}).json()
    outcomes = [e["outcome"] for e in data["security_events"]]
    assert "passkey_counter_regressed" in outcomes


def test_a_synced_passkey_at_zero_signs_in_repeatedly(client: httpx.Client):
    _, authenticator, _ = _register_with_passkey()
    for _ in range(3):
        resp = _sign_in(
            client, authenticator, sign_count=0, backup_eligible=True, backup_state=True
        )
        assert resp.status_code == 200, resp.text


# --- what the design says to assert rather than assume -------------------------------


def test_system_safety_still_demands_the_password_after_a_passkey_sign_in(
    client: httpx.Client,
):
    email, authenticator, _ = _register_with_passkey()
    _set_system_safety_via_db(email, delete_confirmation="password")

    resp = _sign_in(client, authenticator)
    assert resp.status_code == 200, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"

    member = client.post("/v1/members", json={"name": "Gated"}).json()
    no_body = client.delete(f"/v1/members/{member['id']}")
    assert no_body.status_code == 400, no_body.text
    assert no_body.json()["detail"] == "Password required"
    assert client.get(f"/v1/members/{member['id']}").status_code == 200

    ok = client.request(
        "DELETE", f"/v1/members/{member['id']}", json={"password": PASSWORD}
    )
    assert ok.status_code == 204, ok.text


@pytest.mark.skipif(
    not os.environ.get("SHEAF_TEST_REDIS_URL"),
    reason="TOTP replay markers need SHEAF_TEST_REDIS_URL (run via run_tests.sh)",
)
def test_system_safety_still_demands_the_code_on_a_totp_tier(client: httpx.Client):
    email = f"pk-safety-totp-{uuid.uuid4().hex[:8]}@sheaf.dev"
    reg = client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert reg.status_code == 201, reg.text
    client.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
    totp = _enrol_totp(client)
    begin = client.post(
        "/v1/auth/passkeys/register/begin",
        json={"password": PASSWORD, "totp_code": totp.now()},
    )
    assert begin.status_code == 200, begin.text
    clear_totp_replay()
    authenticator = SoftwareAuthenticator()
    done = client.post(
        "/v1/auth/passkeys/register/complete",
        json={"credential": authenticator.create(begin.json()["options"], origin=ORIGIN)},
    )
    assert done.status_code == 201, done.text
    # Enrolling did not touch TOTP.
    assert client.get("/v1/auth/me").json()["totp_enabled"] is True
    _set_system_safety_via_db(email, delete_confirmation="totp")

    del client.headers["Authorization"]
    signed_in = _sign_in(client, authenticator)
    assert signed_in.status_code == 200, signed_in.text
    client.headers["Authorization"] = f"Bearer {signed_in.json()['access_token']}"

    member = client.post("/v1/members", json={"name": "Gated"}).json()
    without = client.delete(f"/v1/members/{member['id']}")
    assert without.status_code == 400, without.text
    assert client.get(f"/v1/members/{member['id']}").status_code == 200
    with_code = client.request(
        "DELETE", f"/v1/members/{member['id']}", json={"totp_code": totp.now()}
    )
    assert with_code.status_code == 204, with_code.text


def test_a_password_reset_does_not_delete_passkeys(client: httpx.Client):
    email, authenticator, passkey_id = _register_with_passkey()
    token = _plant_reset_token(email)
    reset = client.post(
        "/v1/auth/reset-password", json={"token": token, "new_password": "newpassword456"}
    )
    assert reset.status_code == 200, reset.text

    resp = _sign_in(client, authenticator)
    assert resp.status_code == 200, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    assert [p["id"] for p in client.get("/v1/auth/passkeys").json()] == [passkey_id]


def test_a_passkey_session_cannot_change_the_password_without_knowing_it(
    client: httpx.Client,
):
    _, authenticator, _ = _register_with_passkey()
    resp = _sign_in(client, authenticator)
    assert resp.status_code == 200, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    change = client.post(
        "/v1/auth/change-password",
        json={"current_password": "not-the-password", "new_password": "newpassword456"},
    )
    assert change.status_code == 401, change.text
