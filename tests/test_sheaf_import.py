"""Integration tests for the Sheaf-to-Sheaf import *preview* endpoint.

Covers the version-check gate (v1 + v2 accepted, anything else
rejected) and the per-section counts the preview surfaces (members,
journals, messages, polls, reminders, channels). The actual re-import
now runs through the async job runner — covered end-to-end, including a
real export-then-reimport round-trip and per-section toggles, in
test_imports_sheaf_runner.py.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta

import httpx


def _upload(client: httpx.Client, path: str, payload: dict) -> httpx.Response:
    body = json.dumps(payload).encode("utf-8")
    return client.post(
        path,
        files={"file": ("export.json", io.BytesIO(body), "application/json")},
    )


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
