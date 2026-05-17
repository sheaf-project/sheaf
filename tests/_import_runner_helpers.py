"""Shared helpers for the import-runner test suites.

The import-runner background loop is disabled in the test stack
(IMPORT_RUNNER_ENABLED=false), so tests drive it explicitly. This
module owns the single copy of that drive logic — previously
copy-pasted across every test_imports_*_runner.py file.

Not a `test_*.py` module, so pytest does not collect it.
"""

from __future__ import annotations

import subprocess
import time

import httpx


def drive_import_runner(*, setup: str = "") -> None:
    """Drain the import runner inside the test container until empty.

    Each pass claims and processes one pending job; we loop until a
    tick reports nothing processed, so a test's job is fully handled
    before the call returns.

    `setup` is optional Python injected before the drain loop — used
    by the PK-API tests to monkeypatch `fetch_export` so the runner
    doesn't make a real network call.
    """
    script = f"""
import asyncio
from sheaf.database import async_session_factory
from sheaf.services.import_runner import run_import_tick
{setup}
async def main():
    while True:
        async with async_session_factory() as db:
            result = await run_import_tick(db)
        if result.get("items_processed", 0) == 0:
            return
asyncio.run(main())
"""
    subprocess.run(
        [
            "docker", "compose", "-p", "sheaf-test", "exec", "-T", "app",
            "python", "-c", script,
        ],
        check=True,
        timeout=60,
    )


def wait_for_terminal(
    client: httpx.Client, job_id: str, *, timeout_s: float = 10.0
) -> dict:
    """Poll GET /v1/imports/{id} until the job reaches a terminal
    status, then return the final job body."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = client.get(f"/v1/imports/{job_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("complete", "failed", "cancelled"):
            return body
        time.sleep(0.2)
    raise AssertionError(f"import job {job_id} did not finish in {timeout_s}s")
