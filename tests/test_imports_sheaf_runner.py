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
