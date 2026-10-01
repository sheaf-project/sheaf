"""Passkey enrolment and management over the API: `/v1/auth/passkeys`.

Against the test stack, whose base URL is `http://localhost:<port>`, one of
the loopback origins the availability rule accepts, so the routes are live.
The software authenticator in `_passkey_helpers` plays the browser. Each
case pins one rule from the passkey design:

* the step-up: password always, TOTP code when the account has TOTP, and
  the same 401 shapes the rest of the auth surface uses;
* the ceremony: a fresh challenge per beginning, consumed exactly once,
  bound to the account it was issued to, user verification asserted;
* management: rename, delete without any password, the last one included;
* the refusals: API keys throughout, and a 409 rather than a second row
  when an already-enrolled key is offered again;
* the records: the activity log and the Article 15 listing both see it.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

from tests._passkey_helpers import SoftwareAuthenticator, b64url
from tests._totp_helpers import clear_totp_replay
from tests.conftest import BASE_URL
from tests.test_account_lockout import _enrol_totp, _stale_code

PASSWORD = "testpassword123"
# The RP resolution rebuilds the origin from the configured base URL; the
# test stack sets that to exactly the URL pytest talks to.
ORIGIN = BASE_URL.rstrip("/")


def _begin(client: httpx.Client, **extra) -> dict:
    resp = client.post(
        "/v1/auth/passkeys/register/begin", json={"password": PASSWORD, **extra}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["options"]


def _enrol(
    client: httpx.Client,
    authenticator: SoftwareAuthenticator | None = None,
    nickname: str | None = "test key",
    **create_kwargs,
) -> tuple[httpx.Response, SoftwareAuthenticator]:
    authenticator = authenticator or SoftwareAuthenticator()
    options = _begin(client)
    credential = authenticator.create(options, origin=ORIGIN, **create_kwargs)
    resp = client.post(
        "/v1/auth/passkeys/register/complete",
        json={"credential": credential, "nickname": nickname},
    )
    return resp, authenticator


# --- the ordinary path -----------------------------------------------------------


def test_a_fresh_account_has_no_passkeys(auth_client: httpx.Client):
    resp = auth_client.get("/v1/auth/passkeys")
    assert resp.status_code == 200
    assert resp.json() == []


def test_begin_mints_options_for_this_instance_and_account(auth_client: httpx.Client):
    options = _begin(auth_client)
    assert options["rp"]["id"] == "localhost"
    assert options["rp"]["name"] == "Sheaf"
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["authenticatorSelection"]["residentKey"] == "required"
    assert options["attestation"] == "none"
    assert options["excludeCredentials"] == []
    me = auth_client.get("/v1/auth/me").json()
    assert options["user"]["name"] == me["email"]


def test_enrolment_stores_the_credential_and_lists_it(auth_client: httpx.Client):
    resp, authenticator = _enrol(auth_client, nickname="Blue YubiKey")
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["nickname"] == "Blue YubiKey"
    assert created["rp_id"] == "localhost"
    assert created["usable"] is True
    assert created["transports"] == ["usb"]
    assert created["aaguid"] is None
    assert created["backup_eligible"] is False
    assert created["backup_state"] is False
    assert created["last_used_at"] is None
    assert uuid.UUID(created["id"])

    listed = auth_client.get("/v1/auth/passkeys").json()
    assert [p["id"] for p in listed] == [created["id"]]
    # The response never carries the public key or the raw credential id.
    for forbidden in ("public_key", "credential_id", "sign_count"):
        assert forbidden not in created


def test_a_second_key_is_a_second_passkey_and_the_first_is_excluded(
    auth_client: httpx.Client,
):
    first, first_auth = _enrol(auth_client, nickname="first")
    assert first.status_code == 201, first.text
    options = _begin(auth_client)
    excluded = [c["id"] for c in options["excludeCredentials"]]
    assert excluded == [b64url(first_auth.credential_id)]
    second = auth_client.post(
        "/v1/auth/passkeys/register/complete",
        json={
            "credential": SoftwareAuthenticator().create(options, origin=ORIGIN),
            "nickname": "second",
        },
    )
    assert second.status_code == 201, second.text
    names = sorted(p["nickname"] for p in auth_client.get("/v1/auth/passkeys").json())
    assert names == ["first", "second"]


def test_a_synced_passkey_keeps_its_backup_flags(auth_client: httpx.Client):
    aaguid = uuid.uuid4()
    resp, _ = _enrol(
        auth_client,
        SoftwareAuthenticator(aaguid=aaguid.bytes, transports=["internal", "hybrid"]),
        backup_eligible=True,
        backup_state=True,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["aaguid"] == str(aaguid)
    assert body["backup_eligible"] is True
    assert body["backup_state"] is True
    assert body["transports"] == ["internal", "hybrid"]


def test_rename_changes_only_the_nickname(auth_client: httpx.Client):
    created, _ = _enrol(auth_client, nickname="before")
    pid = created.json()["id"]
    resp = auth_client.patch(f"/v1/auth/passkeys/{pid}", json={"nickname": "  after  "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["nickname"] == "after"
    assert resp.json()["id"] == pid
    cleared = auth_client.patch(f"/v1/auth/passkeys/{pid}", json={"nickname": ""})
    assert cleared.status_code == 200
    assert cleared.json()["nickname"] is None


def test_delete_needs_no_password_and_the_last_key_may_go(auth_client: httpx.Client):
    created, _ = _enrol(auth_client)
    pid = created.json()["id"]
    resp = auth_client.delete(f"/v1/auth/passkeys/{pid}")
    assert resp.status_code == 204, resp.text
    assert auth_client.get("/v1/auth/passkeys").json() == []
    # And the account is otherwise untouched: the password still works.
    me = auth_client.get("/v1/auth/me").json()
    login = httpx.post(
        f"{BASE_URL}/v1/auth/login", json={"email": me["email"], "password": PASSWORD}
    )
    assert login.status_code == 200, login.text


def test_unknown_or_foreign_passkey_ids_are_404(auth_client: httpx.Client):
    with httpx.Client(base_url=BASE_URL) as other:
        other_email = f"pk-other-{uuid.uuid4().hex[:8]}@sheaf.dev"
        reg = other.post(
            "/v1/auth/register", json={"email": other_email, "password": PASSWORD}
        )
        assert reg.status_code == 201, reg.text
        other.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        theirs, _ = _enrol(other)
        their_id = theirs.json()["id"]

    assert auth_client.delete(f"/v1/auth/passkeys/{their_id}").status_code == 404
    assert (
        auth_client.patch(f"/v1/auth/passkeys/{their_id}", json={"nickname": "x"}).status_code
        == 404
    )
    assert auth_client.delete(f"/v1/auth/passkeys/{uuid.uuid4()}").status_code == 404


# --- the step-up ---------------------------------------------------------------


def test_begin_refuses_the_wrong_password(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/passkeys/register/begin", json={"password": "not-the-password"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid password"
    assert auth_client.get("/v1/auth/passkeys").json() == []


@pytest.mark.skipif(
    not os.environ.get("SHEAF_TEST_REDIS_URL"),
    reason="TOTP replay markers need SHEAF_TEST_REDIS_URL (run via run_tests.sh)",
)
def test_begin_demands_a_totp_code_when_the_account_has_totp(client: httpx.Client):
    email = f"pk-totp-{uuid.uuid4().hex[:8]}@sheaf.dev"
    reg = client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert reg.status_code == 201, reg.text
    client.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
    totp = _enrol_totp(client)

    without = client.post("/v1/auth/passkeys/register/begin", json={"password": PASSWORD})
    assert without.status_code == 401, without.text
    assert without.headers.get("X-Sheaf-2FA") == "required"

    stale = client.post(
        "/v1/auth/passkeys/register/begin",
        json={"password": PASSWORD, "totp_code": _stale_code(totp)},
    )
    assert stale.status_code == 401, stale.text

    good = client.post(
        "/v1/auth/passkeys/register/begin",
        json={"password": PASSWORD, "totp_code": totp.now()},
    )
    assert good.status_code == 200, good.text
    clear_totp_replay()
    # And the whole ceremony still completes for a TOTP account.
    credential = SoftwareAuthenticator().create(good.json()["options"], origin=ORIGIN)
    done = client.post(
        "/v1/auth/passkeys/register/complete",
        json={"credential": credential, "nickname": "with totp"},
    )
    assert done.status_code == 201, done.text


# --- the ceremony's refusals ------------------------------------------------------


def test_a_challenge_is_consumed_exactly_once(auth_client: httpx.Client):
    options = _begin(auth_client)
    credential = SoftwareAuthenticator().create(options, origin=ORIGIN)
    first = auth_client.post(
        "/v1/auth/passkeys/register/complete", json={"credential": credential}
    )
    assert first.status_code == 201, first.text
    # Same response again: the challenge is gone, and the credential id
    # would collide anyway. The challenge check comes first.
    again = auth_client.post(
        "/v1/auth/passkeys/register/complete", json={"credential": credential}
    )
    assert again.status_code == 400, again.text
    assert "expired or was already used" in again.json()["detail"]
    assert len(auth_client.get("/v1/auth/passkeys").json()) == 1


def test_a_challenge_issued_to_another_account_is_refused(auth_client: httpx.Client):
    with httpx.Client(base_url=BASE_URL) as other:
        other_email = f"pk-steal-{uuid.uuid4().hex[:8]}@sheaf.dev"
        reg = other.post(
            "/v1/auth/register", json={"email": other_email, "password": PASSWORD}
        )
        assert reg.status_code == 201, reg.text
        other.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        their_options = _begin(other)

    credential = SoftwareAuthenticator().create(their_options, origin=ORIGIN)
    resp = auth_client.post(
        "/v1/auth/passkeys/register/complete", json={"credential": credential}
    )
    assert resp.status_code == 400, resp.text
    assert auth_client.get("/v1/auth/passkeys").json() == []


def test_a_response_without_user_verification_is_refused(auth_client: httpx.Client):
    resp, _ = _enrol(auth_client, user_verified=False)
    assert resp.status_code == 400, resp.text
    assert "could not be verified" in resp.json()["detail"]
    assert auth_client.get("/v1/auth/passkeys").json() == []


def test_a_response_from_another_origin_is_refused(auth_client: httpx.Client):
    options = _begin(auth_client)
    credential = SoftwareAuthenticator().create(
        options, origin="https://evil.example.net"
    )
    resp = auth_client.post(
        "/v1/auth/passkeys/register/complete", json={"credential": credential}
    )
    assert resp.status_code == 400, resp.text
    assert auth_client.get("/v1/auth/passkeys").json() == []


def test_a_refused_response_does_not_burn_a_second_attempt_for_a_fresh_begin(
    auth_client: httpx.Client,
):
    # A failed complete consumed its challenge; a new begin mints a new one
    # and the honest retry works. Pinned so "start again" in the error text
    # is true.
    bad, authenticator = _enrol(auth_client, user_verified=False)
    assert bad.status_code == 400
    good, _ = _enrol(auth_client, authenticator)
    assert good.status_code == 201, good.text


def test_garbage_is_a_400_not_a_500(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/passkeys/register/complete",
        json={"credential": {"id": "nope", "response": {"clientDataJSON": "!!"}}},
    )
    assert resp.status_code == 400, resp.text


def test_re_enrolling_the_same_key_is_a_409(auth_client: httpx.Client):
    first, authenticator = _enrol(auth_client)
    assert first.status_code == 201, first.text
    # The browser would refuse thanks to excludeCredentials; a client that
    # ignores the list gets the unique constraint instead of a second row.
    again, _ = _enrol(auth_client, authenticator)
    assert again.status_code == 409, again.text
    assert len(auth_client.get("/v1/auth/passkeys").json()) == 1


# --- API keys ----------------------------------------------------------------


def test_api_keys_are_refused_everywhere(auth_client: httpx.Client):
    created, _ = _enrol(auth_client)
    pid = created.json()["id"]
    key = auth_client.post(
        "/v1/auth/keys", json={"name": "pk-probe", "scopes": ["system:read", "system:write"]}
    )
    assert key.status_code == 201, key.text
    with httpx.Client(
        base_url=BASE_URL, headers={"Authorization": f"Bearer {key.json()['key']}"}
    ) as via_key:
        assert via_key.get("/v1/auth/passkeys").status_code == 403
        assert (
            via_key.post(
                "/v1/auth/passkeys/register/begin", json={"password": PASSWORD}
            ).status_code
            == 403
        )
        assert (
            via_key.post(
                "/v1/auth/passkeys/register/complete", json={"credential": {}}
            ).status_code
            == 403
        )
        assert (
            via_key.patch(f"/v1/auth/passkeys/{pid}", json={"nickname": "x"}).status_code
            == 403
        )
        assert via_key.delete(f"/v1/auth/passkeys/{pid}").status_code == 403
    # Nothing changed.
    assert [p["id"] for p in auth_client.get("/v1/auth/passkeys").json()] == [pid]


# --- the records ---------------------------------------------------------------


def test_enrolment_and_removal_land_in_the_activity_log(auth_client: httpx.Client):
    created, _ = _enrol(auth_client, nickname="logged key")
    pid = created.json()["id"]
    assert auth_client.delete(f"/v1/auth/passkeys/{pid}").status_code == 204
    actions = [
        (e["action"], e["target_label"])
        for e in auth_client.get("/v1/account/activity").json()
    ]
    assert ("passkey_added", "logged key") in actions
    assert ("passkey_removed", "logged key") in actions


def test_the_article_15_listing_includes_passkeys(auth_client: httpx.Client):
    created, authenticator = _enrol(auth_client, nickname="listed key")
    assert created.status_code == 201
    resp = auth_client.post("/v1/account/data", json={"password": PASSWORD})
    assert resp.status_code == 200, resp.text
    passkeys = resp.json()["passkeys"]
    assert len(passkeys) == 1
    entry = passkeys[0]
    assert entry["id"] == created.json()["id"]
    assert entry["nickname"] == "listed key"
    assert entry["rp_id"] == "localhost"
    assert entry["created_at"]
    assert entry["last_used_at"] is None
    # Metadata only, no key material.
    assert "public_key" not in entry
    assert "credential_id" not in entry
