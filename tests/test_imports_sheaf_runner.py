"""End-to-end tests for the Sheaf native re-import runner handler.

Wrap-pattern handler (Phase 6). Verifies the runner plumbing + the
failure paths. The Sheaf export shape is built inline rather than from
a fixture file, matching the existing test_sheaf_import.py convention.
"""

from __future__ import annotations

import json
import uuid

import httpx

from tests._import_runner_helpers import (
    drive_import_runner,
    wait_for_terminal,
)

# Minimal valid Sheaf export — version + a couple of members. Other
# sections empty; enough to prove the runner walks the canonical
# export dict.
_SHEAF_EXPORT = {
    "version": 2,
    "system": {"name": "Reimport System"},
    "members": [
        {"id": "m1", "name": "ReAlice", "pronouns": "she/her"},
        {"id": "m2", "name": "ReBob"},
    ],
    "fronts": [],
    "groups": [],
    "tags": [],
    "custom_fields": [],
}


def _post_file(client: httpx.Client, *, payload: bytes) -> dict:
    resp = client.post(
        "/v1/imports/file",
        files={"file": ("sheaf.json", payload, "application/json")},
        data={"source": "sheaf_file", "idempotency_key": str(uuid.uuid4())},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def test_sheaf_runner_imports_members(auth_client: httpx.Client):
    job = _post_file(auth_client, payload=json.dumps(_SHEAF_EXPORT).encode())
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 2, final["counts"]

    members = auth_client.get("/v1/members").json()
    names = {m["name"] for m in members}
    assert {"ReAlice", "ReBob"}.issubset(names), names


def test_sheaf_runner_roundtrip_from_export(auth_client: httpx.Client):
    """The realistic path: build a system, hit /v1/export, feed those
    exact bytes back through the async import. Exercises the format the
    exporter actually emits, not a hand-written approximation."""
    auth_client.post("/v1/members", json={"name": "RoundtripMember"})
    export = auth_client.get("/v1/export")
    assert export.status_code == 200, export.text

    job = _post_file(auth_client, payload=export.content)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final
    # The exported member comes back in on re-import (alongside the
    # original — re-import is additive, not a replace).
    assert final["counts"]["members_imported"] >= 1, final["counts"]


def test_sheaf_runner_roundtrips_notify_prefs_and_coalesce(auth_client: httpx.Client):
    """Per-member notify_on_front_* prefs and the system coalesce preference
    survive a re-import. The notify_on_front_member_ids cross-reference is
    remapped to the new member id, not left pointing at the stale export id."""
    export = {
        "version": "2",
        "system": {"name": "Notify System", "coalesce_contiguous_fronts": False},
        "members": [
            {
                "id": "m1",
                "name": "NotifyAda",
                "notify_on_front_global": True,
                "notify_on_front_self": False,
                "notify_on_front_member_ids": ["m2"],
            },
            {
                "id": "m2",
                "name": "NotifyBea",
                "notify_on_front_global": False,
                "notify_on_front_self": True,
                "notify_on_front_member_ids": [],
            },
        ],
        "fronts": [],
        "groups": [],
        "tags": [],
        "custom_fields": [],
    }
    job = _post_file(auth_client, payload=json.dumps(export).encode())
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final

    dump = auth_client.get("/v1/export").json()
    # System coalesce preference came back across (default is True; we set
    # False, so a survived round-trip is unambiguous).
    assert dump["system"]["coalesce_contiguous_fronts"] is False

    by_name = {m["name"]: m for m in dump["members"]}
    ada, bea = by_name["NotifyAda"], by_name["NotifyBea"]
    assert ada["notify_on_front_global"] is True
    assert ada["notify_on_front_self"] is False
    assert bea["notify_on_front_self"] is True
    # The cross-reference was remapped to Bea's NEW id, not the stale "m2".
    assert ada["notify_on_front_member_ids"] == [bea["id"]]
    assert "m2" not in ada["notify_on_front_member_ids"]


# A fuller export touching every section the importer now round-trips, with
# the cross-references (revisions -> bio/journal, votes -> options, rules ->
# group/member, reminder -> channel) the importer has to remap.
_TS = "2026-01-02T03:04:05+00:00"
_FULL_EXPORT = {
    "version": "2",
    "system": {
        "name": "Full System",
        "description": "everything",
        "safety": {"grace_period_days": 3, "applies_to_journals": True},
        "retention": {"journal_max_revisions": 7},
    },
    "members": [
        {"id": "m1", "name": "Ada"},
        {"id": "m2", "name": "Bea"},
    ],
    "fronts": [],
    "groups": [
        {"id": "g1", "name": "Inner", "member_ids": ["m1"]},
    ],
    "tags": [],
    "custom_fields": [],
    "journals": [
        {
            "id": "j1",
            "member_id": "m1",
            "title": "Ada's entry",
            "body": "dear diary",
            "visibility": "system",
            "author_member_ids": ["m1"],
            "author_member_names": ["Ada"],
            "image_keys": [],
            "created_at": _TS,
            "updated_at": _TS,
        },
        {
            "id": "j2",
            "member_id": None,
            "title": "System note",
            "body": "house meeting",
            "visibility": "system",
            "author_member_ids": [],
            "author_member_names": [],
            "image_keys": [],
            "created_at": _TS,
            "updated_at": _TS,
        },
    ],
    "revisions": [
        {
            "id": "rev1",
            "target_type": "member_bio",
            "target_id": "m1",
            "editor_member_ids": ["m1"],
            "editor_member_names": ["Ada"],
            "title": None,
            "body": "old bio",
            "image_keys": [],
            "pinned_at": None,
            "created_at": _TS,
        },
        {
            "id": "rev2",
            "target_type": "journal_entry",
            "target_id": "j1",
            "editor_member_ids": [],
            "editor_member_names": [],
            "title": "Ada's entry",
            "body": "older draft",
            "image_keys": [],
            "pinned_at": _TS,
            "created_at": _TS,
        },
    ],
    "messages": [
        {
            "id": "msg1",
            "board_kind": "system",
            "board_member_id": None,
            "author_member_id": "m1",
            "parent_message_id": None,
            "body": "hello board",
            "created_at": _TS,
            "updated_at": _TS,
        },
        {
            "id": "msg2",
            "board_kind": "member",
            "board_member_id": "m2",
            "author_member_id": "m1",
            "parent_message_id": None,
            "body": "hi Bea",
            "created_at": _TS,
            "updated_at": _TS,
        },
        {
            "id": "msg3",
            "board_kind": "system",
            "board_member_id": None,
            "author_member_id": "m2",
            "parent_message_id": "msg1",
            "body": "replying",
            "created_at": _TS,
            "updated_at": _TS,
        },
    ],
    "polls": [
        {
            "id": "p1",
            "question": "lunch?",
            "description": None,
            "kind": "single_choice",
            "results_visibility": "live",
            "closes_at": _TS,
            "retention_days": 30,
            "include_custom_fronts": False,
            "created_at": _TS,
            "options": [
                {"id": "o1", "text": "pizza", "position": 0},
                {"id": "o2", "text": "sushi", "position": 1},
            ],
            "votes": [
                {
                    "voted_as_member_id": "m1",
                    "option_ids": ["o1"],
                    "created_at": _TS,
                    "updated_at": _TS,
                },
            ],
            "events": [
                {
                    "id": "ev1",
                    "voted_as_member_id": "m1",
                    "action": "cast",
                    "option_ids": ["o1"],
                    "fronting_member_ids": ["m1"],
                    "actor_user_id": str(uuid.uuid4()),
                    "created_at": _TS,
                },
            ],
        },
    ],
    "watch_tokens": [
        {
            "id": "w1",
            "label": "Mara",
            "revoked_at": None,
            "created_at": _TS,
            "channels": [
                {
                    "id": "c1",
                    "watch_token_id": "w1",
                    "name": "Mara webhook",
                    "destination_type": "webhook",
                    "destination_config": {"url": "https://example.com/hook"},
                    "event_type": "front_change",
                    "base_all_members": False,
                    "base_include_private": False,
                    "trigger_on_start": True,
                    "trigger_on_stop": False,
                    "trigger_on_cofront_change": False,
                    "cofront_redaction": "count",
                    "payload_sensitivity": "minimal",
                    "debounce_seconds": 30,
                    "aggregation_window_seconds": 0,
                    "quiet_hours": None,
                    "group_rules": [
                        {"group_id": "g1", "rule": "include", "include_private": "inherit"},
                    ],
                    "member_rules": [
                        {"member_id": "m2", "rule": "include"},
                    ],
                    "created_at": _TS,
                },
            ],
        },
    ],
    "uploaded_files": [],
    "reminders": [
        {
            "id": "r1",
            "channel_id": "c1",
            "name": "wake up",
            "title": "Good morning",
            "body": "rise and shine",
            "enabled": True,
            "trigger_type": "automated",
            "trigger_member_id": "m2",
            "trigger_event": "start",
            "delay_seconds": 60,
            "schedule_kind": None,
            "schedule_time": None,
            "schedule_dow_mask": None,
            "schedule_dom": None,
            "schedule_tz": None,
            "cron_expression": None,
            "scope": "system",
            "scope_member_ids": ["m1"],
            "digest_when_absent": True,
            "created_at": _TS,
        },
    ],
}


def test_sheaf_runner_imports_all_sections(auth_client: httpx.Client):
    """Every section the exporter emits round-trips back in, with the
    cross-references remapped to the freshly minted IDs."""
    job = _post_file(auth_client, payload=json.dumps(_FULL_EXPORT).encode())
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    counts = final["counts"]
    assert counts["members_imported"] == 2, counts
    assert counts["groups_imported"] == 1, counts
    assert counts["journals_imported"] == 2, counts
    assert counts["revisions_imported"] == 2, counts
    assert counts["messages_imported"] == 3, counts
    assert counts["polls_imported"] == 1, counts
    assert counts["channels_imported"] == 1, counts
    assert counts["reminders_imported"] == 1, counts


def test_sheaf_runner_section_toggles_respected(auth_client: httpx.Client):
    """Deselecting journals / messages / polls / notifications / reminders
    skips exactly those sections. Reminders ride on notifications, so with
    notifications off the reminder is dropped too."""
    resp = auth_client.post(
        "/v1/imports/file",
        files={"file": ("sheaf.json", json.dumps(_FULL_EXPORT).encode(), "application/json")},
        data={
            "source": "sheaf_file",
            "idempotency_key": str(uuid.uuid4()),
            "options": json.dumps(
                {
                    "journals": False,
                    "messages": False,
                    "polls": False,
                    "notifications": False,
                    "reminders": True,
                }
            ),
        },
    )
    assert resp.status_code == 202, resp.text
    job = resp.json()
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    counts = final["counts"]
    # Members + groups still come across.
    assert counts["members_imported"] == 2, counts
    assert counts["groups_imported"] == 1, counts
    # The deselected sections are absent (zeroed counts are filtered out of
    # the JSONB, so the keys are simply missing).
    assert counts.get("journals_imported", 0) == 0, counts
    assert counts.get("messages_imported", 0) == 0, counts
    assert counts.get("polls_imported", 0) == 0, counts
    assert counts.get("channels_imported", 0) == 0, counts
    assert counts.get("reminders_imported", 0) == 0, counts
    # Member-bio revisions still land (they follow members, not journals);
    # the journal-entry revision is dropped with journals off.
    assert counts.get("revisions_imported", 0) == 1, counts


_FIELDS_EXPORT = {
    "version": "2",
    "system": {"name": "Fields System"},
    "members": [{"id": "m1", "name": "Ada"}],
    "fronts": [],
    "groups": [],
    "tags": [],
    "custom_fields": [
        {
            "id": "f1",
            "name": "Pronouns",
            "field_type": "text",
            "options": None,
            "order": 0,
            "privacy": "private",
            "values": [{"member_id": "m1", "value": "she/her"}],
        }
    ],
}


def test_sheaf_runner_dedupes_custom_field_definitions(auth_client: httpx.Client):
    """Restoring a backup into a system that already has the field reuses the
    existing definition instead of stacking a second "Pronouns" column."""
    j1 = _post_file(auth_client, payload=json.dumps(_FIELDS_EXPORT).encode())
    drive_import_runner()
    f1 = wait_for_terminal(auth_client, j1["id"])
    assert f1["status"] == "complete", f1
    assert f1["counts"]["custom_fields_imported"] == 1, f1["counts"]

    # Second import of the same file: the field already exists, so nothing new
    # is created.
    j2 = _post_file(auth_client, payload=json.dumps(_FIELDS_EXPORT).encode())
    drive_import_runner()
    f2 = wait_for_terminal(auth_client, j2["id"])
    assert f2["status"] == "complete", f2
    assert f2["counts"].get("custom_fields_imported", 0) == 0, f2["counts"]

    # Exactly one definition named Pronouns survives.
    fields = auth_client.get("/v1/fields").json()
    pronouns = [f for f in fields if f["name"] == "Pronouns"]
    assert len(pronouns) == 1, fields


def test_sheaf_runner_fails_on_invalid_json(auth_client: httpx.Client):
    job = _post_file(auth_client, payload=b"not json")
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("invalid JSON" in e["message"] for e in final["events"])


def test_sheaf_runner_fails_on_non_object_root(auth_client: httpx.Client):
    job = _post_file(auth_client, payload=b'"just a string"')
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("must be a JSON object" in e["message"] for e in final["events"])


def test_member_limit_endpoint(auth_client: httpx.Client):
    """The cap endpoint the import flows read for their warning. Shape is
    config-independent: self-hosted is unlimited (limit 0, remaining null),
    SaaS free tier has a numeric cap."""
    data = auth_client.get("/v1/members/limit").json()
    assert set(data) == {"limit", "current", "remaining"}
    assert data["current"] == 0
    if data["limit"] == 0:
        assert data["remaining"] is None
    else:
        assert data["remaining"] == data["limit"]


def test_sheaf_runner_hard_fails_over_member_cap(admin_client: httpx.Client):
    """An import that would push the account past its member cap fails up
    front rather than silently overshooting. Uses a per-user override so the
    check fires regardless of the server's tier config."""
    me = admin_client.get("/v1/auth/me").json()
    patched = admin_client.patch(
        f"/v1/admin/users/{me['id']}", json={"member_limit": 1}
    )
    assert patched.status_code == 200, patched.text

    # _SHEAF_EXPORT has two members; cap is 1.
    job = _post_file(admin_client, payload=json.dumps(_SHEAF_EXPORT).encode())
    drive_import_runner()
    final = wait_for_terminal(admin_client, job["id"])

    assert final["status"] == "failed", final
    blob = (
        (final.get("last_error") or "")
        + " "
        + " ".join(e["message"] for e in final["events"])
    ).lower()
    assert "member" in blob and "limit" in blob, final

    # Nothing was written: the account still has zero members.
    assert admin_client.get("/v1/members/limit").json()["current"] == 0


# A foreign-looking owner UUID. Doesn't have to exist as a real user; the
# import only inspects the structural shape of the key path, not whether
# the embedded user_id resolves to anything live.
_FOREIGN_OWNER = "99999999-9999-9999-9999-999999999999"

# Export carrying every kind of cross-account image reference the
# importer used to leak through. Designed so the resulting account ends
# up with zero references back to the foreign owner's storage.
_CROSS_ACCOUNT_EXPORT = {
    "version": "2",
    "system": {
        "name": "Borrowed",
        "avatar_url": f"avatars/{_FOREIGN_OWNER}/sysav.png",
        "description": (
            "Hi see "
            f"![pic](/v1/files/bios/{_FOREIGN_OWNER}/inline.png)"
            " also https://gravatar.com/x.png"
        ),
        "note": f"note ![n](/v1/files/bios/{_FOREIGN_OWNER}/n.png)",
    },
    "members": [
        {
            "id": "mx",
            "name": "Borrower",
            "avatar_url": f"avatars/{_FOREIGN_OWNER}/m-av.png",
            "description": (
                "bio with internal "
                f"![inline](/v1/files/bios/{_FOREIGN_OWNER}/m-inline.png)"
                " plus external ![ext](https://imgur.com/x.png)"
            ),
        },
        {
            "id": "my",
            "name": "Externals",
            "avatar_url": "https://gravatar.com/avatar/hash.png",
            "description": "just ![ok](https://imgur.com/y.png)",
        },
    ],
    "fronts": [],
    "groups": [],
    "tags": [],
    "custom_fields": [],
    "journals": [
        {
            "id": "j1",
            "title": "entry",
            "body": (
                f"body ![pic](/v1/files/bios/{_FOREIGN_OWNER}/j-pic.png)"
            ),
            "image_keys": [
                f"avatars/{_FOREIGN_OWNER}/jk1.png",
                f"bios/{_FOREIGN_OWNER}/jk2.png",
            ],
        }
    ],
}


def test_sheaf_runner_strips_cross_account_image_refs(auth_client: httpx.Client):
    """Importing an export whose image refs point at another account's
    storage strips those refs rather than carrying them through. External
    URLs (gravatar, imgur) are preserved."""
    me = auth_client.get("/v1/auth/me").json()
    importer_id = me["id"]
    assert importer_id != _FOREIGN_OWNER

    job = _post_file(
        auth_client, payload=json.dumps(_CROSS_ACCOUNT_EXPORT).encode()
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final

    # System avatar with foreign owner -> dropped. Description keeps the
    # external https URL but loses the /v1/files embed.
    system = auth_client.get("/v1/systems/me").json()
    assert system["avatar_url"] is None, system
    assert "v1/files" not in (system["description"] or "")
    assert "gravatar.com" in (system["description"] or "")

    # Members:
    #  - The borrowing member's foreign-key avatar -> None; the external-
    #    URL member's avatar survives untouched.
    members = {m["name"]: m for m in auth_client.get("/v1/members").json()}
    assert members["Borrower"]["avatar_url"] is None, members["Borrower"]
    assert members["Externals"]["avatar_url"] == (
        "https://gravatar.com/avatar/hash.png"
    ), members["Externals"]
    # Borrower's bio dropped the internal embed, kept the external one.
    borrower_bio = members["Borrower"]["description"] or ""
    assert "v1/files" not in borrower_bio
    assert "imgur.com" in borrower_bio

    # Journal entry body lost the internal embed. `image_keys` is an
    # internal column used by the orphan sweeper, not exposed on
    # JournalEntryRead — the body assertion above is the user-visible
    # proof that the strip ran; the corresponding image_keys clear is
    # verified by the strip-helper unit tests.
    journals = auth_client.get("/v1/journals").json()
    j = next(j for j in journals["items"] if j["title"] == "entry")
    assert "v1/files" not in (j["body"] or "")
