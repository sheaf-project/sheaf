"""Unit tests for MaxBodySizeMiddleware.

Runs in-process with a minimal FastAPI app so we can test caps far smaller
than the production default without needing a real 110MB upload.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from sheaf.middleware.body_size import BodyTooLargeError, MaxBodySizeMiddleware


def _app(max_bytes: int) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(BodyTooLargeError)
    async def _handler(_: Request, exc: BodyTooLargeError) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={"detail": f"Too big. Max: {exc.max_bytes}B"},
        )

    @app.post("/echo")
    async def echo(request: Request) -> dict:
        body = await request.body()
        return {"received": len(body)}

    app.add_middleware(MaxBodySizeMiddleware, max_bytes=max_bytes)
    return app


@pytest.mark.asyncio
async def test_accepts_under_cap():
    app = _app(max_bytes=1024)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post("/echo", content=b"x" * 512)
        assert resp.status_code == 200
        assert resp.json() == {"received": 512}


@pytest.mark.asyncio
async def test_rejects_on_content_length_header():
    app = _app(max_bytes=1024)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post("/echo", content=b"x" * 2048)
        assert resp.status_code == 413
        assert "Too big" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_rejects_on_streaming_overflow():
    """No Content-Length (chunked) — the receive wrapper must catch it."""
    app = _app(max_bytes=1024)
    transport = ASGITransport(app=app)

    async def _chunks():
        # Two 800-byte chunks → 1600 bytes, no Content-Length set.
        yield b"x" * 800
        yield b"x" * 800

    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post("/echo", content=_chunks())
        assert resp.status_code == 413


@pytest.mark.asyncio
async def test_non_http_scope_passes_through():
    """Websocket/lifespan scopes must not be interfered with."""
    app = _app(max_bytes=1024)
    # Lifespan startup exercises the non-http branch.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/echo")  # GET has no body; must pass through
        assert resp.status_code == 405  # method not allowed, not 413
