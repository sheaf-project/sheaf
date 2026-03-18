import hashlib

from cryptography.fernet import Fernet

from sheaf.config import settings

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(settings.get_encryption_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a string value. Returns a URL-safe base64 token."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a Fernet token back to plaintext."""
    return _get_fernet().decrypt(token.encode()).decode()


def blind_index(value: str) -> str:
    """Create a SHA-256 blind index for lookups on encrypted fields.

    The value is normalised (lowered, stripped) before hashing so that
    lookups are case-insensitive.
    """
    normalised = value.strip().lower()
    return hashlib.sha256(normalised.encode()).hexdigest()
