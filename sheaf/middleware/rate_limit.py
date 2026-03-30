"""Redis-backed rate limiting for FastAPI.

Two mechanisms:
1. `rate_limit()` — dependency factory for per-endpoint limits.
2. `RateLimitMiddleware` — global per-IP backstop applied to all requests.

Redis key layout:
    sheaf:rl:{scope}:{identifier}:{window_start}
    e.g. sheaf:rl:ip:203.0.113.5:1711670400
         sheaf:rl:user:abc-def:1711670400

Each key is an integer counter with a TTL equal to the window size.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from sheaf.config import settings

logger = logging.getLogger("sheaf.ratelimit")


@dataclass(frozen=True, slots=True)
class Limit:
    """A rate limit: N requests per window_seconds."""
    requests: int
    window: int  # seconds


async def _get_redis():
    from sheaf.auth.sessions import get_redis
    return await get_redis()


def _client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For behind a trusted proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # First IP in the chain is the original client
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
# Dependency factory — per-endpoint limits
# ---------------------------------------------------------------------------

def rate_limit(
    requests: int,
    window: int = 60,
    key: str = "ip",
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
    """
    limit = Limit(requests=requests, window=window)

    async def _check(request: Request):
        if not settings.rate_limit_enabled:
            return

        try:
            r = await _get_redis()
        except Exception:
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

        if not allowed:
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
