"""Focused checks for the public-profile capability-safe rate bucket."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.requests import Request

import sheaf.middleware.rate_limit as rate_limit_module
from sheaf.api.v1.public_profiles import _RATE
from sheaf.config import settings
from sheaf.middleware.public_headers import PublicShareHeadersMiddleware


@pytest.mark.asyncio
async def test_public_profile_routes_use_one_fixed_capability_free_bucket(
    monkeypatch,
):
    captured: dict[str, object] = {}

    async def fake_enforce(request, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(rate_limit_module, "_enforce_limit", fake_enforce)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/v1/public/shared/a-real-secret-token/members",
            "raw_path": b"/v1/public/shared/a-real-secret-token/members",
            "query_string": b"",
            "headers": [],
            "client": ("203.0.113.8", 1234),
            "server": ("example.test", 443),
            "route": SimpleNamespace(path="/public/shared/{token}/members"),
        }
    )

    await _RATE.dependency(request)

    assert captured["bucket"] == "public_profiles"
    assert "a-real-secret-token" not in str(captured)


# ---------------------------------------------------------------------------
# Fail-closed, for real
# ---------------------------------------------------------------------------
#
# `_get_redis` only builds a client - redis.from_url never connects - so the
# first thing that touches the network is the counter command itself. Until
# that was caught, a Redis outage on this router produced an unhandled 500 with
# a stack trace per request instead of the 503 `fail_closed=True` promises.


class _BoomPipeline:
    def incr(self, *args, **kwargs):
        return self

    def expire(self, *args, **kwargs):
        return self

    async def execute(self):
        raise RedisConnectionError("Error connecting to Redis")


class _BoomRedis:
    def pipeline(self):
        return _BoomPipeline()


def _public_request(path: str, route: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("203.0.113.9", 1234),
            "server": ("example.test", 443),
            "route": SimpleNamespace(path=route),
        }
    )


@pytest.fixture
def _redis_is_down(monkeypatch):
    async def fake_get_redis():
        return _BoomRedis()

    monkeypatch.setattr(rate_limit_module, "_get_redis", fake_get_redis)
    monkeypatch.setattr(settings, "rate_limit_enabled", True)


@pytest.mark.asyncio
async def test_redis_failure_is_a_503_on_the_public_bucket(_redis_is_down):
    """The counter command failing must land on the same fail-closed verdict a
    dead connection would, with a Retry-After so a client backs off."""
    request = _public_request(
        "/v1/public/systems/abc/members", "/public/systems/{system_id}/members"
    )
    with pytest.raises(HTTPException) as excinfo:
        await _RATE.dependency(request)

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "Service temporarily unavailable"
    assert excinfo.value.headers["Retry-After"]


@pytest.mark.asyncio
async def test_redis_failure_skips_the_check_when_not_fail_closed(_redis_is_down):
    """An ordinary endpoint stays available through a blip - the old fail-open
    contract, which the unhandled error broke just as thoroughly."""
    dependency = rate_limit_module.rate_limit(60, 60).dependency
    request = _public_request("/v1/members", "/members")

    assert await dependency(request) is None


@pytest.mark.asyncio
async def test_the_503_still_carries_the_public_share_headers(_redis_is_down):
    """The 503 is built by an exception handler, so it never sees the router's
    own Response - `PublicShareHeadersMiddleware` is what keeps a profile URL
    out of a search index and out of a shared cache even when it is failing."""
    app = FastAPI()

    @app.get("/v1/public/shared/{token}/members", dependencies=[_RATE])
    async def members(token: str) -> dict:  # pragma: no cover - never reached
        return {"ok": True}

    app.add_middleware(PublicShareHeadersMiddleware)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        resp = await client.get("/v1/public/shared/secret-token/members")

    assert resp.status_code == 503, resp.text
    assert resp.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert "private" in resp.headers["Cache-Control"]
    assert resp.headers["Retry-After"]
