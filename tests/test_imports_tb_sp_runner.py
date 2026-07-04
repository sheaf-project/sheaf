"""End-to-end tests for the Tupperbox + SimplyPlural import runner handlers.

Both took the wrap-pattern path (Phase 5): defensive parse + hard-
failure surfacing + counts + warning-events, with the per-record walk
still inside the existing run_import. These tests verify the runner
plumbing + the failure paths plus the per-record skip warnings the
walks emit when they encounter malformed or dangling references.
"""

from __future__ import annotations

import base64
import json
import pathlib
import uuid

import httpx

from tests._import_runner_helpers import (
    drive_import_runner,
    wait_for_terminal,
)

TB_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "tupperbox_export_sample.json"
SP_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "sp_export_sample.json"


def _post_file(
    client: httpx.Client,
    *,
    source: str,
    payload: bytes,
    idem_key: str | None = None,
    options: dict | None = None,
) -> dict:
    form: dict[str, str] = {
        "source": source,
        "idempotency_key": idem_key or str(uuid.uuid4()),
    }
    if options is not None:
        form["options"] = json.dumps(options)
    resp = client.post(
        "/v1/imports/file",
        files={"file": ("import.json", payload, "application/json")},
        data=form,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def _warnings(events: list[dict]) -> list[str]:
    """Pull warning-level event messages out of an import job's event log."""
    return [e["message"] for e in events if e["level"] == "warning"]


# --- Tupperbox -------------------------------------------------------------


def test_tupperbox_runner_imports_members(auth_client: httpx.Client):
    job = _post_file(
        auth_client, source="tupperbox_file", payload=TB_FIXTURE.read_bytes()
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] >= 1, final["counts"]
    # An info event summarising the import lands at the import stage.
    assert any(
        e["stage"] == "import" and e["level"] == "info" for e in final["events"]
    ), final["events"]


def test_tupperbox_runner_fails_on_invalid_json(auth_client: httpx.Client):
    job = _post_file(
        auth_client, source="tupperbox_file", payload=b"definitely not json"
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("invalid JSON" in e["message"] for e in final["events"])


def test_tupperbox_runner_fails_on_non_object_root(auth_client: httpx.Client):
    job = _post_file(auth_client, source="tupperbox_file", payload=b"[1, 2, 3]")
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("must be a JSON object" in e["message"] for e in final["events"])


# --- SimplyPlural ----------------------------------------------------------


def test_simplyplural_runner_imports_members(auth_client: httpx.Client):
    job = _post_file(
        auth_client, source="simplyplural_file", payload=SP_FIXTURE.read_bytes()
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 2, final["counts"]

    members = auth_client.get("/v1/members").json()
    names = {m["name"] for m in members}
    assert {"SpAlice", "SpBob"}.issubset(names), names


def test_simplyplural_runner_fails_on_invalid_json(auth_client: httpx.Client):
    job = _post_file(
        auth_client, source="simplyplural_file", payload=b"{ broken"
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("invalid JSON" in e["message"] for e in final["events"])


def test_simplyplural_runner_summary_event(auth_client: httpx.Client):
    """The import-stage info event names every count bucket so the
    report reads coherently even for collections that were empty."""
    job = _post_file(
        auth_client, source="simplyplural_file", payload=SP_FIXTURE.read_bytes()
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    summary = [
        e for e in final["events"]
        if e["stage"] == "import" and e["level"] == "info"
    ]
    assert summary, final["events"]
    assert "members" in summary[-1]["message"]


# --- Per-record skip warnings ----------------------------------------------
#
# These pin down the categories of skip the SP / TB walks now emit so a
# user reading the import detail page sees what didn't come across,
# instead of having to compare counts to the source export by hand.


def _sp_payload(**extras) -> bytes:
    """Build a minimal SP payload with the same base shape as the fixture."""
    base = {
        "version": 2,
        "users": [],
        "members": [
            {"_id": "spm1", "name": "Alpha", "private": False},
            {"_id": "spm2", "name": "Beta", "private": False},
        ],
        "frontStatuses": [],
        "frontHistory": [],
        "groups": [],
        "customFields": [],
        "notes": [],
    }
    base.update(extras)
    return json.dumps(base).encode()


def test_sp_warns_when_field_value_references_unknown_field(
    auth_client: httpx.Client,
):
    payload = _sp_payload(
        customFields=[{"_id": "spf1", "name": "Likes", "type": 0}],
        members=[
            {
                "_id": "spm1",
                "name": "Alpha",
                "private": False,
                "info": {"spf-deleted": "stale", "spf1": "lasers"},
            },
        ],
    )
    job = _post_file(auth_client, source="simplyplural_file", payload=payload)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert any(
        "custom-field values" in w and "field definition wasn't" in w
        for w in _warnings(final["events"])
    ), final["events"]


def test_sp_clamps_deep_group_nesting(auth_client: httpx.Client):
    """SP builds a nested group tree via `parent`; a chain deeper than the API's
    MAX_GROUP_DEPTH is reparented up to fit, so the import completes (rather than
    creating an over-deep tree) and warns. The reparent is only applied when a
    group was too deep, so the depth warning firing means the clamp ran."""
    groups = [{"_id": "g0", "name": "g0", "parent": "root"}]
    for i in range(1, 12):  # 12-deep chain, well past the depth-8 cap
        groups.append({"_id": f"g{i}", "name": f"g{i}", "parent": f"g{i - 1}"})
    payload = _sp_payload(groups=groups)
    job = _post_file(auth_client, source="simplyplural_file", payload=payload)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert any(
        "nesting depth" in w for w in _warnings(final["events"])
    ), final["events"]


def test_sp_warns_when_front_history_member_missing(auth_client: httpx.Client):
    payload = _sp_payload(
        frontHistory=[
            {
                "_id": "fh1",
                "member": "spm-deleted",
                "startTime": 1_700_000_000_000,
            },
            {
                "_id": "fh2",
                "member": None,
                "startTime": 1_700_000_000_000,
            },
        ],
    )
    # `front_history` defaults off; opt in so the walk fires.
    job = _post_file(
        auth_client,
        source="simplyplural_file",
        payload=payload,
        options={"front_history": True},
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    warnings = _warnings(final["events"])
    assert any(
        "front-history rows" in w and "not selected for import" in w
        for w in warnings
    ), warnings
    assert any(
        "no member id" in w for w in warnings
    ), warnings


def test_sp_swaps_front_with_end_before_start(auth_client: httpx.Client):
    # A front whose end precedes its start would violate the
    # ck_fronts_ended_after_started DB constraint and abort the whole import.
    # The importer must normalise it (swap the two) and warn, not crash.
    payload = _sp_payload(
        frontHistory=[
            {
                "_id": "fh-swap",
                "member": "spm1",
                "startTime": 1_700_000_100_000,
                "endTime": 1_700_000_000_000,  # 100s BEFORE startTime
            },
        ],
    )
    job = _post_file(
        auth_client,
        source="simplyplural_file",
        payload=payload,
        options={"front_history": True},
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert any(
        "before the start time" in w and "swapped" in w
        for w in _warnings(final["events"])
    ), final["events"]


def test_sp_warns_when_group_members_or_parent_missing(auth_client: httpx.Client):
    payload = _sp_payload(
        groups=[
            {
                "_id": "spg-root",
                "name": "Root",
                "members": ["spm1", "spm-deleted"],
                "parent": "root",
            },
            {
                "_id": "spg-orphan",
                "name": "Orphan",
                "members": [],
                "parent": "spg-vanished",
            },
        ],
    )
    job = _post_file(auth_client, source="simplyplural_file", payload=payload)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    warnings = _warnings(final["events"])
    assert any(
        "group-membership references" in w for w in warnings
    ), warnings
    assert any(
        "group parent links" in w for w in warnings
    ), warnings


def _tb_payload(*, groups=None, tuppers=None) -> bytes:
    return json.dumps({
        "groups": groups or [],
        "tuppers": tuppers or [],
    }).encode()


def test_tb_warns_when_tupper_has_no_name(auth_client: httpx.Client):
    payload = _tb_payload(
        tuppers=[
            {"id": 1, "name": "Real", "group_id": None},
            {"id": 2, "name": "", "group_id": None},
            {"id": 3, "group_id": None},
        ],
    )
    job = _post_file(auth_client, source="tupperbox_file", payload=payload)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    warnings = _warnings(final["events"])
    assert any(
        "tupper rows with no name" in w for w in warnings
    ), warnings


def test_tb_warns_when_group_missing_name_or_id(auth_client: httpx.Client):
    payload = _tb_payload(
        groups=[
            {"id": 10, "name": "Real"},
            {"id": 11, "name": ""},
            {"name": "No id"},
        ],
        tuppers=[{"id": 1, "name": "Alpha", "group_id": 10}],
    )
    job = _post_file(auth_client, source="tupperbox_file", payload=payload)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    warnings = _warnings(final["events"])
    assert any(
        "group rows with no name" in w for w in warnings
    ), warnings
    assert any(
        "group rows with no id" in w for w in warnings
    ), warnings


def test_tb_warns_when_tupper_group_id_unknown(auth_client: httpx.Client):
    payload = _tb_payload(
        groups=[{"id": 10, "name": "Known"}],
        tuppers=[
            {"id": 1, "name": "Alpha", "group_id": 10},
            {"id": 2, "name": "Beta", "group_id": 999},
        ],
    )
    job = _post_file(auth_client, source="tupperbox_file", payload=payload)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert any(
        "group references on tuppers" in w
        and "not present in the export" in w
        for w in _warnings(final["events"])
    ), final["events"]


# --- Re-import idempotence (content dedup) -----------------------------------


def test_tb_reimport_skips_groups(auth_client: httpx.Client):
    payload = _tb_payload(
        tuppers=[{"id": 1, "name": "TbIdem", "group_id": 9}],
        groups=[{"id": 9, "name": "Tb Group"}],
    )
    first = _post_file(auth_client, source="tupperbox_file", payload=payload)
    drive_import_runner()
    assert wait_for_terminal(auth_client, first["id"])["status"] == "complete"

    second = _post_file(auth_client, source="tupperbox_file", payload=payload)
    drive_import_runner()
    f2 = wait_for_terminal(auth_client, second["id"])
    assert f2["status"] == "complete", f2
    assert f2["counts"].get("groups_imported", 0) == 0, f2["counts"]
    assert f2["counts"].get("groups_skipped", 0) == 1, f2["counts"]
    assert f2["counts"].get("members_skipped", 0) == 1, f2["counts"]


def test_sp_reimport_is_idempotent_and_does_not_crash(auth_client: httpx.Client):
    """Re-import with custom-field VALUES used to violate the
    UNIQUE(field_id, member_id) constraint (reused definition + skipped
    member); now it skips cleanly across every section."""
    payload = _sp_payload(
        members=[
            {
                "_id": "spm1",
                "name": "SpIdemA",
                "private": False,
                "info": {"spf1": "tea"},
            },
            {"_id": "spm2", "name": "SpIdemB", "private": False},
        ],
        customFields=[{"_id": "spf1", "name": "Sp Likes", "type": 0}],
        groups=[
            {"_id": "spg1", "name": "Sp Group", "members": ["spm1", "spm2"]}
        ],
        frontHistory=[
            {
                "_id": "spfh1",
                "member": "spm1",
                "startTime": 1748736000000,
                "endTime": 1748739600000,
                "live": False,
            }
        ],
    )
    # front_history defaults off; the idempotence claim needs it on.
    sp_options = {"front_history": True}
    first = _post_file(
        auth_client, source="simplyplural_file", payload=payload, options=sp_options
    )
    drive_import_runner()
    f1 = wait_for_terminal(auth_client, first["id"])
    assert f1["status"] == "complete", f1
    assert f1["counts"].get("custom_fields_imported", 0) == 1, f1["counts"]
    assert f1["counts"].get("fronts_imported", 0) == 1, f1["counts"]

    second = _post_file(
        auth_client, source="simplyplural_file", payload=payload, options=sp_options
    )
    drive_import_runner()
    f2 = wait_for_terminal(auth_client, second["id"])
    # The headline assertion: complete, not failed on the value
    # constraint.
    assert f2["status"] == "complete", f2
    assert f2["counts"].get("members_skipped", 0) == 2, f2["counts"]
    assert f2["counts"].get("groups_skipped", 0) == 1, f2["counts"]
    assert f2["counts"].get("fronts_skipped", 0) == 1, f2["counts"]
    assert f2["counts"].get("custom_fields_skipped", 0) == 1, f2["counts"]
    assert f2["counts"].get("fronts_imported", 0) == 0, f2["counts"]


# --- Format robustness (skylar's SP export-variant survey) ------------------
#
# Real SP exports carry shape/field variants that tidy fixtures don't. Each
# test pins one variant that previously skipped data or crashed the import.


def test_sp_imports_map_keyed_collections(auth_client: httpx.Client):
    """Some SP exports key a collection by id ({"id": {...}}) rather than using
    an array. Both shapes must import (a map previously iterated its string keys
    and crashed)."""
    payload = _sp_payload(
        members={
            "spm1": {"_id": "spm1", "name": "MapAlice", "private": False},
            "spm2": {"_id": "spm2", "name": "MapBob", "private": False},
        },
    )
    job = _post_file(auth_client, source="simplyplural_file", payload=payload)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final
    assert final["counts"].get("members_imported", 0) == 2, final["counts"]
    names = {m["name"] for m in auth_client.get("/v1/members").json()}
    assert {"MapAlice", "MapBob"}.issubset(names), names


def test_sp_parses_varied_timestamp_shapes(auth_client: httpx.Client):
    """Front timestamps come as int millis, numeric strings, zone-less ISO
    strings, and Firebase {_seconds} objects. All four yield a front; only a
    genuinely junk one is skipped with a warning."""
    payload = _sp_payload(
        members=[{"_id": "spm1", "name": "TimeAlice", "private": False}],
        frontHistory=[
            {"_id": "f-int", "member": "spm1", "startTime": 1_700_000_000_000},
            {"_id": "f-str", "member": "spm1", "startTime": "1700000100000"},
            {
                "_id": "f-iso",
                "member": "spm1",
                "startTime": "2023-11-14T16:13:20.000",
            },
            {
                "_id": "f-fb",
                "member": "spm1",
                "startTime": {"_seconds": 1_700_000_200, "_nanoseconds": 0},
            },
            {"_id": "f-junk", "member": "spm1", "startTime": "not a time"},
        ],
    )
    job = _post_file(
        auth_client,
        source="simplyplural_file",
        payload=payload,
        options={"front_history": True},
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final
    assert final["counts"].get("fronts_imported", 0) == 4, final["counts"]
    assert any(
        "unparseable startTime" in w for w in _warnings(final["events"])
    ), final["events"]


def test_sp_reads_variant_collection_keys(auth_client: httpx.Client):
    """Newer exports use customFronts (not frontStatuses) and fronters (not
    frontHistory)."""
    payload = _sp_payload(
        members=[{"_id": "spm1", "name": "VarAlice", "private": False}],
        customFronts=[{"_id": "cf1", "name": "Asleep", "private": False}],
        fronters=[{"_id": "fr1", "member": "spm1", "startTime": 1_700_000_000_000}],
    )
    job = _post_file(
        auth_client,
        source="simplyplural_file",
        payload=payload,
        options={"front_history": True},
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final
    assert final["counts"].get("custom_fronts_imported", 0) == 1, final["counts"]
    assert final["counts"].get("fronts_imported", 0) == 1, final["counts"]
    names = {m["name"] for m in auth_client.get("/v1/members").json()}
    assert "Asleep" in names, names


def test_sp_constructs_avatar_from_uuid_and_normalizes_argb(
    auth_client: httpx.Client,
):
    """avatarUuid + system-owner id builds the serve.apparyllis.com URL, and an
    8-char ARGB colour reduces to #rrggbb."""
    payload = _sp_payload(
        users=[{"_id": "ownerX", "uid": "ownerX", "username": "Sys"}],
        members=[
            {
                "_id": "spm1",
                "name": "AvAlice",
                "private": False,
                "avatarUuid": "av-123",
                "color": "#ff0088ff",
            },
        ],
    )
    job = _post_file(auth_client, source="simplyplural_file", payload=payload)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final
    member = next(
        m for m in auth_client.get("/v1/members").json() if m["name"] == "AvAlice"
    )
    assert (
        member["avatar_url"]
        == "https://serve.apparyllis.com/avatars/ownerX/av-123"
    ), member
    assert member["color"] == "#0088ff", member


def test_sp_survives_malformed_field_types(auth_client: httpx.Client):
    """Wrong-typed names/descriptions/colours coerce away instead of crashing
    the job, and no member content leaks into the event log."""
    payload = _sp_payload(
        members=[
            {"_id": "spm1", "name": "Alpha", "private": False},
            {
                "_id": "spm2",
                "name": "Beta",
                "desc": {"x": 1},
                "displayName": 7,
                "pronouns": ["they"],
                "color": 999,
                "private": False,
            },
            # Junk name falls back to "unnamed" but still imports.
            {"_id": "spm3", "name": 12345, "private": False},
        ],
    )
    job = _post_file(auth_client, source="simplyplural_file", payload=payload)
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final
    assert final["counts"].get("members_imported", 0) == 3, final["counts"]
    blob = " ".join(e["message"] for e in final["events"])
    for leaked in ("Alpha", "Beta", "12345"):
        assert leaked not in blob, blob


# --- Chat message import (Tier 2) ------------------------------------------


def test_sp_imports_chat_messages_and_skips_encrypted(auth_client: httpx.Client):
    """Plaintext chat imports to the system board; a legacy still-encrypted
    message (16-byte base64 iv + base64 ciphertext) is detected and skipped."""
    enc_iv = base64.b64encode(bytes(16)).decode()
    enc_body = base64.b64encode(bytes([0xFF, 0xFE, 0xFD, 0xFC]) * 10).decode()
    payload = _sp_payload(
        members=[{"_id": "spm1", "name": "ChatAlice", "private": False}],
        channels=[{"_id": "ch1", "name": "General"}],
        messages={
            "ch1": [
                {
                    "_id": "m1",
                    "sender": "spm1",
                    "message": "hello board",
                    "timestamp": 1_700_000_000_000,
                },
                {
                    "_id": "m2",
                    "sender": "spm1",
                    "message": enc_body,
                    "iv": enc_iv,
                    "timestamp": 1_700_000_001_000,
                },
            ],
        },
    )
    job = _post_file(
        auth_client,
        source="simplyplural_file",
        payload=payload,
        options={"messages": True},
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final
    assert final["counts"].get("messages_imported", 0) == 1, final["counts"]
    assert final["counts"].get("messages_encrypted_skipped", 0) == 1, final["counts"]
    assert any(
        "still encrypted" in w for w in _warnings(final["events"])
    ), final["events"]
    bodies = [m["body"] for m in auth_client.get("/v1/export").json()["messages"]]
    assert any(b == "hello board" for b in bodies), bodies  # single channel, no prefix


def test_sp_chat_replies_mentions_and_channel_prefix(auth_client: httpx.Client):
    """Reply chains rebuild, mention tokens rewrite to @name, and multi-channel
    chat is prefixed with the channel name."""
    payload = _sp_payload(
        members=[
            {"_id": "spm1", "name": "ChatAlice", "private": False},
            {"_id": "spm2", "name": "ChatBob", "private": False},
        ],
        channels=[
            {"_id": "ch1", "name": "General"},
            {"_id": "ch2", "name": "Random"},
        ],
        messages={
            "ch1": [
                {
                    "_id": "m1",
                    "sender": "spm1",
                    "message": "hey <###@spm2###>",
                    "timestamp": 1_700_000_000_000,
                },
                {
                    "_id": "m2",
                    "sender": "spm2",
                    "message": "reply!",
                    "replyTo": "m1",
                    "timestamp": 1_700_000_001_000,
                },
            ],
            "ch2": [
                {
                    "_id": "m3",
                    "sender": "spm1",
                    "message": "other channel",
                    "timestamp": 1_700_000_002_000,
                },
            ],
        },
    )
    job = _post_file(
        auth_client,
        source="simplyplural_file",
        payload=payload,
        options={"messages": True},
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final
    assert final["counts"].get("messages_imported", 0) == 3, final["counts"]
    msgs = auth_client.get("/v1/export").json()["messages"]
    bodies = [m["body"] for m in msgs]
    assert any(b.startswith("[General] ") for b in bodies), bodies
    assert any(b.startswith("[Random] ") for b in bodies), bodies
    assert any("@ChatBob" in b for b in bodies), bodies
    reply = next(m for m in msgs if "reply!" in m["body"])
    parent = next(m for m in msgs if "hey" in m["body"])
    assert reply["parent_message_id"] == parent["id"], (reply, parent)


def test_sp_imports_chatmessages_flat_array(auth_client: httpx.Client):
    """The alternate `chatMessages` flat-array shape (each message carries its
    own channel) imports too, via the content/writtenAt field aliases."""
    payload = _sp_payload(
        members=[{"_id": "spm1", "name": "FlatAlice", "private": False}],
        channels=[{"_id": "ch1", "name": "General"}],
        chatMessages=[
            {
                "_id": "m1",
                "channel": "ch1",
                "sender": "spm1",
                "content": "flat hello",
                "writtenAt": 1_700_000_000_000,
            },
        ],
    )
    job = _post_file(
        auth_client,
        source="simplyplural_file",
        payload=payload,
        options={"messages": True},
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final
    assert final["counts"].get("messages_imported", 0) == 1, final["counts"]
    bodies = [m["body"] for m in auth_client.get("/v1/export").json()["messages"]]
    assert any("flat hello" in b for b in bodies), bodies
