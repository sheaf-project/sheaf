"""End-to-end tests for the OpenPlural import runner.

The OpenPlural importer translates an envelope back to the native shape
and delegates to the Sheaf JSON / archive importers, so these tests
build real OpenPlural payloads with ``build_envelope`` (the exporter)
and drive them through ``POST /v1/imports/file`` with
``source=openplural_file``. That makes each happy-path test a genuine
export->import round-trip. Failure paths cover the version guard, the
member-cap precheck, and the zip guards inherited from the archive path.
"""

from __future__ import annotations

import base64
import io
import json
import uuid
import zipfile

import httpx

from sheaf.services.openplural_export import build_envelope
from tests._import_runner_helpers import (
    drive_import_runner,
    set_member_limit,
    wait_for_terminal,
)

_EXPORTED_AT = "2026-06-20T00:00:00+00:00"

# 4x4 RGBA PNG that survives a real normalize_image decode pass.
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x04\x00\x00\x00\x04"
    b"\x08\x06\x00\x00\x00\xa9\xf1\x9e~\x00\x00\x00\x15IDATx\x9cc\xfc\xcf"
    b"\xc0\xf0\x9f\x01\t01\xa0\x01\xc2\x02\x00\x83\xd1\x02\x06\x02\x90\xef"
    b"X\x00\x00\x00\x00IEND\xaeB`\x82"
)
_AVATAR_KEY = "avatars/00000000-0000-0000-0000-00000000bbbb/op_avatar.png"


def _native(*, avatar: bool = False) -> dict:
    return {
        "version": "2",
        "system": {"name": "OP System", "privacy": "public"},
        "members": [
            {
                "id": "m1",
                "name": "OpIris",
                "pronouns": "they/them",
                "birthday": "1991-04-02",
                "pluralkit_id": "opabc",
                "is_custom_front": False,
                "privacy": "private",
                "avatar_url": f"/v1/files/{_AVATAR_KEY}" if avatar else None,
            },
            {"id": "m2", "name": "OpJay"},
        ],
        "fronts": [
            {
                "id": "f1",
                "started_at": "2026-01-01T00:00:00+00:00",
                "ended_at": None,
                "member_ids": ["m1"],
                "custom_status": "co-fronting",
            }
        ],
        "groups": [{"id": "g1", "name": "Inner", "member_ids": ["m1", "m2"]}],
        "tags": [{"id": "t1", "name": "host", "color": "#abc", "member_ids": ["m1"]}],
        "custom_fields": [],
        "journals": [],
    }


def _envelope_bytes(native: dict) -> bytes:
    env = build_envelope(native, exported_at=_EXPORTED_AT)
    return json.dumps(env).encode("utf-8")


