"""Integration tests for the lightweight scratchpad notes on members
and the owning system."""

from __future__ import annotations

import httpx

# --- Member notes --------------------------------------------------------


def test_create_member_with_note(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/members",
        json={"name": "Alice", "note": "trigger: loud noises"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["note"] == "trigger: loud noises"


def test_create_member_without_note_returns_null(auth_client: httpx.Client):
    resp = auth_client.post("/v1/members", json={"name": "Bob"})
    assert resp.status_code == 201
    assert resp.json()["note"] is None


def test_update_member_note(auth_client: httpx.Client):
    member = auth_client.post("/v1/members", json={"name": "C"}).json()
    resp = auth_client.patch(
        f"/v1/members/{member['id']}",
        json={"note": "fav drink: oat flat white"},
    )
    assert resp.status_code == 200
    assert resp.json()["note"] == "fav drink: oat flat white"


def test_update_member_note_overwrite(auth_client: httpx.Client):
    """Notes are overwrite-only: updating replaces, no history captured."""
    member = auth_client.post(
        "/v1/members", json={"name": "D", "note": "first"},
    ).json()
    resp = auth_client.patch(
        f"/v1/members/{member['id']}",
        json={"note": "second"},
    )
    assert resp.json()["note"] == "second"

    # No revision should have been captured for the note (revisions are
    # for bios only).
    revs = auth_client.get(f"/v1/members/{member['id']}/revisions").json()
    assert revs == []


def test_clear_member_note_with_empty_string(auth_client: httpx.Client):
    member = auth_client.post(
        "/v1/members", json={"name": "E", "note": "stale"},
    ).json()
    resp = auth_client.patch(
        f"/v1/members/{member['id']}",
        json={"note": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["note"] is None


def test_member_note_length_cap(auth_client: httpx.Client):
    member = auth_client.post("/v1/members", json={"name": "F"}).json()
    too_long = "x" * 5001
    resp = auth_client.patch(
        f"/v1/members/{member['id']}",
        json={"note": too_long},
    )
    assert resp.status_code == 422


# --- System notes --------------------------------------------------------


def test_update_system_note(auth_client: httpx.Client):
    resp = auth_client.patch(
        "/v1/systems/me",
        json={"note": "household contacts list..."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["note"] == "household contacts list..."


def test_get_system_returns_decrypted_note(auth_client: httpx.Client):
    auth_client.patch("/v1/systems/me", json={"note": "system scratchpad"})
    resp = auth_client.get("/v1/systems/me")
    assert resp.status_code == 200
    assert resp.json()["note"] == "system scratchpad"


def test_clear_system_note_with_empty_string(auth_client: httpx.Client):
    auth_client.patch("/v1/systems/me", json={"note": "stuff"})
    resp = auth_client.patch("/v1/systems/me", json={"note": ""})
    assert resp.status_code == 200
    assert resp.json()["note"] is None


def test_system_note_length_cap(auth_client: httpx.Client):
    too_long = "x" * 5001
    resp = auth_client.patch("/v1/systems/me", json={"note": too_long})
    assert resp.status_code == 422


# --- Export --------------------------------------------------------------


def test_notes_appear_in_export_decrypted(auth_client: httpx.Client):
    auth_client.patch("/v1/systems/me", json={"note": "system note"})
    auth_client.post(
        "/v1/members",
        json={"name": "Alice", "note": "alice's scratchpad"},
    )
    export = auth_client.get("/v1/export").json()
    assert export["system"]["note"] == "system note"
    assert any(m["note"] == "alice's scratchpad" for m in export["members"])
