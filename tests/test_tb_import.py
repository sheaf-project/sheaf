"""Integration tests for the Tupperbox import *preview* endpoint.

The actual import now runs through the async job runner — covered
end-to-end in test_imports_tb_sp_runner.py. What's left here is the
synchronous preview path: parse an export, summarise it, write nothing.
"""

import pathlib

import httpx
import pytest

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "tupperbox_export_sample.json"


@pytest.fixture
def tb_export_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


def _upload(client: httpx.Client, path: str, payload: bytes, **params):
    """Multipart-upload a TB export. Asserts the response is 2xx so any
    server-side failure surfaces here instead of as a downstream
    KeyError when a later assertion looks for a missing member."""
    resp = client.post(
        path,
        files={"file": ("tb_export.json", payload, "application/json")},
        params=params,
    )
    assert 200 <= resp.status_code < 300, resp.text
    return resp


# --- Preview path ------------------------------------------------------------


def test_preview_summarises_export(auth_client: httpx.Client, tb_export_bytes: bytes):
    resp = _upload(auth_client, "/v1/import/tupperbox/preview", tb_export_bytes)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["member_count"] == 3
    assert data["group_count"] == 2
    ids = {m["id"] for m in data["members"]}
    assert ids == {"200001", "200002", "200003"}
    names = {m["name"] for m in data["members"]}
    assert names == {"Alpha", "Beta", "Gamma"}


def test_preview_rejects_invalid_json(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/import/tupperbox/preview",
        files={"file": ("not_json.json", b"this is not json", "application/json")},
    )
    assert resp.status_code == 400


def test_preview_rejects_non_object(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/import/tupperbox/preview",
        files={"file": ("array.json", b"[1, 2, 3]", "application/json")},
    )
    assert resp.status_code == 400
