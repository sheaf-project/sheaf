"""Integration tests for the Sheaf-to-Sheaf import *preview* endpoint.

Covers the version-check gate (v1 + v2 accepted, anything else
rejected) and forward-compat with v2-only top-level keys (reminders /
polls / etc. silently ignored). The actual re-import now runs through
the async job runner — covered end-to-end, including a real
export-then-reimport round-trip, in test_imports_sheaf_runner.py.
"""

from __future__ import annotations

import io
import json

import httpx


def _upload(client: httpx.Client, path: str, payload: dict) -> httpx.Response:
    body = json.dumps(payload).encode("utf-8")
    return client.post(
        path,
        files={"file": ("export.json", io.BytesIO(body), "application/json")},
    )


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
    journals / revisions / uploaded_files. Those aren't re-importable
    yet but their presence must not gate the preview."""
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
            "watch_tokens": [{"id": "w1"}],
            "polls": [{"id": "p1"}],
            "journals": [{"id": "j1"}],
            "revisions": [],
            "uploaded_files": [],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["member_count"] == 1
