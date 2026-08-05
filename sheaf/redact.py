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
    """Remove a public-profile bearer token from an HTTP path."""
    return _SHARED_LINK_PATH.sub(r"\1<redacted>", path)


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
