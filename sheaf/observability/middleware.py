"""HTTP RED middleware.

Emits per-request counters and a duration histogram, scoped by route
TEMPLATE (e.g. `/v1/members/{member_id}`) — never the raw URL — so
cardinality stays bounded. Status codes collapse to a class label
(`2xx`, `3xx`, ...) for the same reason.

Mounted in main.py after the body-size and rate-limit middlewares so
the timing it measures matches what the user actually experienced.
The `/metrics` route itself is excluded so scrape traffic doesn't
pollute its own metrics.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from sheaf.observability.metrics import (
    http_request_duration_seconds,
    http_requests_in_progress,
    http_requests_total,
)


def _status_class(status: int) -> str:
    return f"{status // 100}xx"


def _route_template(request: Request) -> str:
    """Return the Starlette route template, or '<unmatched>'.

    `request.scope["route"]` is set by Starlette's router after the
    route resolves. If we get here without a match (404), there's no
    template — collapse to a single label so URL scanners can't
    explode cardinality.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path or "<unmatched>"


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip our own scrape path so it doesn't appear in its own metrics.
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        in_progress = http_requests_in_progress.labels(method=method)
        in_progress.inc()
        start = time.perf_counter()
        try:
            response: Response = await call_next(request)
            status = response.status_code
            return response
        except Exception:
            # Counted as 5xx — the exception_handler in main.py will turn
            # it into a 500 response, but our middleware sees the raise.
            status = 500
            raise
        finally:
            elapsed = time.perf_counter() - start
            route = _route_template(request)
            http_requests_total.labels(
                method=method, route=route, status_class=_status_class(status),
            ).inc()
            http_request_duration_seconds.labels(method=method, route=route).observe(elapsed)
            in_progress.dec()
