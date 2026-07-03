"""Redis-backed rate limiting for FastAPI.

Two mechanisms:
1. `rate_limit()` — dependency factory for per-endpoint limits.
2. `RateLimitMiddleware` — global per-IP backstop applied to all requests.

Redis key layout:
    sheaf:rl:{scope}:{identifier}:{window_start}
    e.g. sheaf:rl:ip:203.0.113.5:1711670400
         sheaf:rl:user:abc-def:1711670400

Each key is an integer counter with a TTL equal to the window size.

Two further, coarser write-side guards live here too (see the
write_rate_limit / check_front_switch_rate helpers below):
  - a single per-account bucket shared across the whole mutating surface
    (sheaf:rl:user:{user_id}:write), so one account has a combined
    writes-per-minute ceiling rather than a per-route one; and
  - a per-system token bucket for front switches
    (sheaf:rl:frontswitch:{system_id}), catching a stuck switch-client
    on a system that may legitimately have several writers.
Both are DB-protection under preserve-by-default, not product limits;
self-hosted operators can raise or disable them via config.

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
# Dependency factory - per-endpoint limits
# ---------------------------------------------------------------------------

async def _enforce_limit(
    request: Request,
    *,
    limit: Limit,
    key: str,
    bucket: str | None,
    fail_closed: bool,
    detail: str = "Rate limit exceeded. Try again later.",
) -> None:
    """Core counter check shared by the dependency factories.

    When `bucket` is None the matched route path is used as the key suffix,
    giving the per-endpoint limit rate_limit() has always provided. When
    `bucket` is a string, that fixed name replaces the route segment so
    several endpoints share ONE counter - the shared-bucket approach the
    combined per-user write limit relies on.
    """
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
        logger.warning("Redis unavailable - skipping rate limit check")
        return

    # Build the identifier
    if key == "user":
        # request.state.user_id is set by get_current_user for every auth
        # method (session, JWT, and API key all stamp the account id), so a
        # per-user bucket is really per-account regardless of how the caller
        # authenticated - an API key and a browser session on the same
        # account draw down the same counter.
        user_id = getattr(request.state, "user_id", None)
        identifier = (
            f"ip:{_client_ip(request)}" if user_id is None
            else f"user:{user_id}"
        )
    else:
        identifier = f"ip:{_client_ip(request)}"

    # A fixed bucket collapses several endpoints into one shared counter;
    # without it the route path keeps limits per-endpoint.
    route = request.scope.get("path", request.url.path)
    key_suffix = bucket if bucket is not None else route
    redis_key = f"{identifier}:{key_suffix}"

    allowed, remaining, reset = await _check_limit(r, redis_key, limit)

    # Metric bucket: an explicit shared bucket names its own label so a
    # combined limit stays legible on dashboards instead of collapsing into
    # "other". Otherwise derive it from the matched route template (with path
    # params left as placeholders) so per-instance path noise doesn't bloat
    # label cardinality. route_template restores the "/v1" prefix Starlette
    # 1.0 drops from route.path.
    full_route = route_template(request)
    metric_bucket = bucket if bucket is not None else _route_to_bucket(full_route)
    scope_label = "per_user" if (key == "user" and identifier.startswith("user:")) else "per_ip"
    rate_limit_checks_total.labels(
        bucket=metric_bucket,
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
                    bucket=metric_bucket,
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
            detail=detail,
            headers={
                **_rate_limit_headers(limit, remaining, reset),
                "Retry-After": str(reset),
            },
        )


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
            used to bypass brute-force protection. Default False - most
            endpoints are better off staying available on Redis blips.
    """
    limit = Limit(requests=requests, window=window)

    async def _check(request: Request):
        await _enforce_limit(
            request, limit=limit, key=key, bucket=None, fail_closed=fail_closed,
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
# Combined per-account write limit
# ---------------------------------------------------------------------------
#
# One shared bucket across the whole mutating surface (fronts, journals,
# messages, members, reminders). Under preserve-by-default a single looping
# client would otherwise create unbounded rows, so every write draws down one
# combined per-account budget rather than a per-endpoint one. Unlike
# rate_limit(), the ceiling is read from settings at request time so it can be
# tuned or switched off without a code change, and 0 disables it outright.

# Fixed key suffix + metric label for the shared write counter. All the write
# endpoints must pass the same value or the combined limit fragments.
_WRITE_BUCKET = "write"
_WRITE_LIMIT_DETAIL = (
    "Write rate limit exceeded: too many changes in a short time. "
    "Slow down and try again shortly."
)


def write_rate_limit():
    """FastAPI dependency: the combined per-account write rate limit.

    Apply to every mutating endpoint that should count against the shared
    budget. All applications share ONE Redis counter per account
    (sheaf:rl:user:{user_id}:write), so the limit is a single combined
    writes-per-minute ceiling, not 60/min per route.

    Keyed on the authenticated account, which resolves identically for
    session, JWT, and API-key auth (see _enforce_limit) - the scope is the
    account, not the auth method.

    Fail-open on Redis errors: this is DB-protection, not a security control
    (the fail-closed limits on the auth endpoints cover brute force), so a
    Redis blip should not block every write on the instance.
    """
    from sheaf.auth.dependencies import get_current_user

    async def _check_write(
        request: Request,
        _user=Depends(get_current_user),
    ):
        per_min = settings.write_rate_per_user_per_min
        if per_min <= 0:
            return  # 0 (or negative) disables the combined write limit
        await _enforce_limit(
            request,
            limit=Limit(requests=per_min, window=60),
            key="user",
            bucket=_WRITE_BUCKET,
            fail_closed=False,
            detail=_WRITE_LIMIT_DETAIL,
        )

    return Depends(_check_write)


# ---------------------------------------------------------------------------
# Per-system front-switch token bucket
# ---------------------------------------------------------------------------
#
# Separate from the combined write limit: keyed on the system (which may have
# several legitimate writers), this specifically catches a stuck switch-client
# or looping integration hammering POST /fronts. A classic token bucket -
# sustained refill at N/min with a small burst capacity - fits the shape
# better than a fixed window: a normal cadence of switches never trips it, but
# a runaway loop drains the bucket and is throttled.
#
# The refill-and-take is done in one Lua script so the read-modify-write is
# atomic under concurrent switches on the same system (a plain GET/SET would
# race). Returns 1 when a token was taken (allowed), 0 when the bucket was
# empty (over the limit).
_FRONT_SWITCH_BUCKET_LUA = """
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil then
  tokens = capacity
  ts = now
end
local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * rate)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HMSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], ttl)
return allowed
"""


def _front_switch_key(system_id) -> str:
    return f"sheaf:rl:frontswitch:{system_id}"


async def check_front_switch_rate(system_id) -> bool:
    """Per-system front-switch guard. Returns True if a switch may proceed,
    False if the system is over its switch rate and the caller should 429.

    Sustained rate is front_switch_rate_per_system_per_min; short bursts up
    to front_switch_rate_burst are absorbed. Setting the per-minute rate to 0
    disables the guard.

    Fail-open on any Redis error: the combined per-user write limit and the
    global per-IP backstop remain as coarser nets, and a Redis outage must
    not wedge fronting for every system on the instance.
    """
    if not settings.rate_limit_enabled:
        return True
    per_min = settings.front_switch_rate_per_system_per_min
    if per_min <= 0:
        return True  # 0 (or negative) disables the per-system switch guard

    capacity = max(1, settings.front_switch_rate_burst)
    rate = per_min / 60.0  # tokens per second
    # Long enough for an empty bucket to refill fully, plus a margin, so an
    # active system keeps its refill state while an idle one ages out.
    ttl = int(capacity / rate) + 60

    try:
        r = await _get_redis()
        allowed = await r.eval(
            _FRONT_SWITCH_BUCKET_LUA,
            1,
            _front_switch_key(system_id),
            rate,
            capacity,
            time.time(),
            ttl,
        )
    except Exception:
        logger.warning("Redis unavailable - allowing front switch")
        return True

    ok = bool(allowed)
    rate_limit_checks_total.labels(
        bucket="front_switch",
        scope="per_system",
        outcome="allowed" if ok else "blocked",
    ).inc()
    return ok


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
