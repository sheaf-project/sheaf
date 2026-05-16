"""End-to-end tests for the Tupperbox + SimplyPlural import runner handlers.

Both took the wrap-pattern path (Phase 5): defensive parse + hard-
failure surfacing + counts + warning-events, with the per-record walk
still inside the existing run_import. These tests verify the runner
plumbing + the failure paths, not per-record error attribution (which
those importers don't have yet — see the deferred deep-instrumentation
task).
"""

from __future__ import annotations

import pathlib
import subprocess
import time
import uuid

import httpx

TB_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "tupperbox_export_sample.json"
SP_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "sp_export_sample.json"


def _drive_runner() -> None:
    """Drain the import runner inside the test container until empty."""
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


def _post_file(
    client: httpx.Client,
    *,
    source: str,
    payload: bytes,
    idem_key: str | None = None,
) -> dict:
    resp = client.post(
        "/v1/imports/file",
        files={"file": ("import.json", payload, "application/json")},
        data={"source": source, "idempotency_key": idem_key or str(uuid.uuid4())},
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


# --- Tupperbox -------------------------------------------------------------


def test_tupperbox_runner_imports_members(auth_client: httpx.Client):
    job = _post_file(
        auth_client, source="tupperbox_file", payload=TB_FIXTURE.read_bytes()
    )
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

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
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("invalid JSON" in e["message"] for e in final["events"])


def test_tupperbox_runner_fails_on_non_object_root(auth_client: httpx.Client):
    job = _post_file(auth_client, source="tupperbox_file", payload=b"[1, 2, 3]")
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("must be a JSON object" in e["message"] for e in final["events"])


# --- SimplyPlural ----------------------------------------------------------


def test_simplyplural_runner_imports_members(auth_client: httpx.Client):
    job = _post_file(
        auth_client, source="simplyplural_file", payload=SP_FIXTURE.read_bytes()
    )
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 2, final["counts"]

    members = auth_client.get("/v1/members").json()
    names = {m["name"] for m in members}
    assert {"SpAlice", "SpBob"}.issubset(names), names


def test_simplyplural_runner_fails_on_invalid_json(auth_client: httpx.Client):
    job = _post_file(
        auth_client, source="simplyplural_file", payload=b"{ broken"
    )
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("invalid JSON" in e["message"] for e in final["events"])


def test_simplyplural_runner_summary_event(auth_client: httpx.Client):
    """The import-stage info event names every count bucket so the
    report reads coherently even for collections that were empty."""
    job = _post_file(
        auth_client, source="simplyplural_file", payload=SP_FIXTURE.read_bytes()
    )
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

    summary = [
        e for e in final["events"]
        if e["stage"] == "import" and e["level"] == "info"
    ]
    assert summary, final["events"]
    assert "members" in summary[-1]["message"]
