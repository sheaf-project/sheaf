"""End-to-end tests for share-view portability (export -> import).

The rule the whole feature hangs on: a curated view travels, a GRANT never
does. An export therefore carries view names, flags and membership but no
token, no token hash and no grant row, and an import restores the curation
while leaving the system published to nobody.

Covers both file formats: the native Sheaf export and the OpenPlural
envelope (whose importer lifts the `extensions.sheaf.share_views` and
relationships passthrough sections back into the native shape).

Driven through the running stack like the other import-runner suites:
build the payload, POST /v1/imports/file, drain the runner, read the
result back through the owner API.
"""

from __future__ import annotations

import json
import uuid

import httpx

from sheaf.services.openplural_export import build_envelope
from tests._import_runner_helpers import drive_import_runner, wait_for_terminal

_EXPORTED_AT = "2026-07-01T00:00:00+00:00"

# Keys that would mean grant material leaked into a portable file. `token`
# and `token_hash` are the link capability; `grants` / `share_grants` would
# be the rows themselves.
_FORBIDDEN_EXPORT_KEYS = {"token", "token_hash", "grants", "share_grants"}


# --- helpers -----------------------------------------------------------------


def _post_file(
    client: httpx.Client, payload: bytes, *, source: str = "sheaf_file"
) -> dict:
    resp = client.post(
        "/v1/imports/file",
        files={"file": ("export.json", payload, "application/json")},
        data={"source": source, "idempotency_key": str(uuid.uuid4())},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def _run(client: httpx.Client, payload: bytes, *, source: str = "sheaf_file") -> dict:
    job = _post_file(client, payload, source=source)
    drive_import_runner()
    final = wait_for_terminal(client, job["id"])
    assert final["status"] == "complete", final
    return final


def _messages(job: dict) -> str:
    return " ".join(e["message"] for e in job["events"])


def _keys_anywhere(node: object, found: set[str]) -> None:
    """Collect every mapping key appearing anywhere in a JSON document."""
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            _keys_anywhere(value, found)
    elif isinstance(node, list):
        for item in node:
            _keys_anywhere(item, found)


def _assert_inert(client: httpx.Client, *, system_id: str) -> None:
    """Nothing an import created is published: no grants, and the anonymous
    surface serves nothing for this system."""
    grants = client.get("/v1/share-grants")
    assert grants.status_code == 200, grants.text
    assert grants.json() == [], grants.json()

    views = client.get("/v1/share-views").json()
    assert all(v["is_shared"] is False for v in views), views

    anon = httpx.Client(base_url=str(client.base_url))
    try:
        # 404 whether public profiles are disabled in this config or simply
        # ungranted - the point is that no import can make this resolve.
        assert anon.get(f"/v1/public/systems/{system_id}").status_code == 404
    finally:
        anon.close()


def _system_id(client: httpx.Client) -> str:
    return client.get("/v1/systems/me").json()["id"]


def _view_by_name(client: httpx.Client, name: str) -> dict:
    views = client.get("/v1/share-views").json()
    match = [v for v in views if v["name"] == name]
    assert len(match) == 1, views
    return match[0]


def _group_by_name(dump: dict, name: str) -> dict:
    match = [g for g in dump["groups"] if g["name"] == name]
    assert len(match) == 1, dump["groups"]
    return match[0]


# --- payloads ----------------------------------------------------------------


def _native_with_views(view_name: str = "Portable View") -> dict:
    """A native export whose share view references a member, a field, a group,
    and a member the owner marked never-shareable."""
    return {
        "version": "2",
        "system": {"name": "Share Portability"},
        "members": [
            {"id": "m1", "name": "ShareAda", "privacy": "public"},
            {
                "id": "m2",
                "name": "ShareSecret",
                "privacy": "public",
                "never_shareable": True,
            },
        ],
        "fronts": [],
        "groups": [
            {
                "id": "g1",
                "name": "ShareGroup",
                "privacy": "friends",
                "member_ids": ["m1"],
            }
        ],
        "tags": [],
        "custom_fields": [
            {
                "id": "f1",
                "name": "ShareField",
                "field_type": "text",
                "options": None,
                "order": 0,
                "privacy": "public",
                "values": [],
            }
        ],
        "share_views": [
            {
                "name": view_name,
                "include_members": False,
                "include_bio": True,
                "include_fronting": True,
                "fronting_show_count": False,
                "include_relationships": True,
                "include_groups": True,
                "member_permalinks": True,
                "member_ids": ["m1", "m2"],
                "field_ids": ["f1"],
                "group_ids": ["g1"],
            }
        ],
    }


# --- export side -------------------------------------------------------------


def test_export_carries_views_and_no_grant_material(auth_client: httpx.Client):
    """A published system exports its view (name + every display flag + the
    member/field/group picks) and nothing at all about the grant that
    publishes it - not the row, not the token, not the token's hash."""
    member = auth_client.post(
        "/v1/members", json={"name": "ExportedShared", "privacy": "public"}
    ).json()["id"]
    field = auth_client.post(
        "/v1/fields", json={"name": "ExportedField", "field_type": "text"}
    ).json()["id"]
    group = auth_client.post("/v1/groups", json={"name": "ExportedGroup"}).json()["id"]

    view = auth_client.post(
        "/v1/share-views",
        json={
            "name": "Exported View",
            "include_members": False,
            "include_bio": True,
            "include_fronting": True,
            "fronting_show_count": False,
            "include_relationships": True,
            "include_groups": True,
            "member_permalinks": True,
        },
    )
    assert view.status_code == 201, view.text
    vid = view.json()["id"]
    assert (
        auth_client.post(
            f"/v1/share-views/{vid}/members", json={"member_id": member}
        ).status_code
        == 200
    )
    assert (
        auth_client.post(
            f"/v1/share-views/{vid}/fields", json={"field_id": field}
        ).status_code
        == 200
    )
    auth_client.put(f"/v1/groups/{group}/members", json={"member_ids": [member]})
    assert (
        auth_client.post(
            f"/v1/share-views/{vid}/groups", json={"group_id": group}
        ).status_code
        == 200
    )

    # Publish it behind a link, so a live grant and a real token exist.
    assert auth_client.post("/v1/auth/me/attest-adult").status_code == 200
    granted = auth_client.post(
        "/v1/share-grants", json={"view_id": vid, "subject_type": "link"}
    )
    assert granted.status_code == 201, granted.text
    raw_token = granted.json()["token"]
    assert raw_token

    export = auth_client.get("/v1/export")
    assert export.status_code == 200, export.text
    body = export.json()

    views = body["share_views"]
    assert len(views) == 1, views
    exported = views[0]
    assert exported["name"] == "Exported View"
    assert exported["include_members"] is False
    assert exported["include_bio"] is True
    assert exported["include_fronting"] is True
    assert exported["fronting_show_count"] is False
    assert exported["include_relationships"] is True
    assert exported["include_groups"] is True
    # Not a staged flag, but still the owner's setting, so it round-trips.
    assert exported["member_permalinks"] is True
    assert exported["member_ids"] == [member]
    assert exported["field_ids"] == [field]
    assert exported["group_ids"] == [group]

    keys: set[str] = set()
    _keys_anywhere(body, keys)
    assert not (keys & _FORBIDDEN_EXPORT_KEYS), sorted(keys & _FORBIDDEN_EXPORT_KEYS)
    # And the capability itself never appears in the bytes, under any key.
    assert raw_token not in export.text


# --- native import -----------------------------------------------------------


def test_native_import_restores_views_remapped(auth_client: httpx.Client):
    """Views come back with their flags and with every reference remapped to
    the freshly minted ids - and land inert."""
    final = _run(auth_client, json.dumps(_native_with_views()).encode())
    assert final["counts"]["share_views_imported"] == 1, final["counts"]

    dump = auth_client.get("/v1/export").json()
    member_id = {m["name"]: m["id"] for m in dump["members"]}
    group_id = {g["name"]: g["id"] for g in dump["groups"]}
    field_id = {f["name"]: f["id"] for f in dump["custom_fields"]}

    # The group's own exposure ceiling travels with it. `friends` on purpose:
    # it is below public, so the importer's publish hold never comes into it
    # and this really is testing the round-trip rather than the guard.
    assert _group_by_name(dump, "ShareGroup")["privacy"] == "friends"

    view = _view_by_name(auth_client, "Portable View")
    assert view["include_members"] is False
    assert view["include_bio"] is True
    assert view["include_fronting"] is True
    assert view["fronting_show_count"] is False
    assert view["include_groups"] is True
    assert view["member_permalinks"] is True
    # The relationships flag travels too - and lands inert like the rest of the
    # view, since no grant comes with it.
    assert view["include_relationships"] is True
    assert [vm["member_id"] for vm in view["members"]] == [member_id["ShareAda"]]
    assert [vf["field_id"] for vf in view["fields"]] == [field_id["ShareField"]]
    assert [vg["group_id"] for vg in view["groups"]] == [group_id["ShareGroup"]]
    # Stale export-side ids never survive into the restored view.
    assert "m1" not in json.dumps(view)

    _assert_inert(auth_client, system_id=dump["system"]["id"])


def test_never_shareable_member_is_stripped_on_import(auth_client: httpx.Client):
    """The file says the member is in the view; the member's own
    never_shareable flag says otherwise, and the flag wins."""
    _run(auth_client, json.dumps(_native_with_views()).encode())

    dump = auth_client.get("/v1/export").json()
    secret = next(m for m in dump["members"] if m["name"] == "ShareSecret")
    assert secret["never_shareable"] is True

    view = _view_by_name(auth_client, "Portable View")
    assert secret["id"] not in {vm["member_id"] for vm in view["members"]}


def test_name_collision_skips_the_whole_view(auth_client: httpx.Client):
    """A second import must not merge into an existing view - merging could
    add members to a view that is already published. The skip is counted and
    reported, not silent."""
    payload = json.dumps(_native_with_views()).encode()
    first = _run(auth_client, payload)
    assert first["counts"]["share_views_imported"] == 1, first["counts"]

    second = _run(auth_client, payload)
    assert second["counts"].get("share_views_imported", 0) == 0, second["counts"]
    assert second["counts"]["share_views_skipped"] == 1, second["counts"]
    assert "Skipped share view 'Portable View'" in _messages(second), second["events"]

    # Still exactly one view, and its membership was not touched.
    views = auth_client.get("/v1/share-views").json()
    assert len(views) == 1, views
    assert len(views[0]["members"]) == 1, views


def test_import_ignores_grant_shaped_junk(auth_client: httpx.Client):
    """A hand-crafted file that tries to smuggle a grant in - as extra keys on
    the view, and as top-level sections - imports the view and nothing else.
    The importer reads known keys only; this test keeps it that way."""
    payload = _native_with_views("Junk View")
    payload["share_views"][0].update(
        {
            "grants": [
                {
                    "subject_type": "public",
                    "status": "active",
                    "token_hash": "deadbeef",
                    "revoked_at": None,
                }
            ],
            "token": "not-a-real-token",
            "token_hash": "also-not-real",
            "subject_type": "public",
            "status": "active",
            "is_shared": True,
        }
    )
    payload["share_grants"] = [
        {
            "view_name": "Junk View",
            "subject_type": "public",
            "status": "active",
            "token_hash": "deadbeef",
        }
    ]
    payload["grants"] = list(payload["share_grants"])

    final = _run(auth_client, json.dumps(payload).encode())
    assert final["counts"]["share_views_imported"] == 1, final["counts"]

    view = _view_by_name(auth_client, "Junk View")
    assert view["is_shared"] is False, view
    _assert_inert(auth_client, system_id=_system_id(auth_client))


def test_import_guards_malformed_share_views(auth_client: httpx.Client):
    """Non-object entries are skipped with a warning rather than raising, and
    an over-long name is clamped (and reported) instead of overflowing the
    column."""
    payload = _native_with_views("x" * 250)
    payload["share_views"] += ["a string", 42, None]

    final = _run(auth_client, json.dumps(payload).encode())
    assert final["counts"]["share_views_imported"] == 1, final["counts"]

    messages = _messages(final)
    assert "3 share view(s) were not JSON objects" in messages, final["events"]
    assert "share view name" in messages, final["events"]

    views = auth_client.get("/v1/share-views").json()
    assert len(views) == 1, views
    assert views[0]["name"] == "x" * 100, views[0]["name"]


# --- OpenPlural round trip ---------------------------------------------------


def _openplural_native() -> dict:
    native = _native_with_views("OpenPlural View")
    native["system"]["name"] = "OP Share Portability"
    native["relationship_types"] = [
        {
            "id": "rt1",
            "name": "OpPartner",
            "symmetry": "symmetric",
            "forward_label": "partner",
            "reverse_label": None,
        }
    ]
    native["member_relationships"] = [
        {
            "source_id": "m1",
            "target_id": "m2",
            "relationship_type_id": "rt1",
            "mutual": False,
            "visibility": "private",
            "created_at": "2026-05-01T00:00:00+00:00",
        }
    ]
    native["groups"].append({"id": "g2", "name": "OpGroupTwo", "member_ids": []})
    native["group_relationships"] = [
        {
            "source_id": "g1",
            "target_id": "g2",
            "relationship_type_id": "rt1",
            "mutual": False,
            "visibility": "private",
            "created_at": "2026-05-02T00:00:00+00:00",
        }
    ]
    return native


def test_openplural_envelope_carries_the_passthrough_sections():
    """Sanity check on the exporter half: the sections the importer lifts back
    are actually written where it looks for them."""
    env = build_envelope(_openplural_native(), exported_at=_EXPORTED_AT)
    sheaf_ext = env["extensions"]["sheaf"]
    assert sheaf_ext["share_views"][0]["name"] == "OpenPlural View"
    assert sheaf_ext["relationship_types"][0]["name"] == "OpPartner"
    assert len(sheaf_ext["member_relationships"]) == 1
    assert len(sheaf_ext["group_relationships"]) == 1


def test_openplural_roundtrip_restores_views_and_relationships(
    auth_client: httpx.Client,
):
    """The sections the OpenPlural exporter parks under extensions.sheaf.* come
    back on import - they used to be written and then dropped on the way in.
    Share views arrive inert here too."""
    env = build_envelope(_openplural_native(), exported_at=_EXPORTED_AT)
    final = _run(
        auth_client, json.dumps(env).encode(), source="openplural_file"
    )
    counts = final["counts"]
    assert counts["share_views_imported"] == 1, counts
    assert counts["relationship_types_imported"] == 1, counts
    assert counts["member_relationships_imported"] == 1, counts
    assert counts["group_relationships_imported"] == 1, counts

    dump = auth_client.get("/v1/export").json()
    member_id = {m["name"]: m["id"] for m in dump["members"]}
    field_id = {f["name"]: f["id"] for f in dump["custom_fields"]}
    group_id = {g["name"]: g["id"] for g in dump["groups"]}

    # Group privacy has no OpenPlural v0.1 core field, so it rides
    # extensions.sheaf on the group record; this proves that passthrough works
    # in both directions.
    assert _group_by_name(dump, "ShareGroup")["privacy"] == "friends"

    view = _view_by_name(auth_client, "OpenPlural View")
    assert view["include_members"] is False
    assert view["include_bio"] is True
    assert view["fronting_show_count"] is False
    assert view["include_relationships"] is True
    assert view["include_groups"] is True
    assert view["member_permalinks"] is True
    # The never-shareable member is stripped on this path too.
    assert [vm["member_id"] for vm in view["members"]] == [member_id["ShareAda"]]
    assert [vf["field_id"] for vf in view["fields"]] == [field_id["ShareField"]]
    assert [vg["group_id"] for vg in view["groups"]] == [group_id["ShareGroup"]]

    assert {t["name"] for t in dump["relationship_types"]} == {"OpPartner"}
    assert len(dump["member_relationships"]) == 1
    assert len(dump["group_relationships"]) == 1

    _assert_inert(auth_client, system_id=dump["system"]["id"])
