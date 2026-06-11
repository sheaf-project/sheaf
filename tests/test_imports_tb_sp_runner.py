"""End-to-end tests for the Tupperbox + SimplyPlural import runner handlers.

Both took the wrap-pattern path (Phase 5): defensive parse + hard-
failure surfacing + counts + warning-events, with the per-record walk
still inside the existing run_import. These tests verify the runner
plumbing + the failure paths plus the per-record skip warnings the
walks emit when they encounter malformed or dangling references.
"""

from __future__ import annotations

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
