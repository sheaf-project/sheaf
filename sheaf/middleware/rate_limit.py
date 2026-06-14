"""Redis-backed rate limiting for FastAPI.

Two mechanisms:
1. `rate_limit()` — dependency factory for per-endpoint limits.
2. `RateLimitMiddleware` — global per-IP backstop applied to all requests.

Redis key layout:
    sheaf:rl:{scope}:{identifier}:{window_start}
    e.g. sheaf:rl:ip:203.0.113.5:1711670400
         sheaf:rl:user:abc-def:1711670400

Each key is an integer counter with a TTL equal to the window size.

Blocked checks that can be attributed to an authenticated user are
additionally recorded to a per-user history list for admin abuse
triage (see record_user_hit / read_user_hit_history):
    sheaf:rlh:{user_id}
a capped LPUSH list of JSON entries with a retention TTL.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from sheaf.config import settings
from sheaf.observability.metrics import rate_limit_checks_total
from sheaf.observability.middleware import route_template

logger = logging.getLogger("sheaf.ratelimit")


# Map route templates to a stable bucket label. The set is intentionally
# small so dashboards stay legible; routes that don't match collapse to
# "other" (which is itself useful — a sudden spike there means a new
# rate-limited endpoint shipped without a bucket mapping).
def _route_to_bucket(route: str) -> str:
    if route.startswith("/v1/auth/"):
        suffix = route[len("/v1/auth/"):]
        if suffix == "login":
            return "login"
        if suffix == "register":
            return "register"
        if suffix in ("request-password-reset", "reset-password", "forgot-password"):
            return "password_reset"
        if suffix in ("verify-email", "resend-verification"):
            return "email_verification"
        if suffix.startswith("totp"):
            return "totp"
        if suffix in ("delete-account",):
            return "account_delete"
        if suffix.startswith("change-"):
            return "account_change"
        return "auth_other"
    if route.startswith("/v1/account/"):
        return "account_data"
    if route.startswith("/v1/files"):
        return "upload"
    if route.startswith("/v1/exports") or route.startswith("/v1/export"):
        return "export"
    if route.startswith("/v1/notifications/"):
        return "redeem"
    if route.startswith("/v1/webhooks/"):
        return "webhook"
    if route.startswith("/v1/admin/"):
        return "admin"
    return "other"


@dataclass(frozen=True, slots=True)
class Limit:
    """A rate limit: N requests per window_seconds."""
    requests: int
    window: int  # seconds


async def _get_redis():
    from sheaf.auth.sessions import get_redis
    return await get_redis()


def _client_ip(request: Request) -> str:
    """Extract client IP using the shared utility."""
    from sheaf.request import client_ip
    return client_ip(request)


async def _check_limit(
    redis,
    key: str,
    limit: Limit,
) -> tuple[bool, int, int]:
    """Check and increment a rate limit counter.

    Returns (allowed, remaining, reset_seconds).
    """
    now = int(time.time())
    window_start = now - (now % limit.window)
    redis_key = f"sheaf:rl:{key}:{window_start}"
    reset = window_start + limit.window - now

    pipe = redis.pipeline()
    pipe.incr(redis_key)
    pipe.expire(redis_key, limit.window + 1)  # +1s buffer
    results = await pipe.execute()

    current = results[0]
    remaining = max(0, limit.requests - current)
    allowed = current <= limit.requests

    return allowed, remaining, reset


def _rate_limit_headers(limit: Limit, remaining: int, reset: int) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": str(limit.requests),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset),
    }


# ---------------------------------------------------------------------------
# Per-user hit history - admin abuse-triage trail
# ---------------------------------------------------------------------------
#
# The Prometheus counters answer "is something being hammered"; this
# answers "what has THIS account tripped recently". Recording happens
# only on BLOCKED checks where the request carries an authenticated
# user id, so the volume is self-limiting (at most one entry per 429).
# Storage is a capped Redis list with a retention TTL - nothing here
# touches Postgres, and an idle key ages out on its own.

def _history_key(user_id) -> str:
    return f"sheaf:rlh:{user_id}"


async def record_user_hit(
    redis,
    user_id,
    *,
    bucket: str,
    scope: str,
    route: str,
    ip: str | None,
) -> None:
    """Append one blocked-check entry to the user's history list.

    Best-effort by contract: callers wrap this in try/except so a Redis
    hiccup can never turn a clean 429 into a 500. LTRIM bounds the list
    length; EXPIRE bounds idle lifetime. A user who keeps tripping
    refreshes the TTL, so the read side filters by timestamp too.
    """
    entry = json.dumps(
        {
            "t": int(time.time()),
            "bucket": bucket,
            "scope": scope,
            "route": route,
            "ip": ip,
        },
        separators=(",", ":"),
    )
    pipe = redis.pipeline()
    pipe.lpush(_history_key(user_id), entry)
    pipe.ltrim(_history_key(user_id), 0, settings.rate_limit_history_max_entries - 1)
    pipe.expire(_history_key(user_id), settings.rate_limit_history_hours * 3600)
    await pipe.execute()


async def delete_user_hit_history(user_id) -> None:
    """Remove a user's hit history outright (account deletion path)."""
    redis = await _get_redis()
    await redis.delete(_history_key(user_id))


