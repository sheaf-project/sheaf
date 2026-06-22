"""Unit tests for the OpenPlural exporter / inverse-importer transforms.

These are pure-function tests (no DB, no HTTP): they exercise
``openplural_export.build_envelope`` and ``openplural_import.to_native``
directly, plus the version guard and lineage handling. The end-to-end
job-runner behaviour (guards, dedup, image restore) is covered by
``test_imports_openplural_runner.py``.
"""

from __future__ import annotations

import pytest

from sheaf.services.import_parsing import ImportPayloadError
from sheaf.services.openplural_export import (
    OPENPLURAL_IMPL_VERSION,
    OPENPLURAL_VERSION,
    build_envelope,
)
from sheaf.services.openplural_import import inherited_lineage, parse_json, to_native

_EXPORTED_AT = "2026-06-20T00:00:00+00:00"


def _native() -> dict:
    return {
        "version": "2",
        "system": {
            "id": "s1", "name": "Sys", "description": "d", "note": "sysnote",
            "tag": "|S|", "avatar_url": "/v1/files/avatars/u/a.png",
            "color": "#fff", "privacy": "public", "date_format": "ymd",
            "replace_fronts_default": True, "coalesce_contiguous_fronts": False,
            "delete_confirmation": "password",
            "safety": {"grace_period_days": 7},
            "retention": {"journal_max_revisions": 10},
        },
        "members": [
            {
                "id": "m1", "name": "Alex", "display_name": "A",
                "description": "bio", "pronouns": "they",
                "avatar_url": "/v1/files/avatars/u/m.png", "banner_url": None,
                "color": "#000", "birthday": "1990-03-19", "pluralkit_id": "abcde",
                "emoji": "X", "is_custom_front": False, "privacy": "private",
                "note": "mn", "quick_switch_pin": "1",
                "notify_on_front_global": True, "notify_on_front_self": False,
                "notify_on_front_member_ids": ["m2"],
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            {"id": "m2", "name": "Jay", "is_custom_front": True, "birthday": "03-19"},
        ],
        "fronts": [
            {
                "id": "f1", "started_at": "2026-01-01T00:00:00+00:00",
                "ended_at": None, "member_ids": ["m1", "m2"],
                "custom_status": "busy",
            }
        ],
        "groups": [
            {"id": "g1", "name": "G", "description": None, "color": None,
             "parent_id": None, "member_ids": ["m1"]}
        ],
        "tags": [{"id": "t1", "name": "tag", "color": "#f00", "member_ids": ["m1"]}],
        "custom_fields": [
            {"id": "c1", "name": "CF", "field_type": "text", "options": None,
             "order": 0, "privacy": "public",
             "values": [{"member_id": "m1", "value": "v"}]}
        ],
        "journals": [
            {"id": "j1", "member_id": "m1", "title": "T", "body": "B",
             "visibility": "private", "author_user_id": "u",
             "author_member_ids": ["m1"], "author_member_names": ["Alex"],
             "image_keys": ["/v1/files/journals/u/x.png"],
             "created_at": "2026-01-01T00:00:00+00:00",
             "updated_at": "2026-01-01T00:00:00+00:00"}
        ],
        "revisions": [{"id": "r1", "target_type": "member_bio"}],
        "watch_tokens": [{"id": "w1", "label": "L"}],
        "uploaded_files": [{"key": "k", "size_bytes": 1}],
        "reminders": [{"id": "rm1", "name": "R"}],
        "polls": [{"id": "p1", "question": "Q?"}],
        "messages": [
            {"id": "msg1", "board_kind": "system", "board_member_id": None,
             "author_member_id": "m1", "parent_message_id": None, "body": "hi",
             "created_at": "2026-01-01T00:00:00+00:00",
             "updated_at": "2026-01-01T00:00:00+00:00"}
        ],
    }


def test_envelope_producer_and_version_stamp():
    env = build_envelope(_native(), exported_at=_EXPORTED_AT, app_version="1.1.0")
    assert env["openplural_version"] == OPENPLURAL_VERSION == "0.1"
    p = env["producer"]
    assert p["app"] == "Sheaf"
    assert p["app_id"] == "sheaf"
    assert p["app_version"] == "1.1.0"
    assert p["exporter_version"] == OPENPLURAL_IMPL_VERSION
    assert env["exported_at"] == _EXPORTED_AT


def test_core_records_mapped():
    env = build_envelope(_native(), exported_at=_EXPORTED_AT)
    assert env["systems"][0]["name"] == "Sys"
    assert env["systems"][0]["privacy"] == "public"
    # pluralkit_id becomes a source_ref, not a core member field.
    m1 = next(m for m in env["members"] if m["id"] == "m1")
    assert {"app": "pluralkit", "collection": "members", "id": "abcde"} in m1["source_refs"]
    # birthday precision derivation.
    assert m1["birthday"] == {"value": "1990-03-19", "precision": "day", "year_visible": True}
    m2 = next(m for m in env["members"] if m["id"] == "m2")
    assert m2["birthday"]["precision"] == "month_day"
    assert m2["birthday"]["year_visible"] is False
    # normalized memberships / assignments.
    assert {"group_id": "g1", "member_id": "m1"} in env["group_memberships"]
    assert env["taxonomy_terms"][0]["kind"] == "tag"
    assert {"term_id": "t1", "subject_type": "member", "subject_id": "m1"} in env[
        "taxonomy_assignments"
    ]
    # front assignments.
    fp = env["front_periods"][0]
    assert {a["member_id"] for a in fp["assignments"]} == {"m1", "m2"}
    assert fp["status"] == "busy"
    # boards module.
    assert env["boards"]["posts"][0]["body"] == "hi"
    assert "boards" in env["capabilities"]["modules"]


def test_extensions_passthrough_and_uri_only_warning():
    env = build_envelope(_native(), exported_at=_EXPORTED_AT)
    sheaf_ext = env["extensions"]["sheaf"]
    for section in ("polls", "reminders", "revisions", "watch_tokens", "uploaded_files"):
        assert sheaf_ext[section], section
    # uri-only assets warn (sync path, no bundle bytes).
    assert any(w["code"] == "asset_uri_only" for w in env["warnings"])
    # assets are uri-only: no bundle_path recorded.
    for a in env["assets"]:
        assert "bundle_path" not in (a.get("extensions", {}).get("sheaf", {}))


def test_bundle_mode_records_bundle_path():
    env = build_envelope(
        _native(), exported_at=_EXPORTED_AT, include_asset_bytes=True
    )
    # Internal refs carry a bundle_path + storage_key; no uri-only warning.
    avatars = [
        a for a in env["assets"]
        if a.get("extensions", {}).get("sheaf", {}).get("bundle_path")
    ]
    assert avatars, env["assets"]
    sk = avatars[0]["extensions"]["sheaf"]["storage_key"]
    assert avatars[0]["extensions"]["sheaf"]["bundle_path"] == f"assets/{sk}"
    assert not any(w["code"] == "asset_uri_only" for w in env["warnings"])


def test_lineage_appends_and_accumulates():
    env = build_envelope(_native(), exported_at=_EXPORTED_AT, app_version="1.1.0")
    lineage = env["extensions"]["sheaf"]["lineage"]
    assert lineage[-1] == {
        "app": "sheaf", "app_version": "1.1.0",
        "exporter_version": OPENPLURAL_IMPL_VERSION, "exported_at": _EXPORTED_AT,
    }
    # A prior hop carried in is preserved ahead of Sheaf's entry.
    prior = [{"app": "simply_plural", "exported_at": "2023-01-01T00:00:00+00:00"}]
    env2 = build_envelope(
        _native(), exported_at=_EXPORTED_AT, inherited_lineage=prior
    )
    chain = env2["extensions"]["sheaf"]["lineage"]
    assert chain[0]["app"] == "simply_plural"
    assert chain[-1]["app"] == "sheaf"
    assert inherited_lineage(env2) == chain


def test_round_trip_to_native_restores_fields():
    env = build_envelope(_native(), exported_at=_EXPORTED_AT)
    back = to_native(env)
    assert back["version"] == "2"
    assert back["system"]["name"] == "Sys"
    assert back["system"]["note"] == "sysnote"
    assert back["system"]["date_format"] == "ymd"
    assert back["system"]["safety"] == {"grace_period_days": 7}
    m1 = next(m for m in back["members"] if m["id"] == "m1")
    assert m1["name"] == "Alex"
    assert m1["pluralkit_id"] == "abcde"
    assert m1["emoji"] == "X"
    assert m1["note"] == "mn"
    assert m1["notify_on_front_member_ids"] == ["m2"]
    assert back["groups"][0]["member_ids"] == ["m1"]
    assert back["tags"][0]["member_ids"] == ["m1"]
    assert back["custom_fields"][0]["values"] == [{"member_id": "m1", "value": "v"}]
    front = back["fronts"][0]
    assert set(front["member_ids"]) == {"m1", "m2"}
    assert front["custom_status"] == "busy"
    j = back["journals"][0]
    assert j["title"] == "T" and j["member_id"] == "m1"
    assert j["author_member_names"] == ["Alex"]
    assert back["messages"][0]["body"] == "hi"
    assert back["messages"][0]["board_kind"] == "system"
    # passthrough sections restored verbatim.
    assert back["polls"][0]["question"] == "Q?"
    assert back["reminders"][0]["name"] == "R"
    assert back["watch_tokens"][0]["label"] == "L"
    assert back["revisions"][0]["target_type"] == "member_bio"


def test_import_rejects_unknown_version():
    bad = build_envelope(_native(), exported_at=_EXPORTED_AT)
    bad["openplural_version"] = "0.2"
    with pytest.raises(ImportPayloadError, match="unsupported openplural_version"):
        to_native(bad)


def test_parse_json_rejects_non_dict_and_bad_version():
    import json

    with pytest.raises(ImportPayloadError):
        parse_json(json.dumps([1, 2, 3]).encode())
    with pytest.raises(ImportPayloadError, match="unsupported openplural_version"):
        parse_json(json.dumps({"openplural_version": "9.9"}).encode())


def test_front_events_convert_to_intervals():
    """A switch-log file (front_events) becomes Sheaf interval fronts: each
    event runs until the next, an empty-assignment event is a gap, and the
    last event stays open-ended."""
    env = {
        "openplural_version": "0.1",
        "members": [{"id": "m1", "name": "A"}, {"id": "m2", "name": "B"}],
        "front_events": [
            {"id": "e1", "at": "2026-01-01T00:00:00+00:00",
             "assignments": [{"member_id": "m1"}]},
            {"id": "e2", "at": "2026-01-02T00:00:00+00:00",
             "assignments": [{"member_id": "m1"}, {"member_id": "m2"}]},
            {"id": "e3", "at": "2026-01-03T00:00:00+00:00", "assignments": []},
        ],
    }
    fronts = to_native(env)["fronts"]
    assert len(fronts) == 2  # e3 is a gap, emits no interval
    assert fronts[0]["member_ids"] == ["m1"]
    assert fronts[0]["started_at"] == "2026-01-01T00:00:00+00:00"
    assert fronts[0]["ended_at"] == "2026-01-02T00:00:00+00:00"
    assert sorted(fronts[1]["member_ids"]) == ["m1", "m2"]
    # The gap event closes the second interval.
    assert fronts[1]["ended_at"] == "2026-01-03T00:00:00+00:00"


def test_front_events_dedup_against_periods():
    """A file carrying both a period and an identical event collapses to one
    front (no double-import); the open-ended period is preserved."""
    env = {
        "openplural_version": "0.1",
        "members": [{"id": "m1", "name": "A"}],
        "front_periods": [
            {"id": "p1", "started_at": "2026-01-01T00:00:00+00:00", "ended_at": None,
             "assignments": [{"member_id": "m1"}]},
        ],
        "front_events": [
            {"id": "e1", "at": "2026-01-01T00:00:00+00:00",
             "assignments": [{"member_id": "m1"}]},
        ],
    }
    fronts = to_native(env)["fronts"]
    assert len(fronts) == 1
    assert fronts[0]["ended_at"] is None


def test_privacy_buckets_round_to_known():
    native = _native()
    native["members"][0]["privacy"] = "weird-value"
    env = build_envelope(native, exported_at=_EXPORTED_AT)
    m1 = next(m for m in env["members"] if m["id"] == "m1")
    assert m1["privacy"] == "unknown"
