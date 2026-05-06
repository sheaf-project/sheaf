"""Integration tests for the PluralKit data importer.

These tests cover the file-upload path end-to-end via the public HTTP
API. The live-API path (which would forward a real PK token to
api.pluralkit.me) is not exercised here — that path's behaviour is
tested at the unit level in test_pk_import_unit.py.

The fixture in tests/fixtures/pk_export_sample.json is a small, synthetic
PK export designed to cover:
  - mixed public/private members
  - a member with a year-less PK birthday (0004-MM-DD sentinel)
  - a group referencing two of the three members
  - a switch log that exercises join, leave, replace, and "nobody fronting"
    transitions
"""

import json
import pathlib

import httpx
import pytest

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "pk_export_sample.json"


@pytest.fixture
def pk_export_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


@pytest.fixture
def pk_export() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def _upload(client: httpx.Client, path: str, payload: bytes, **params):
    return client.post(
        path,
        files={"file": ("pk_export.json", payload, "application/json")},
        params=params,
    )


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


# --- Default import (members + groups; no front history by default) ---------


def test_import_default_creates_members_and_groups(
    auth_client: httpx.Client, pk_export_bytes: bytes
):
    resp = _upload(auth_client, "/v1/import/pluralkit", pk_export_bytes)
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["members_imported"] == 3
    assert result["groups_imported"] == 1
    assert result["fronts_imported"] == 0  # front_history default is False
    assert result["warnings"] == []

    members = auth_client.get("/v1/members").json()
    by_name = {m["name"]: m for m in members}
    assert set(by_name) == {"Alice", "Bob", "Carol"}


def test_import_populates_pluralkit_id(
    auth_client: httpx.Client, pk_export_bytes: bytes
):
    _upload(auth_client, "/v1/import/pluralkit", pk_export_bytes)
    members = auth_client.get("/v1/members").json()
    by_name = {m["name"]: m for m in members}
    assert by_name["Alice"]["pluralkit_id"] == "alice"
    assert by_name["Bob"]["pluralkit_id"] == "bobxyz"
    assert by_name["Carol"]["pluralkit_id"] == "carol1"


def test_import_maps_per_field_data(
    auth_client: httpx.Client, pk_export_bytes: bytes
):
    _upload(auth_client, "/v1/import/pluralkit", pk_export_bytes)
    members = auth_client.get("/v1/members").json()
    alice = next(m for m in members if m["name"] == "Alice")
    assert alice["display_name"] == "Alice the First"
    assert alice["pronouns"] == "she/her"
    assert alice["color"] == "#ff0000"
    assert alice["birthday"] == "1990-04-15"
    assert alice["privacy"] == "public"


def test_import_collapses_year_less_birthday(
    auth_client: httpx.Client, pk_export_bytes: bytes
):
    """PK uses 0004-MM-DD as the year-less sentinel; we collapse to MM-DD."""
    _upload(auth_client, "/v1/import/pluralkit", pk_export_bytes)
    members = auth_client.get("/v1/members").json()
    bob = next(m for m in members if m["name"] == "Bob")
    assert bob["birthday"] == "07-20"


def test_import_resolves_visibility_to_privacy_enum(
    auth_client: httpx.Client, pk_export_bytes: bytes
):
    _upload(auth_client, "/v1/import/pluralkit", pk_export_bytes)
    members = auth_client.get("/v1/members").json()
    by_name = {m["name"]: m for m in members}
    assert by_name["Alice"]["privacy"] == "public"
    assert by_name["Bob"]["privacy"] == "private"
    assert by_name["Carol"]["privacy"] == "public"


def test_import_groups_link_correct_members(
    auth_client: httpx.Client, pk_export_bytes: bytes
):
    _upload(auth_client, "/v1/import/pluralkit", pk_export_bytes)
    groups = auth_client.get("/v1/groups").json()
    assert len(groups) == 1
    inner = groups[0]
    assert inner["name"] == "Inner Circle"
    members = auth_client.get(f"/v1/groups/{inner['id']}/members").json()
    names = sorted(m["name"] for m in members)
    assert names == ["Alice", "Bob"]


# --- Front history path ------------------------------------------------------


def test_import_with_front_history_emits_intervals(
    auth_client: httpx.Client, pk_export_bytes: bytes
):
    """The four-switch fixture should produce three intervals once converted.

    Switch log oldest-to-newest:
      09:00  alice
      10:00  alice + bobxyz   <- Front #1 (alice solo) ends here, Front #2 starts
      11:00  carol1            <- Front #2 ends here, Front #3 starts
      12:00  []                <- Front #3 ends here (nobody fronting)
    """
    resp = _upload(
        auth_client,
        "/v1/import/pluralkit",
        pk_export_bytes,
        front_history="true",
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["fronts_imported"] == 3

    fronts = auth_client.get("/v1/fronts").json()
    members = auth_client.get("/v1/members").json()
    name_by_id = {m["id"]: m["name"] for m in members}

    fronts_sorted = sorted(fronts, key=lambda f: f["started_at"])
    assert len(fronts_sorted) == 3

    first, second, third = fronts_sorted
    assert {name_by_id[mid] for mid in first["member_ids"]} == {"Alice"}
    assert first["ended_at"] is not None  # closed by next switch

    assert {name_by_id[mid] for mid in second["member_ids"]} == {"Alice", "Bob"}
    assert second["ended_at"] is not None

    assert {name_by_id[mid] for mid in third["member_ids"]} == {"Carol"}
    # Last switch was "nobody fronting", so the third Front is closed too.
    assert third["ended_at"] is not None


# --- Selective import (member_ids filter) ------------------------------------


def test_member_ids_filter_drops_unselected(
    auth_client: httpx.Client, pk_export_bytes: bytes
):
    resp = _upload(
        auth_client,
        "/v1/import/pluralkit",
        pk_export_bytes,
        member_ids="alice,carol1",
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["members_imported"] == 2

    names = sorted(m["name"] for m in auth_client.get("/v1/members").json())
    assert names == ["Alice", "Carol"]


def test_filter_warns_when_switches_reference_dropped_members(
    auth_client: httpx.Client, pk_export_bytes: bytes
):
    """Switches mentioning Bob should produce a warning, not a hard error,
    when Bob was deselected at preview time."""
    resp = _upload(
        auth_client,
        "/v1/import/pluralkit",
        pk_export_bytes,
        member_ids="alice,carol1",
        front_history="true",
    )
    assert resp.status_code == 200
    result = resp.json()
    # The Alice + Bob switch becomes effectively Alice-only after filtering,
    # which means we still get three intervals; only the warning differs.
    assert any("not selected" in w.lower() for w in result["warnings"])


# --- System-profile copy -----------------------------------------------------


def test_system_profile_does_not_overwrite_existing_name(
    auth_client: httpx.Client, pk_export_bytes: bytes
):
    """If the user already named their Sheaf system, leave it alone."""
    auth_client.patch("/v1/systems/me", json={"name": "Existing Name"})
    _upload(
        auth_client,
        "/v1/import/pluralkit",
        pk_export_bytes,
        system_profile="true",
    )
    system = auth_client.get("/v1/systems/me").json()
    assert system["name"] == "Existing Name"
    # The PK tag/color did fill in (since they were unset).
    assert system["tag"] == "tst"
    assert system["color"] == "#ffaa00"
