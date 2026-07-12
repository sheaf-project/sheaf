"""End-to-end coverage for the System.timezone display preference.

Counterpart to test_patch_null_rejection.py: timezone is the one system pref
where explicit `null` is *accepted* (null = auto = device-local), so it needs
its own null-is-fine assertion plus the unknown-zone rejection and the export
round-trip. Needs the docker stack (auth_client)."""

from __future__ import annotations

import httpx


def test_default_timezone_is_null(auth_client: httpx.Client):
    sys = auth_client.get("/v1/systems/me").json()
    assert sys["timezone"] is None


def test_patch_sets_and_reads_timezone(auth_client: httpx.Client):
    resp = auth_client.patch(
        "/v1/systems/me", json={"timezone": "America/New_York"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone"] == "America/New_York"
    # Persisted, not just echoed.
    assert auth_client.get("/v1/systems/me").json()["timezone"] == "America/New_York"


def test_patch_accepts_generic_zone(auth_client: httpx.Client):
    resp = auth_client.patch("/v1/systems/me", json={"timezone": "EST"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone"] == "EST"


def test_patch_null_sets_auto(auth_client: httpx.Client):
    # Pin a zone, then clear it back to auto with an explicit null.
    auth_client.patch("/v1/systems/me", json={"timezone": "Europe/London"})
    resp = auth_client.patch("/v1/systems/me", json={"timezone": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone"] is None


def test_patch_rejects_unknown_timezone(auth_client: httpx.Client):
    resp = auth_client.patch(
        "/v1/systems/me", json={"timezone": "Mars/Olympus_Mons"}
    )
    assert resp.status_code == 422


def test_omitted_timezone_left_unchanged(auth_client: httpx.Client):
    auth_client.patch("/v1/systems/me", json={"timezone": "Asia/Tokyo"})
    # A PATCH that doesn't mention timezone must not reset it.
    auth_client.patch("/v1/systems/me", json={"name": "Renamed"})
    assert auth_client.get("/v1/systems/me").json()["timezone"] == "Asia/Tokyo"


def test_export_includes_timezone(auth_client: httpx.Client):
    auth_client.patch("/v1/systems/me", json={"timezone": "America/Chicago"})
    export = auth_client.get("/v1/export").json()
    assert export["system"]["timezone"] == "America/Chicago"
