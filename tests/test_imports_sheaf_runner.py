"""End-to-end tests for the Sheaf native re-import runner handler.

Wrap-pattern handler (Phase 6). Verifies the runner plumbing + the
failure paths. The Sheaf export shape is built inline rather than from
a fixture file, matching the existing test_sheaf_import.py convention.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid

import httpx

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


def _drive_runner() -> None:
    subprocess.run(
        [
            "docker", "compose", "-p", "sheaf-test", "exec", "-T", "app",
            "python", "-c",
            """
import asyncio
from sheaf.database import async_session_factory
from sheaf.services.import_runner import run_import_tick

async def main():
    while True:
        async with async_session_factory() as db:
            result = await run_import_tick(db)
        if result.get("items_processed", 0) == 0:
            return
asyncio.run(main())
""",
        ],
        check=True,
        timeout=60,
    )


def _post_file(client: httpx.Client, *, payload: bytes) -> dict:
    resp = client.post(
        "/v1/imports/file",
        files={"file": ("sheaf.json", payload, "application/json")},
        data={"source": "sheaf_file", "idempotency_key": str(uuid.uuid4())},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def _wait_for_terminal(
    client: httpx.Client, job_id: str, *, timeout_s: float = 10.0
) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = client.get(f"/v1/imports/{job_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("complete", "failed", "cancelled"):
            return body
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} not terminal in {timeout_s}s")


def test_sheaf_runner_imports_members(auth_client: httpx.Client):
    job = _post_file(auth_client, payload=json.dumps(_SHEAF_EXPORT).encode())
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

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
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete", final
    # The exported member comes back in on re-import (alongside the
    # original — re-import is additive, not a replace).
    assert final["counts"]["members_imported"] >= 1, final["counts"]


def test_sheaf_runner_fails_on_invalid_json(auth_client: httpx.Client):
    job = _post_file(auth_client, payload=b"not json")
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("invalid JSON" in e["message"] for e in final["events"])


def test_sheaf_runner_fails_on_non_object_root(auth_client: httpx.Client):
    job = _post_file(auth_client, payload=b'"just a string"')
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("must be a JSON object" in e["message"] for e in final["events"])
