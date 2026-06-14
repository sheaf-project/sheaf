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


def route_template(request: Request) -> str:
    """Return the full matched route template, e.g. '/v1/members/{id}'.

    `request.scope["route"]` is set by Starlette's router after the route
    resolves. If we get here without a match (404), there's no template -
    collapse to a single label so URL scanners can't explode cardinality.

    Starlette 1.0 changed `route.path` to be relative to the outermost
    prefixed router: a route under `APIRouter(prefix="/v1")` reports
    "/members/{id}", not "/v1/members/{id}", and the dropped prefix is NOT
    moved into root_path. Left as-is that silently relabels every metric and
    rate-limit bucket without the "/v1" prefix. Reconstruct the full template
    by prepending the leading segments of the real request path that the
    relative template omits, keeping the path params templated so cardinality
    stays bounded.
    """
    route = request.scope.get("route")
    tmpl = getattr(route, "path", None)
    if not tmpl:
        return "<unmatched>"
    real = request.scope.get("path") or request.url.path
    real_segs = [s for s in real.split("/") if s]
    tmpl_segs = [s for s in tmpl.split("/") if s]
    missing = len(real_segs) - len(tmpl_segs)
    if missing > 0:
        return "/" + "/".join(real_segs[:missing]) + tmpl
    return tmpl


# Backwards-compatible private alias (kept so existing imports don't break).
_route_template = route_template


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
