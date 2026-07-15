"""End-to-end tests for the Ampersand JSON import runner.

Builds an Ampersand-shape export inline (``{revision, config,
database}``) with sanitised data and drives the runner. Covers the
plumbing, the preview endpoint, and the mapping decisions worth locking
in: systems -> nested groups, custom fronts, role -> tag, age -> custom
field, inline data-URI avatars decoded + stored, board polls, and a
synthesised reminder channel.
"""

from __future__ import annotations

import base64
import json
import uuid

import httpx

from tests._import_runner_helpers import (
    drive_import_runner,
    set_member_limit,
    wait_for_terminal,
)

# 4x4 RGBA PNG that survives a real decode through normalize_image.
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x04\x00\x00\x00\x04"
    b"\x08\x06\x00\x00\x00\xa9\xf1\x9e~\x00\x00\x00\x15IDATx\x9cc\xfc\xcf"
    b"\xc0\xf0\x9f\x01\t01\xa0\x01\xc2\x02\x00\x83\xd1\x02\x06\x02\x90\xef"
    b"X\x00\x00\x00\x00IEND\xaeB`\x82"
)
_PNG_DATA_URI = "data:image/png;base64," + base64.b64encode(_TINY_PNG).decode()


def _sample_data(*, member_count: int = 1, with_avatar: bool = True) -> dict:
    """Build a sanitised Ampersand export payload.

    One primary system with a nested child, `member_count` real members
    (the first optionally carrying an inline avatar) plus one custom
    front, a custom field + age, a member tag + role, a fronting entry, a
    journal post, a note, a board message with a poll and a comment, and a
    reminder.
    """
    sys_root = str(uuid.uuid4())
    sys_child = str(uuid.uuid4())
    field_id = str(uuid.uuid4())
    tag_id = str(uuid.uuid4())

    members = []
    member_ids = []
    for i in range(member_count):
        mid = str(uuid.uuid4())
        member_ids.append(mid)
        m = {
            "uuid": mid,
            "name": f"Member{i}",
            "system": sys_root,
            "pronouns": "they/them",
            "description": "a headmate",
            "color": "#205c90",
            "age": 20 + i,
            "role": "protector",
            "tags": [tag_id],
            "isCustomFront": False,
            "isPinned": False,
            "isArchived": False,
            "dateCreated": "2026-01-29T06:25:14.854Z",
            "customFields": {field_id: "wolf"},
        }
        if with_avatar and i == 0:
            m["image"] = _PNG_DATA_URI
        members.append(m)

    custom_front = {
        "uuid": str(uuid.uuid4()),
        "name": "Away",
        "system": sys_root,
        "isCustomFront": True,
        "isPinned": False,
        "isArchived": False,
        "dateCreated": "2026-01-29T06:25:14.854Z",
        "tags": [],
    }

    return {
        "revision": {"count": 2404, "humanReadable": "0.3.0"},
        "config": {
            "appConfig": {"defaultSystem": sys_root},
            "accessibilityConfig": {},
            "securityConfig": {"password": "should-never-be-touched"},
        },
        "database": {
            "systems": [
                {"uuid": sys_root, "name": "Root system", "description": "d", "parent": None},
                {"uuid": sys_child, "name": "Subsystem", "parent": sys_root},
            ],
            "members": [*members, custom_front],
            "customFields": [{"uuid": field_id, "name": "Species", "priority": 1, "default": True}],
            "tags": [
                {"uuid": tag_id, "name": "cool", "type": "member", "color": "#205c90"},
                {"uuid": str(uuid.uuid4()), "name": "journal-only", "type": "journal"},
            ],
            "frontingEntries": [
                {
                    "uuid": str(uuid.uuid4()),
                    "member": member_ids[0],
                    "startTime": "2026-02-01T10:00:00.000Z",
                    "endTime": "2026-02-01T11:00:00.000Z",
                    "isMainFronter": True,
                    "customStatus": "busy",
                }
            ],
            "journalPosts": [
                {
                    "uuid": str(uuid.uuid4()),
                    "members": [member_ids[0]],
                    "date": "2026-07-01T02:08:55.278Z",
                    "title": "test journal",
                    "subtitle": "a subtitle",
                    "body": "journal body",
                    "tags": [],
                    "contentWarning": "heavy",
                }
            ],
            "notes": [
                {
                    "uuid": str(uuid.uuid4()),
                    "title": "sticky",
                    "content": "note body",
                    "priority": 1,
                }
            ],
            "boardMessages": [
                {
                    "uuid": str(uuid.uuid4()),
                    "members": [member_ids[0]],
                    "title": "board title",
                    "body": "board body",
                    "date": "2026-07-01T02:00:00.000Z",
                    "isPinned": True,
                    "comments": [
                        {
                            "member": member_ids[0],
                            "comment": "a comment",
                            "date": "2026-07-01T02:05:00.000Z",
                        }
                    ],
                    "poll": {
                        "multipleChoice": False,
                        "entries": [
                            {"choice": "yes", "votes": [{"member": member_ids[0], "reason": "y"}]},
                            {"choice": "no", "votes": []},
                        ],
                    },
                }
            ],
            "reminders": [
                {
                    "uuid": str(uuid.uuid4()),
                    "active": True,
                    "title": "awoo",
                    "message": "remember to awoo",
                    "trigger": "fronting",
                    "delay": 120000,
                    "members": [member_ids[0]],
                }
            ],
            "assets": [{"uuid": str(uuid.uuid4()), "friendlyName": "img", "tags": []}],
            "filterQueries": [],
        },
    }


