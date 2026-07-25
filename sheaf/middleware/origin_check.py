"""CSRF defence: Origin validation on cookie-authenticated mutations.

Cookie auth is ambient authority - the browser attaches `sheaf_session` /
`sheaf_refresh` to any request aimed at this host, including ones a hostile
page initiates. SameSite=Lax (already set on the cookies) blocks most
cross-site sends but deliberately exempts top-level POST navigations, which
is exactly the classic CSRF shape (auto-submitting form). This middleware
closes that gap.

Policy, deliberately minimal:

- Only unsafe methods (POST/PUT/PATCH/DELETE) are checked, and only when
  the request carries one of our auth cookies. Bearer-token requests are
  not CSRF-able (no ambient credential) and pass untouched, so API keys,
  scripts, and the mobile apps never see this check.
- Browsers attach an Origin header to every unsafe request. If one is
  present it must match the request's own Host, the configured
  SHEAF_BASE_URL, or an entry in CSRF_TRUSTED_ORIGINS. `Origin: null`
  (sandboxed iframes, some privacy modes' cross-site sends) is rejected
  when cookies ride along.
- No Origin header at all passes: non-browser clients don't send one and
  can't be CSRF'd into attaching cookies in the first place.

Comparison is by netloc (host[:port]), case-insensitive, scheme ignored -
TLS termination at a reverse proxy makes the scheme an unreliable signal
server-side, and the host is the part an attacker's origin can't fake.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from sheaf.config import settings
from sheaf.request import client_ip

logger = logging.getLogger("sheaf")

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_AUTH_COOKIES = ("sheaf_session", "sheaf_refresh")


def _allowed_netlocs(request: Request) -> set[str]:
    allowed: set[str] = set()
    host = request.headers.get("host", "").strip().lower()
    if host:
        allowed.add(host)
    if settings.sheaf_base_url:
        netloc = urlparse(settings.sheaf_base_url).netloc.strip().lower()
        if netloc:
            allowed.add(netloc)
    for entry in settings.csrf_trusted_origins.split(","):
        entry = entry.strip().lower()
        if not entry:
            continue
        netloc = urlparse(entry).netloc if "//" in entry else entry
        if netloc:
            allowed.add(netloc)
    return allowed


class OriginCheckMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in _UNSAFE_METHODS and any(
            c in request.cookies for c in _AUTH_COOKIES
        ):
            origin = request.headers.get("origin")
            if origin is not None:
                origin_netloc = urlparse(origin).netloc.strip().lower()
                if not origin_netloc or origin_netloc not in _allowed_netlocs(
                    request
                ):
                    # CSRF tripwire: a cookie-authed unsafe request from an
                    # origin we don't trust (or `Origin: null`). A burst is an
                    # active attack signal or a misconfigured legit origin;
                    # either way it was invisible before. Origin/host are not
                    # secrets.
                    logger.warning(
                        "cross-origin request rejected (CSRF guard): "
                        "method=%s path=%s origin=%s host=%s ip=%s",
                        request.method,
                        request.url.path,
                        origin,
                        request.headers.get("host"),
                        client_ip(request),
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "detail": (
                                "Cross-origin request rejected. If this "
                                "instance is legitimately served from "
                                "multiple origins, add them to "
                                "CSRF_TRUSTED_ORIGINS."
                            )
                        },
                    )
        return await call_next(request)
