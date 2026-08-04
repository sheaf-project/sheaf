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
    """HMAC key for signed file URLs.

    If FILE_SIGNING_KEY is set, use it directly (raw UTF-8 bytes). This is
    required for the signed + CDN paradigm, where a Cloudflare Worker must
    share the same key to validate URLs at the edge.

    Otherwise, derive from the JWT secret — safe for the non-CDN paradigms
    where the key never leaves the app.
    """
    if settings.file_signing_key:
        return settings.file_signing_key.encode()
    return hmac.new(
        settings.jwt_secret_key.encode(),
        b"sheaf-file-signing",
        hashlib.sha256,
    ).digest()


def _signed_url_params(key: str) -> tuple[str, int]:
    """Return (token, expires_at) for a key using the current signing window."""
    window = settings.file_url_expiry_seconds
    window_start = (int(time.time()) // window) * window
    expires_at = window_start + 2 * window
    msg = f"{key}:{expires_at}".encode()
    token = hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()
    return token, expires_at


def sign_file_url(key: str) -> str:
    """Generate a stable signed serve URL for an internal file key.

    Uses window-based expiry: all requests within the same time window produce
    the same URL, keeping URLs stable for browser image caching.
    The URL is always valid for at least one full window (up to two windows
    at the start of a window).
    """
    token, expires_at = _signed_url_params(key)
    return f"/v1/files/{key}?token={token}&expires={expires_at}"


def sign_cdn_url(key: str) -> str:
    """Generate a signed URL on the CDN hostname (s3_public_url).

    Used for the signed + CDN paradigm where a Cloudflare Worker sitting
    on that hostname validates the same HMAC and fetches the private
    bucket object directly. See selfhost-utils/cf-image-worker/.
    """
    token, expires_at = _signed_url_params(key)
    base = settings.s3_public_url.rstrip("/")
    return f"{base}/{key}?token={token}&expires={expires_at}"


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


def _to_internal_key(url: str) -> str | None:
    """If `url` points at our own storage, return the bare key; else None.

    "Our own storage" means either the app serve path (/v1/files/...) or the
    configured CDN hostname (settings.s3_public_url). Query params are
    stripped. Bare keys (no scheme, no leading slash) are returned as-is.
    Anything else — Gravatar, avatars.dicebear.com, a user-typed URL — is
    treated as external and returns None so callers can pass it through.
    """
    if url.startswith("/v1/files/"):
        return url.removeprefix("/v1/files/").split("?", 1)[0]
    if settings.s3_public_url:
        base = settings.s3_public_url.rstrip("/") + "/"
        if url.startswith(base):
            return url.removeprefix(base).split("?", 1)[0]
    if url.startswith(("http://", "https://", "/")):
        return None
    # Already a bare key (e.g. "avatars/…/uuid.png"). Strip any query params
    # defensively.
    return url.split("?", 1)[0]


def normalize_avatar_url(url: str | None) -> str | None:
    """Strip a signed/serve/CDN URL back to a bare storage key for DB storage.

    Handles:
    - /v1/files/avatars/user_id/uuid.png?token=... → avatars/user_id/uuid.png
    - https://{s3_public_url}/avatars/user_id/uuid.png?token=... → avatars/user_id/uuid.png
    - avatars/user_id/uuid.png → avatars/user_id/uuid.png  (already bare)
    - https://gravatar.com/img.png → https://gravatar.com/img.png  (external, unchanged)
    - None → None

    When allow_external_images is disabled, external URLs are dropped to None
    so an instance policy change doesn't leak through to new writes.
    """
    if url is None:
        return None
    key = _to_internal_key(url)
    if key is not None:
        return key
    if not settings.allow_external_images:
        return None
    return url


def resolve_avatar_url(url: str | None) -> str | None:
    """Convert a stored avatar_url (key or external URL) to a displayable URL.

    Priority:
    1. None → None
    2. External URL (not ours) → returned as-is
    3. Internal URL or bare key, S3 + s3_public_url + signed → {cdn}/{key}?token=…
    4. Internal URL or bare key, S3 + s3_public_url + unsigned → {cdn}/{key}
    5. Internal URL or bare key, image_serving=signed → /v1/files/{key}?token=…
    6. Internal URL or bare key, image_serving=unsigned → /v1/files/{key}

    Recognising our own CDN hostname matters for DB rows written before this
    code landed: they store the full CDN URL, and we want them signed on
    read just the same as bare keys.
    """
    if url is None:
        return None
    key = _to_internal_key(url)
    if key is None:
        return url
    if settings.storage_backend == "s3" and settings.s3_public_url:
        if settings.image_serving == "signed":
            return sign_cdn_url(key)
        return f"{settings.s3_public_url.rstrip('/')}/{key}"
    if settings.image_serving == "signed":
        return sign_file_url(key)
    return f"/v1/files/{key}"


def resolve_description_urls(text: str | None) -> str | None:
    """Sign image URLs in markdown descriptions for display.

    Handles all three forms an internal reference can take:
      1. /v1/files/{key} (the canonical storage form)
      2. {s3_public_url}/{key} (legacy rows written before the CDN fix)
      3. bare key (unusual, but _to_internal_key accepts it)

    Anything _to_internal_key doesn't recognise is left untouched.
    """
    if text is None:
        return None

    def _replace(m: re.Match) -> str:
        prefix, url, suffix = m.group(1), m.group(2), m.group(3)
        key = _to_internal_key(url)
        if key is None:
            return prefix + url + suffix
        resolved = resolve_avatar_url(key)
        return f"{prefix}{resolved or '/v1/files/' + key}{suffix}"

    return _MD_IMAGE_URL_RE.sub(_replace, text)


# What an external image URL becomes on the anonymous public surface. A
# fragment never triggers a fetch, so a client that does not special-case the
# sentinel shows alt text or a broken image - the fail-closed direction. The
# web app renders it as a neutral "external image" chip.
EXTERNAL_IMAGE_HIDDEN = "#external-image-hidden"

# Reference-style images: ![alt][ref], the collapsed ![alt][], and the shortcut
# ![alt] paired with a link-reference definition further down the text.
_MD_REF_IMAGE_RE = re.compile(r"(!\[[^\]]*\])(\[[^\]]*\])")
_MD_REF_IMAGE_FULL_RE = re.compile(r"!\[([^\]]*)\]\[([^\]]*)\]")
_MD_SHORTCUT_IMAGE_RE = re.compile(r"!\[([^\]]*)\](?![\[(])")
_MD_LINK_DEFINITION_RE = re.compile(r"(?m)^ {0,3}\[([^\]]+)\]:")
# Definition with its target: `[label]: <url> "optional title"`.
_MD_LINK_DEFINITION_URL_RE = re.compile(r"(?m)^ {0,3}\[([^\]]+)\]:\s*<?([^\s>]+)>?")


def _hide_reference_images(text: str) -> str:
    """Rewrite reference-style markdown images to the hidden sentinel.

    Their target lives in a link-reference definition that a plain link may
    share, so the URL cannot be rewritten in isolation and the image itself is
    hidden instead. A reference image pointing at one of our own files is
    hidden too: reference syntax never comes out of the image picker, and
    hiding a hosted image is the direction that fails safe.
    """
    text = _MD_REF_IMAGE_RE.sub(rf"\1({EXTERNAL_IMAGE_HIDDEN})", text)
    labels = {m.casefold() for m in _MD_LINK_DEFINITION_RE.findall(text)}
    if not labels:
        return text

    def _shortcut(m: re.Match) -> str:
        if m.group(1).casefold() not in labels:
            return m.group(0)  # no definition: markdown renders it as text
        return f"![{m.group(1)}]({EXTERNAL_IMAGE_HIDDEN})"

    return _MD_SHORTCUT_IMAGE_RE.sub(_shortcut, text)


def resolve_description_urls_public(text: str | None) -> str | None:
    """resolve_description_urls for the anonymous public surface.

    Internal refs resolve to signed URLs exactly as the private variant does.
    Every other image URL is replaced with EXTERNAL_IMAGE_HIDDEN, because the
    browser that would fetch it belongs to a VISITOR who consented to nothing:
    rendering it hands an owner-chosen host that visitor's IP address, user
    agent, and a timestamp for every view of the page. The authenticated app
    still renders externals - that leak is the owner's own, and theirs to
    accept.

    data: URIs carry their own bytes and never touch the network, so they are
    passed through untouched. (The private variant does not special-case them,
    so they fall into _to_internal_key's bare-key branch and come back
    mangled; nothing depends on that, and the fix belongs with the write path.)
    """
    if text is None:
        return None

    def _replace(m: re.Match) -> str:
        prefix, url, suffix = m.group(1), m.group(2), m.group(3)
        if url.startswith("data:"):
            return prefix + url + suffix
        key = _to_internal_key(url)
        if key is None:
            return f"{prefix}{EXTERNAL_IMAGE_HIDDEN}{suffix}"
        resolved = resolve_avatar_url(key)
        return f"{prefix}{resolved or '/v1/files/' + key}{suffix}"

    return _hide_reference_images(_MD_IMAGE_URL_RE.sub(_replace, text))


def resolve_avatar_url_public(url: str | None) -> str | None:
    """resolve_avatar_url for the anonymous public surface.

    Internal keys resolve to a signed URL as usual; anything external returns
    None so the client falls back to initials. Same reasoning as
    resolve_description_urls_public: an avatar pointed at a third-party host
    makes every anonymous visitor's browser announce itself to that host.

    data: URIs are dropped rather than passed through. They cannot leak an
    address, but normalize_avatar_url has never been able to store one - it
    hands the value to _to_internal_key, which classifies it as a bare storage
    key - so there is no legitimate data: avatar to preserve, and initials beat
    signing a nonsense key.
    """
    if url is None or url.startswith("data:"):
        return None
    key = _to_internal_key(url)
    if key is None:
        return None
    return resolve_avatar_url(key)


# Prefixes our uploads write under: {prefix}/{user_id}/{uuid}.{ext}. The
# second path segment identifies the owning account, which is what the
# ownership filters below key off.
_MEDIA_KEY_PREFIXES = ("avatars", "bios", "banners")


def internal_key_owner(key: str) -> str | None:
    """Return the owner user-id embedded in an internal storage key.

    Uploads key every blob as ``{prefix}/{user_id}/{uuid}.{ext}`` (see
    files.upload_file), so the second path segment is the owning account.
    Returns None if `key` doesn't match that layout (e.g. an ``exports/`` key
    or anything malformed), which the callers treat as "not this user's".
    """
    parts = key.split("/")
    if len(parts) >= 3 and parts[0] in _MEDIA_KEY_PREFIXES:
        return parts[1]
    return None


def owned_avatar_url(value: str | None, owner_id: object) -> str | None:
    """Drop an internal storage key that isn't in `owner_id`'s namespace.

    None and external URLs pass through unchanged (externals are already
    gated by normalize_avatar_url). An internal key is kept only when its
    embedded owner segment matches `owner_id`.

    Without this, a caller could persist another account's key in their own
    avatar_url / banner_url. That key is re-signed into a live serve URL on
    every read (see MemberRead._sign_avatar_url), so it becomes an
    authorization oracle: a cross-tenant read of the other account's file,
    and a way to keep fetching it after the owner has un-shared or deleted
    the original. Enforced at the write handlers, which have the
    authenticated user (the schema validators that call normalize_* run
    without any request/user context).
    """
    if value is None:
        return None
    key = _to_internal_key(value)
    if key is None:
        return value  # external URL, left to normalize_avatar_url's gate
    if internal_key_owner(key) == str(owner_id):
        return key
    return None


def owned_description_urls(text: str | None, owner_id: object) -> str | None:
    """Strip embedded internal file refs not in `owner_id`'s namespace.

    The markdown twin of owned_avatar_url for bio / journal bodies: external
    refs and the surrounding prose are left untouched; an internal
    ``/v1/files/{key}`` (or bare-key / CDN) ref is dropped whole unless its
    key belongs to `owner_id`. Same oracle risk as owned_avatar_url.
    """
    if text is None:
        return None

    def _filter(m: re.Match) -> str:
        prefix, url, suffix = m.group(1), m.group(2), m.group(3)
        key = _to_internal_key(url)
        if key is None:
            return prefix + url + suffix  # external, leave as-is
        if internal_key_owner(key) == str(owner_id):
            return prefix + url + suffix
        return ""  # foreign internal ref: drop the whole image

    return _MD_IMAGE_URL_RE.sub(_filter, text)


def _expand_reference_images(text: str) -> str:
    """Rewrite reference-style markdown images into inline form.

    `![alt][ref]`, `![alt][]`, and shortcut `![alt]` resolve their URL from a
    link-reference definition elsewhere in the text, so a URL-policy pass that
    only looks inside `![...](...)` never sees it - reference syntax was a
    clean bypass of both the allow_external_images policy and the
    safe-external-URL validation. Expanding the image to inline form (the
    definition line itself is left alone - plain links may share it) puts
    every image URL in front of the policy in `normalize_description_urls`.

    An image whose label has no definition is left untouched: markdown
    renders it as literal text, which fetches nothing.

    The read-side counterpart for the anonymous surface is
    `_hide_reference_images`, which blanket-hides instead: stored rows from
    before this expansion existed can still carry reference images, so the
    public path must not trust the write path to have seen them.
    """
    defs = {
        label.casefold(): url
        for label, url in _MD_LINK_DEFINITION_URL_RE.findall(text)
    }
    if not defs:
        return text

    def _full(m: re.Match) -> str:
        label = (m.group(2) or m.group(1)).casefold()
        url = defs.get(label)
        if url is None:
            return m.group(0)
        return f"![{m.group(1)}]({url})"

    text = _MD_REF_IMAGE_FULL_RE.sub(_full, text)

    def _shortcut(m: re.Match) -> str:
        url = defs.get(m.group(1).casefold())
        if url is None:
            return m.group(0)
        return f"![{m.group(1)}]({url})"

    return _MD_SHORTCUT_IMAGE_RE.sub(_shortcut, text)


def normalize_description_urls(text: str | None) -> str | None:
    """Normalize markdown image URLs for safe DB storage.

    Our own files - referenced as /v1/files/{key}, {s3_public_url}/{key}, or
    a bare key - are canonicalised to /v1/files/{key} with any signed query
    params stripped. External URLs are validated (HTTPS, no internal IPs);
    unsafe ones and all externals under allow_external_images=False are
    stripped from the text. Reference-style images are expanded to inline
    form first so their URLs face the same policy instead of hiding in a
    link-reference definition.

    The CDN form matters: without recognising it, hosted bio images
    re-rendered with a CDN URL round-trip through the client and come back
    looking external — which then either silently deletes them (policy off)
    or preserves a stale signed URL that 404s once its token expires.
    """
    if text is None:
        return None

    text = _expand_reference_images(text)

    def _normalize(m: re.Match) -> str:
        prefix, url, suffix = m.group(1), m.group(2), m.group(3)

        key = _to_internal_key(url)
        if key is not None:
            return f"{prefix}/v1/files/{key}{suffix}"

        if url.startswith("http"):
            if not settings.allow_external_images:
                return ""
            if _is_safe_external_url(url):
                return prefix + url + suffix
            return ""

        return prefix + url + suffix

    return _MD_IMAGE_URL_RE.sub(_normalize, text)
