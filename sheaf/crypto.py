"""Application-level field encryption.

XChaCha20-Poly1305 via libsodium (PyNaCl) — 256-bit key, 192-bit nonce,
AEAD construction with no padding oracle surface.

Ciphertext format: base64(nonce + ciphertext + tag)
"""

import base64
import hashlib
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


def blind_index(value: str) -> str:
    """SHA-256 blind index for lookups on encrypted fields.

    Normalised (lowered, stripped) before hashing for case-insensitive lookups.
    """
    normalised = value.strip().lower()
    return hashlib.sha256(normalised.encode()).hexdigest()
