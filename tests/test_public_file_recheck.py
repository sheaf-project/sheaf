"""In-process unit tests for the public-media re-check and its cache header.

The behavioural (stack) suite proves the end-to-end contract - media 404s after
a revoke / going private / suspension, serves while live. What is pinned here,
without a live stack, is the wiring around that gate: that a served response now
carries the short, non-immutable Cache-Control (the bytes can stop being
authorized before the capability's HMAC expires), that a "not serving" verdict
turns into the same uniform 404 as every other refusal on the route, and that a
key with no owner segment is refused BEFORE the database is consulted.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import sheaf.api.v1.files as files_module
import sheaf.middleware.rate_limit as rate_limit_module
from sheaf.api.v1.files import public_serve_router
from sheaf.database import get_db


class _FakeStorage:
    async def get(self, path: str) -> bytes:
        # 1x1 PNG magic bytes are enough for _media_response's content typing.
        return b"\x89PNG\r\n\x1a\n"


def _build_app(monkeypatch, *, serving: bool) -> tuple[FastAPI, dict]:
    calls: dict = {"recheck_owner": None, "storage_get": 0}

    # The route is only reached past the flag; force it on for the test app.
    monkeypatch.setattr(files_module.settings, "public_profiles_enabled", True)
    # Neutralise the HMAC/canonicalisation gates - they have their own tests;
    # here we are exercising what happens AFTER a valid capability.
    monkeypatch.setattr(files_module, "_canonical_public_file_request", lambda *a: True)
    monkeypatch.setattr(files_module, "verify_public_file_token", lambda *a: True)

    async def _fake_recheck(db, owner_id):
        calls["recheck_owner"] = owner_id
        return serving

    monkeypatch.setattr(files_module, "account_serving_public_media", _fake_recheck)

    class _CountingStorage(_FakeStorage):
        async def get(self, path: str) -> bytes:
            calls["storage_get"] += 1
            return await super().get(path)

    monkeypatch.setattr(files_module, "get_storage", lambda: _CountingStorage())

    # The route carries the public-media rate-limit dependency, which would
    # otherwise reach Redis. Same shortcut the rate-limit unit test uses.
    async def _no_enforce(request, **kwargs):
        return None

    monkeypatch.setattr(rate_limit_module, "_enforce_limit", _no_enforce)

    app = FastAPI()

    @app.exception_handler(Exception)
    async def _unhandled(_request, exc):  # pragma: no cover - surfaces test bugs
        raise exc

    app.include_router(public_serve_router, prefix="/v1")

    async def _dummy_db():
        yield None

    app.dependency_overrides[get_db] = _dummy_db
    return app, calls


@pytest.mark.asyncio
async def test_serving_media_gets_short_non_immutable_cache(monkeypatch):
    app, calls = _build_app(monkeypatch, serving=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        resp = await client.get(
            "/v1/public/files/avatars/owner-1/pic.png",
            params={"token": "a" * 64, "expires": "1893456000"},
        )
    assert resp.status_code == 200, resp.text
    # The decided header: 60s, no immutable. Immutability is now wrong because
    # the bytes can be de-authorized before the URL's HMAC lapses.
    assert resp.headers["cache-control"] == "public, max-age=60"
    assert "immutable" not in resp.headers["cache-control"]
    # The re-check ran, keyed on the owner segment of the key.
    assert calls["recheck_owner"] == "owner-1"


@pytest.mark.asyncio
async def test_not_serving_media_404s_uniformly(monkeypatch):
    app, calls = _build_app(monkeypatch, serving=False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        resp = await client.get(
            "/v1/public/files/avatars/owner-1/pic.png",
            params={"token": "a" * 64, "expires": "1893456000"},
        )
    assert resp.status_code == 404
    # And the bytes were never fetched: a dark account does not even hit storage.
    assert calls["storage_get"] == 0


@pytest.mark.asyncio
async def test_ownerless_key_refused_before_the_database(monkeypatch):
    app, calls = _build_app(monkeypatch, serving=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        # No owner segment: "avatars/<file>" is only two parts, so
        # internal_key_owner returns None and the route bails to a 404 without
        # asking the database anything.
        resp = await client.get(
            "/v1/public/files/avatars/pic.png",
            params={"token": "a" * 64, "expires": "1893456000"},
        )
    assert resp.status_code == 404
    assert calls["recheck_owner"] is None
    assert calls["storage_get"] == 0
