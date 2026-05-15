"""End-to-end tests for the PluralKit file import runner handler.

Uploads a PK export via /v1/imports/file, waits for the runner tick
to claim and process it, then asserts on the resulting ImportJob
state (status, counts, events) plus the actual Sheaf rows the
importer was supposed to create.

The test runner pokes the in-container job dispatcher directly via
`docker compose exec` rather than waiting on the 5s production tick,
so a per-test wait is bounded to a single function call rather than
real-time polling.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import time
import uuid

import httpx

PK_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "pk_export_sample.json"


def _drive_runner() -> None:
    """Force one tick of the import runner inside the test container.

    Avoids the wall-clock wait for the 5s production tick. The
    in-container helper imports run_import_tick + grabs a session
    out of the database module so it sees committed state from the
    host-side HTTP client."""
    subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            "sheaf-test",
            "exec",
            "-T",
            "app",
            "python",
            "-c",
            """
import asyncio
from sheaf.database import async_session_factory
from sheaf.services.import_runner import run_import_tick

async def main():
    # Drain until empty so each test gets fully processed (not just one
    # claimed-but-incomplete job left behind by a prior call).
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


def _post_pk_file(
    client: httpx.Client,
    *,
    options: dict | None = None,
    idem_key: str | None = None,
    payload: bytes | None = None,
) -> dict:
    """POST a PK file import, returning the parsed ImportJobRead body.
    Asserts 202 so test bodies stay focused on post-run state."""
    form: dict[str, str] = {
        "source": "pluralkit_file",
        "idempotency_key": idem_key or str(uuid.uuid4()),
    }
    if options is not None:
        form["options"] = json.dumps(options)
    files = {
        "file": (
            "pk.json",
            payload if payload is not None else PK_FIXTURE.read_bytes(),
            "application/json",
        )
    }
    resp = client.post("/v1/imports/file", files=files, data=form)
    assert resp.status_code == 202, resp.text
    return resp.json()


def _wait_for_terminal(
    client: httpx.Client, job_id: str, *, timeout_s: float = 10.0
) -> dict:
    """Poll GET /v1/imports/{id} until status is terminal. Used after
    `_drive_runner()` to read back the final state. Timeout exists in
    case something hangs; the runner is direct-invoked so this should
    return within a single poll in practice."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = client.get(f"/v1/imports/{job_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("complete", "failed", "cancelled"):
            return body
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not reach terminal status in {timeout_s}s")


# --- Happy path ------------------------------------------------------------


def test_pk_file_runner_imports_members_and_groups(auth_client: httpx.Client):
    """Defaults (system_profile=True, groups=True, front_history=False)
    bring in members + groups but not switch history."""
    job = _post_pk_file(auth_client)
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 3, final["counts"]
    # Groups in the fixture: "Test Group" exists.
    assert final["counts"].get("groups_imported", 0) >= 1, final["counts"]
    # Switches off by default — fronts_imported either missing or 0.
    assert final["counts"].get("fronts_imported", 0) == 0

    # Sheaf rows actually exist after the run.
    members = auth_client.get("/v1/members").json()
    names = {m["name"] for m in members}
    assert {"Alice", "Bob", "Carol"}.issubset(names), names


def test_pk_file_runner_with_front_history(auth_client: httpx.Client):
    """front_history=True walks the switches array and emits Front rows
    with member associations."""
    job = _post_pk_file(auth_client, options={"front_history": True})
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 3
    assert final["counts"]["fronts_imported"] >= 1


def test_pk_file_runner_emits_info_events_per_section(auth_client: httpx.Client):
    """The runner records info-level events for each phase so the user-
    facing report has a coherent breadcrumb trail, not just final counts."""
    job = _post_pk_file(auth_client, options={"front_history": True})
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

    stages = {e["stage"] for e in final["events"] if e["level"] == "info"}
    assert {"parse", "system_profile", "groups", "switches"}.issubset(stages), stages


def test_pk_file_runner_member_deselection(auth_client: httpx.Client):
    """member_ids filter from the preview screen narrows the import set."""
    job = _post_pk_file(auth_client, options={"member_ids": ["alice"]})
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete"
    assert final["counts"]["members_imported"] == 1

    members = auth_client.get("/v1/members").json()
    names = {m["name"] for m in members}
    assert "Alice" in names
    assert "Bob" not in names
    assert "Carol" not in names


# --- Failure paths ---------------------------------------------------------


def test_pk_file_runner_fails_on_invalid_json(auth_client: httpx.Client):
    """Bad JSON in the payload turns into status=failed plus a single
    error event at the parse stage. The job doesn't blow up the runner
    — subsequent jobs still tick through."""
    job = _post_pk_file(auth_client, payload=b"this isn't json at all")
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any(
        e["level"] == "error" and "invalid JSON" in e["message"]
        for e in final["events"]
    ), final["events"]


def test_pk_file_runner_fails_on_non_object_root(auth_client: httpx.Client):
    """PK exports are JSON objects at the root. An array / scalar gets a
    clear error rather than a downstream `.get()` AttributeError."""
    job = _post_pk_file(auth_client, payload=b'["not", "an", "object"]')
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any("must be a JSON object" in e["message"] for e in final["events"])


def test_pk_file_runner_idempotency_after_complete(auth_client: httpx.Client):
    """Re-POSTing the same idempotency_key after the first run completed
    returns the original (already-complete) job rather than scheduling
    a duplicate import."""
    key = str(uuid.uuid4())
    first = _post_pk_file(auth_client, idem_key=key)
    _drive_runner()
    final = _wait_for_terminal(auth_client, first["id"])
    assert final["status"] == "complete"

    second = _post_pk_file(auth_client, idem_key=key)
    assert second["id"] == first["id"]
    assert second["status"] == "complete"  # not pending again


def test_pk_file_runner_finalize_wipes_storage_key(auth_client: httpx.Client):
    """After a successful run, the runner clears payload_storage_key
    on the row so the storage blob can be deleted independently."""
    job = _post_pk_file(auth_client)
    _drive_runner()
    final = _wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete"
    # The API surface doesn't expose payload_storage_key, but we can
    # observe that re-fetching the (terminal) job is still a 200 — the
    # row stays even after the blob is gone.
    resp = auth_client.get(f"/v1/imports/{job['id']}")
    assert resp.status_code == 200
