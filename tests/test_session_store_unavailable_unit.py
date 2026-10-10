"""The session store is fail-closed: a Redis outage is a 503, not a 500.

Pure unit tests, no stack. `get_redis` only builds a client (redis.from_url
never connects), so the first thing that touches the network is the command
itself; until the session layer caught that, an outage turned every
authenticated request into an unhandled 500 with a stack trace, while the rate
limiter one module over answered with a clean 503 and Retry-After. The two now
share one policy.
"""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError

import sheaf.auth.sessions as sessions
from sheaf.auth.sessions import SessionStoreUnavailable


class _BoomPipeline:
    def hset(self, *args, **kwargs):
        return self

    def expire(self, *args, **kwargs):
        return self

    def sadd(self, *args, **kwargs):
        return self

    def delete(self, *args, **kwargs):
        return self

    async def execute(self):
        raise RedisConnectionError("Error connecting to Redis")


class _BoomRedis:
    """Every command fails the way a dead server fails."""

    def pipeline(self):
        return _BoomPipeline()

    def __getattr__(self, name):
        async def boom(*args, **kwargs):
            raise RedisConnectionError("Error connecting to Redis")

        return boom


class _OSErrorRedis:
    """The pool failing while opening the socket, which surfaces unwrapped."""

    def __getattr__(self, name):
        async def boom(*args, **kwargs):
            raise OSError(111, "Connection refused")

        return boom


@pytest.fixture
def _redis_is_down(monkeypatch):
    async def fake_get_redis():
        return _BoomRedis()

    monkeypatch.setattr(sessions, "get_redis", fake_get_redis)


@pytest.mark.asyncio
async def test_session_lookup_raises_the_fail_closed_error(_redis_is_down):
    """The hot-path read in the auth dependency: a session that cannot be
    checked is not treated as absent (401) and not as a crash (500)."""
    with pytest.raises(SessionStoreUnavailable):
        await sessions.get_session_user_id("some-session-id")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda: sessions.touch_session("sid", ip="203.0.113.1"),
        lambda: sessions.create_session(uuid.uuid4(), ip=None, user_agent="x"),
        lambda: sessions.get_session_info("sid"),
        lambda: sessions.list_user_sessions(uuid.uuid4()),
        lambda: sessions.delete_session("sid"),
        lambda: sessions.delete_other_sessions(uuid.uuid4(), "keep"),
        lambda: sessions.delete_all_user_sessions(uuid.uuid4()),
        lambda: sessions.rename_session("sid", "phone"),
        lambda: sessions.register_refresh_jti("jti", "sid", 60),
        lambda: sessions.consume_refresh_jti("jti"),
        lambda: sessions.cache_refresh_rotation("jti", "tok"),
        lambda: sessions.get_cached_refresh_rotation("jti"),
        lambda: sessions.revoke_refresh_jti("jti"),
        lambda: sessions.set_admin_step_up(uuid.uuid4(), "sid"),
        lambda: sessions.check_admin_step_up(uuid.uuid4(), "sid"),
    ],
    ids=lambda fn: fn.__code__.co_names[-1] if fn.__code__.co_names else "call",
)
async def test_every_session_operation_is_fail_closed(_redis_is_down, call):
    """Not only the two reads on the hot route: a logout, a rotation or a
    step-up that cannot be recorded must not be reported as done either."""
    with pytest.raises(SessionStoreUnavailable):
        await call()


@pytest.mark.asyncio
async def test_a_bare_oserror_from_the_pool_is_caught_too(monkeypatch):
    async def fake_get_redis():
        return _OSErrorRedis()

    monkeypatch.setattr(sessions, "get_redis", fake_get_redis)
    with pytest.raises(SessionStoreUnavailable):
        await sessions.get_session_user_id("sid")


@pytest.mark.asyncio
async def test_our_own_bugs_are_not_laundered_into_an_outage(monkeypatch):
    """Only the transport errors are translated. A programming error inside a
    session function stays what it is, so it gets fixed instead of being read
    off a dashboard as a Redis blip."""

    class _BuggyRedis:
        async def hget(self, *args, **kwargs):
            raise TypeError("our bug")

    async def fake_get_redis():
        return _BuggyRedis()

    monkeypatch.setattr(sessions, "get_redis", fake_get_redis)
    with pytest.raises(TypeError):
        await sessions.get_session_user_id("sid")


@pytest.mark.asyncio
async def test_the_app_answers_503_with_retry_after(_redis_is_down, monkeypatch):
    """Through the real app and its exception handler: an authenticated route
    during an outage is a 503 with Retry-After, the same verdict as a
    fail-closed rate limit, not the generic 500."""
    from sheaf.config import settings
    from sheaf.database import get_db
    from sheaf.main import app

    # Keep the rate limiter out of the way: with it on, the login-style
    # fail-closed buckets would answer first and this would test the wrong
    # path. The auth dependency is the subject here. Likewise the database:
    # `get_db` is resolved before the dependency body runs and would try to
    # connect, and there is no Postgres in a pure unit run.
    monkeypatch.setattr(settings, "rate_limit_enabled", False)

    async def no_db():
        yield None

    monkeypatch.setitem(app.dependency_overrides, get_db, no_db)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        resp = await client.get(
            "/v1/auth/me", cookies={"sheaf_session": "not-a-real-session"}
        )

    assert resp.status_code == 503, resp.text
    assert resp.json() == {"detail": "Service temporarily unavailable"}
    assert resp.headers["Retry-After"] == sessions.REDIS_RETRY_AFTER


@pytest.mark.asyncio
async def test_the_handler_is_wired_on_a_minimal_app(_redis_is_down):
    """The handler itself, isolated from the rest of the app's middleware."""
    from sheaf.main import session_store_unavailable_handler

    app = FastAPI()
    app.add_exception_handler(SessionStoreUnavailable, session_store_unavailable_handler)

    @app.get("/who")
    async def who() -> dict:
        user_id = await sessions.get_session_user_id("sid")
        return {"user_id": str(user_id)}  # pragma: no cover - never reached

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        resp = await client.get("/who")

    assert resp.status_code == 503
    assert resp.headers["Retry-After"]


def test_rate_limiter_and_sessions_share_one_policy():
    """The two fail-closed paths must not drift: same error tuple, same
    Retry-After."""
    import sheaf.middleware.rate_limit as rate_limit_module

    assert rate_limit_module._REDIS_ERRORS is sessions.REDIS_ERRORS
    assert rate_limit_module._REDIS_RETRY_AFTER == sessions.REDIS_RETRY_AFTER
