"""Unit tests for PublicShareHeadersMiddleware.

Runs in-process against a minimal app so the error paths (exception handlers,
a middleware that answers before the router) can be exercised without a live
stack. The behavioural suite covers the real routes; what is pinned here is
that a response NOT built by the public router still carries the headers.
"""

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from sheaf.middleware.public_headers import PublicShareHeadersMiddleware


def _app() -> FastAPI:
    app = FastAPI()

    # Same shape as sheaf/main.py: a handler that builds a fresh response and
    # therefore never sees whatever the route set on its own.
    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.get("/v1/public/shared/{token}")
    async def shared(token: str, response: Response) -> dict:
        if token != "good":
            raise HTTPException(status_code=404, detail="Not found")
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "private, max-age=60"
        return {"ok": True}

    @app.get("/v1/public/systems/{system_id}")
    async def public_system(system_id: str, response: Response) -> dict:
        if system_id != "good":
            raise HTTPException(status_code=404, detail="Not found")
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "public, max-age=60"
        return {"ok": True}

    @app.get("/v1/public/shared/{token}/strict")
    async def strict(token: str, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return {"ok": True}

    @app.get("/v1/public/shared/{token}/wrong")
    async def wrong(token: str, response: Response) -> dict:
        response.headers["Cache-Control"] = "public, max-age=300"
        return {"ok": True}

    @app.get("/v1/auth/me")
    async def me() -> dict:
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.add_middleware(PublicShareHeadersMiddleware)
    return app


async def _get(path: str):
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://t"
    ) as c:
        return await c.get(path)


@pytest.mark.asyncio
async def test_success_keeps_the_routes_own_headers():
    resp = await _get("/v1/public/shared/good")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "private, max-age=60"
    assert resp.headers["x-robots-tag"] == "noindex, nofollow"


@pytest.mark.asyncio
async def test_bad_token_404_still_carries_them():
    """The gap this middleware exists for: a 404 is heuristically cacheable,
    and the exception handler's response never touched the route's."""
    resp = await _get("/v1/public/shared/revoked")
    assert resp.status_code == 404
    assert "private" in resp.headers["cache-control"]
    assert "no-store" in resp.headers["cache-control"]
    assert resp.headers["x-robots-tag"] == "noindex, nofollow"


@pytest.mark.asyncio
async def test_public_profile_404_carries_them_too():
    resp = await _get("/v1/public/systems/gone")
    assert resp.status_code == 404
    assert "no-store" in resp.headers["cache-control"]
    assert resp.headers["x-robots-tag"] == "noindex, nofollow"


@pytest.mark.asyncio
async def test_stricter_cache_control_is_not_loosened():
    resp = await _get("/v1/public/shared/good/strict")
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_token_path_can_never_be_shared_cacheable():
    """A token URL is a bearer capability, so `public` is never right on one
    even if a route asks for it. The TTL it chose is kept."""
    resp = await _get("/v1/public/shared/good/wrong")
    assert resp.headers["cache-control"] == "private, max-age=300"


@pytest.mark.asyncio
async def test_non_share_paths_are_untouched():
    resp = await _get("/v1/auth/me")
    assert resp.status_code == 401
    assert "cache-control" not in resp.headers
    assert "x-robots-tag" not in resp.headers


def test_prefixes_match_the_real_routes():
    """Guard against the router prefix moving out from under the constants."""
    from sheaf.main import app as real_app
    from sheaf.middleware.public_headers import _PUBLIC_PREFIX, _TOKEN_PREFIX

    paths = [
        r.path
        for r in real_app.routes
        if getattr(r, "tags", None) and "public-profiles" in r.tags
    ]
    assert paths, "public-profile routes not found"
    assert all(p.startswith(_PUBLIC_PREFIX) for p in paths)
    assert any(p.startswith(_TOKEN_PREFIX) for p in paths)
