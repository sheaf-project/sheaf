"""Application-level field encryption and keyed lookup hashes.

XChaCha20-Poly1305 via libsodium (PyNaCl) — 256-bit key, 192-bit nonce,
AEAD construction with no padding oracle surface.

Ciphertext format: base64(nonce + ciphertext + tag)

This module also owns two keyed hashes that are required for the app to
function:

- ``blind_index``: keyed HMAC-SHA-256 used to look up encrypted rows by
  plaintext (e.g. find a user by email at login). Keyed off the encryption
  key, so losing SHEAF_ENCRYPTION_KEY means nobody can log in.
- ``hash_mail_token``: keyed HMAC-SHA-256 used to store the DB-side form of
  password-reset and email-verification tokens. Keyed off JWT_SECRET_KEY.
"""

import base64
import hashlib
import hmac
import os

import nacl.secret

from sheaf.config import settings

_box: nacl.secret.SecretBox | None = None


def _get_box() -> nacl.secret.SecretBox:
    global _box
    if _box is None:
        raw_key = settings.get_encryption_key()
        # Derive 32-byte key uniformly from whatever the user provided
        derived = hashlib.sha256(raw_key).digest()
        _box = nacl.secret.SecretBox(derived)
    return _box


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns URL-safe base64 token."""
    box = _get_box()
    nonce = os.urandom(nacl.secret.SecretBox.NONCE_SIZE)
    ct = box.encrypt(plaintext.encode(), nonce)
    return base64.urlsafe_b64encode(ct).decode()


def decrypt(token: str) -> str:
    """Decrypt a token back to plaintext."""
    box = _get_box()
    raw = base64.urlsafe_b64decode(token)
    return box.decrypt(raw).decode()


def decrypt_field(token: str, field: str) -> str:
    """`decrypt()` wrapper that bumps the decrypt-failure metric on error.

    `field` labels the failure so dashboards can answer "which field is
    drifting?" — should always be zero; non-zero indicates encryption-key
    drift or storage corruption. Re-raises the original exception so
    callers behave identically to plain `decrypt()`.
    """
    try:
        return decrypt(token)
    except Exception:
        # Import here to avoid an import cycle (crypto is imported very
        # early in the bootstrap path before observability is ready).
        from sheaf.observability.metrics import decrypt_failures_total
        decrypt_failures_total.labels(field=field).inc()
        raise


_blind_index_key_cache: bytes | None = None


def _blind_index_key() -> bytes:
    """Derive a dedicated HMAC key for blind indexes from the encryption key.

    Domain-separated via a fixed label so the same encryption key can produce
    unrelated subkeys for different purposes. Cached after first computation.
    """
    global _blind_index_key_cache
    if _blind_index_key_cache is None:
        raw_key = settings.get_encryption_key()
        _blind_index_key_cache = hmac.new(
            b"sheaf-blind-index-v1", raw_key, hashlib.sha256,
        ).digest()
    return _blind_index_key_cache


def blind_index(value: str) -> str:
    """Keyed HMAC-SHA-256 blind index for lookups on encrypted fields.

    Normalised (lowered, stripped) before hashing for case-insensitive lookups.
    Keyed so an attacker with a DB dump can't precompute a rainbow table over
    known email lists to reverse any `email_hash` row — they'd also need the
    in-memory encryption key.
    """
    normalised = value.strip().lower()
    return hmac.new(
        _blind_index_key(), normalised.encode(), hashlib.sha256,
    ).hexdigest()


def hash_mail_token(token: str) -> str:
    """Keyed HMAC-SHA-256 hash of a mail-delivered token for DB storage.

    Used for the password-reset and email-verification tokens. Plain
    hashlib.sha256 would let an attacker with a DB dump verify guessed
    plaintexts offline; HMAC under the JWT secret ties verification to
    the running app's in-memory key.
    """
    key = settings.jwt_secret_key.encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()
