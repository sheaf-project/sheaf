"""End-to-end tests for the BerryTree import runner.

Fixtures are built inline and are entirely synthetic. The shape mirrors the
one real (itself synthetic) BerryTree export we were able to obtain, minus
its `system.email`: that field is the thing the importer is specifically
never allowed to read, and a fixture carrying a plausible address would make
the test that proves it a little too comfortable.

Beyond the standard importer guards (member cap, parse guard, internal image
refs, avatar policy) these lock in the decisions that are specific to this
format:
- `custom_statuses` carries two different things, split by `kind`.
- `counts_toward_headcount: false` is Sheaf's custom front.
- Front entries name either a member or a status, never both.
- Fronting types annotate a front rather than being one.
- Sections we cannot read are counted and reported, never silently dropped.
- BerryTree's own `_partial_errors` text stays out of the plaintext job log.
"""

from __future__ import annotations

import json
import uuid

import httpx

from tests._import_runner_helpers import (
    drive_import_runner,
    set_member_limit,
    wait_for_terminal,
)

_MEMBER_A = "c5b433df-5792-4e9b-be12-773cae0a4aae"
_MEMBER_B = "359ac66f-14bd-4ff8-84d3-8dbc26c33276"
_STATUS_ASLEEP = "71596da0-47b2-4861-8001-9378c3b08283"
_TYPE_COCON = "7260a1d6-c21d-404d-84ec-ef8152d61710"
_FOLDER_TOP = "5ca9ae9c-9499-426a-ae6d-82df494249c0"
_FOLDER_NESTED = "eea498fd-96a8-4cdf-9ac1-54a05e2c7aba"


def _member(mid: str, name: str, **overrides) -> dict:
    base = {
        "id": mid,
        "name": name,
        "display_name": "",
        "pronouns": "",
        "role": "",
        "description": "",
        "color": "#818CF8",
        "avatar": "",
        "banner": None,
        "emoji": "",
        "is_private": False,
        "custom_fields": [],
        "tags": [],
        "folder_id": None,
        "folder_ids": [],
        "pinned": False,
        "mood": "",
        "is_template": False,
        "archived": False,
        "proxy_tags": [],
        "counts_toward_headcount": True,
        "has_avatar": False,
        "created_at": "2026-09-16T16:16:57.762230+00:00",
    }
    base.update(overrides)
    return base


def _sample_data(
    *,
    with_templates: bool = False,
    with_custom_status: bool = True,
    with_folders: bool = True,
    unsupported: bool = False,
    partial_errors: bool = False,
    member_count: int = 2,
    **member_overrides,
) -> dict:
    """Build a synthetic BerryTree export.

    Tests tweak the kwargs to exercise one mapping decision at a time
    without rebuilding the whole structure.
    """
    members = [_member(_MEMBER_A, "Alpha", **member_overrides)]
    if member_count > 1:
        members.append(_member(_MEMBER_B, "Beta"))
    for i in range(2, member_count):
        members.append(_member(str(uuid.uuid4()), f"Extra {i}"))
    if with_templates:
        members.append(
            _member(str(uuid.uuid4()), "Blank Template", is_template=True)
        )

    custom_statuses = [
        {
            "id": _TYPE_COCON,
            "name": "Co-conscious",
            "color": "#8b5cf6",
            "emoji": "",
            "description": "",
            "image_url": "",
            "kind": "type",
            "category": "Fronting Types",
            "sort_order": 1,
            "created_at": "2026-09-16T16:16:47.657453+00:00",
        }
    ]
    if with_custom_status:
        custom_statuses.append(
            {
                "id": _STATUS_ASLEEP,
                "name": "Asleep",
                "color": "#94A3B8",
                "emoji": "",
                "description": "",
                "image_url": "",
                "kind": "status",
                "category": None,
                "sort_order": 0,
                "created_at": "2026-09-16T16:17:45.674769+00:00",
            }
        )

    front_entries = [
        {
            "id": str(uuid.uuid4()),
            "member_id": _MEMBER_A,
            "custom_status_id": None,
            "fronting_type_id": _TYPE_COCON,
            "note": "at the dentist",
            "status_label": "front",
            "custom_status": "",
            "started_at": "2026-09-16T16:18:11.023909+00:00",
            "ended_at": "2026-09-16T16:18:22.512385+00:00",
        }
    ]
    if with_custom_status:
        front_entries.append(
            {
                "id": str(uuid.uuid4()),
                "member_id": None,
                "custom_status_id": _STATUS_ASLEEP,
                "fronting_type_id": None,
                "note": "",
                "status_label": "front",
                "custom_status": "",
                "started_at": "2026-09-16T16:17:56.271031+00:00",
                "ended_at": "2026-09-16T16:17:59.655761+00:00",
            }
        )

    folders = []
    if with_folders:
        folders = [
            {
                "id": _FOLDER_TOP,
                "name": "Front runners",
                "color": "#F472B6",
                "emoji": "",
                "description": "the usual suspects",
                "image": "",
                "is_private": False,
                "parent_id": None,
                "show_in_main_list": True,
                "created_at": "2026-09-16T16:17:14.276061+00:00",
            },
            {
                "id": _FOLDER_NESTED,
                "name": "Inside one",
                "color": "#F472B6",
                "emoji": "",
                "description": "",
                "image": "",
                "is_private": False,
                "parent_id": _FOLDER_TOP,
                "show_in_main_list": True,
                "created_at": "2026-09-16T16:17:29.933628+00:00",
            },
        ]
        members[0]["folder_ids"] = [_FOLDER_NESTED]

    return {
        "app": "BerryTree",
        "schema_version": 3,
        "exported_at": "2026-09-16T16:18:28.837880+00:00",
        # No `email` key: see the module docstring.
        "system": {"username": "tester", "system_name": "Test System"},
        "members": members,
        "front_entries": front_entries,
        "custom_statuses": custom_statuses,
        "chat": [],
        "polls": [],
        "reminders": [],
        "privacy_buckets": [],
        "useful_links": [],
        "folders": folders,
        "sub_systems": [],
        "notes": [],
        "journal": (
            [{"id": str(uuid.uuid4()), "title": "dear diary"}] if unsupported else []
        ),
        "relationships": [],
        "places": [],
        "map_nodes": [],
        "field_templates": [],
        "external_contacts": [],
        "external_relationships": [],
        "cross_system_relationships": [],
        "system_contexts": [
            {
                "id": "main-ctx",
                "kind": "main",
                "name": "Test System",
                "description": "we are testing",
                "avatar": "",
                "banner": "",
                "color": "#818CF8",
                "emoji": "",
                "tag": "",
                "pronouns": "",
                "custom_fields": [],
                "order": 0,
                "is_private": False,
                "created_at": "2026-09-16T16:16:37.939599+00:00",
            }
        ],
        "layers": [],
        "_partial_errors": (
            ["chat export failed: connection reset"] if partial_errors else []
        ),
    }


