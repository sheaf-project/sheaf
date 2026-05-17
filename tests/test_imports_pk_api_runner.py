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
import uuid

import httpx

from tests._import_runner_helpers import drive_import_runner, wait_for_terminal

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
    """Drive the runner with `fetch_export` monkeypatched.

    pk_import_runner does `from ... import fetch_export`, so the stub
    has to replace *that module's* binding, not pk_api's. The patch is
    injected via the shared helper's `setup` hook.

    fail_mode="" -> stub returns the export above (success path).
    fail_mode="pkapi" -> stub raises PKApiError (rejection path).
    """
    if fail_mode == "pkapi":
        body = (
            "    from sheaf.services.pk_api import PKApiError\n"
            "    raise PKApiError('PluralKit token rejected (401).', 401)\n"
        )
    else:
        body = f"    return {_STUB_EXPORT_PY}\n"
    setup = (
        "import sheaf.services.pk_import_runner as pir\n"
        "async def fake_fetch(token, *, include_switches=True):\n"
        f"{body}"
        "pir.fetch_export = fake_fetch\n"
    )
    drive_import_runner(setup=setup)


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


# --- Happy path ------------------------------------------------------------


def test_pk_api_runner_imports_fetched_system(auth_client: httpx.Client):
    """The stub fetch returns a 2-member export; the handler walks it
    into the user's system exactly like the file handler would."""
    job = _post_api_import(auth_client)
    _drive_runner_pk_api()
    final = wait_for_terminal(auth_client, job["id"])

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
    final = wait_for_terminal(auth_client, job["id"])

    fetch_events = [e for e in final["events"] if e["stage"] == "fetch"]
    assert fetch_events, final["events"]
    assert any("fetched 2 members" in e["message"] for e in fetch_events)


# --- Failure path ----------------------------------------------------------


def test_pk_api_runner_fails_on_pk_rejection(auth_client: httpx.Client):
    """A PKApiError (bad token, 404, rate limit, upstream 5xx) is a hard
    failure — status=failed with the PK-provided message surfaced."""
    job = _post_api_import(auth_client)
    _drive_runner_pk_api(fail_mode="pkapi")
    final = wait_for_terminal(auth_client, job["id"])

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
    final = wait_for_terminal(auth_client, job["id"])
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
