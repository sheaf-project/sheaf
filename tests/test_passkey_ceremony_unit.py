"""The registration ceremony core, without a server stack.

Pins what `sheaf.auth.passkey_ceremony` promises independently of any
endpoint: the options it mints ask for a discoverable, user-verified
credential and exclude what the account already holds; and verification
refuses a response that is off by one thing (no user verification, the
wrong origin, the wrong RP ID, a challenge that is not the one issued, a
tampered body) while accepting the honest one and reporting its metadata
faithfully. The software authenticator in `_passkey_helpers` is the other
party in every case.
"""

from __future__ import annotations

import json
import uuid

import pytest

from sheaf.auth.passkey_ceremony import (
    CHALLENGE_TTL_SECONDS,
    PasskeyCeremonyError,
    challenge_from_credential,
    registration_options,
    verify_enrolment,
)
from sheaf.auth.passkeys import RelyingParty
from tests._passkey_helpers import SoftwareAuthenticator, b64url, unb64url

RP = RelyingParty(rp_id="sheaf.example.net", origin="https://sheaf.example.net")
USER_ID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")


def _begin(exclude: list[bytes] | None = None):
    start = registration_options(
        rp=RP,
        user_id=USER_ID,
        user_name="someone@example.org",
        user_display_name="The Example System",
        exclude_credential_ids=exclude or [],
    )
    return start, json.loads(start.options_json)


# --- the options ---------------------------------------------------------------


def test_options_name_the_relying_party_and_the_account():
    start, options = _begin()
    assert options["rp"] == {"id": "sheaf.example.net", "name": "Sheaf"}
    assert options["user"]["name"] == "someone@example.org"
    assert options["user"]["displayName"] == "The Example System"
    # The user handle is the account id's raw bytes, never the email.
    assert options["user"]["id"] == b64url(USER_ID.bytes)
    assert options["challenge"] == b64url(start.challenge)
    assert len(start.challenge) >= 32


def test_options_ask_for_a_discoverable_user_verified_credential():
    _, options = _begin()
    selection = options["authenticatorSelection"]
    assert selection["residentKey"] == "required"
    assert selection["requireResidentKey"] is True
    assert selection["userVerification"] == "required"
    assert options["attestation"] == "none"
    assert options["timeout"] == CHALLENGE_TTL_SECONDS * 1000


def test_options_exclude_the_credentials_the_account_already_has():
    existing = [b"\x01" * 16, b"\x02" * 16]
    _, options = _begin(exclude=existing)
    assert [c["id"] for c in options["excludeCredentials"]] == [
        b64url(cid) for cid in existing
    ]
    assert all(c["type"] == "public-key" for c in options["excludeCredentials"])


def test_two_beginnings_never_share_a_challenge():
    a, _ = _begin()
    b, _ = _begin()
    assert a.challenge != b.challenge


# --- verification, the honest case -------------------------------------------


def test_honest_response_verifies_and_reports_its_metadata():
    start, options = _begin()
    authenticator = SoftwareAuthenticator(transports=["usb", "nfc"])
    credential = authenticator.create(options, origin=RP.origin)

    assert challenge_from_credential(credential) == start.challenge
    verified = verify_enrolment(
        credential=credential, expected_challenge=start.challenge, rp=RP
    )
    assert verified.credential_id == authenticator.credential_id
    assert verified.public_key == authenticator.cose_public_key()
    assert verified.sign_count == 0
    assert verified.aaguid is None  # all-zero aaguid means "no information"
    assert verified.transports == ["usb", "nfc"]
    assert verified.backup_eligible is False
    assert verified.backup_state is False


def test_a_synced_passkey_reports_backup_flags_and_aaguid():
    start, options = _begin()
    aaguid = uuid.uuid4()
    authenticator = SoftwareAuthenticator(aaguid=aaguid.bytes, transports=["internal", "hybrid"])
    credential = authenticator.create(
        options, origin=RP.origin, backup_eligible=True, backup_state=True
    )
    verified = verify_enrolment(
        credential=credential, expected_challenge=start.challenge, rp=RP
    )
    assert verified.aaguid == aaguid
    assert verified.backup_eligible is True
    assert verified.backup_state is True
    assert verified.transports == ["internal", "hybrid"]


# --- verification, one thing wrong at a time ----------------------------------


def _refused(credential: dict, challenge: bytes, rp: RelyingParty = RP) -> str:
    with pytest.raises(PasskeyCeremonyError) as excinfo:
        verify_enrolment(credential=credential, expected_challenge=challenge, rp=rp)
    return excinfo.value.reason


def test_a_response_without_user_verification_is_refused():
    start, options = _begin()
    credential = SoftwareAuthenticator().create(
        options, origin=RP.origin, user_verified=False
    )
    assert _refused(credential, start.challenge) == "verification_failed"


def test_a_response_from_another_origin_is_refused():
    start, options = _begin()
    credential = SoftwareAuthenticator().create(
        options, origin="https://evil.example.net"
    )
    assert _refused(credential, start.challenge) == "verification_failed"


def test_a_response_bound_to_another_rp_id_is_refused():
    start, options = _begin()
    # Same origin string claimed in client data, but the authenticator
    # hashed a different RP ID into the authenticator data.
    credential = SoftwareAuthenticator().create(
        options, origin=RP.origin, rp_id="other.example.net"
    )
    assert _refused(credential, start.challenge) == "verification_failed"


def test_a_response_to_a_different_challenge_is_refused():
    start, options = _begin()
    other, _ = _begin()
    credential = SoftwareAuthenticator().create(options, origin=RP.origin)
    assert _refused(credential, other.challenge) == "verification_failed"


def test_a_truncated_attestation_object_is_refused():
    # Under "none" attestation nothing signs the attested credential data,
    # so a flipped byte inside the COSE key is simply a different key (the
    # authenticator is self-asserting it either way). What must not slip
    # through is structural damage: a response cut short mid-object.
    start, options = _begin()
    credential = SoftwareAuthenticator().create(options, origin=RP.origin)
    blob = unb64url(credential["response"]["attestationObject"])
    credential["response"]["attestationObject"] = b64url(blob[: len(blob) - 20])
    assert _refused(credential, start.challenge) == "verification_failed"


def test_a_client_data_challenge_swap_is_refused():
    # The challenge in client data is what the lookup keys on; swapping it
    # for another valid-looking one must fail verification, not merely
    # miss the lookup, because the lookup is the API layer's job.
    start, options = _begin()
    credential = SoftwareAuthenticator().create(
        options, origin=RP.origin, challenge=b64url(b"\x42" * 32)
    )
    assert challenge_from_credential(credential) == b"\x42" * 32
    assert _refused(credential, start.challenge) == "verification_failed"


def test_garbage_is_reported_as_malformed_not_a_crash():
    with pytest.raises(PasskeyCeremonyError) as excinfo:
        challenge_from_credential({"id": "x", "response": {}})
    assert excinfo.value.reason == "malformed"