def _bundle_bytes(native: dict, images: dict[str, bytes]) -> bytes:
    env = build_envelope(native, exported_at=_EXPORTED_AT, include_asset_bytes=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("openplural.json", json.dumps(env))
        zf.writestr("README.txt", "test bundle")
        for key, blob in images.items():
            zf.writestr(f"assets/{key}", blob)
    return buf.getvalue()


def _post(
    client: httpx.Client,
    payload: bytes,
    *,
    filename: str = "export.openplural.json",
    options: dict | None = None,
) -> dict:
    form: dict[str, str] = {
        "source": "openplural_file",
        "idempotency_key": str(uuid.uuid4()),
    }
    if options is not None:
        form["options"] = json.dumps(options)
    resp = client.post(
        "/v1/imports/file",
        files={"file": (filename, payload, "application/json")},
        data=form,
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def _files_list(client: httpx.Client) -> list[dict]:
    resp = client.get("/v1/files/list")
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- Happy paths -------------------------------------------------------------


def test_json_round_trip_imports_core_records(auth_client: httpx.Client):
    """A Sheaf-produced OpenPlural JSON re-imports its members, fronts,
    groups, and tags."""
    job = _post(auth_client, _envelope_bytes(_native()))
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 2, final["counts"]
    assert final["counts"]["fronts_imported"] == 1, final["counts"]
    assert final["counts"]["groups_imported"] == 1, final["counts"]
    assert final["counts"]["tags_imported"] == 1, final["counts"]

    members = {m["name"]: m for m in auth_client.get("/v1/members").json()}
    assert set(members) == {"OpIris", "OpJay"}
    # pluralkit_id survives the source_ref round-trip.
    assert members["OpIris"]["pluralkit_id"] == "opabc", members["OpIris"]


def test_bundle_round_trip_restores_avatar(auth_client: httpx.Client):
    """An .openplural.zip bundle restores the avatar blob under a fresh
    key owned by the importing user."""
    payload = _bundle_bytes(_native(avatar=True), {_AVATAR_KEY: _TINY_PNG})
    job = _post(auth_client, payload, filename="export.openplural.zip")
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"].get("images_imported", 0) == 1, final["counts"]

    files = {f["key"] for f in _files_list(auth_client)}
    assert _AVATAR_KEY not in files  # foreign key never reused
    iris = next(m for m in auth_client.get("/v1/members").json() if m["name"] == "OpIris")
    avatar_key = (iris["avatar_url"] or "").removeprefix("/v1/files/")
    assert avatar_key in files, iris["avatar_url"]


def _inline_asset_envelope(*, carrier: str) -> bytes:
    """A bare-JSON OpenPlural envelope from a foreign producer whose avatar
    bytes ride inline on the asset (not in a bundle). ``carrier`` selects
    where the bytes sit: ``uri_data`` (a ``data:`` URI a producer put in
    the ``uri`` field, as pluralport does), ``data_uri`` (the spec field),
    or ``data_base64``."""
    b64 = base64.b64encode(_TINY_PNG).decode()
    asset_id = str(uuid.uuid4())
    asset: dict = {"id": asset_id, "kind": "avatar", "mime_type": "image/png"}
    if carrier == "uri_data":
        asset["uri"] = f"data:image/png;base64,{b64}"
    elif carrier == "data_uri":
        asset["data_uri"] = f"data:image/png;base64,{b64}"
    else:
        asset["data_base64"] = b64
    env = {
        "openplural_version": "0.1",
        "exported_at": _EXPORTED_AT,
        "producer": {"app": "Pluralport", "app_id": "pluralport"},
        "systems": [{"id": "s1", "name": "OP System", "privacy": "public"}],
        "members": [
            {"id": "m1", "name": "OpIris", "avatar_asset_id": asset_id},
            {"id": "m2", "name": "OpJay"},
        ],
        "assets": [asset],
    }
    return json.dumps(env).encode("utf-8")


def _assert_inline_avatar_restored(client: httpx.Client, job_id: str) -> None:
    final = wait_for_terminal(client, job_id)
    assert final["status"] == "complete", final
    assert final["counts"].get("images_imported", 0) == 1, final["counts"]
    files = {f["key"] for f in _files_list(client)}
    members = {m["name"]: m for m in client.get("/v1/members").json()}
    iris_key = (members["OpIris"]["avatar_url"] or "").removeprefix("/v1/files/")
    assert iris_key in files, members["OpIris"]["avatar_url"]
    # The member with no avatar asset stays avatar-less.
    assert not members["OpJay"]["avatar_url"]


def test_json_inline_data_in_uri_restores_avatar(auth_client: httpx.Client):
    """A bare JSON whose avatar is a ``data:`` URI stuffed in the asset
    ``uri`` (pluralport's shape) decodes and stores it, instead of dropping
    it as an unusable external URL."""
    job = _post(auth_client, _inline_asset_envelope(carrier="uri_data"))
    drive_import_runner()
    _assert_inline_avatar_restored(auth_client, job["id"])


def test_json_inline_data_uri_field_restores_avatar(auth_client: httpx.Client):
    """The spec-correct inline carrier (``data_uri``) also restores."""
    job = _post(auth_client, _inline_asset_envelope(carrier="data_uri"))
    drive_import_runner()
    _assert_inline_avatar_restored(auth_client, job["id"])


def test_json_inline_data_base64_field_restores_avatar(auth_client: httpx.Client):
    """The other spec inline carrier (``data_base64``) also restores."""
    job = _post(auth_client, _inline_asset_envelope(carrier="data_base64"))
    drive_import_runner()
    _assert_inline_avatar_restored(auth_client, job["id"])


def test_json_inline_asset_images_off_imports_member_without_avatar(
    auth_client: httpx.Client,
):
    """With images disabled the member still imports; the inline avatar is
    dropped rather than left as a broken synthetic key."""
    job = _post(
        auth_client,
        _inline_asset_envelope(carrier="uri_data"),
        options={"images": False},
    )
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final
    assert final["counts"].get("images_imported", 0) == 0, final["counts"]
    iris = next(m for m in auth_client.get("/v1/members").json() if m["name"] == "OpIris")
    assert not iris["avatar_url"], iris


def test_preview_reports_counts_and_lineage(auth_client: httpx.Client):
    """The preview endpoint summarises without writing, and surfaces the
    lineage length of the file."""
    resp = auth_client.post(
        "/v1/import/openplural/preview",
        files={"file": ("e.json", _envelope_bytes(_native()), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["system_name"] == "OP System"
    assert body["member_count"] == 2
    assert body["front_count"] == 1
    assert body["archive"] is False
    # Sheaf stamped one lineage entry on export.
    assert body["lineage_length"] == 1
    # Preview must not have created anything.
    assert auth_client.get("/v1/members").json() == []


def test_front_events_imported_as_fronts(auth_client: httpx.Client):
    """A switch-log style file (front_events, no front_periods) imports its
    fronting history as intervals instead of dropping it."""
    env = {
        "openplural_version": "0.1",
        "producer": {"app": "PluralKit", "app_id": "pluralkit"},
        "systems": [{"id": "s1", "name": "SwitchLog Sys", "privacy": "public"}],
        "members": [
            {"id": "m1", "name": "EventIris", "privacy": "private"},
            {"id": "m2", "name": "EventJay", "privacy": "private"},
        ],
        "front_events": [
            {"id": "e1", "at": "2026-01-01T00:00:00+00:00",
             "assignments": [{"member_id": "m1"}]},
            {"id": "e2", "at": "2026-01-02T00:00:00+00:00",
             "assignments": [{"member_id": "m1"}, {"member_id": "m2"}]},
        ],
    }
    job = _post(auth_client, json.dumps(env).encode())
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 2, final["counts"]
    assert final["counts"]["fronts_imported"] == 2, final["counts"]


def _foreign_envelope_bytes() -> bytes:
    """An envelope as if from another app, carrying data Sheaf cannot model."""
    env = {
        "openplural_version": "0.1",
        "producer": {"app": "Prism", "app_id": "prism"},
        "systems": [{"id": "s1", "name": "Foreign Sys", "privacy": "public"}],
        "members": [{"id": "m1", "name": "ForeignIris", "privacy": "private"}],
        "taxonomy_terms": [
            {"id": "t1", "kind": "tag", "name": "host"},
            {"id": "r1", "kind": "role", "name": "protector"},
        ],
        "taxonomy_assignments": [
            {"term_id": "r1", "subject_type": "member", "subject_id": "m1"},
        ],
        "chat": {"messages": [{"id": "x1", "body": "secret chat"}]},
        "relationships": {"edges": [{"a": "m1", "b": "m1", "type": "self"}]},
        "extensions": {"prism": {"theme": "dark"}},
    }
    return json.dumps(env).encode("utf-8")


def test_foreign_data_preserved_and_re_exported(auth_client: httpx.Client):
    """Data Sheaf cannot model (foreign extensions, chat/relationships,
    non-tag taxonomy) survives an import and comes back out on a Sheaf
    OpenPlural export, instead of being dropped."""
    job = _post(auth_client, _foreign_envelope_bytes())
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 1, final["counts"]
    # The preserve stage recorded what it kept.
    assert any(
        e["stage"] == "preserve" and "preserved" in e["message"]
        for e in final["events"]
    ), final["events"]

    # Re-export as OpenPlural: the foreign residual is merged back in.
    resp = auth_client.get("/v1/export", params={"format": "openplural"})
    assert resp.status_code == 200, resp.text
    env = resp.json()
    assert env["extensions"]["prism"] == {"theme": "dark"}
    assert "sheaf" in env["extensions"]
    assert env["chat"]["messages"][0]["body"] == "secret chat"
    assert env["relationships"]["edges"][0]["type"] == "self"
    assert "chat" in env["capabilities"]["modules"]
    assert any(t.get("id") == "r1" for t in env["taxonomy_terms"]), env["taxonomy_terms"]


# --- Failure paths -----------------------------------------------------------


def test_unknown_version_fails(auth_client: httpx.Client):
    env = build_envelope(_native(), exported_at=_EXPORTED_AT)
    env["openplural_version"] = "0.2"
    job = _post(auth_client, json.dumps(env).encode())
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any(
        "unsupported openplural_version" in e["message"] for e in final["events"]
    ), final["events"]


def test_member_cap_fails_before_writes(auth_client: httpx.Client):
    set_member_limit(auth_client, 1)
    job = _post(auth_client, _envelope_bytes(_native()))
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("limited to" in e["message"] for e in final["events"]), final["events"]
    assert auth_client.get("/v1/members").json() == []


def test_garbage_json_fails(auth_client: httpx.Client):
    job = _post(auth_client, b"this is not json at all {{{")
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "failed", final


def test_bundle_rejects_missing_openplural_json(auth_client: httpx.Client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data.json", "{}")
    job = _post(auth_client, buf.getvalue(), filename="x.openplural.zip")
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any(
        "must contain openplural.json" in e["message"] for e in final["events"]
    ), final["events"]


def test_bundle_rejects_decompression_bomb(auth_client: httpx.Client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("openplural.json", b" " * (260 * 1024 * 1024))
    job = _post(auth_client, buf.getvalue(), filename="x.openplural.zip")
    drive_import_runner()
    final = wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any(
        "decompresses to more than" in e["message"] for e in final["events"]
    ), final["events"]
