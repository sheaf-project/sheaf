"""Integration tests for the PluralKit import *preview* endpoint.

The actual import now runs through the async job runner — its
behaviour is covered end-to-end in test_imports_pk_runner.py and
test_imports_pk_api_runner.py, and the per-field normalisation helpers
have unit coverage in test_pk_import_unit.py. What's left here is the
synchronous preview path: parse an export, summarise it, write nothing.
"""

import pathlib

import httpx
import pytest

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "pk_export_sample.json"


@pytest.fixture
def pk_export_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


def _upload(client: httpx.Client, path: str, payload: bytes, **params):
    """Multipart-upload a PK export. Asserts the response is 2xx so any
    server-side failure surfaces here instead of as a downstream
    KeyError when a later assertion looks for a missing member."""
    resp = client.post(
        path,
        files={"file": ("pk_export.json", payload, "application/json")},
        params=params,
    )
    assert 200 <= resp.status_code < 300, resp.text
    return resp


# --- Preview path ------------------------------------------------------------


def test_preview_summarises_export(auth_client: httpx.Client, pk_export_bytes: bytes):
    resp = _upload(auth_client, "/v1/import/pluralkit/preview", pk_export_bytes)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["system_name"] == "Test PK System"
    assert data["member_count"] == 3
    assert data["group_count"] == 1
    assert data["switch_count"] == 4
    hids = {m["id"] for m in data["members"]}
    assert hids == {"alice", "bobxyz", "carol1"}


def test_preview_rejects_invalid_json(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/import/pluralkit/preview",
        files={"file": ("not_json.json", b"this is not json", "application/json")},
    )
    assert resp.status_code == 400


def test_preview_rejects_non_object(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/import/pluralkit/preview",
        files={"file": ("array.json", b"[1, 2, 3]", "application/json")},
    )
    assert resp.status_code == 400
