"""Focused checks for the public-media capability-safe rate bucket."""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

import sheaf.middleware.rate_limit as rate_limit_module
from sheaf.api.v1.files import _PUBLIC_FILE_RATE


@pytest.mark.asyncio
async def test_public_file_route_uses_a_fixed_capability_free_bucket(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_enforce(request, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(rate_limit_module, "_enforce_limit", fake_enforce)
    query = b"token=a-real-secret-token&expires=1893456000"
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/v1/public/files/avatars/one-users-key.png",
            "raw_path": b"/v1/public/files/avatars/one-users-key.png",
            "query_string": query,
            "headers": [],
            "client": ("203.0.113.8", 1234),
            "server": ("example.test", 443),
            "route": SimpleNamespace(path="/public/files/{path:path}"),
        }
    )

    await _PUBLIC_FILE_RATE.dependency(request)

    assert captured["bucket"] == "public_files"
    # Neither the capability nor the varying key path may reach the counter.
    assert "a-real-secret-token" not in str(captured)
    assert "one-users-key" not in str(captured)
    # Sized for image fan-out, not page fetches.
    assert captured["limit"].requests == 300
    assert captured["limit"].window == 60
