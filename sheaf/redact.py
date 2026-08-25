"""Redaction helpers for log lines.

Email addresses are encrypted at rest, so writing them in plaintext to
application logs (journald, container stdout, log shippers) reopens the
exposure the encryption was meant to close. `redact_email` keeps just
enough to be operationally useful - the domain, for spotting a bouncing
provider, and the first/last local-part character to tell two addresses
apart at a glance - without dumping the full address.
"""

from __future__ import annotations

import logging
import re

_SHARED_LINK_PATH = re.compile(r"((?:/(?:v1/)?public/shared|/s)/)[^/?\s]+")

# The anonymous public-file route keys blobs as
# ``/v1/public/files/{prefix}/{owner_id}/{uuid}.{ext}?token=...&expires=...``.
# A single access-log line for it leaks two secrets at once: the owner's
# account id sits in the path and deanonymises the profile, and a live HMAC
# capability rides in the query - a working bearer credential for the blob,
# every bit as sensitive as a share token. Redact the owner segment while
# keeping the prefix (avatars/bios/banners) and the random filename, which are
# harmless and useful for triage.
_PUBLIC_FILE_OWNER = re.compile(r"(/(?:v1/)?public/files/[^/?\s]+/)[^/?\s]+")

# Strip the signed-URL bearer token wherever it appears in a query string. The
# public-file route above is the reason this exists, but the internal signed
# serve route (/v1/files/...?token=...) carries the same live capability, so a
# blanket rule closes both. ``expires`` is only a timestamp and stays put.
_SIGNED_URL_TOKEN = re.compile(r"([?&]token=)[^&\s]+")


def redact_email(addr: str | None) -> str:
    """Mask an email address for logging.

    `alice@example.com` -> `a***e@example.com`. Short or malformed local
    parts collapse to a single `*`; anything without an `@` becomes
    `<redacted>` so a stray value can't leak verbatim.
    """
    if not addr or "@" not in addr:
        return "<redacted>"
    local, _, domain = addr.partition("@")
    if not domain:
        return "<redacted>"
    masked = (
        "*"
        if len(local) <= 2
        else f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}"
    )
    return f"{masked}@{domain}"


def redact_share_token_path(path: str) -> str:
    """Strip public-profile bearer tokens and owner ids from an HTTP path.

    Covers three leaks: the share-link token in ``/s/`` and
    ``/v1/public/shared/`` paths, the owner id embedded in a
    ``/v1/public/files/`` path, and the signed-URL ``token`` query value.
    """
    path = _SHARED_LINK_PATH.sub(r"\1<redacted>", path)
    path = _PUBLIC_FILE_OWNER.sub(r"\1<redacted>", path)
    path = _SIGNED_URL_TOKEN.sub(r"\1<redacted>", path)
    return path


class ShareTokenAccessLogFilter(logging.Filter):
    """Redact the path argument emitted by Uvicorn's access logger."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn's access tuple is (client, method, path, version, status).
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            redacted = list(args)
            redacted[2] = redact_share_token_path(args[2])
            record.args = tuple(redacted)
        return True


def install_access_log_redaction() -> None:
    """Install the Uvicorn filter once, including under repeated test imports."""
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, ShareTokenAccessLogFilter) for f in access_logger.filters):
        access_logger.addFilter(ShareTokenAccessLogFilter())
