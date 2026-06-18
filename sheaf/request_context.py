"""Request-scoped context for cross-cutting metadata.

A small contextvar carrying the resolved client IP for the current
request. Set once by the admin auth dependencies (which already receive
the Request and run in the endpoint's async task), then read by
`log_admin_action` so every admin audit row can record the originating
IP without threading `request` through ~two dozen call sites.

Deliberately set in a dependency, NOT in a BaseHTTPMiddleware: contextvar
writes inside Starlette's BaseHTTPMiddleware do not reliably propagate
down to the endpoint (it runs the handler in a separate anyio task), so
middleware-set contextvars read back as the default. Dependencies run in
the same task as the path operation, so the value is visible to the
endpoint and anything it awaits.
"""

from __future__ import annotations

import contextvars

_client_ip: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "client_ip", default=None
)
_user_agent: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "user_agent", default=None
)


def set_request_origin(ip: str | None, user_agent: str | None) -> None:
    _client_ip.set(ip)
    _user_agent.set(user_agent or None)


def get_client_ip() -> str | None:
    return _client_ip.get()


def get_user_agent() -> str | None:
    return _user_agent.get()
