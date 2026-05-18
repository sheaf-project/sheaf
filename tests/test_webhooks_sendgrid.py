"""Unit tests for the SendGrid Signed Event Webhook verification helpers.

These exercise the crypto directly (no running server): generate an
EC P-256 key pair, sign payloads the way SendGrid does, and assert the
verifier accepts genuine signatures and rejects everything else.
"""

import base64
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from sheaf.api.v1.webhooks import _timestamp_within_skew, _verify_sendgrid_signature


def _new_keypair() -> tuple[ec.EllipticCurvePrivateKey, str]:
    """Return (private_key, base64-DER-SPKI public key) on the P-256 curve
    SendGrid uses."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_der = private_key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    return private_key, base64.b64encode(public_der).decode()


def _sign(private_key: ec.EllipticCurvePrivateKey, timestamp: str, body: bytes) -> str:
    """Produce a SendGrid-style base64 DER ECDSA signature over timestamp+body."""
    signature = private_key.sign(
        timestamp.encode() + body, ec.ECDSA(hashes.SHA256())
    )
    return base64.b64encode(signature).decode()


# --- _verify_sendgrid_signature -------------------------------------------


def test_genuine_signature_accepted():
    private_key, public_key_b64 = _new_keypair()
    timestamp = "1700000000"
    body = b'[{"event":"bounce","email":"x@example.com"}]'
    signature = _sign(private_key, timestamp, body)

    assert _verify_sendgrid_signature(public_key_b64, timestamp, body, signature)


def test_tampered_body_rejected():
    private_key, public_key_b64 = _new_keypair()
    timestamp = "1700000000"
    signature = _sign(private_key, timestamp, b'[{"event":"bounce"}]')

    assert not _verify_sendgrid_signature(
        public_key_b64, timestamp, b'[{"event":"delivered"}]', signature
    )


def test_tampered_timestamp_rejected():
    """The timestamp is part of the signed payload — changing it post-sign
    invalidates the signature even if the body is untouched."""
    private_key, public_key_b64 = _new_keypair()
    body = b'[{"event":"bounce"}]'
    signature = _sign(private_key, "1700000000", body)

    assert not _verify_sendgrid_signature(
        public_key_b64, "1700009999", body, signature
    )


def test_signature_from_other_key_rejected():
    """A signature made with a different private key must not verify against
    our configured public key."""
    attacker_key, _ = _new_keypair()
    _, our_public_key_b64 = _new_keypair()
    timestamp = "1700000000"
    body = b'[{"event":"bounce"}]'
    forged = _sign(attacker_key, timestamp, body)

    assert not _verify_sendgrid_signature(
        our_public_key_b64, timestamp, body, forged
    )


def test_garbage_signature_rejected():
    _, public_key_b64 = _new_keypair()
    assert not _verify_sendgrid_signature(
        public_key_b64, "1700000000", b"body", "not-base64-!!!"
    )
    # Well-formed base64 that isn't a valid DER ECDSA signature.
    assert not _verify_sendgrid_signature(
        public_key_b64, "1700000000", b"body", base64.b64encode(b"junk").decode()
    )


def test_malformed_public_key_returns_false_not_crash():
    private_key, _ = _new_keypair()
    signature = _sign(private_key, "1700000000", b"body")
    # Empty and non-key inputs must be handled gracefully.
    assert not _verify_sendgrid_signature("", "1700000000", b"body", signature)
    assert not _verify_sendgrid_signature(
        base64.b64encode(b"not a key").decode(), "1700000000", b"body", signature
    )


# --- _timestamp_within_skew ------------------------------------------------


def test_fresh_timestamp_within_skew():
    assert _timestamp_within_skew(str(int(time.time())), 600)


def test_stale_timestamp_rejected():
    old = str(int(time.time()) - 5000)
    assert not _timestamp_within_skew(old, 600)


def test_future_timestamp_rejected():
    future = str(int(time.time()) + 5000)
    assert not _timestamp_within_skew(future, 600)


def test_non_numeric_timestamp_rejected():
    assert not _timestamp_within_skew("not-a-timestamp", 600)
    assert not _timestamp_within_skew("", 600)
