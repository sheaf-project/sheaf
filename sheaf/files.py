"""File URL signing and resolution utilities."""

import hashlib
import hmac
import ipaddress
import re
import time
from urllib.parse import urlparse

from sheaf.config import settings

# Matches /v1/files/ URLs in markdown image syntax, with optional query params
_MD_FILE_URL_RE = re.compile(r"(/v1/files/[^)?\s]+)(\?[^)\s]*)?")

# Matches all markdown image URLs: ![alt](url)
_MD_IMAGE_URL_RE = re.compile(r"(!\[[^\]]*\]\()([^)]+)(\))")

# Private/internal IP ranges that should never be fetched
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # AWS metadata, link-local
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # IPv6 private
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]


def _is_safe_external_url(url: str) -> bool:
    """Check if an external URL is safe to embed. Rejects internal IPs and non-HTTPS."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme != "https":
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    # Reject localhost variants
    if hostname in ("localhost", "metadata.google.internal"):
        return False

    # Try to parse as IP and check against private ranges
    try:
        addr = ipaddress.ip_address(hostname)
        for network in _PRIVATE_NETWORKS:
            if addr in network:
                return False
    except ValueError:
        pass  # It's a hostname, not an IP — that's fine

    # Reject non-standard ports
    return not (parsed.port and parsed.port not in (443,))



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


def normalize_avatar_url(url: str | None) -> str | None:
    """Strip a signed/serve URL back to a bare storage key for DB storage.

    Handles:
    - /v1/files/avatars/user_id/uuid.png?token=...&expires=... → avatars/user_id/uuid.png
    - /v1/files/bios/user_id/uuid.png?token=...&expires=...   → bios/user_id/uuid.png
    - avatars/user_id/uuid.png → avatars/user_id/uuid.png  (already bare)
    - https://example.com/img.png → https://example.com/img.png  (external, unchanged)
    - None → None
    """
    if url is None:
        return None
    if url.startswith("http"):
        return url
    # Strip /v1/files/ prefix if present
    key = url.removeprefix("/v1/files/")
    # Strip query params (token, expires)
    if "?" in key:
        key = key.split("?", 1)[0]
    return key


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


def resolve_description_urls(text: str | None) -> str | None:
    """Sign all /v1/files/ URLs in markdown description text for display."""
    if text is None:
        return None

    def _replace(m: re.Match) -> str:
        bare_path = m.group(1)  # /v1/files/{key}
        key = bare_path.removeprefix("/v1/files/")
        resolved = resolve_avatar_url(key)
        return resolved or bare_path

    return _MD_FILE_URL_RE.sub(_replace, text)


def normalize_description_urls(text: str | None) -> str | None:
    """Normalize URLs in markdown descriptions for safe DB storage.

    1. Strip signed query params from /v1/files/ URLs
    2. Validate external image URLs (must be HTTPS, no internal IPs)
    3. Remove unsafe external image references
    """
    if text is None:
        return None

    # Strip signed query params from hosted file URLs
    def _strip_signed(m: re.Match) -> str:
        bare_path = m.group(1)
        query = m.group(2) or ""
        if query:
            return bare_path
        return bare_path + query

    text = _MD_FILE_URL_RE.sub(_strip_signed, text)

    # Validate external image URLs
    def _validate_image(m: re.Match) -> str:
        prefix = m.group(1)   # ![alt](
        url = m.group(2)      # the URL
        suffix = m.group(3)   # )

        # Hosted images are fine
        if url.startswith("/v1/files/") or url.startswith("/"):
            return prefix + url + suffix

        # External URLs must pass validation
        if url.startswith("http"):
            if _is_safe_external_url(url):
                return prefix + url + suffix
            # Unsafe URL — remove the image reference
            return ""

        return prefix + url + suffix

    return _MD_IMAGE_URL_RE.sub(_validate_image, text)
