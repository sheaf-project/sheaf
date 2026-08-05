"""Focused checks for the public-profile capability-safe rate bucket."""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

import sheaf.middleware.rate_limit as rate_limit_module
from sheaf.api.v1.public_profiles import _RATE


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
