"""The WebAuthn registration ceremony and its challenge store.

Two halves. The challenge store keeps the one-time challenge a ceremony must
echo back, in Redis, single-use through an atomic get-and-delete, and bound
to the account it was issued to so a challenge minted for one user can never
be redeemed by another. It fails closed: if Redis is unreachable the call
raises and the ceremony does not happen, the same posture as the TOTP replay
guard, because a challenge that could be replayed is worse than one that
could not be issued.

The ceremony half wraps py_webauthn. Registration asks for a discoverable
credential (the sign-in screen is one tap with no email typed) with user
verification required, and asserts the verification flag server-side rather
than merely requesting it: possession of the key plus the PIN or biometric
that unlocked it is what makes one tap a legitimate two factors. Attestation
is "none" on purpose. We are not building a device allow-list; asking for an
attestation statement would add certificate-chain code for no decision this
product makes.

Nothing here decides who may enrol. The API layer does the password and TOTP
step-up before it ever asks for options; this module only knows how to mint
and verify.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass

from webauthn import generate_registration_options, verify_registration_response
from webauthn.helpers import (
    base64url_to_bytes,
    options_to_json,
    parse_client_data_json,
    parse_registration_credential_json,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from sheaf.auth.passkeys import RelyingParty

# What the authenticator shows as the service name. Instance operators have
# no other user-facing name for the instance today; the origin in the
# credential already tells the user which site it is for.
RP_NAME = "Sheaf"

# How long a ceremony may take between "begin" and "complete". Long enough
# for someone to find the right key and touch it, or work through a phone's
# cross-device flow; short enough that an abandoned begin does not sit in
# Redis for long.
CHALLENGE_TTL_SECONDS = 300

_REGISTRATION_KEY_PREFIX = "sheaf:passkey:reg:"


class PasskeyCeremonyError(Exception):
    """A ceremony response that did not verify. `reason` is a short stable
    label for the security event; `detail` is what the user is told."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _challenge_key(challenge: bytes) -> str:
    token = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode()
    return f"{_REGISTRATION_KEY_PREFIX}{token}"


async def store_registration_challenge(challenge: bytes, user_id: uuid.UUID) -> None:
    """Remember a freshly minted registration challenge for one account.

    Keyed by the challenge itself, so `complete` can look it up from the
    client data it receives, and holding the account id so the binding is
    checked on redemption. Fails closed on a Redis error.
    """
    from sheaf.auth.sessions import get_redis

    r = await get_redis()
    await r.set(_challenge_key(challenge), str(user_id), ex=CHALLENGE_TTL_SECONDS)


async def take_registration_challenge(challenge: bytes) -> uuid.UUID | None:
    """Consume a registration challenge, returning the account it was issued
    to, or None if it is unknown, expired, or already used.

    GETDEL, so two concurrent completions of one challenge cannot both
    succeed: exactly one of them sees the value.
    """
    from sheaf.auth.sessions import get_redis

    r = await get_redis()
    raw = await r.getdel(_challenge_key(challenge))
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class RegistrationStart:
    """What `begin` hands back: the options for the browser, and the
    challenge the server has to remember."""

    options_json: str
    challenge: bytes


def registration_options(
    *,
    rp: RelyingParty,
    user_id: uuid.UUID,
    user_name: str,
    user_display_name: str,
    exclude_credential_ids: list[bytes],
) -> RegistrationStart:
    """Mint the `navigator.credentials.create()` options for one enrolment.

    `exclude_credential_ids` is every credential the account already has, so
    an authenticator that is already enrolled answers with a clean "already
    registered" instead of silently minting a second key and, on a hardware
    token, burning a second resident-key slot. The user handle is the account
    id's raw bytes: stable for the account's life and never the email, which
    the spec says must not be used there.
    """
    options = generate_registration_options(
        rp_id=rp.rp_id,
        rp_name=RP_NAME,
        user_id=user_id.bytes,
        user_name=user_name,
        user_display_name=user_display_name,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            require_resident_key=True,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=cid) for cid in exclude_credential_ids
        ],
        timeout=CHALLENGE_TTL_SECONDS * 1000,
    )
    return RegistrationStart(
        options_json=options_to_json(options), challenge=bytes(options.challenge)
    )


def challenge_from_credential(credential: dict) -> bytes:
    """The challenge a registration response claims to answer, read from its
    client data. Needed before verification to look the challenge up."""
    try:
        parsed = parse_registration_credential_json(credential)
        client_data = parse_client_data_json(parsed.response.client_data_json)
    except WebAuthnException as exc:
        raise PasskeyCeremonyError("malformed", "That passkey response could not be read.") from exc
    return bytes(client_data.challenge)


@dataclass(frozen=True, slots=True)
class VerifiedEnrolment:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    aaguid: uuid.UUID | None
    backup_eligible: bool
    backup_state: bool
    transports: list[str]


def verify_enrolment(
    *, credential: dict, expected_challenge: bytes, rp: RelyingParty
) -> VerifiedEnrolment:
    """Verify a registration response against the challenge and this
    instance's relying party, requiring user verification.

    Origin and RP ID are the instance's own from the availability rule,
    never taken from the request: the whole point of the rule is that the
    server knows its own name.
    """
    try:
        parsed = parse_registration_credential_json(credential)
        verified = verify_registration_response(
            credential=parsed,
            expected_challenge=expected_challenge,
            expected_rp_id=rp.rp_id,
            expected_origin=rp.origin,
            require_user_presence=True,
            require_user_verification=True,
        )
    except WebAuthnException as exc:
        raise PasskeyCeremonyError(
            "verification_failed",
            "That passkey could not be verified. Try again from the beginning.",
        ) from exc

    aaguid: uuid.UUID | None = None
    if verified.aaguid:
        try:
            aaguid = uuid.UUID(str(verified.aaguid))
        except ValueError:
            aaguid = None
    if aaguid is not None and aaguid.int == 0:
        # The all-zero aaguid is "no information", not an authenticator model.
        aaguid = None

    transports = [
        t.value if hasattr(t, "value") else str(t)
        for t in (parsed.response.transports or [])
    ]
    return VerifiedEnrolment(
        credential_id=bytes(verified.credential_id),
        public_key=bytes(verified.credential_public_key),
        sign_count=int(verified.sign_count),
        aaguid=aaguid,
        backup_eligible=verified.credential_device_type.value == "multi_device",
        backup_state=bool(verified.credential_backed_up),
        transports=transports,
    )


def credential_id_from_b64url(value: str) -> bytes:
    return base64url_to_bytes(value)
