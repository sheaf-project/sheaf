"""Redaction helpers for log lines.

Email addresses are encrypted at rest, so writing them in plaintext to
application logs (journald, container stdout, log shippers) reopens the
exposure the encryption was meant to close. `redact_email` keeps just
enough to be operationally useful - the domain, for spotting a bouncing
provider, and the first/last local-part character to tell two addresses
apart at a glance - without dumping the full address.
"""

from __future__ import annotations


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
