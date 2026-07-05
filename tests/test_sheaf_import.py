"""Integration tests for the Sheaf-to-Sheaf import *preview* endpoint.

Covers the version-check gate (v1 + v2 accepted, anything else
rejected) and the per-section counts the preview surfaces (members,
journals, messages, polls, reminders, channels). The actual re-import
now runs through the async job runner — covered end-to-end, including a
real export-then-reimport round-trip and per-section toggles, in
test_imports_sheaf_runner.py.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx

from tests.conftest import BASE_URL


def _upload(client: httpx.Client, path: str, payload: dict) -> httpx.Response:
    body = json.dumps(payload).encode("utf-8")
    return client.post(
        path,
        files={"file": ("export.json", io.BytesIO(body), "application/json")},
    )


def _register_client(c: httpx.Client) -> str:
    """Register a fresh user on the given client and return the email, so a
    test can reach into the DB to set that user's system settings."""
    email = f"ret-preview-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = c.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201, resp.text
    c.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return email


def _set_front_retention(email: str, days: int) -> None:
    """Set the user's ``System.front_retention_days`` directly in the DB.

    The real toggle routes through a SafetyChangeRequest (asymmetric
    loosening), which is not what these preview tests exercise - they only
    need the setting in place - so write it straight to the row."""

    async def _run() -> None:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker

        from sheaf.config import settings
        from sheaf.crypto import blind_index
        from sheaf.models.system import System
        from sheaf.models.user import User

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session() as db:
            user = (
                await db.execute(
                    select(User).where(User.email_hash == blind_index(email))
                )
            ).scalar_one()
            system = (
                await db.execute(select(System).where(System.user_id == user.id))
            ).scalar_one()
            system.front_retention_days = days
            await db.commit()
        await engine.dispose()

    asyncio.run(_run())


def _future_iso(days: int = 30) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _open_poll(pid: str) -> dict:
    """A native-shape poll that would import OPEN (closes_at in the future)."""
    return {"id": pid, "question": "q?", "closes_at": _future_iso(), "options": []}


def test_preview_rejects_missing_version(auth_client: httpx.Client):
    resp = _upload(
        auth_client,
        "/v1/import/sheaf/preview",
        {"system": {"name": "Whatever"}, "members": []},
    )
    assert resp.status_code == 400
    assert "version" in resp.json()["detail"].lower()


def test_preview_rejects_unknown_version(auth_client: httpx.Client):
    resp = _upload(
        auth_client,
        "/v1/import/sheaf/preview",
        {"version": "99", "system": {"name": "x"}, "members": []},
    )
    assert resp.status_code == 400


