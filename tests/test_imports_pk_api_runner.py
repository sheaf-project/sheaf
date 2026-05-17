"""End-to-end tests for the PluralKit API import runner handler.

The handler calls `fetch_export`, which in production hits
api.pluralkit.me. These tests can't reach the real API, so the
in-container runner-drive script monkeypatches
`pk_import_runner.fetch_export` (the name the handler actually
resolves — it's a `from`-import, so patching pk_api directly wouldn't
take) with a stub before ticking the runner.
"""

from __future__ import annotations

import subprocess
import time
import uuid

import httpx

# Minimal valid PK-export shape the stub fetch returns. One member,
# no groups, no switches — enough to prove the handler walks the
# canonical export dict the same way the file handler does.
_STUB_EXPORT_PY = """{
    "version": 2,
    "id": "stubsy",
    "name": "Stub API System",
    "tag": "stub",
    "members": [
        {"id": "stbm1", "name": "ApiAlice"},
        {"id": "stbm2", "name": "ApiBob"}
    ],
    "groups": [],
    "switches": []
}"""


def _drive_runner_pk_api(*, fail_mode: str = "") -> None:
    """Tick the import runner inside the container with fetch_export
    stubbed.

    fail_mode="" -> stub returns the export above (success path).
    fail_mode="pkapi" -> stub raises PKApiError (rejection path).
    """
    if fail_mode == "pkapi":
        stub = (
            "async def fake_fetch(token, *, include_switches=True):\n"
            "    from sheaf.services.pk_api import PKApiError\n"
            "    raise PKApiError('PluralKit token rejected (401).', 401)\n"
        )
    else:
        stub = (
            "async def fake_fetch(token, *, include_switches=True):\n"
            f"    return {_STUB_EXPORT_PY}\n"
        )
    script = f"""
import asyncio
from sheaf.database import async_session_factory
from sheaf.services.import_runner import run_import_tick
import sheaf.services.pk_import_runner as pir

{stub}
pir.fetch_export = fake_fetch

async def main():
    while True:
        async with async_session_factory() as db:
            result = await run_import_tick(db)
        if result.get("items_processed", 0) == 0:
            return
asyncio.run(main())
"""
    subprocess.run(
        ["docker", "compose", "-p", "sheaf-test", "exec", "-T", "app", "python", "-c", script],
        check=True,
        timeout=60,
    )


def _post_api_import(client: httpx.Client, *, idem_key: str | None = None) -> dict:
    resp = client.post(
        "/v1/imports/api",
        json={
            "source": "pluralkit_api",
            "idempotency_key": idem_key or str(uuid.uuid4()),
            "pk_token": "fake-token-for-test",
        },
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


# --- Happy path ------------------------------------------------------------


def test_pk_api_runner_imports_fetched_system(auth_client: httpx.Client):
    """The stub fetch returns a 2-member export; the handler walks it
    into the user's system exactly like the file handler would."""
    job = _post_api_import(auth_client)
    _drive_runner_pk_api()
    final = _wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "complete", final
    assert final["counts"]["members_imported"] == 2, final["counts"]

    members = auth_client.get("/v1/members").json()
    names = {m["name"] for m in members}
    assert {"ApiAlice", "ApiBob"}.issubset(names), names


def test_pk_api_runner_emits_fetch_events(auth_client: httpx.Client):
    """The handler records fetch-stage info events so the report shows
    the live-pull step distinctly from the file-parse step."""
    job = _post_api_import(auth_client)
    _drive_runner_pk_api()
    final = _wait_for_terminal(auth_client, job["id"])

    fetch_events = [e for e in final["events"] if e["stage"] == "fetch"]
    assert fetch_events, final["events"]
    assert any("fetched 2 members" in e["message"] for e in fetch_events)


# --- Failure path ----------------------------------------------------------


def test_pk_api_runner_fails_on_pk_rejection(auth_client: httpx.Client):
    """A PKApiError (bad token, 404, rate limit, upstream 5xx) is a hard
    failure — status=failed with the PK-provided message surfaced."""
    job = _post_api_import(auth_client)
    _drive_runner_pk_api(fail_mode="pkapi")
    final = _wait_for_terminal(auth_client, job["id"])

    assert final["status"] == "failed", final
    assert any(
        e["level"] == "error" and "PluralKit" in e["message"]
        for e in final["events"]
    ), final["events"]


# --- Credential handling ---------------------------------------------------


def test_pk_api_runner_wipes_credential_on_finalize(auth_client: httpx.Client):
    """After the job finalizes, the encrypted PK token must be gone from
    payload_metadata — checked directly against the DB row since the
    API response never exposes payload_metadata."""
    job = _post_api_import(auth_client)
    _drive_runner_pk_api()
    final = _wait_for_terminal(auth_client, job["id"])
    assert final["status"] == "complete"

    # Inspect the row in-container: encrypted_credential must be absent.
    script = f"""
import asyncio
from sqlalchemy import select
from sheaf.database import async_session_factory
from sheaf.models.import_job import ImportJob

async def main():
    async with async_session_factory() as db:
        row = (await db.execute(
            select(ImportJob).where(ImportJob.id == '{job["id"]}')
        )).scalar_one()
        meta = row.payload_metadata or {{}}
        assert 'encrypted_credential' not in meta, (
            f'credential not wiped: {{list(meta)}}'
        )
        print('credential-wiped-ok')

asyncio.run(main())
"""
    result = subprocess.run(
        ["docker", "compose", "-p", "sheaf-test", "exec", "-T", "app", "python", "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "credential-wiped-ok" in result.stdout, result.stdout + result.stderr
