"""A software WebAuthn authenticator for tests.

Just enough of an authenticator to drive the registration ceremony end to
end without a browser or hardware: a P-256 key pair, a credential id, and
the ability to answer a `PublicKeyCredentialCreationOptions` with the exact
JSON `PublicKeyCredential.toJSON()` would produce ("none" attestation). Every
byte the server checks is constructible here on purpose, so the negative
tests can flip one thing at a time: the user-verification flag, the origin,
the RP ID hash, the challenge.

Sign-in (assertion) support is added when that ceremony lands.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from dataclasses import dataclass, field

import cbor2
from cryptography.hazmat.primitives.asymmetric import ec

# Authenticator data flag bits (WebAuthn 6.1).
FLAG_UP = 0x01  # user present
FLAG_UV = 0x04  # user verified
FLAG_AT = 0x40  # attested credential data included
FLAG_BE = 0x08  # backup eligible
FLAG_BS = 0x10  # backup state


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass
class SoftwareAuthenticator:
    """One credential's worth of authenticator. Each instance is a distinct
    key, so two of them enrolled on one account are two passkeys."""

    private_key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    credential_id: bytes = field(default_factory=lambda: os.urandom(32))
    aaguid: bytes = field(default_factory=lambda: b"\x00" * 16)
    sign_count: int = 0
    transports: list[str] = field(default_factory=lambda: ["usb"])

    def cose_public_key(self) -> bytes:
        numbers = self.private_key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def authenticator_data(
        self,
        rp_id: str,
        *,
        user_verified: bool = True,
        backup_eligible: bool = False,
        backup_state: bool = False,
        include_credential: bool = True,
    ) -> bytes:
        flags = FLAG_UP
        if user_verified:
            flags |= FLAG_UV
        if backup_eligible:
            flags |= FLAG_BE
        if backup_state:
            flags |= FLAG_BS
        if include_credential:
            flags |= FLAG_AT
        data = hashlib.sha256(rp_id.encode()).digest()
        data += bytes([flags])
        data += struct.pack(">I", self.sign_count)
        if include_credential:
            data += self.aaguid
            data += struct.pack(">H", len(self.credential_id))
            data += self.credential_id
            data += self.cose_public_key()
        return data

    def create(
        self,
        options: dict,
        *,
        origin: str | None = None,
        rp_id: str | None = None,
        challenge: str | None = None,
        user_verified: bool = True,
        backup_eligible: bool = False,
        backup_state: bool = False,
    ) -> dict:
        """Answer creation options the way a browser would.

        `options` is the `publicKey` object as the server returned it. The
        keyword overrides exist for the negative tests: by default the
        response is honest about origin, RP ID and challenge.
        """
        rp_id = rp_id or options["rp"]["id"]
        origin = origin or self._origin_for(rp_id)
        challenge = challenge or options["challenge"]
        client_data = json.dumps(
            {
                "type": "webauthn.create",
                "challenge": challenge,
                "origin": origin,
                "crossOrigin": False,
            }
        ).encode()
        auth_data = self.authenticator_data(
            rp_id,
            user_verified=user_verified,
            backup_eligible=backup_eligible,
            backup_state=backup_state,
        )
        attestation_object = cbor2.dumps(
            {"fmt": "none", "attStmt": {}, "authData": auth_data}
        )
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "cross-platform",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation_object),
                "transports": list(self.transports),
            },
            "clientExtensionResults": {},
        }

    @staticmethod
    def _origin_for(rp_id: str) -> str:
        # The tests that do not pass an explicit origin run against the
        # test stack, whose base URL is http://localhost:<port>. Callers
        # that need the real one pass it; this default only exists so the
        # pure unit tests read naturally.
        return f"https://{rp_id}"