def test_preview_accepts_v1(auth_client: httpx.Client):
    resp = _upload(
        auth_client,
        "/v1/import/sheaf/preview",
        {
            "version": "1",
            "system": {"name": "Old"},
            "members": [{"id": "m1", "name": "Alice"}],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["system_name"] == "Old"
    assert body["member_count"] == 1


def test_preview_accepts_v2_with_extra_keys(auth_client: httpx.Client):
    """A current-format export carries reminders / polls / watch_tokens /
    journals / revisions / uploaded_files. The preview surfaces a count for
    each so the user can see what's about to come across."""
    resp = _upload(
        auth_client,
        "/v1/import/sheaf/preview",
        {
            "version": "2",
            "system": {"name": "Current"},
            "members": [{"id": "m1", "name": "Alice"}],
            "fronts": [],
            "groups": [],
            "tags": [],
            "custom_fields": [],
            "reminders": [{"id": "r1", "name": "drift-by"}],
            "watch_tokens": [{"id": "w1", "channels": [{"id": "c1"}, {"id": "c2"}]}],
            "polls": [{"id": "p1"}],
            "journals": [{"id": "j1"}],
            "messages": [{"id": "msg1"}],
            "revisions": [],
            "uploaded_files": [],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["member_count"] == 1
    assert body["journal_count"] == 1
    assert body["message_count"] == 1
    assert body["poll_count"] == 1
    assert body["reminder_count"] == 1
    # channel_count sums channels across all watch tokens.
    assert body["channel_count"] == 2


def test_preview_warns_when_open_polls_exceed_concurrent_cap(
    auth_client: httpx.Client,
):
    """More incoming OPEN polls than the tier's concurrent-open cap allows
    surfaces the same clamp warning the import would raise, so the user can
    cancel/adjust before enqueueing."""
    cap = auth_client.get("/v1/polls/server-config").json()[
        "max_concurrent_open_polls"
    ]
    if cap == 0:
        # Unlimited tier (selfhosted-style deployment): the clamp never fires.
        return
    over = 2
    polls = [_open_poll(f"p{i}") for i in range(cap + over)]
    resp = _upload(
        auth_client,
        "/v1/import/sheaf/preview",
        {"version": "2", "system": {"name": "S"}, "members": [], "polls": polls},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["open_poll_count"] == cap + over
    hits = [w for w in body["limit_warnings"] if "concurrent-open-poll" in w]
    assert hits, body["limit_warnings"]
    # A fresh account starts with 0 open polls, so the overage is exactly `over`.
    assert hits[0].startswith(f"{over} poll(s)")


def test_preview_no_warning_when_open_polls_within_cap(auth_client: httpx.Client):
    cap = auth_client.get("/v1/polls/server-config").json()[
        "max_concurrent_open_polls"
    ]
    # Unlimited tier: any count is within cap. Otherwise stay one under.
    n = 3 if cap == 0 else max(cap - 1, 0)
    polls = [_open_poll(f"p{i}") for i in range(n)]
    resp = _upload(
        auth_client,
        "/v1/import/sheaf/preview",
        {"version": "2", "system": {"name": "S"}, "members": [], "polls": polls},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["open_poll_count"] == n
    assert not [w for w in body["limit_warnings"] if "concurrent-open-poll" in w]


def _front_payload() -> dict:
    """A minimal v2 export carrying one front, so front_count > 0."""
    return {
        "version": "2",
        "system": {"name": "S"},
        "members": [{"id": "m1", "name": "Alice"}],
        "fronts": [{"id": "f1", "members": ["m1"]}],
    }


def test_preview_warns_when_retention_on_and_import_has_front_history():
    """A system with front-history retention turned on that previews an import
    containing fronting history is told, up front, that the imported history
    older than its window will age out after the import grace - so there is no
    surprise deletion later."""
    with httpx.Client(base_url=BASE_URL) as c:
        email = _register_client(c)
        _set_front_retention(email, 30)
        resp = _upload(c, "/v1/import/sheaf/preview", _front_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["front_count"] == 1
    hits = [w for w in body["limit_warnings"] if "front-history retention" in w]
    assert hits, body["limit_warnings"]
    # The window and the grace are both named, so the message is self-contained.
    assert "30 days" in hits[0]
    assert "14 days" in hits[0]


def test_preview_no_retention_warning_when_retention_off():
    """With retention off (the default, 0) the same front-carrying import
    previews with no retention warning - nothing would age out."""
    with httpx.Client(base_url=BASE_URL) as c:
        _register_client(c)
        resp = _upload(c, "/v1/import/sheaf/preview", _front_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["front_count"] == 1
    assert not [w for w in body["limit_warnings"] if "front-history retention" in w]


def test_preview_no_retention_warning_when_import_has_no_fronts():
    """Retention on but the import carries no fronting history: no warning,
    because there is nothing that could age out."""
    with httpx.Client(base_url=BASE_URL) as c:
        email = _register_client(c)
        _set_front_retention(email, 30)
        payload = _front_payload()
        payload["fronts"] = []
        resp = _upload(c, "/v1/import/sheaf/preview", payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["front_count"] == 0
    assert not [w for w in body["limit_warnings"] if "front-history retention" in w]
