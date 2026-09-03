"""Protective headers for the anonymous share surface, on every response.

The public-profile handlers set `X-Robots-Tag` and a `Cache-Control` on the
success path, but a response built anywhere else - the global exception
handlers in `sheaf/main.py`, the rate limiter's 429, anything a future error
path adds - is a fresh `JSONResponse` that never sees them. A 404 for a revoked
token is a heuristically cacheable status, so that gap meant a shared cache
could hold "this link is dead" (or the reverse, once re-issued) keyed by a URL
that carries a bearer capability.

Stamping it here rather than in each handler is the point: nothing new has to
remember. Two rules, both fail-safe:

- `X-Robots-Tag` is filled in when missing. Link sharing, not search presence.
- `Cache-Control` is filled in when missing, with `private, no-store` - the
  conservative choice for a response nobody deliberately gave a TTL, and never
  an override of a route that asked for something stricter.
- Under the token-addressed paths a response may not be shared-cacheable at
  all, so an existing header that says neither `private` nor `no-store` gains
  `private` (and loses `public`). The token IS the authorisation, so an
  intermediary holding that response would both keep serving it after the link
  is rotated and park the token in someone else's storage.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders

# The anonymous surface, and the subset of it addressed by a raw link token.
# Pinned against the real routes by tests/test_public_share_headers.py.
_PUBLIC_PREFIX = "/v1/public/"
_TOKEN_PREFIX = "/v1/public/shared/"

_ROBOTS = "noindex, nofollow"
_FALLBACK_CACHE = "private, no-store"


def _force_unshared(value: str) -> str:
    """Return `value` with a shared cache ruled out, keeping its other terms."""
    directives = [d.strip() for d in value.split(",") if d.strip()]
    kept = [d for d in directives if d.lower() != "public"]
    return ", ".join(["private", *kept])


class PublicShareHeadersMiddleware:
    """Pure-ASGI stamp of the share surface's protective headers."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith(
            _PUBLIC_PREFIX
        ):
            await self.app(scope, receive, send)
            return

        token_keyed = scope["path"].startswith(_TOKEN_PREFIX)

        async def wrapped_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "x-robots-tag" not in headers:
                    headers["x-robots-tag"] = _ROBOTS
                cache = headers.get("cache-control")
                if cache is None:
                    headers["cache-control"] = _FALLBACK_CACHE
                elif token_keyed and not any(
                    d in cache.lower() for d in ("private", "no-store")
                ):
                    headers["cache-control"] = _force_unshared(cache)
            await send(message)

        await self.app(scope, receive, wrapped_send)
