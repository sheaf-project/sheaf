"""File URL signing and resolution utilities."""

import hashlib
import hmac
import time

from sheaf.config import settings


def _signing_key() -> bytes:
    """Derive a file-specific HMAC key from the JWT secret to avoid cross-use."""
    return hmac.new(
        settings.jwt_secret_key.encode(),
        b"sheaf-file-signing",
        hashlib.sha256,
    ).digest()


def sign_file_url(key: str) -> str:
    """Generate a stable signed serve URL for an internal file key.

    Uses window-based expiry: all requests within the same time window produce
    the same URL, keeping URLs stable for browser image caching.
    The URL is always valid for at least one full window (up to two windows
    at the start of a window).
    """
    window = settings.file_url_expiry_seconds
    window_start = (int(time.time()) // window) * window
    expires_at = window_start + 2 * window
    msg = f"{key}:{expires_at}".encode()
    token = hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()
    return f"/v1/files/{key}?token={token}&expires={expires_at}"


def verify_file_token(key: str, token: str, expires: str) -> bool:
    """Verify a signed file URL token. Returns False if invalid or expired."""
    try:
        expires_at = int(expires)
    except ValueError:
        return False
    if time.time() > expires_at:
        return False
    msg = f"{key}:{expires_at}".encode()
    expected = hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, token)


def resolve_avatar_url(url: str | None) -> str | None:
    """Convert a stored avatar_url (key or external URL) to a displayable URL.

    Priority:
    1. None → None
    2. External URL (starts with http) → returned as-is
    3. S3 + s3_public_url set → {cdn_url}/{key}  (CDN serves directly from S3)
    4. image_serving=signed → /v1/files/{key}?token=…  (HMAC gated)
    5. image_serving=unsigned → /v1/files/{key}  (open access)
    """
    if url is None:
        return None
    if url.startswith("http"):
        return url
    key = url.removeprefix("/v1/files/")
    if settings.storage_backend == "s3" and settings.s3_public_url:
        return f"{settings.s3_public_url.rstrip('/')}/{key}"
    if settings.image_serving == "signed":
        return sign_file_url(key)
    return f"/v1/files/{key}"
