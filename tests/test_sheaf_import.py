"""Integration tests for the Sheaf-to-Sheaf import endpoint.

Covers the version-check gate (v1 + v2 accepted, anything else rejected),
forward-compat with v2-only top-level keys (reminders / polls / etc. are
silently ignored), and a basic round-trip on the importable subset.
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


def test_roundtrip_export_then_import(auth_client: httpx.Client):
    """Build a non-trivial system, hit /v1/export, then feed the bytes
    straight back into /v1/import/sheaf. The format that the export
    emits today must be accepted by the importer that ships with it.
    Future v→v+1 bumps that break this test catch a forgotten import-
    side update."""
    # Build the source system: members in a group, with a tag, a custom
    # field, and an open front. Mix of fields the importer handles.
    alice = auth_client.post(
        "/v1/members",
        json={"name": "Alice", "pronouns": "she/her", "color": "#ff0066"},
    ).json()
    bob = auth_client.post(
        "/v1/members",
        json={"name": "Bob", "is_custom_front": False},
    ).json()

    auth_client.patch(
        "/v1/systems/me",
        json={
            "name": "Source System",
            "description": "before-export",
            "note": "household scratchpad",
        },
    )
    auth_client.patch(
        f"/v1/members/{alice['id']}",
        json={"note": "alice's quick reference"},
    )

    group = auth_client.post(
        "/v1/groups", json={"name": "Inner circle"},
    ).json()
    auth_client.put(
        f"/v1/groups/{group['id']}/members",
        json={"member_ids": [alice["id"], bob["id"]]},
    )

    tag = auth_client.post("/v1/tags", json={"name": "Trusted"}).json()
    auth_client.put(
        f"/v1/tags/{tag['id']}/members",
        json={"member_ids": [alice["id"]]},
    )

    field = auth_client.post(
        "/v1/fields",
        json={"name": "Birthstone", "field_type": "text"},
    ).json()
    auth_client.put(
        f"/v1/members/{alice['id']}/fields",
        json=[{"field_id": field["id"], "value": "amethyst"}],
    )

    auth_client.post("/v1/fronts", json={"member_ids": [alice["id"]]})

    # Export.
    export_resp = auth_client.get("/v1/export")
    assert export_resp.status_code == 200, export_resp.text
    payload = export_resp.json()
    assert payload["version"] == "2"

    # Importer wipe-or-merge semantics: import into the same system. The
    # importer creates fresh entities (new UUIDs), so we expect counts
    # to double rather than fail.
    members_before = len(auth_client.get("/v1/members").json())

    resp = _upload(auth_client, "/v1/import/sheaf", payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["members_imported"] == 2
    assert body["fronts_imported"] >= 1
    assert body["groups_imported"] == 1
    assert body["tags_imported"] == 1
    assert body["custom_fields_imported"] == 1

    # New members landed alongside the originals.
    after = auth_client.get("/v1/members").json()
    assert len(after) == members_before + 2
    names = sorted(m["name"] for m in after)
    assert names.count("Alice") == 2
    assert names.count("Bob") == 2

    # Notes round-trip through encryption: every imported Alice has the
    # original note text, and the system note re-decrypts to the original.
    alice_notes = [m["note"] for m in after if m["name"] == "Alice"]
    assert alice_notes == ["alice's quick reference"] * 2
    system_after = auth_client.get("/v1/systems/me").json()
    assert system_after["note"] == "household scratchpad"


def test_run_import_v2_imports_known_fields(auth_client: httpx.Client):
    resp = _upload(
        auth_client,
        "/v1/import/sheaf",
        {
            "version": "2",
            "system": {"name": "Imported System"},
            "members": [
                {"id": "m1", "name": "Alice"},
                {"id": "m2", "name": "Bob"},
            ],
            "fronts": [
                {
                    "id": "f1",
                    "started_at": "2026-05-01T12:00:00+00:00",
                    "ended_at": None,
                    "member_ids": ["m1"],
                }
            ],
            "groups": [
                {"id": "g1", "name": "Inner circle", "member_ids": ["m1", "m2"]}
            ],
            "tags": [],
            "custom_fields": [],
            # v2-only fields the importer ignores by design — must not crash.
            "reminders": [{"id": "r1", "name": "ignored"}],
            "polls": [{"id": "p1"}],
            "watch_tokens": [],
            "journals": [],
            "revisions": [],
            "uploaded_files": [],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["members_imported"] == 2
    assert body["fronts_imported"] == 1
    assert body["groups_imported"] == 1
