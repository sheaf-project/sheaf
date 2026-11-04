"""ntfy delivery outcome by HTTP status. Pure: the SSRF resolver and the HTTP
client are stubbed, so this pins the classification only.

A 404 from a publish URL cannot come from ntfy itself (publishing to a topic
creates it), so it means the server URL does not reach an ntfy publish
endpoint and is treated as permanent, the way a vanished webhook is. Other
4xx answers are owner-fixable and stay transient.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from sheaf.services.notifications import handlers
from sheaf.services.notifications.payload import RenderedMessage


def _stub_transport(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    async def fake_resolve(url: str):
        return SimpleNamespace(
            url=url, headers={}, extensions={}, allowlisted_private=False
        )

    class _Client:
        async def post(self, *args, **kwargs):
            return SimpleNamespace(status_code=status_code, text="")

    @asynccontextmanager
    async def fake_client():
        yield _Client()

    monkeypatch.setattr(handlers, "resolve_pinned", fake_resolve)
    monkeypatch.setattr(handlers, "safe_client", fake_client)


def _channel():
    return SimpleNamespace(
        id=uuid.uuid4(),
        destination_config={"server_url": "https://ntfy.example.com", "topic": "t"},
    )


def _deliver(status_code: int, monkeypatch: pytest.MonkeyPatch):
    _stub_transport(monkeypatch, status_code)
    return asyncio.run(
        handlers._deliver_ntfy(_channel(), RenderedMessage(title="t", body="b"))
    )


@pytest.mark.parametrize("status_code", [404, 410])
def test_a_missing_publish_endpoint_is_permanent(
    status_code: int, monkeypatch: pytest.MonkeyPatch
):
    outcome = _deliver(status_code, monkeypatch)
    assert outcome.ok is False
    assert outcome.permanent is True
    assert str(status_code) in outcome.error


@pytest.mark.parametrize("status_code", [401, 403, 413, 429, 500, 502])
def test_other_failures_stay_transient(
    status_code: int, monkeypatch: pytest.MonkeyPatch
):
    outcome = _deliver(status_code, monkeypatch)
    assert outcome.ok is False
    assert outcome.permanent is False
    assert outcome.transient is True


def test_success(monkeypatch: pytest.MonkeyPatch):
    assert _deliver(200, monkeypatch).ok is True