async def read_user_hit_history(user_id) -> list[dict]:
    """Return the user's recorded hits, newest first.

    Entries older than the retention window are filtered out here
    rather than trusting the key TTL alone - a fresh hit refreshes the
    whole key's TTL, which would otherwise resurrect stale entries.
    Unparseable entries are dropped silently (format drift across
    deploys shouldn't 500 the admin panel).
    """
    redis = await _get_redis()
    raw = await redis.lrange(_history_key(user_id), 0, -1)
    cutoff = int(time.time()) - settings.rate_limit_history_hours * 3600
    out: list[dict] = []
    for item in raw:
        try:
            entry = json.loads(item)
        except (ValueError, TypeError):
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("t"), int):
            continue
        if entry["t"] < cutoff:
            continue
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Dependency factory — per-endpoint limits
# ---------------------------------------------------------------------------

def rate_limit(
    requests: int,
    window: int = 60,
    key: str = "ip",
    fail_closed: bool = False,
):
    """FastAPI dependency that enforces a rate limit on a single endpoint.

    Usage:
        @router.post("/register", dependencies=[rate_limit(3, 60)])
        @router.post("/upload", dependencies=[rate_limit(10, 60, "user")])

    Args:
        requests: Max requests allowed in the window.
        window: Window size in seconds (default 60).
        key: "ip" for per-IP or "user" for per-authenticated-user.
             "user" keys fall back to IP if auth hasn't resolved yet.
        fail_closed: When True, reject requests with 503 if Redis is
            unreachable. Use on auth endpoints so a Redis outage can't be
            used to bypass brute-force protection. Default False — most
            endpoints are better off staying available on Redis blips.
    """
    limit = Limit(requests=requests, window=window)

    async def _check(request: Request):
        if not settings.rate_limit_enabled:
            return

        try:
            r = await _get_redis()
        except Exception as exc:
            if fail_closed:
                logger.error(
                    "Redis unavailable on fail-closed endpoint: %s",
                    request.url.path,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Service temporarily unavailable",
                ) from exc
            logger.warning("Redis unavailable — skipping rate limit check")
            return

        # Build the identifier
        if key == "user":
            # request.state.user_id is set by get_current_user
            user_id = getattr(request.state, "user_id", None)
            identifier = (
                f"ip:{_client_ip(request)}" if user_id is None
                else f"user:{user_id}"
            )
        else:
            identifier = f"ip:{_client_ip(request)}"

        # Include the route path so limits are per-endpoint
        route = request.scope.get("path", request.url.path)
        redis_key = f"{identifier}:{route}"

        allowed, remaining, reset = await _check_limit(r, redis_key, limit)

        # Bucket is derived from the matched route template (with path params
        # left as placeholders) so per-instance path noise doesn't bloat label
        # cardinality. route_template restores the "/v1" prefix Starlette 1.0
        # drops from route.path.
        full_route = route_template(request)
        bucket = _route_to_bucket(full_route)
        scope_label = "per_user" if (key == "user" and identifier.startswith("user:")) else "per_ip"
        rate_limit_checks_total.labels(
            bucket=bucket,
            scope=scope_label,
            outcome="allowed" if allowed else "blocked",
        ).inc()

        if not allowed:
            # Attribute the hit to a user when we can. key="user" limits
            # always have one; key="ip" limits only when another dep
            # resolved auth first. Best-effort: never let the history
            # write break the 429 itself.
            hit_user_id = getattr(request.state, "user_id", None)
            if hit_user_id is not None and settings.rate_limit_history_enabled:
                try:
                    await record_user_hit(
                        r,
                        hit_user_id,
                        bucket=bucket,
                        scope=scope_label,
                        route=full_route,
                        ip=_client_ip(request),
                    )
                except Exception:
                    logger.warning(
                        "Failed to record rate-limit hit history",
                        exc_info=True,
                    )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Try again later.",
                headers={
                    **_rate_limit_headers(limit, remaining, reset),
                    "Retry-After": str(reset),
                },
            )

    if key == "user":
        # For per-user limits, we need auth to resolve first.
        # Depend on get_current_user so FastAPI orders them correctly.
        from sheaf.auth.dependencies import get_current_user

        async def _check_after_auth(
            request: Request,
            _user=Depends(get_current_user),
        ):
            return await _check(request)

        return Depends(_check_after_auth)

    return Depends(_check)


# ---------------------------------------------------------------------------
# Middleware — global per-IP backstop
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global per-IP rate limit applied to all requests.

    This is a coarse backstop — individual endpoints should use rate_limit()
    for tighter limits. The middleware catches broad abuse patterns (scanners,
    scripts hammering random endpoints).
    """

    async def dispatch(self, request: Request, call_next):
        if not settings.rate_limit_enabled:
            return await call_next(request)

        try:
            r = await _get_redis()
        except Exception:
            return await call_next(request)

        ip = _client_ip(request)
        limit = Limit(
            requests=settings.rate_limit_global_per_ip,
            window=settings.rate_limit_global_window,
        )

        allowed, remaining, reset = await _check_limit(
            r, f"ip:{ip}:global", limit,
        )

        rate_limit_checks_total.labels(
            bucket="global",
            scope="global",
            outcome="allowed" if allowed else "blocked",
        ).inc()

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers={
                    **_rate_limit_headers(limit, remaining, reset),
                    "Retry-After": str(reset),
                },
            )

        response = await call_next(request)
        # Add global rate limit headers unless per-endpoint already set them (429)
        if response.status_code != 429:
            for k, v in _rate_limit_headers(limit, remaining, reset).items():
                response.headers[k] = v
        return response
