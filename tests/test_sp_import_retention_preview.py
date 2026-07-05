"""Integration tests for the front-history-retention warning on the
SimplyPlural import *preview* endpoint.

The SP preview endpoint gained a `db` dependency purely to read the previewing
system's `front_retention_days`, so it can warn - before the user confirms -
that imported fronting history older than their retention window will age out
after the import grace. The over-cap SP warnings are covered as pure units in
test_sp_caps.py; this exercises the endpoint-level retention warning, which
needs the DB (and so cannot be a pure unit).
"""

from __future__ import annotations

import io
import json

import httpx

from tests.conftest import BASE_URL
from tests.test_sheaf_import import _register_client, _set_front_retention


def _upload_sp(client: httpx.Client, payload: dict) -> httpx.Response:
    body = json.dumps(payload).encode("utf-8")
    return client.post(
        "/v1/import/simplyplural/preview",
        files={"file": ("sp-export.json", io.BytesIO(body), "application/json")},
    )


def _sp_payload_with_front_history() -> dict:
    """A minimal SP export whose `frontHistory` collection is non-empty, so
    front_history_count > 0."""
    return {
        "members": [{"_id": "m1", "name": "Alice"}],
        "frontHistory": [{"_id": "fh1", "member": "m1"}],
    }


def test_sp_preview_warns_when_retention_on_and_import_has_front_history():
    with httpx.Client(base_url=BASE_URL) as c:
        email = _register_client(c)
        _set_front_retention(email, 30)
        resp = _upload_sp(c, _sp_payload_with_front_history())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["front_history_count"] == 1
    hits = [w for w in body["limit_warnings"] if "front-history retention" in w]
    assert hits, body["limit_warnings"]
    assert "30 days" in hits[0]
    assert "14 days" in hits[0]


def test_sp_preview_no_retention_warning_when_retention_off():
    with httpx.Client(base_url=BASE_URL) as c:
        _register_client(c)
        resp = _upload_sp(c, _sp_payload_with_front_history())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["front_history_count"] == 1
    assert not [w for w in body["limit_warnings"] if "front-history retention" in w]
