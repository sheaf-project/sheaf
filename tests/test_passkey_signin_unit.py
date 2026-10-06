"""The sign-in (assertion) half of the ceremony core, without a server stack.

Pins what `sheaf.auth.passkey_ceremony` promises for sign-in: the options
ask for a discoverable, user-verified assertion with no allow list; an
honest assertion verifies against the stored public key and reports its
counter; and one thing wrong at a time (no user verification, another
origin, another RP ID, another key, a tampered signature, the wrong
challenge) is refused. The signature counter is reported, never enforced:
synced passkeys say zero forever, and the regression flag only trips when
the stored count was above zero.
"""

from __future__ import annotations

import json
import uuid

import pytest

from sheaf.auth.passkey_ceremony import (
    CHALLENGE_TTL_SECONDS,
    PasskeyCeremonyError,
    assertion_identity,
    authentication_options,
    registration_options,
    verify_assertion,
    verify_enrolment,
)
from sheaf.auth.passkeys import RelyingParty
from tests._passkey_helpers import SoftwareAuthenticator, b64url

RP = RelyingParty(rp_id="sheaf.example.net", origin="https://sheaf.example.net")


def _enrolled(authenticator: SoftwareAuthenticator | None = None):
    """An authenticator that has been through registration, plus the public
    key the server would have stored for it."""
    authenticator = authenticator or SoftwareAuthenticator()
    start = registration_options(
        rp=RP,
        user_id=uuid.uuid4(),
        user_name="someone@example.org",
        user_display_name="Someone",
        exclude_credential_ids=[],
    )
    verified = verify_enrolment(
        credential=authenticator.create(json.loads(start.options_json), origin=RP.origin),
        expected_challenge=start.challenge,
        rp=RP,
    )
    return authenticator, verified.public_key


def _begin():
    start = authentication_options(rp=RP)
    return start, json.loads(start.options_json)


# --- the options ---------------------------------------------------------------


def test_options_are_discoverable_and_require_user_verification():
    start, options = _begin()
    assert options["rpId"] == "sheaf.example.net"
    assert options["userVerification"] == "required"
    assert options["challenge"] == b64url(start.challenge)
    assert options["timeout"] == CHALLENGE_TTL_SECONDS * 1000
    # No allow list: the browser offers what it holds, the user picks.
    assert not options.get("allowCredentials")


def test_two_beginnings_never_share_a_challenge():
    a, _ = _begin()
    b, _ = _begin()
    assert a.challenge != b.challenge


# --- the honest case -----------------------------------------------------------


def test_an_honest_assertion_verifies_and_reports_the_counter():
    authenticator, public_key = _enrolled()
    start, options = _begin()
    credential = authenticator.get(options, origin=RP.origin, sign_count=7)

    identity = assertion_identity(credential)
    assert identity.challenge == start.challenge
    assert identity.credential_id == authenticator.credential_id

    verified = verify_assertion(
        credential=credential,
        expected_challenge=start.challenge,
        rp=RP,
        public_key=public_key,
        stored_sign_count=3,
    )
    assert verified.new_sign_count == 7
    assert verified.sign_count_regressed is False
    assert verified.backup_state is False


def test_backup_state_follows_the_response():
    authenticator, public_key = _enrolled()
    start, options = _begin()
    credential = authenticator.get(
        options, origin=RP.origin, backup_eligible=True, backup_state=True
    )
    verified = verify_assertion(
        credential=credential,
        expected_challenge=start.challenge,
        rp=RP,
        public_key=public_key,
        stored_sign_count=0,
    )
    assert verified.backup_state is True


# --- the counter -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "received", "regressed"),
    [
        (0, 0, False),  # a synced passkey, forever zero
        (0, 1, False),  # first use of a hardware key
        (5, 6, False),  # ordinary increment
        (5, 5, True),  # did not advance
        (5, 3, True),  # went backwards
    ],
)
def test_the_counter_is_reported_not_enforced(stored, received, regressed):
    authenticator, public_key = _enrolled()
    start, options = _begin()
    credential = authenticator.get(options, origin=RP.origin, sign_count=received)
    verified = verify_assertion(
        credential=credential,
        expected_challenge=start.challenge,
        rp=RP,
        public_key=public_key,
        stored_sign_count=stored,
    )
    assert verified.new_sign_count == received
    assert verified.sign_count_regressed is regressed


# --- one thing wrong at a time ---------------------------------------------------


def _refused(credential, challenge, public_key) -> str:
    with pytest.raises(PasskeyCeremonyError) as excinfo:
        verify_assertion(
            credential=credential,
            expected_challenge=challenge,
            rp=RP,
            public_key=public_key,
            stored_sign_count=0,
        )
    return excinfo.value.reason


def test_without_user_verification_is_refused():
    authenticator, public_key = _enrolled()
    start, options = _begin()
    credential = authenticator.get(options, origin=RP.origin, user_verified=False)
    assert _refused(credential, start.challenge, public_key) == "verification_failed"


def test_from_another_origin_is_refused():
    authenticator, public_key = _enrolled()
    start, options = _begin()
    credential = authenticator.get(options, origin="https://evil.example.net")
    assert _refused(credential, start.challenge, public_key) == "verification_failed"


def test_bound_to_another_rp_id_is_refused():
    authenticator, public_key = _enrolled()
    start, options = _begin()
    credential = authenticator.get(options, origin=RP.origin, rp_id="other.example.net")
    assert _refused(credential, start.challenge, public_key) == "verification_failed"


def test_signed_by_another_key_is_refused():
    _, public_key = _enrolled()
    impostor = SoftwareAuthenticator()
    start, options = _begin()
    credential = impostor.get(options, origin=RP.origin)
    assert _refused(credential, start.challenge, public_key) == "verification_failed"


def test_a_tampered_signature_is_refused():
    authenticator, public_key = _enrolled()
    start, options = _begin()
    credential = authenticator.get(options, origin=RP.origin, tamper_signature=True)
    assert _refused(credential, start.challenge, public_key) == "verification_failed"


def test_answering_a_different_challenge_is_refused():
    authenticator, public_key = _enrolled()
    start, options = _begin()
    other, _ = _begin()
    credential = authenticator.get(options, origin=RP.origin)
    assert _refused(credential, other.challenge, public_key) == "verification_failed"


def test_garbage_is_malformed_not_a_crash():
    with pytest.raises(PasskeyCeremonyError) as excinfo:
        assertion_identity({"id": "x", "response": {}})
    assert excinfo.value.reason == "malformed"
