"""Integration tests for the Tupperbox data importer.

These cover the file-upload path end-to-end via the public HTTP API.
The fixture in tests/fixtures/tupperbox_export_sample.json is a small
synthetic Tupperbox export covering:
  - a fully-populated tupper (display name, description, birthday, avatar)
  - a minimal tupper (no nick, no description, no avatar, no birthday)
  - a group-less tupper that also exercises dropped fields (banner, tag)
  - two groups (one with an avatar that gets dropped, one without)
"""

import json
import pathlib

import httpx
import pytest

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "tupperbox_export_sample.json"


@pytest.fixture
def tb_export_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


@pytest.fixture
def tb_export() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


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


# --- Default import ----------------------------------------------------------


def test_import_default_creates_members_and_groups(
    auth_client: httpx.Client, tb_export_bytes: bytes
):
    resp = _upload(auth_client, "/v1/import/tupperbox", tb_export_bytes)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["members_imported"] == 3
    assert result["groups_imported"] == 2
    assert result["warnings"] == []

    members = auth_client.get("/v1/members").json()
    by_name = {m["name"]: m for m in members}
    assert set(by_name) == {"Alpha", "Beta", "Gamma"}


def test_import_maps_per_field_data(
    auth_client: httpx.Client, tb_export_bytes: bytes
):
    _upload(auth_client, "/v1/import/tupperbox", tb_export_bytes)
    members = auth_client.get("/v1/members").json()
    alpha = next(m for m in members if m["name"] == "Alpha")
    assert alpha["display_name"] == "Alpha the First"
    assert alpha["avatar_url"] == "https://cdn.tupperbox.app/example/alpha.webp"
    assert alpha["birthday"] == "1990-04-15"
    # Tupperbox has no privacy model — everything defaults to private.
    assert alpha["privacy"] == "private"
    # Tupperbox has no member colour or pronouns.
    assert alpha["color"] is None
    assert alpha["pronouns"] is None


def test_import_handles_minimal_member(
    auth_client: httpx.Client, tb_export_bytes: bytes
):
    """Beta has no nick, no description, no avatar, no birthday."""
    _upload(auth_client, "/v1/import/tupperbox", tb_export_bytes)
    members = auth_client.get("/v1/members").json()
    beta = next(m for m in members if m["name"] == "Beta")
    assert beta["display_name"] is None
    assert beta["description"] is None
    assert beta["avatar_url"] is None
    assert beta["birthday"] is None


def test_import_groups_link_correct_members(
    auth_client: httpx.Client, tb_export_bytes: bytes
):
    """Alpha + Beta are in 'Core'; Gamma is group-less."""
    _upload(auth_client, "/v1/import/tupperbox", tb_export_bytes)
    groups = auth_client.get("/v1/groups").json()
    by_name = {g["name"]: g for g in groups}
    assert set(by_name) == {"Core", "Visitors"}

    core_members = auth_client.get(f"/v1/groups/{by_name['Core']['id']}/members").json()
    assert sorted(m["name"] for m in core_members) == ["Alpha", "Beta"]

    visitors_members = auth_client.get(
        f"/v1/groups/{by_name['Visitors']['id']}/members"
    ).json()
    assert visitors_members == []


def test_import_skips_groups_when_disabled(
    auth_client: httpx.Client, tb_export_bytes: bytes
):
    resp = _upload(
        auth_client,
        "/v1/import/tupperbox",
        tb_export_bytes,
        groups="false",
    )
    assert resp.status_code == 200
    assert resp.json()["groups_imported"] == 0
    assert auth_client.get("/v1/groups").json() == []


# --- Selective import (member_ids filter) ------------------------------------


def test_member_ids_filter_drops_unselected(
    auth_client: httpx.Client, tb_export_bytes: bytes
):
    resp = _upload(
        auth_client,
        "/v1/import/tupperbox",
        tb_export_bytes,
        member_ids="200001,200003",
    )
    assert resp.status_code == 200
    assert resp.json()["members_imported"] == 2

    names = sorted(m["name"] for m in auth_client.get("/v1/members").json())
    assert names == ["Alpha", "Gamma"]


def test_filter_drops_group_memberships_for_unselected(
    auth_client: httpx.Client, tb_export_bytes: bytes
):
    """When Beta is deselected, the Core group keeps Alpha but not Beta."""
    _upload(
        auth_client,
        "/v1/import/tupperbox",
        tb_export_bytes,
        member_ids="200001",
    )
    groups = auth_client.get("/v1/groups").json()
    core = next(g for g in groups if g["name"] == "Core")
    members = auth_client.get(f"/v1/groups/{core['id']}/members").json()
    assert sorted(m["name"] for m in members) == ["Alpha"]