def _post_file(
    client: httpx.Client,
    data: dict,
    *,
    idem_key: str | None = None,
    options: dict | None = None,
) -> dict:
    form: dict[str, str] = {
        "source": "ampersand_file",
        "idempotency_key": idem_key or str(uuid.uuid4()),
    }
    if options is not None:
        form["options"] = json.dumps(options)
    payload = json.dumps(data).encode()
    resp = client.post(
        "/v1/imports/file",
        files={"file": ("export.json", payload, "application/json")},
        data=form,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


# --- Preview --------------------------------------------------------------


def test_preview_returns_summary(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/import/ampersand/preview",
        files={"file": ("export.json", json.dumps(_sample_data()).encode(), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["system_count"] == 2
    assert body["member_count"] == 1
    assert body["custom_front_count"] == 1
    assert body["poll_count"] == 1
    assert body["asset_count"] == 1


def test_preview_rejects_non_json(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/import/ampersand/preview",
        files={"file": ("export.json", b"not json", "application/json")},
    )
    assert resp.status_code == 400


# --- Full import ----------------------------------------------------------


def test_full_import_maps_all_sections(auth_client: httpx.Client):
    job = _post_file(auth_client, _sample_data())
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final
    c = final["counts"]
    assert c["members_imported"] == 1
    assert c["custom_fronts_imported"] == 1
    assert c["groups_imported"] == 2
    assert c["tags_imported"] >= 2  # "cool" tag + "protector" role
    assert c["custom_fields_imported"] >= 2  # Species + synthesised Age
    assert c["fronts_imported"] == 1
    assert c["journals_imported"] == 1
    assert c["notes_imported"] == 1
    # Board post + its one comment = 2 messages.
    assert c["messages_imported"] == 2
    assert c["polls_imported"] == 1
    assert c["reminders_imported"] == 1
    assert c["images_imported"] == 1

    # Roster grew: 1 member + 1 custom front.
    members = auth_client.get("/v1/members").json()
    fronts = [m for m in members if m.get("is_custom_front")]
    assert len(fronts) == 1
    imported = next(m for m in members if m["name"] == "Member0")
    assert imported["avatar_url"]  # inline avatar was decoded + stored

    # Systems became groups, and the child nests under the root.
    groups = auth_client.get("/v1/groups").json()
    by_name = {g["name"]: g for g in groups}
    assert "Root system" in by_name and "Subsystem" in by_name
    assert by_name["Subsystem"]["parent_id"] == by_name["Root system"]["id"]


def test_images_option_off_skips_avatars(auth_client: httpx.Client):
    job = _post_file(auth_client, _sample_data(), options={"images": False})
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final
    assert final["counts"]["images_imported"] == 0
    assert final["counts"]["members_imported"] == 1


def test_member_cap_fails_job_before_writing(auth_client: httpx.Client):
    set_member_limit(auth_client, 1)
    try:
        before = len(auth_client.get("/v1/members").json())
        # 2 real members + 1 custom front = 3 new roster rows > cap of 1.
        job = _post_file(auth_client, _sample_data(member_count=2))
        drive_import_runner()
        final = wait_for_terminal(auth_client, job["id"])
        assert final["status"] == "failed", final
        after = len(auth_client.get("/v1/members").json())
        assert after == before  # nothing written
    finally:
        set_member_limit(auth_client, 0)


def test_reimport_dedups_members(auth_client: httpx.Client):
    data = _sample_data()
    first = _post_file(auth_client, data)
    drive_import_runner()
    assert wait_for_terminal(auth_client, first["id"])["status"] == "complete"

    second = _post_file(auth_client, data)
    drive_import_runner()
    final = wait_for_terminal(auth_client, second["id"])
    assert final["status"] == "complete", final
    # Members match by name on the second pass; none re-created.
    assert final["counts"]["members_imported"] == 0
    assert final["counts"]["members_skipped"] >= 1