def _payload(data: dict) -> bytes:
    return json.dumps(data).encode()


def _post_file(
    client: httpx.Client,
    data: dict,
    *,
    idem_key: str | None = None,
    options: dict | None = None,
) -> dict:
    form: dict[str, str] = {
        "source": "berrytree_file",
        "idempotency_key": idem_key or str(uuid.uuid4()),
    }
    if options is not None:
        form["options"] = json.dumps(options)
    resp = client.post(
        "/v1/imports/file",
        files={"file": ("export.json", _payload(data), "application/json")},
        data=form,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def _run(client: httpx.Client, data: dict, **kwargs) -> dict:
    job = _post_file(client, data, **kwargs)
    drive_import_runner()
    return wait_for_terminal(client, job["id"])


# --- Preview --------------------------------------------------------------


def test_preview_returns_summary(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/import/berrytree/preview",
        files={
            "file": (
                "export.json",
                _payload(_sample_data()),
                "application/json",
            )
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["member_count"] == 2
    assert body["custom_front_count"] == 1
    assert body["fronting_type_count"] == 1
    assert body["front_history_count"] == 2
    assert body["folder_count"] == 2
    assert body["schema_version"] == 3
    assert {m["name"] for m in body["members"]} == {"Alpha", "Beta"}


def test_preview_rejects_non_json(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/import/berrytree/preview",
        files={"file": ("export.json", b"not json at all", "application/json")},
    )
    assert resp.status_code == 400


def test_preview_rejects_a_json_array(auth_client: httpx.Client):
    """A BerryTree export is an object. An array parses fine but is not one."""
    resp = auth_client.post(
        "/v1/import/berrytree/preview",
        files={"file": ("export.json", b"[1, 2, 3]", "application/json")},
    )
    assert resp.status_code == 400


def test_preview_reports_sections_we_cannot_read(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/import/berrytree/preview",
        files={
            "file": (
                "export.json",
                _payload(_sample_data(unsupported=True)),
                "application/json",
            )
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"name": "journal entries", "count": 1} in body["unsupported_sections"]


def test_preview_shows_the_exporters_own_failures(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/import/berrytree/preview",
        files={
            "file": (
                "export.json",
                _payload(_sample_data(partial_errors=True)),
                "application/json",
            )
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["export_errors"] == ["chat export failed: connection reset"]


# --- Runner: happy path ---------------------------------------------------


def test_runner_imports_members_fronts_and_folders(auth_client: httpx.Client):
    final = _run(auth_client, _sample_data())

    assert final["status"] == "complete", final
    counts = final["counts"]
    assert counts["members_imported"] == 2, counts
    assert counts["custom_fronts_imported"] == 1, counts
    assert counts["fronts_imported"] == 2, counts
    assert counts["groups_imported"] == 2, counts

    names = {m["name"] for m in auth_client.get("/v1/members").json()}
    assert {"Alpha", "Beta", "Asleep"}.issubset(names)


def test_runner_maps_folders_to_nested_groups(auth_client: httpx.Client):
    final = _run(auth_client, _sample_data())
    assert final["status"] == "complete", final

    groups = {g["name"]: g for g in auth_client.get("/v1/groups").json()}
    assert {"Front runners", "Inside one"}.issubset(groups.keys())
    assert groups["Inside one"]["parent_id"] == groups["Front runners"]["id"]


def test_runner_custom_status_lands_as_a_guarded_custom_front(
    auth_client: httpx.Client,
):
    """BerryTree has no per-status share guard to carry, so an imported
    "Asleep" takes the safe server default and cannot announce itself on a
    shared page until the owner releases it."""
    final = _run(auth_client, _sample_data())
    assert final["status"] == "complete", final

    by_name = {m["name"]: m for m in auth_client.get("/v1/members").json()}
    assert by_name["Asleep"]["is_custom_front"] is True
    assert by_name["Asleep"]["fronting_private"] is True
    assert by_name["Alpha"]["fronting_private"] is False


def test_runner_headcount_flag_makes_a_member_a_custom_front(
    auth_client: httpx.Client,
):
    """`counts_toward_headcount: false` is the same idea as a Sheaf custom
    front: a roster row that fronts but is not a person to be counted."""
    final = _run(
        auth_client,
        _sample_data(member_count=1, counts_toward_headcount=False),
    )
    assert final["status"] == "complete", final

    by_name = {m["name"]: m for m in auth_client.get("/v1/members").json()}
    assert by_name["Alpha"]["is_custom_front"] is True


def test_runner_keeps_the_fronting_type_on_the_front(auth_client: httpx.Client):
    """Sheaf has no fronting-type list, so the type's name rides along on the
    front's free-text status where it stays visible, together with the note."""
    final = _run(auth_client, _sample_data())
    assert final["status"] == "complete", final

    fronts = auth_client.get("/v1/fronts", params={"limit": 50}).json()
    statuses = [f["custom_status"] for f in fronts if f["custom_status"]]
    assert "Co-conscious - at the dentist" in statuses


def test_runner_front_entry_without_a_member_resolves_to_the_status(
    auth_client: httpx.Client,
):
    """A BerryTree front names EITHER a member or a custom status. The
    status-only entry must still produce a front, attached to the imported
    custom front."""
    final = _run(auth_client, _sample_data())
    assert final["status"] == "complete", final

    by_name = {m["name"]: m for m in auth_client.get("/v1/members").json()}
    asleep_id = by_name["Asleep"]["id"]
    fronts = auth_client.get("/v1/fronts", params={"limit": 50}).json()
    assert any(asleep_id in f["member_ids"] for f in fronts), fronts


def test_runner_maps_role_and_mood_to_custom_fields(auth_client: httpx.Client):
    final = _run(
        auth_client,
        _sample_data(member_count=1, role="host", mood="tired"),
    )
    assert final["status"] == "complete", final

    field_names = {f["name"] for f in auth_client.get("/v1/fields").json()}
    assert {"Role", "Mood"}.issubset(field_names)


def test_runner_imports_tags_from_members(auth_client: httpx.Client):
    final = _run(
        auth_client, _sample_data(member_count=1, tags=["protector", "host"])
    )
    assert final["status"] == "complete", final
    assert final["counts"]["tags_imported"] == 2, final["counts"]

    by_name = {m["name"]: m for m in auth_client.get("/v1/members").json()}
    tags = auth_client.get(f"/v1/members/{by_name['Alpha']['id']}/tags").json()
    assert {t["name"] for t in tags} == {"protector", "host"}


# --- Runner: honest reporting ---------------------------------------------


def test_runner_reports_sections_it_cannot_read(auth_client: httpx.Client):
    """The core contract of an experimental importer: an export carrying
    journals must never look like a clean import that brought everything."""
    final = _run(auth_client, _sample_data(unsupported=True))
    assert final["status"] == "complete", final

    hits = [
        e for e in final["events"]
        if e["level"] == "warning" and "1 journal entries" in e["message"]
    ]
    assert hits, final["events"]
    assert "get in touch" in hits[0]["message"]


def test_runner_logs_export_error_count_but_not_the_text(
    auth_client: httpx.Client,
):
    """BerryTree's `_partial_errors` strings can quote the content of the
    record that failed to export. The job event log is stored in plaintext,
    so the count lands there and the text stays in the preview response."""
    final = _run(auth_client, _sample_data(partial_errors=True))
    assert final["status"] == "complete", final

    log = json.dumps(final["events"])
    assert "connection reset" not in log
    assert any(
        "1 section(s) it could not write" in e["message"] for e in final["events"]
    ), final["events"]


def test_runner_skips_templates_by_default_and_says_so(auth_client: httpx.Client):
    final = _run(auth_client, _sample_data(with_templates=True))
    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 2, final["counts"]
    assert final["counts"]["templates_skipped"] == 1, final["counts"]

    names = {m["name"] for m in auth_client.get("/v1/members").json()}
    assert "Blank Template" not in names
    assert any(
        "template member" in e["message"] for e in final["events"]
    ), final["events"]


def test_runner_imports_templates_when_asked(auth_client: httpx.Client):
    final = _run(
        auth_client, _sample_data(with_templates=True), options={"templates": True}
    )
    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 3, final["counts"]

    names = {m["name"] for m in auth_client.get("/v1/members").json()}
    assert "Blank Template" in names


def test_runner_warns_when_an_avatar_cannot_be_carried(auth_client: httpx.Client):
    """BerryTree's server is unreachable, so an avatar referenced by an
    opaque handle cannot be fetched. The reference is dropped rather than
    stored dangling, and the user is told to re-upload."""
    final = _run(
        auth_client,
        _sample_data(
            member_count=1, has_avatar=True, avatar="berrytree-internal-handle"
        ),
    )
    assert final["status"] == "complete", final

    by_name = {m["name"]: m for m in auth_client.get("/v1/members").json()}
    assert by_name["Alpha"]["avatar_url"] is None
    assert any(
        "could not be carried across" in e["message"] for e in final["events"]
    ), final["events"]


# --- Runner: guards -------------------------------------------------------


def test_runner_never_touches_the_account_email(auth_client: httpx.Client):
    """`system.email` is an address on somebody else's service. Sheaf's own
    email column is the user's login identity, and an uploaded file has no
    business writing it."""
    before = auth_client.get("/v1/auth/me").json()["email"]
    data = _sample_data()
    data["system"]["email"] = "attacker@example.invalid"

    final = _run(auth_client, data)
    assert final["status"] == "complete", final

    assert auth_client.get("/v1/auth/me").json()["email"] == before
    assert "attacker@example.invalid" not in json.dumps(final["events"])


def test_runner_enforces_the_member_cap(auth_client: httpx.Client):
    set_member_limit(auth_client, 1)
    try:
        before = len(auth_client.get("/v1/members").json())
        # 2 members + 1 custom front = 3 new roster rows > cap of 1.
        job = _post_file(auth_client, _sample_data())
        drive_import_runner()
        final = wait_for_terminal(auth_client, job["id"])
        assert final["status"] == "failed", final
        # Nothing written: the cap is checked before the first flush.
        assert len(auth_client.get("/v1/members").json()) == before
    finally:
        set_member_limit(auth_client, 0)


def test_runner_rejects_a_malformed_payload(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/imports/file",
        files={"file": ("export.json", b"{not json", "application/json")},
        data={
            "source": "berrytree_file",
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 202, resp.text
    drive_import_runner()
    final = wait_for_terminal(auth_client, resp.json()["id"])
    assert final["status"] == "failed", final


def test_runner_strips_internal_image_refs_from_descriptions(
    auth_client: httpx.Client,
):
    """A crafted export embedding this instance's own storage path would
    otherwise be re-signed into a live capability URL on read, giving the
    importing profile a cross-tenant read of someone else's upload."""
    foreign = f"avatars/{uuid.uuid4()}/{uuid.uuid4()}.png"
    final = _run(
        auth_client,
        _sample_data(
            member_count=1,
            description=f"Hello ![x](/v1/files/{foreign}) there",
        ),
    )
    assert final["status"] == "complete", final

    by_name = {m["name"]: m for m in auth_client.get("/v1/members").json()}
    detail = auth_client.get(f"/v1/members/{by_name['Alpha']['id']}").json()
    assert "/v1/files/" not in (detail["description"] or "")
    assert "Hello" in (detail["description"] or "")


def test_reimport_dedups_members_folders_and_fronts(auth_client: httpx.Client):
    data = _sample_data()
    first = _run(auth_client, data)
    assert first["status"] == "complete", first

    second = _run(auth_client, data)
    assert second["status"] == "complete", second
    assert second["counts"]["members_imported"] == 0, second["counts"]
    assert second["counts"]["members_skipped"] == 3, second["counts"]
    assert second["counts"]["groups_imported"] == 0, second["counts"]
    assert second["counts"]["fronts_imported"] == 0, second["counts"]
